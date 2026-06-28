"""Tracking Dashboard page (PRD §5.4)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core import config_store, gate, secrets, serper, xlsx_writer
from core.query_engine import generate_queries, read_coin_list

st.set_page_config(page_title="Tracking Dashboard", page_icon="🚀", layout="wide")
gate.require_auth()
gate.logout_button()
st.title("🚀 Tracking Dashboard")

cfg = config_store.load_config()
templates = config_store.load_templates()

if not config_store.is_brand_configured(cfg):
    st.error("Set your **Brand Name** on the **Global Config** page before running tracking.")
    st.stop()
if not secrets.has_api_key():
    st.error("Set and save your **Serper API key** on the **Global Config** page before running tracking.")
    st.stop()

# --- Step 1: upload coin list ------------------------------------------ #
st.subheader("1 · Upload coin list (XLSX)")
coin_file = st.file_uploader("Coin list — first sheet is used", type=["xlsx"])

coins = None
if coin_file is not None:
    try:
        coins = read_coin_list(coin_file)
        st.session_state["coins"] = coins
    except Exception as exc:
        st.error(f"Could not read the file: {exc}")
coins = st.session_state.get("coins") if coins is None else coins

if coins is not None:
    st.success(f"Loaded {len(coins)} coins. Columns: " + ", ".join(coins.columns))
    st.dataframe(coins.head(20), use_container_width=True)

# --- Step 2: select templates ------------------------------------------ #
st.subheader("2 · Select templates")
if not templates:
    st.warning("Add at least one template on the **Template Manager** page to continue.")
template_labels = {t["text"]: t for t in templates}
chosen = st.multiselect("Templates to apply", list(template_labels.keys()),
                        default=list(template_labels.keys()))

# --- Step 3: generate queries ------------------------------------------ #
st.subheader("3 · Generate queries")
can_generate = coins is not None and bool(chosen)
if not can_generate:
    st.info("Upload a coin list and select at least one template to enable generation.")

if st.button("⚙️ Generate query list", type="primary", disabled=not can_generate):
    selected = [template_labels[c] for c in chosen]
    queries, warnings = generate_queries(coins, selected)
    st.session_state["queries"] = queries
    st.session_state["query_warnings"] = warnings

queries = st.session_state.get("queries", [])
warnings = st.session_state.get("query_warnings", [])

if warnings:
    for w in warnings:
        st.warning("⚠️ " + w)

# --- Step 4: preview ---------------------------------------------------- #
if queries:
    st.subheader("4 · Preview")
    st.metric("Total queries", len(queries))
    preview = pd.DataFrame(queries)[["query", "symbol", "template"]]
    preview.columns = ["Query", "Coin", "Template"]
    st.dataframe(preview, use_container_width=True, height=320)

    # --- Step 5: review config ----------------------------------------- #
    st.subheader("5 · Review search config")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Region (gl)", cfg["region"])
    c2.metric("Language (hl)", cfg["language"])
    c3.metric("Device", cfg["device"])
    c4.metric("Pages", cfg["num_pages"])
    st.caption("Edit these on the ⚙️ Global Config page.")

    # --- Step 6: run tracking ------------------------------------------ #
    st.subheader("6 · Run tracking")
    est_calls = len(queries) * int(cfg["num_pages"])
    st.caption(f"This will make ~{est_calls} Serper API calls "
               f"({len(queries)} queries × {cfg['num_pages']} page(s)).")

    if st.button("▶️ Run Tracking", type="primary"):
        progress = st.progress(0.0, text="Starting…")
        status = st.empty()

        def _cb(done: int, total: int, errors: int) -> None:
            progress.progress(done / total, text=f"{done}/{total} queries — {errors} errors")

        try:
            with st.spinner("Querying Serper.dev…"):
                rows, errors = serper.run_tracking(secrets.get_api_key(), queries, cfg, _cb)
        except serper.SerperBlockedError as exc:
            st.error(str(exc))
            st.info("Add a Proxy on the ⚙️ Global Config page and try again.")
            st.stop()
        except serper.SerperError as exc:
            st.error(str(exc))
            st.stop()

        progress.progress(1.0, text="Complete")
        if errors:
            status.warning(f"Completed with {errors} per-query error(s).")
        else:
            status.success("Tracking complete.")

        run_id = config_store.new_run_id()
        run_date = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        out_path = config_store.OUTPUTS_DIR / f"serp_report_{run_id}.xlsx"
        xlsx_writer.build_report(
            rows=rows, config=cfg, run_date=run_date,
            query_count=len(queries), output_path=out_path, error_count=errors,
        )

        brand_matches = sum(1 for r in rows if r.get("Is Brand"))
        config_store.add_history_entry({
            "run_id": run_id,
            "run_date": run_date,
            "brand": cfg["brand_name"],
            "query_count": len(queries),
            "result_rows": len(rows),
            "brand_matches": brand_matches,
            "errors": errors,
            "region": cfg["region"],
            "language": cfg["language"],
            "device": cfg["device"],
            "pages": cfg["num_pages"],
            "output_path": str(out_path),
        })

        st.session_state["last_report"] = str(out_path)
        st.success(f"Report saved — {len(rows)} result rows, {brand_matches} brand match(es).")

# --- Step 7: export ----------------------------------------------------- #
last = st.session_state.get("last_report")
if last and Path(last).exists():
    st.subheader("7 · Export")
    with open(last, "rb") as fh:
        st.download_button("⬇️ Download XLSX", data=fh.read(),
                           file_name=Path(last).name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary")
