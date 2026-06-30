"""Batched, crash-resilient tracking runner.

Large query sets (thousands of queries) are split into fixed-size batches. After
**every batch** progress is persisted: a local checkpoint always, and — when a
durable sink is supplied (e.g. :mod:`core.sheets_sink`) — the result rows are
also streamed to external storage and then *dropped from memory*. That keeps
memory flat regardless of run size and means an interrupted run (Serper block,
browser refresh, Streamlit Cloud reboot or crash) loses no finished work and can
be *resumed* without re-spending API credits on queries already done.

A ``sink`` is any object exposing ``start_run(cp)``, ``write_batch(cp, rows)``
and ``read_all_rows(run_id)``.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Protocol

from core import config_store, serper

# Config keys snapshotted into the checkpoint so a resumed run uses the exact
# same search parameters it started with.
_SNAPSHOT_KEYS = (
    "brand_name", "region", "language", "device", "num_pages", "delay_ms",
    "proxy", "batch_size",
)

ProgressCb = Callable[[int, int, int], None]
BatchCb = Callable[[dict[str, Any]], None]


class Sink(Protocol):
    def start_run(self, checkpoint: dict[str, Any]) -> None: ...
    def write_batch(self, checkpoint: dict[str, Any], rows: list[dict[str, Any]]) -> None: ...
    def read_all_rows(self, run_id: str) -> list[dict[str, Any]]: ...


def batch_bounds(total: int, batch_size: int) -> list[tuple[int, int]]:
    """Half-open ``[start, end)`` index ranges, one per batch."""
    batch_size = max(1, int(batch_size))
    return [(s, min(s + batch_size, total)) for s in range(0, total, batch_size)]


def _make_checkpoint(
    queries: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    run_id: str,
    created: str,
    next_index: int = 0,
    errors: int = 0,
    status: str = "running",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created": created,
        "signature": config_store.query_signature(queries),
        "config": {k: config.get(k) for k in _SNAPSHOT_KEYS},
        "batch_size": int(config.get("batch_size", 500) or 500),
        "queries": queries,
        "rows": [],
        "next_index": int(next_index),
        "errors": int(errors),
        "status": status,
    }


def init_run(queries: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Create and persist a fresh checkpoint for a new tracking run."""
    cp = _make_checkpoint(
        queries, config,
        run_id=config_store.new_run_id(),
        created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    config_store.save_checkpoint(cp)
    return cp


def resume_run(
    queries: list[dict[str, Any]],
    config: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild a checkpoint from an external progress record (e.g. after a reboot
    wiped local disk). ``queries`` must be regenerated identically (same coin
    list + templates) so the signature matches."""
    cp = _make_checkpoint(
        queries, config,
        run_id=existing["run_id"],
        created=existing.get("created", ""),
        next_index=existing.get("next_index", 0),
        errors=existing.get("errors", 0),
        status=existing.get("status", "paused"),
    )
    cp["batch_size"] = int(existing.get("batch_size") or cp["batch_size"])
    config_store.save_checkpoint(cp)
    return cp


def run(
    api_key: str,
    checkpoint: dict[str, Any],
    sink: Sink | None = None,
    progress_cb: ProgressCb | None = None,
    batch_cb: BatchCb | None = None,
    max_batches: int = 0,
) -> dict[str, Any]:
    """Process batches starting from ``checkpoint['next_index']``.

    ``max_batches`` limits how many batches to run in this call (``0`` = all
    remaining). After each batch, rows are flushed (to ``sink`` if given, else
    accumulated in ``checkpoint['rows']``) and the local checkpoint is saved. On
    an unrecoverable :class:`SerperError` the partial batch is still flushed
    before the error is re-raised. Returns the (mutated) checkpoint.
    """
    queries = checkpoint["queries"]
    config = checkpoint["config"]
    total = len(queries)
    delay = float(config.get("delay_ms", 200)) / 1000.0
    proxy = config.get("proxy", "")
    checkpoint["status"] = "running"

    ran = 0
    for b_start, b_end in batch_bounds(total, checkpoint["batch_size"]):
        if b_end <= checkpoint["next_index"]:
            continue  # whole batch already finished in a previous call
        if max_batches and ran >= max_batches:
            break

        pending: list[dict[str, Any]] = []
        try:
            for idx in range(max(b_start, checkpoint["next_index"]), b_end):
                item = queries[idx]
                try:
                    pending.extend(serper.track_query(api_key, item, config, proxy))
                except serper.SerperError:
                    checkpoint["status"] = "blocked"
                    raise
                except Exception:  # per-query network/parse failure (PRD §8)
                    checkpoint["errors"] += 1
                checkpoint["next_index"] = idx + 1
                if progress_cb:
                    progress_cb(checkpoint["next_index"], total, checkpoint["errors"])
                if delay and idx < total - 1:
                    time.sleep(delay)
        finally:
            # Always durably persist whatever this batch produced — a full batch
            # or a partial one cut short by a block — so credits are never wasted.
            if sink is not None:
                # Local backup first, then push to the durable sink. If the sink
                # write fails, the rows are still on local disk and the error
                # surfaces (rather than being silently lost).
                checkpoint["rows"] = pending
                config_store.save_checkpoint(checkpoint)
                sink.write_batch(checkpoint, pending)
                checkpoint["rows"] = []  # sink succeeded -> free memory (stay flat)
            else:
                checkpoint["rows"].extend(pending)
                config_store.save_checkpoint(checkpoint)

        ran += 1
        if batch_cb:
            batch_cb(checkpoint)

    if checkpoint["next_index"] >= total:
        checkpoint["status"] = "complete"
    elif checkpoint["status"] != "blocked":
        checkpoint["status"] = "paused"
    if sink is not None:
        sink.write_batch(checkpoint, [])  # flush final status to the index
    config_store.save_checkpoint(checkpoint)
    return checkpoint
