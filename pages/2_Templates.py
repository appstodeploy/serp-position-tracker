"""Template Manager page (PRD §5.2)."""
from __future__ import annotations

import streamlit as st

from core import config_store, gate
from core.query_engine import extract_placeholders

st.set_page_config(page_title="Template Manager", page_icon="📋", layout="wide")
gate.require_auth()
gate.logout_button()
st.title("📋 Template Manager")
st.caption("Build query patterns with `{column_name}` placeholders (case-sensitive, "
           "must match your coin-list headers).")

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
        st.success("Template added.")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))

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
                    st.success("Saved.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            confirm = st.checkbox("confirm", key=f"confirm_{tpl['id']}", label_visibility="collapsed")
            if st.button("🗑 Delete", key=f"del_{tpl['id']}", disabled=not confirm,
                         help="Tick the box to enable deletion"):
                config_store.delete_template(tpl["id"])
                st.rerun()
