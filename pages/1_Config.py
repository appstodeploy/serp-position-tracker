"""Global Configuration page (PRD §5.1)."""
from __future__ import annotations

import streamlit as st

from core import config_store, gate, secrets, serper

st.set_page_config(page_title="Global Config", page_icon="⚙️", layout="wide")
gate.require_auth()
gate.logout_button()
st.title("⚙️ Global Config")
st.caption("Brand setup, Serper API key and search parameters. "
           "Non-secret settings persist across sessions; your API key stays private to your session.")

cfg = config_store.load_config()

REGION_OPTIONS = ["ir", "us", "ae", "tr", "de", "gb"]
LANGUAGE_OPTIONS = ["fa", "en", "ar", "tr"]

# --------------------------------------------------------------------- #
# API key — session-scoped & private (PRD §6 Security). Kept OUT of the
# shared config form so it is never written to config.json.
# --------------------------------------------------------------------- #
st.subheader("🔑 Your Serper API key")
st.caption("Stored only in your browser session — never saved to disk or shared. "
           "Each user enters their own; nobody can see anyone else's.")

current_key = secrets.get_api_key()
kc1, kc2 = st.columns([4, 1])
with kc1:
    key_input = st.text_input(
        "Serper API Key", value=current_key, type="password",
        placeholder="paste your serper.dev key",
        help="Get one at https://serper.dev — masked after entry.",
    )
with kc2:
    st.write("")
    st.write("")
    if st.button("💾 Save key", type="primary", use_container_width=True):
        secrets.set_api_key(key_input)
        st.success("Key saved for this session.")
        st.rerun()

if secrets.has_api_key():
    st.caption("Status: ✅ a key is set for this session.")
else:
    st.caption("Status: ⚠️ no key set yet.")

if current_key and st.button("🗑 Clear my key"):
    secrets.clear_api_key()
    st.rerun()

st.divider()

# --------------------------------------------------------------------- #
# Shared (non-secret) configuration.
# --------------------------------------------------------------------- #
with st.form("global_config"):
    st.subheader("Brand identity")
    brand_name = st.text_input("Brand Name *", value=cfg.get("brand_name", ""),
                               help="Displayed on reports and used for brand-position detection.")
    logo_file = st.file_uploader("Brand Logo (PNG/JPG)", type=["png", "jpg", "jpeg"])
    if cfg.get("brand_logo_path"):
        st.caption(f"Current logo: {cfg['brand_logo_path']}")

    st.subheader("Network")
    proxy = st.text_input(
        "Proxy (optional)", value=cfg.get("proxy", ""),
        placeholder="socks5://127.0.0.1:1080  or  http://user:pass@host:port",
        help="Route Serper calls through a proxy/VPN. Required when "
             "google.serper.dev is geo-blocked on your network (e.g. from Iran).",
    )

    st.subheader("Search parameters")
    c1, c2, c3 = st.columns(3)
    with c1:
        region = st.selectbox("Search Region (gl) *", REGION_OPTIONS,
                              index=REGION_OPTIONS.index(cfg["region"]) if cfg["region"] in REGION_OPTIONS else 0)
    with c2:
        language = st.selectbox("Search Language (hl) *", LANGUAGE_OPTIONS,
                               index=LANGUAGE_OPTIONS.index(cfg["language"]) if cfg["language"] in LANGUAGE_OPTIONS else 0)
    with c3:
        device = st.radio("Device Type *", ["desktop", "mobile"],
                          index=0 if cfg.get("device", "desktop") == "desktop" else 1)

    c4, c5, c6 = st.columns(3)
    with c4:
        num_pages = st.number_input("Number of Pages *", min_value=1, max_value=10,
                                    value=int(cfg.get("num_pages", 1)),
                                    help="1 = first 10 results, 2 = 20, etc.")
    with c5:
        delay_ms = st.number_input("Delay between calls (ms)", min_value=0, max_value=5000,
                                   value=int(cfg.get("delay_ms", 200)), step=50,
                                   help="Respect Serper rate limits (PRD §6).")
    with c6:
        batch_size = st.number_input(
            "Batch size", min_value=10, max_value=5000,
            value=int(cfg.get("batch_size", 500)), step=50,
            help="Queries per batch on the Tracking Dashboard. A checkpoint is "
                 "saved after each batch, so an interrupted large run can resume "
                 "without re-spending credits. Smaller = safer, more checkpoints.",
        )

    submitted = st.form_submit_button("💾 Save configuration", type="primary")

if submitted:
    if not brand_name.strip():
        st.error("Brand Name is required.")
    else:
        logo_path = cfg.get("brand_logo_path", "")
        if logo_file is not None:
            logo_path = config_store.save_logo(logo_file)
        config_store.save_config({
            "brand_name": brand_name.strip(),
            "brand_logo_path": logo_path,
            "proxy": proxy.strip(),
            "region": region,
            "language": language,
            "device": device,
            "num_pages": int(num_pages),
            "delay_ms": int(delay_ms),
            "batch_size": int(batch_size),
        })
        st.success("Configuration saved.")
        cfg = config_store.load_config()

st.divider()
if st.button("🔌 Test API key"):
    if not secrets.has_api_key():
        st.error("Set and save your API key above first.")
    else:
        with st.spinner("Calling Serper.dev…"):
            ok, message = serper.validate_api_key(
                secrets.get_api_key(), cfg["region"], cfg["language"], cfg.get("proxy", "")
            )
        if ok:
            st.success("API key is valid ✅")
        else:
            st.error(message)
            if "blocked" in message.lower() or "reach" in message.lower():
                st.info("Tip: this is a network/geo block, not a bad key. "
                        "Add a Proxy above (e.g. `socks5://127.0.0.1:1080`) and test again.")
