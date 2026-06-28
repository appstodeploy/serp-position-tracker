"""Reports & History page (PRD §5.6 / §7 step 8)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core import config_store, gate

st.set_page_config(page_title="Reports", page_icon="📊", layout="wide")
gate.require_auth()
gate.logout_button()
st.title("📊 Reports")
st.caption("Historical tracking runs and downloadable XLSX reports.")

history = config_store.load_history()
if not history:
    st.info("No runs yet. Execute a campaign on the 🚀 Tracking Dashboard.")
    st.stop()

table = pd.DataFrame(history)[
    ["run_date", "brand", "query_count", "result_rows", "brand_matches", "errors",
     "region", "language", "device", "pages"]
].rename(columns={
    "run_date": "Run Date", "brand": "Brand", "query_count": "Queries",
    "result_rows": "Result Rows", "brand_matches": "Brand Matches", "errors": "Errors",
    "region": "Region", "language": "Lang", "device": "Device", "pages": "Pages",
})
st.dataframe(table, use_container_width=True)

st.divider()
st.subheader("Download a report")
for entry in history:
    path = Path(entry.get("output_path", ""))
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"**{entry['run_date']}** — {entry['brand']}  ·  "
                        f"{entry['query_count']} queries, {entry['result_rows']} rows, "
                        f"{entry['brand_matches']} brand match(es)")
            st.caption(f"{entry['region']}/{entry['language']} · {entry['device']} · "
                       f"{entry['pages']} page(s) · {entry['errors']} error(s)")
        with c2:
            if path.exists():
                with open(path, "rb") as fh:
                    st.download_button(
                        "⬇️ XLSX", data=fh.read(), file_name=path.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_{entry['run_id']}",
                    )
            else:
                st.caption("file missing")
