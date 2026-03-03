from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from scopus_serial_client import ScopusSerialClient, parse_serial_entry


st.set_page_config(page_title="Scopus Journal Metrics Dashboard", layout="wide")


def _get_secret(name: str) -> str | None:
    # Works locally (env var) or on Streamlit Cloud (secrets)
    if hasattr(st, "secrets") and name in st.secrets:
        return st.secrets[name]
    return os.getenv(name)


API_KEY = _get_secret("e41082bd17ee1c978f3f97f017d7c93b")
INST_TOKEN = _get_secret("ELSEVIER_INST_TOKEN")


st.title("Scopus Journal Metrics Dashboard")
st.caption("CiteScore / SNIP / SJR + historical series from Elsevier Serial Title (Scopus) API.")


if not API_KEY:
    st.error(
        "Missing API key. Set ELSEVIER_API_KEY as an environment variable or in Streamlit secrets."
    )
    st.stop()


client = ScopusSerialClient(api_key=API_KEY, inst_token=INST_TOKEN)


@st.cache_data(ttl=60 * 60)  # 1 hour cache to reduce quota usage
def cached_search(title: str):
    return client.search_serial_titles(title, content="journal", view="STANDARD", count=50, start=0)


@st.cache_data(ttl=60 * 60)  # 1 hour cache
def cached_retrieve(issn: str, view: str):
    return client.retrieve_by_issn(issn, view=view)


with st.sidebar:
    st.header("Journal")
    query = st.text_input("Search by journal title", value="")
    do_search = st.button("Search", type="primary", use_container_width=True)
    st.divider()
    st.subheader("Refresh")
    if st.button("Clear cache (force refresh)", use_container_width=True):
        st.cache_data.clear()
        st.success("Cache cleared. Run search again.")


if not do_search or not query.strip():
    st.info("Enter a journal title in the sidebar and click **Search**.")
    st.stop()


try:
    search_json, search_quota = cached_search(query.strip())
except Exception as e:
    st.error(f"Search failed: {e}")
    st.stop()


resp = search_json.get("serial-metadata-response", {})
entries = resp.get("entry", []) or []
if not entries:
    st.warning("No results found.")
    st.stop()


# Build a selection table
rows = []
for e in entries:
    rows.append({
        "Title": e.get("dc:title"),
        "Publisher": e.get("dc:publisher"),
        "ISSN": e.get("prism:issn"),
        "E-ISSN": e.get("prism:eIssn"),
        "Source ID": e.get("source-id"),
        "OA": e.get("openaccess"),
    })
df = pd.DataFrame(rows).dropna(subset=["Title"])

st.subheader("Search results")
st.dataframe(df, use_container_width=True, hide_index=True)

options = []
for _, r in df.iterrows():
    label = f"{r['Title']}  |  ISSN: {r.get('ISSN')}  |  Publisher: {r.get('Publisher')}"
    options.append((label, r.get("ISSN")))

selected_label = st.selectbox("Select the journal to view metrics", [o[0] for o in options])
selected_issn = dict(options).get(selected_label)

if not selected_issn or pd.isna(selected_issn):
    st.error("Selected row has no ISSN. Try another result (or search by ISSN directly in the code).")
    st.stop()


# Fetch ENHANCED view (metrics + yearly-data)
try:
    enh_json, enh_quota = cached_retrieve(str(selected_issn), view="ENHANCED")
    enh = parse_serial_entry(enh_json)
except Exception as e:
    st.error(f"Retrieval failed: {e}")
    st.stop()


with st.sidebar:
    st.subheader("Quota (last call)")
    q = enh_quota or search_quota or {}
    st.write(
        {
            "X-RateLimit-Limit": q.get("X-RateLimit-Limit"),
            "X-RateLimit-Remaining": q.get("X-RateLimit-Remaining"),
            "X-RateLimit-Reset": q.get("X-RateLimit-Reset"),
            "X-ELS-Status": q.get("X-ELS-Status"),
        }
    )


# Header
st.markdown(f"## {enh.get('title')}")
meta_cols = st.columns(3)
meta_cols[0].write(f"**Publisher:** {enh.get('publisher')}")
meta_cols[1].write(f"**ISSN / E-ISSN:** {enh.get('issn')} / {enh.get('eissn')}")
meta_cols[2].write(f"**Scopus Source ID:** {enh.get('source_id')}")

subjects = enh.get("subjects", [])
if subjects:
    st.write("**Subject areas:** " + ", ".join([f"{s.get('name')} ({s.get('abbrev')}{s.get('code')})" for s in subjects if s.get("name")]))


# KPIs
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

def latest(series):
    return series[-1] if series else None

sjr_latest = latest(enh.get("sjr_series", []))
snip_latest = latest(enh.get("snip_series", []))

kpi1.metric(
    "CiteScore (latest complete year)",
    value=enh.get("citescore_current") or "—",
    help=f"Year: {enh.get('citescore_current_year') or '—'}"
)
kpi2.metric(
    "CiteScore Tracker (in-progress year)",
    value=enh.get("citescore_tracker") or "—",
    help=f"Year: {enh.get('citescore_tracker_year') or '—'}"
)
kpi3.metric(
    "SJR (latest)",
    value=(sjr_latest["value"] if sjr_latest else "—"),
    help=f"Year: {(sjr_latest['year'] if sjr_latest else '—')}"
)
kpi4.metric(
    "SNIP (latest)",
    value=(snip_latest["value"] if snip_latest else "—"),
    help=f"Year: {(snip_latest['year'] if snip_latest else '—')}"
)

st.caption(f"Last refreshed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

# Charts
left, right = st.columns(2)

with left:
    st.subheader("SJR over time")
    sjr_df = pd.DataFrame(enh.get("sjr_series", []))
    if not sjr_df.empty:
        fig = px.line(sjr_df, x="year", y="value", markers=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No SJR series available in this response/view.")

with right:
    st.subheader("SNIP over time")
    snip_df = pd.DataFrame(enh.get("snip_series", []))
    if not snip_df.empty:
        fig = px.line(snip_df, x="year", y="value", markers=True)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No SNIP series available in this response/view.")

st.subheader("Yearly publication & citation data")
yd_df = pd.DataFrame(enh.get("yearly_data", []))
if not yd_df.empty:
    # Show two simple charts
    c1, c2 = st.columns(2)
    with c1:
        if "publicationCount" in yd_df.columns:
            fig = px.bar(yd_df, x="year", y="publicationCount")
            st.plotly_chart(fig, use_container_width=True)
    with c2:
        if "citeCountSCE" in yd_df.columns:
            fig = px.bar(yd_df, x="year", y="citeCountSCE")
            st.plotly_chart(fig, use_container_width=True)

    st.dataframe(yd_df.sort_values("year", ascending=False), use_container_width=True, hide_index=True)

    st.download_button(
        "Download yearly data (CSV)",
        data=yd_df.to_csv(index=False).encode("utf-8"),
        file_name=f"{enh.get('issn','journal')}_yearly_data.csv",
        mime="text/csv",
    )
else:
    st.info("No yearly-data available in this response/view.")


st.divider()
st.subheader("Attribution / notes")
st.write(
    "If you display SNIP and SJR values publicly, Elsevier’s technical documentation recommends including the provided explanatory text and a “Powered by Scopus” attribution."
)
