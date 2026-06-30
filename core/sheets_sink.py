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

from typing import Any

import streamlit as st

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
