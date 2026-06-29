"""Batched, crash-resilient tracking runner.

Large query sets (thousands of queries) are split into fixed-size batches. A
checkpoint is persisted to disk after **every batch**, holding the rows fetched
so far and the index of the next unprocessed query. So if the run stops for any
reason — a Serper block, a browser refresh, a Streamlit Cloud restart or an
outright crash — finished work is never lost and the run can be *resumed* from
where it left off instead of re-spending API credits on queries already done.

This directly addresses the failure mode where a 6000-query run died part-way
through and the spent API credits produced no usable output.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from core import config_store, serper

# Config keys snapshotted into the checkpoint so a resumed run uses the exact
# same search parameters it started with.
_SNAPSHOT_KEYS = (
    "brand_name", "region", "language", "device", "num_pages", "delay_ms",
    "proxy", "batch_size",
)

ProgressCb = Callable[[int, int, int], None]
BatchCb = Callable[[dict[str, Any]], None]


def batch_bounds(total: int, batch_size: int) -> list[tuple[int, int]]:
    """Half-open ``[start, end)`` index ranges, one per batch."""
    batch_size = max(1, int(batch_size))
    return [(s, min(s + batch_size, total)) for s in range(0, total, batch_size)]


def init_run(queries: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Create and persist a fresh checkpoint for a new tracking run."""
    run_id = config_store.new_run_id()
    checkpoint = {
        "run_id": run_id,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signature": config_store.query_signature(queries),
        "config": {k: config.get(k) for k in _SNAPSHOT_KEYS},
        "batch_size": int(config.get("batch_size", 500) or 500),
        "queries": queries,
        "rows": [],
        "next_index": 0,
        "errors": 0,
        "status": "running",
    }
    config_store.save_checkpoint(checkpoint)
    return checkpoint


def run(
    api_key: str,
    checkpoint: dict[str, Any],
    progress_cb: ProgressCb | None = None,
    batch_cb: BatchCb | None = None,
    max_batches: int = 0,
) -> dict[str, Any]:
    """Process batches starting from ``checkpoint['next_index']``.

    ``max_batches`` limits how many batches to run in this call (``0`` = all
    remaining). The checkpoint is saved to disk after each completed batch and
    again before a :class:`SerperError` is re-raised, so progress always
    survives. Returns the (mutated) checkpoint; ``status`` is ``"complete"`` when
    every query is done, otherwise ``"paused"`` (or ``"blocked"`` if it raised).
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
        for idx in range(max(b_start, checkpoint["next_index"]), b_end):
            item = queries[idx]
            try:
                checkpoint["rows"].extend(serper.track_query(api_key, item, config, proxy))
            except serper.SerperError:
                # Unrecoverable (bad key / geo-block / persistent rate limit):
                # persist progress, then bubble up so the UI can show the cause.
                checkpoint["status"] = "blocked"
                config_store.save_checkpoint(checkpoint)
                raise
            except Exception:  # per-query network/parse failure (PRD §8)
                checkpoint["errors"] += 1
            checkpoint["next_index"] = idx + 1
            if progress_cb:
                progress_cb(checkpoint["next_index"], total, checkpoint["errors"])
            if delay and idx < total - 1:
                time.sleep(delay)
        config_store.save_checkpoint(checkpoint)  # checkpoint after each batch
        ran += 1
        if batch_cb:
            batch_cb(checkpoint)

    checkpoint["status"] = "complete" if checkpoint["next_index"] >= total else "paused"
    config_store.save_checkpoint(checkpoint)
    return checkpoint
