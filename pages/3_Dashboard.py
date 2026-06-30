"""Tracking Dashboard page (PRD §5.4)."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from core import batch_runner, config_store, gate, secrets, serper, sheets_sink, xlsx_writer
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

    # --- Step 6: run tracking (batched, durable & crash-safe) ---------- #
    st.subheader("6 · Run tracking")
    total_q = len(queries)
    batch_size = int(cfg.get("batch_size", 500) or 500)
    n_batches = max(1, math.ceil(total_q / batch_size))
    est_calls = total_q * int(cfg["num_pages"])
    st.caption(
        f"~{est_calls} Serper call(s) — {total_q} queries × {cfg['num_pages']} page(s), "
        f"split into **{n_batches} batch(es)** of up to {batch_size}. Progress is saved "
        "after every batch, so a crash, refresh or block never loses finished work or "
        "re-charges credits already spent. Change the batch size on ⚙️ Global Config."
    )

    sink = sheets_sink if sheets_sink.is_configured() else None
    if sink:
        st.success("🟢 **Durable storage ON** — results stream live to your Google Sheet "
                   "and survive any Streamlit reboot.  ["
                   f"open the sheet]({sheets_sink.sheet_url()})")
    else:
        st.warning(
            "🟡 **Durable storage OFF** — results are kept only on the app's temporary "
            "disk and **will be lost if Streamlit Cloud reboots** (it does so on long runs). "
            "For large runs, configure Google Sheets in Streamlit secrets — see the README. "
            "Local checkpoints still protect against a browser refresh.",
            icon="⚠️",
        )

    signature = config_store.query_signature(queries)
    existing = sheets_sink.find_run(signature) if sink else config_store.find_resumable(signature)
    is_resume = bool(existing and existing.get("status") not in ("complete", "discarded")
                     and existing.get("next_index", 0) > 0)

    if is_resume:
        done = existing["next_index"]
        st.info(f"⏸ Resumable run **{existing['run_id']}** — {done}/{total_q} queries done, "
                f"{existing.get('errors', 0)} error(s). Click **Resume tracking** to continue "
                f"from query {done + 1}; nothing already fetched is charged again.")
        rc1, rc2 = st.columns(2)
        with rc1:
            if sink:
                st.markdown(f"[⬇️ View / download results so far]({sheets_sink.sheet_url()})")
            elif existing.get("rows"):
                partial = config_store.OUTPUTS_DIR / f"serp_partial_{existing['run_id']}.xlsx"
                xlsx_writer.build_report(
                    rows=existing["rows"], config=cfg, run_date=existing.get("created", ""),
                    query_count=len(existing["queries"]), output_path=partial,
                    error_count=existing["errors"],
                )
                with open(partial, "rb") as fh:
                    st.download_button(
                        "⬇️ Download partial results", data=fh.read(), file_name=partial.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
        with rc2:
            if st.button("🗑 Discard & start fresh"):
                if sink:
                    sheets_sink.discard_run(existing["run_id"])
                else:
                    config_store.delete_checkpoint(existing["run_id"])
                st.session_state.pop("active_run_id", None)
                st.rerun()

    run_all = st.checkbox(
        "Run all remaining batches now", value=True,
        help="Uncheck to run a single batch per click — safer on flaky networks, "
             "and lets you stop and resume between batches.",
    )
    max_batches = 0 if run_all else 1

    label = "▶️ Resume tracking" if is_resume else "▶️ Run Tracking"
    if st.button(label, type="primary"):
        if is_resume and sink:
            cp = batch_runner.resume_run(queries, cfg, existing)
        elif is_resume:
            cp = existing  # local checkpoint already carries collected rows
        else:
            cp = batch_runner.init_run(queries, cfg)
        if sink:
            sheets_sink.start_run(cp)
        st.session_state["active_run_id"] = cp["run_id"]

        progress = st.progress(cp["next_index"] / total_q if total_q else 0.0, text="Starting…")

        def _cb(done: int, total: int, errors: int) -> None:
            progress.progress(done / total, text=f"{done}/{total} queries — {errors} error(s)")

        try:
            with st.spinner("Querying Serper.dev…"):
                cp = batch_runner.run(secrets.get_api_key(), cp, sink=sink,
                                      progress_cb=_cb, max_batches=max_batches)
        except serper.SerperBlockedError as exc:
            st.error(str(exc))
            st.warning(f"Progress saved ({cp['next_index']}/{total_q} queries). Fix the Proxy "
                       "on ⚙️ Global Config, then click **Resume tracking** — finished queries "
                       "won't be charged again.")
            st.session_state["active_run_id"] = cp["run_id"]
            st.rerun()
        except serper.SerperError as exc:
            st.error(str(exc))
            st.warning(f"Progress saved ({cp['next_index']}/{total_q} queries). "
                       "Resolve the issue, then click **Resume tracking**.")
            st.session_state["active_run_id"] = cp["run_id"]
            st.rerun()
        except Exception as exc:  # e.g. a Google Sheets / storage hiccup
            st.error(f"Run interrupted: {type(exc).__name__}: {exc}")
            st.warning(f"Progress saved ({cp['next_index']}/{total_q} queries). "
                       "Fix the issue, then click **Resume tracking**.")
            st.session_state["active_run_id"] = cp["run_id"]
            st.rerun()

        if cp["status"] == "complete":
            progress.progress(1.0, text="Complete")
            rows = sheets_sink.read_all_rows(cp["run_id"]) if sink else cp["rows"]
            out_path = config_store.OUTPUTS_DIR / f"serp_report_{cp['run_id']}.xlsx"
            xlsx_writer.build_report(
                rows=rows, config=cfg, run_date=cp.get("created", ""),
                query_count=len(cp["queries"]), output_path=out_path, error_count=cp["errors"],
            )
            brand_matches = sum(1 for r in rows if r.get("Is Brand"))
            config_store.add_history_entry({
                "run_id": cp["run_id"], "run_date": cp.get("created", ""),
                "brand": cfg["brand_name"], "query_count": len(cp["queries"]),
                "result_rows": len(rows), "brand_matches": brand_matches,
                "errors": cp["errors"], "region": cfg["region"], "language": cfg["language"],
                "device": cfg["device"], "pages": cfg["num_pages"], "output_path": str(out_path),
            })
            if not sink:
                config_store.delete_checkpoint(cp["run_id"])
            st.session_state.pop("active_run_id", None)
            st.session_state["last_report"] = str(out_path)
            st.session_state["flash"] = (
                f"✅ Tracking complete — {len(rows)} result rows, "
                f"{brand_matches} brand match(es), {cp['errors']} error(s)."
            )
        else:
            where = "saved to your Google Sheet" if sink else "saved locally"
            st.session_state["flash"] = (
                f"⏸ Ran a batch — {cp['next_index']}/{total_q} queries done ({where}). "
                "Click **Resume tracking** for the next batch."
            )
        st.rerun()

# --- Step 7: export ----------------------------------------------------- #
flash = st.session_state.pop("flash", None)
if flash:
    st.success(flash)

last = st.session_state.get("last_report")
if last and Path(last).exists():
    st.subheader("7 · Export")
    with open(last, "rb") as fh:
        st.download_button("⬇️ Download XLSX", data=fh.read(),
                           file_name=Path(last).name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary")
