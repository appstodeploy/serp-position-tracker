"""Template Manager page (PRD §5.2)."""
from __future__ import annotations

import streamlit as st

from core import config_store, gate, sheets_sink
from core.query_engine import extract_placeholders

st.set_page_config(page_title="Template Manager", page_icon="📋", layout="wide")
gate.require_auth()
gate.logout_button()
# Repopulate templates from the durable sheet if a reboot wiped the disk.
sheets_sink.restore_state_if_empty()
st.title("📋 Template Manager")
st.caption("Build query patterns with `{column_name}` placeholders (case-sensitive, "
           "must match your coin-list headers).")


def _mirror_to_sheet() -> None:
    """Keep the durable Google Sheet copy of the template bank in sync."""
    if sheets_sink.is_configured():
        try:
            sheets_sink.push_templates(config_store.load_templates())
        except Exception as exc:  # durability is best-effort
            st.warning(f"Saved locally, but couldn't mirror templates to the sheet: {exc}")

with st.expander("Examples", expanded=False):
    st.markdown(
        "| Template | Generated (Bitcoin) |\n"
        "|---|---|\n"
        "| `خرید {fa_name}` | خرید بیت‌کوین |\n"
        "| `قیمت {en_name}` | قیمت Bitcoin |\n"
        "| `{fa_name} چیست` | بیت‌کوین چیست |\n"
        "| `{symbol} price in Iran` | BTC price in Iran |"
    )

# --- Add template ------------------------------------------------------- #
st.subheader("Add a template")
with st.form("add_template", clear_on_submit=True):
    new_text = st.text_input("Template string", placeholder="خرید {fa_name}")
    add = st.form_submit_button("➕ Add", type="primary")
if add:
    try:
        config_store.add_template(new_text)
        _mirror_to_sheet()
        st.success("Template added.")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))

# --- Recover templates lost in a reboot -------------------------------- #
if sheets_sink.is_configured():
    unfinished = [r for r in sheets_sink.list_runs()
                  if str(r.get("status")) not in ("complete", "discarded")]
    if unfinished and not config_store.load_templates():
        last = unfinished[-1]
        st.info(
            f"🔁 A resumable run (**{last.get('run_id')}**, "
            f"{last.get('next_index', 0)}/{last.get('total', '?')} queries done) is in "
            "your Google Sheet but your templates are empty (likely wiped by a reboot). "
            "Recover them so you can regenerate the **identical** query list and resume "
            "without re-charging finished queries."
        )
        if st.button("🛟 Recover templates from my sheet", type="primary"):
            texts = sheets_sink.recover_templates(str(last.get("run_id")))
            if not texts:
                st.error("Couldn't read templates from the sheet's results.")
            else:
                for t in texts:
                    config_store.add_template(t)
                _mirror_to_sheet()
                st.success(f"Recovered {len(texts)} template(s). Go to the Dashboard, "
                           "upload the same coin file, Generate, and click Resume.")
                st.rerun()

st.divider()

# --- List / edit / delete ---------------------------------------------- #
templates = config_store.load_templates()
st.subheader(f"Template bank ({len(templates)})")

if not templates:
    st.info("No templates yet. Add at least one above to continue.")

for tpl in templates:
    placeholders = extract_placeholders(tpl["text"])
    with st.container(border=True):
        c1, c2 = st.columns([5, 1])
        with c1:
            edited = st.text_input(
                "Template", value=tpl["text"], key=f"edit_{tpl['id']}",
                label_visibility="collapsed",
            )
            if placeholders:
                st.caption("Placeholders: " + ", ".join("{" + p + "}" for p in placeholders))
            else:
                st.caption("⚠️ No placeholders detected — this template is a static query.")
        with c2:
            if st.button("Save", key=f"save_{tpl['id']}"):
                try:
                    config_store.update_template(tpl["id"], edited)
                    _mirror_to_sheet()
                    st.success("Saved.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            confirm = st.checkbox("confirm", key=f"confirm_{tpl['id']}", label_visibility="collapsed")
            if st.button("🗑 Delete", key=f"del_{tpl['id']}", disabled=not confirm,
                         help="Tick the box to enable deletion"):
                config_store.delete_template(tpl["id"])
                _mirror_to_sheet()
                st.rerun()
