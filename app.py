"""SERP Position Tracker — Streamlit entry point (PRD §4).

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

from core import config_store, gate, secrets

load_dotenv()

st.set_page_config(
    page_title="SERP Position Tracker",
    page_icon="🔍",
    layout="wide",
)
gate.require_auth()
gate.logout_button()

# Light RTL assist for Persian text in inputs/tables (PRD §6 RTL Support).
st.markdown(
    """
    <style>
      textarea, input[type="text"] { unicode-bidi: plaintext; }
      .stDataFrame { direction: ltr; }
    </style>
    """,
    unsafe_allow_html=True,
)

config = config_store.load_config()
brand = config.get("brand_name") or "SERP Position Tracker"

col_logo, col_title = st.columns([1, 6])
with col_logo:
    logo = config.get("brand_logo_path")
    if logo:
        try:
            st.image(logo, width=90)
        except Exception:
            st.write("🔍")
    else:
        st.markdown("# 🔍")
with col_title:
    st.title(brand)
    st.caption("Cryptocurrency Keyword Ranking Tool — Iranian Market (Persian SERP)")

st.divider()

missing = []
if not config_store.is_brand_configured(config):
    missing.append("**Brand Name**")
if not secrets.has_api_key():
    missing.append("**Serper API key**")

if missing:
    st.warning(
        "⚙️ Setup needed: add your " + " and ".join(missing) +
        " on the **Global Config** page before running a tracking campaign."
    )
else:
    proxy_note = "  ·  proxy on" if config.get("proxy") else ""
    st.success(f"Configured for **{brand}** — region `{config['region']}`, "
               f"language `{config['language']}`, {config['device']}, "
               f"{config['num_pages']} page(s).{proxy_note}")

st.markdown(
    """
### How it works
1. **⚙️ Global Config** — set your brand, Serper API key and search parameters.
2. **📋 Template Manager** — build query templates with `{column}` placeholders.
3. **🚀 Tracking Dashboard** — upload a coin list, generate queries, run tracking.
4. **📊 Reports** — review past runs and re-download XLSX reports.

Use the sidebar to navigate between pages.
"""
)

with st.sidebar:
    st.header("Status")
    st.metric("Templates", len(config_store.load_templates()))
    st.metric("Past runs", len(config_store.load_history()))
    st.caption(f"Brand: {brand}")
