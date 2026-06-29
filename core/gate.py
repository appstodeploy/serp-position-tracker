"""Shared password gate for the whole app.

Streamlit serves every page at its own URL (``/Config``, ``/Reports`` …) and
runs that page's script independently. So protecting only the home page leaves
the others reachable. Every page must call :func:`require_auth` at the very top.

The password is read from ``st.secrets["app_password"]`` (set it in the
Streamlit Cloud *Secrets* manager) or the ``APP_PASSWORD`` env var for local
runs. It is compared in constant time and never stored in the repo.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

_AUTH_FLAG = "auth_ok"


def _expected_password() -> str:
    try:
        if "app_password" in st.secrets:  # type: ignore[operator]
            return str(st.secrets["app_password"])
    except Exception:
        pass
    return os.getenv("APP_PASSWORD", "")


def _login_screen(expected: str) -> None:
    st.markdown("## 🔒 This app is protected")
    st.caption("Enter the access password to continue.")
    with st.form("login_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock", type="primary")
    if submitted:
        if hmac.compare_digest(pw, expected):
            st.session_state[_AUTH_FLAG] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def require_auth() -> None:
    """Block the page until the correct password is entered.

    If no password is configured the app stays open but shows a warning, so
    local development is never accidentally locked out.
    """
    expected = _expected_password()
    if not expected:
        st.warning(
            "🔓 No app password set — this app is currently **public**. "
            "Set `app_password` in Streamlit secrets (or the `APP_PASSWORD` "
            "env var) to lock it.",
            icon="⚠️",
        )
        return
    if st.session_state.get(_AUTH_FLAG):
        return
    _login_screen(expected)


def logout_button() -> None:
    """Render a sidebar logout control (only when a password is configured)."""
    if not _expected_password():
        return
    with st.sidebar:
        if st.session_state.get(_AUTH_FLAG) and st.button("🚪 Log out"):
            st.session_state[_AUTH_FLAG] = False
            st.rerun()
