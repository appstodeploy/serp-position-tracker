"""Durable result storage in Google Sheets.

Streamlit Community Cloud gives each app an *ephemeral* disk that is wiped on
every platform reboot — so for large runs (thousands of queries) the local
checkpoint is not enough: a reboot loses everything. This module streams result
rows into a Google Sheet the user owns, after every batch, so progress survives
any restart and can be watched live / downloaded at any time.

Layout inside the user's spreadsheet (one Google Sheet, ``gsheet_id`` in
secrets):
  * ``_runs``      — index/progress tab: one row per run with ``next_index`` so
                     an interrupted run can resume.
  * ``r_<run_id>`` — one tab per run holding the result rows.

Configuration (Streamlit secrets / .streamlit/secrets.toml)::

    gsheet_id = "the-id-from-your-sheet-url"

    [gcp_service_account]
    type = "service_account"
    project_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "name@project.iam.gserviceaccount.com"
    ...

Share the Google Sheet with ``client_email`` as **Editor**.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from core import config_store

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Result columns persisted to the per-run worksheet (order matters).
ROW_COLUMNS = [
    "Query", "Template", "Coin (fa_name)", "Coin (en_name)", "Coin (symbol)",
    "Position", "Page", "Domain", "Title", "URL", "Snippet", "Is Brand",
]
_RUNS_HEADER = [
    "run_id", "signature", "created", "total", "batch_size",
    "next_index", "errors", "status",
]
_RUNS_TAB = "_runs"


# --------------------------------------------------------------------------- #
# Configuration / connection
# --------------------------------------------------------------------------- #
def _sheet_id() -> str:
    try:
        return str(st.secrets.get("gsheet_id", "")).strip()
    except Exception:
        return ""


def is_configured() -> bool:
    """True when a service account and target sheet are present in secrets."""
    try:
        return ("gcp_service_account" in st.secrets) and bool(_sheet_id())
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def _spreadsheet():
    import gspread
    from google.oauth2.service_account import Credentials

    info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(_sheet_id())


def check_access() -> tuple[bool, str]:
    """Probe the connection so the Config page can report a precise error."""
    if not is_configured():
        return False, "Google Sheets not configured (missing gsheet_id or gcp_service_account)."
    try:
        ss = _spreadsheet()
        return True, f"Connected to Google Sheet: {ss.title!r}."
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the user
        return False, (f"Could not open the sheet ({type(exc).__name__}: {exc}). "
                       "Check gsheet_id and that the sheet is shared with the "
                       "service account's client_email as Editor.")


# --------------------------------------------------------------------------- #
# Worksheet helpers
# --------------------------------------------------------------------------- #
def _results_tab(run_id: str) -> str:
    return f"r_{run_id}"


def _get_or_create_ws(ss, title: str, header: list[str]):
    import gspread
    try:
        return ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1, cols=max(len(header), 1))
        if header:
            ws.append_row(header, value_input_option="RAW")
        return ws


def _row_from_dict(r: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    for c in ROW_COLUMNS:
        v = r.get(c, "")
        out.append("TRUE" if (c == "Is Brand" and v) else ("" if v is None else v))
    return out


def _dict_from_row(values: list[str]) -> dict[str, Any]:
    d = {c: (values[i] if i < len(values) else "") for i, c in enumerate(ROW_COLUMNS)}
    d["Is Brand"] = str(d.get("Is Brand", "")).strip().upper() == "TRUE"
    return d


# --------------------------------------------------------------------------- #
# Public sink API (used by batch_runner)
# --------------------------------------------------------------------------- #
def start_run(checkpoint: dict[str, Any]) -> None:
    """Ensure the per-run results tab and the _runs progress row exist."""
    ss = _spreadsheet()
    _get_or_create_ws(ss, _results_tab(checkpoint["run_id"]), ROW_COLUMNS)
    _get_or_create_ws(ss, _RUNS_TAB, _RUNS_HEADER)
    save_progress(checkpoint)


def append_rows(checkpoint: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ss = _spreadsheet()
    ws = _get_or_create_ws(ss, _results_tab(checkpoint["run_id"]), ROW_COLUMNS)
    ws.append_rows([_row_from_dict(r) for r in rows], value_input_option="RAW")


def save_progress(checkpoint: dict[str, Any]) -> None:
    """Upsert this run's row in the _runs progress index."""
    ss = _spreadsheet()
    ws = _get_or_create_ws(ss, _RUNS_TAB, _RUNS_HEADER)
    record = [
        checkpoint["run_id"], checkpoint["signature"], checkpoint.get("created", ""),
        len(checkpoint["queries"]), checkpoint["batch_size"],
        checkpoint["next_index"], checkpoint["errors"], checkpoint["status"],
    ]
    col_a = ws.col_values(1)  # run_id column
    if checkpoint["run_id"] in col_a:
        row_idx = col_a.index(checkpoint["run_id"]) + 1
        ws.update(f"A{row_idx}:H{row_idx}", [record], value_input_option="RAW")
    else:
        ws.append_row(record, value_input_option="RAW")


def write_batch(checkpoint: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Durably persist a finished batch: append its rows, then update progress."""
    append_rows(checkpoint, rows)
    save_progress(checkpoint)


def sheet_url() -> str:
    sid = _sheet_id()
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else ""


def discard_run(run_id: str) -> None:
    """Mark a run as discarded so it is no longer offered for resume."""
    try:
        ss = _spreadsheet()
        ws = ss.worksheet(_RUNS_TAB)
    except Exception:
        return
    col_a = ws.col_values(1)
    if run_id in col_a:
        row_idx = col_a.index(run_id) + 1
        ws.update_cell(row_idx, _RUNS_HEADER.index("status") + 1, "discarded")


_DONE_STATUSES = {"complete", "discarded"}


def find_run(signature: str) -> dict[str, Any] | None:
    """Most recent unfinished run for this query set, read from the sheet."""
    try:
        ss = _spreadsheet()
        ws = ss.worksheet(_RUNS_TAB)
    except Exception:
        return None
    records = ws.get_all_records()  # list of dicts keyed by _RUNS_HEADER
    matches = [r for r in records
               if str(r.get("signature")) == signature
               and str(r.get("status")) not in _DONE_STATUSES]
    if not matches:
        return None
    last = matches[-1]
    return {
        "run_id": str(last["run_id"]),
        "signature": signature,
        "created": str(last.get("created", "")),
        "batch_size": int(last.get("batch_size") or 500),
        "next_index": int(last.get("next_index") or 0),
        "errors": int(last.get("errors") or 0),
        "status": str(last.get("status") or "paused"),
    }


def read_all_rows(run_id: str) -> list[dict[str, Any]]:
    """Read every result row back (for building the final branded report)."""
    ss = _spreadsheet()
    ws = ss.worksheet(_results_tab(run_id))
    values = ws.get_all_values()
    return [_dict_from_row(v) for v in values[1:]]  # skip header


# --------------------------------------------------------------------------- #
# Durable app state — templates & config mirrored to the sheet so a Streamlit
# reboot (which wipes the temporary disk) no longer deletes them. Stored as JSON
# in a tiny key/value tab; restored to local disk on the next session.
# --------------------------------------------------------------------------- #
_STATE_TAB = "_state"
_STATE_HEADER = ["key", "value"]
_DURABLE_CONFIG_KEYS = (
    "brand_name", "region", "language", "device",
    "num_pages", "delay_ms", "proxy", "batch_size",
)


def _kv_set(key: str, obj: Any) -> None:
    ss = _spreadsheet()
    ws = _get_or_create_ws(ss, _STATE_TAB, _STATE_HEADER)
    payload = json.dumps(obj, ensure_ascii=False)
    col_a = ws.col_values(1)
    if key in col_a:
        row_idx = col_a.index(key) + 1
        ws.update(f"A{row_idx}:B{row_idx}", [[key, payload]], value_input_option="RAW")
    else:
        ws.append_row([key, payload], value_input_option="RAW")


def _kv_get(key: str, default: Any = None) -> Any:
    try:
        ss = _spreadsheet()
        ws = ss.worksheet(_STATE_TAB)
    except Exception:
        return default
    for row in ws.get_all_values()[1:]:  # skip header
        if row and row[0] == key:
            raw = row[1] if len(row) > 1 else ""
            if not raw:
                return default
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return default
    return default


def push_templates(templates: list[dict[str, Any]]) -> None:
    """Mirror the template bank to the sheet (call after every edit)."""
    _kv_set("templates", templates)


def pull_templates() -> list[dict[str, Any]] | None:
    val = _kv_get("templates")
    return val if isinstance(val, list) else None


def push_config(config: dict[str, Any]) -> None:
    """Mirror the non-secret config (brand, search params, batch size) to the sheet."""
    _kv_set("config", {k: config.get(k) for k in _DURABLE_CONFIG_KEYS})


def pull_config() -> dict[str, Any] | None:
    val = _kv_get("config")
    return val if isinstance(val, dict) else None


def restore_state_if_empty() -> None:
    """Repopulate templates/config from the sheet after a reboot wiped local
    disk. Best-effort, runs at most once per session, and never blocks the page."""
    if not is_configured() or st.session_state.get("_durable_restored"):
        return
    st.session_state["_durable_restored"] = True
    try:
        if not config_store.load_templates():
            tpls = pull_templates()
            if tpls:
                config_store.save_templates(tpls)
        cfg = config_store.load_config()
        if not cfg.get("brand_name"):
            saved = pull_config()
            if saved:
                merged = {**cfg, **{k: v for k, v in saved.items() if v not in (None, "")}}
                config_store.save_config(merged)
    except Exception:
        pass  # restore is a convenience; a failure must never break the page


def list_runs() -> list[dict[str, Any]]:
    """All runs recorded in the progress index, oldest first."""
    try:
        ss = _spreadsheet()
        ws = ss.worksheet(_RUNS_TAB)
    except Exception:
        return []
    return ws.get_all_records()


def recover_templates(run_id: str) -> list[str]:
    """Reconstruct the distinct template texts (in original order) from a run's
    saved result rows — used to rebuild templates lost in a reboot so the same
    query list can be regenerated and the run resumed without re-charging."""
    try:
        ss = _spreadsheet()
        ws = ss.worksheet(_results_tab(run_id))
    except Exception:
        return []
    col = ws.col_values(ROW_COLUMNS.index("Template") + 1)[1:]  # skip header
    seen: list[str] = []
    for t in col:
        t = (t or "").strip()
        if t and t not in seen:
            seen.append(t)
    return seen
