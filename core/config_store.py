"""Read/write persistence for global config, templates and run history.

All state lives as JSON files under the ``data/`` directory so that templates
and configuration survive app restarts (PRD §6 Persistence, §7 Data Flow).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUTS_DIR = BASE_DIR / "outputs"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"

CONFIG_PATH = DATA_DIR / "config.json"
TEMPLATES_PATH = DATA_DIR / "templates.json"
HISTORY_PATH = DATA_DIR / "runs_history.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "brand_name": "",
    "brand_logo_path": "",
    "region": "ir",
    "language": "fa",
    "device": "desktop",
    "num_pages": 1,
    "delay_ms": 200,
    "proxy": "",
    "batch_size": 250,
}

# Keys that must never be written to the shared config file (PRD §6 Security).
# The Serper API key lives only in per-session state (see core/secrets.py).
SECRET_KEYS = {"serper_api_key"}


def _ensure_dirs() -> None:
    for d in (DATA_DIR, ASSETS_DIR, OUTPUTS_DIR, CHECKPOINTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    _ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Global configuration
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    stored = _read_json(CONFIG_PATH, {})
    # Defensively strip any secret that an older file version may have saved.
    for k in SECRET_KEYS:
        stored.pop(k, None)
    cfg.update(stored)
    return cfg


def save_config(config: dict[str, Any]) -> None:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    for k in SECRET_KEYS:
        merged.pop(k, None)  # never persist secrets to the shared file
    _write_json(CONFIG_PATH, merged)


def is_brand_configured(config: dict[str, Any]) -> bool:
    """Non-secret readiness check (brand identity). The API key is checked
    separately via core.secrets because it is session-scoped."""
    return bool(config.get("brand_name"))


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def load_templates() -> list[dict[str, Any]]:
    return _read_json(TEMPLATES_PATH, [])


def save_templates(templates: list[dict[str, Any]]) -> None:
    _write_json(TEMPLATES_PATH, templates)


def add_template(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Template text cannot be empty.")
    templates = load_templates()
    record = {"id": uuid.uuid4().hex, "text": text}
    templates.append(record)
    save_templates(templates)
    return record


def update_template(template_id: str, text: str) -> None:
    text = text.strip()
    if not text:
        raise ValueError("Template text cannot be empty.")
    templates = load_templates()
    for tpl in templates:
        if tpl["id"] == template_id:
            tpl["text"] = text
            break
    save_templates(templates)


def delete_template(template_id: str) -> None:
    templates = [t for t in load_templates() if t["id"] != template_id]
    save_templates(templates)


# --------------------------------------------------------------------------- #
# Run history
# --------------------------------------------------------------------------- #
def load_history() -> list[dict[str, Any]]:
    return _read_json(HISTORY_PATH, [])


def add_history_entry(entry: dict[str, Any]) -> None:
    history = load_history()
    history.insert(0, entry)
    _write_json(HISTORY_PATH, history)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_logo(uploaded_file) -> str:
    """Persist an uploaded logo to assets/ and return its path."""
    _ensure_dirs()
    suffix = Path(uploaded_file.name).suffix or ".png"
    dest = ASSETS_DIR / f"brand_logo{suffix}"
    with dest.open("wb") as fh:
        fh.write(uploaded_file.getbuffer())
    return str(dest)


# --------------------------------------------------------------------------- #
# Run checkpoints — crash-safe state for batched tracking (see batch_runner).
# A checkpoint holds the full query list, the rows fetched so far and the index
# of the next unprocessed query, so an interrupted run can resume without
# re-spending API credits on work already done.
# --------------------------------------------------------------------------- #
def query_signature(queries: list[dict[str, Any]]) -> str:
    """Stable short fingerprint of a query set, used to match a resumable run."""
    h = hashlib.sha256()
    for q in queries:
        h.update(str(q.get("query", "")).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _checkpoint_path(run_id: str) -> Path:
    return CHECKPOINTS_DIR / f"{run_id}.json"


def save_checkpoint(checkpoint: dict[str, Any]) -> None:
    _write_json(_checkpoint_path(checkpoint["run_id"]), checkpoint)


def load_checkpoint(run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    return _read_json(_checkpoint_path(run_id), None)


def list_checkpoints() -> list[dict[str, Any]]:
    if not CHECKPOINTS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in CHECKPOINTS_DIR.glob("*.json"):
        cp = _read_json(p, None)
        if cp:
            out.append(cp)
    out.sort(key=lambda c: c.get("created", ""), reverse=True)
    return out


def delete_checkpoint(run_id: str) -> None:
    p = _checkpoint_path(run_id)
    if p.exists():
        p.unlink()


def find_resumable(signature: str) -> dict[str, Any] | None:
    """Most recent unfinished checkpoint matching this query set, if any."""
    for cp in list_checkpoints():
        if cp.get("signature") == signature and cp.get("status") != "complete":
            return cp
    return None
