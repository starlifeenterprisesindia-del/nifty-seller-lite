from __future__ import annotations

import streamlit as st


GLOSSARY = [
    ("One Brain Bias", "Current combined market direction: Bullish, Bearish, Neutral ya Conflict. Yeh final evidence summary hai, guaranteed prediction nahi."),
    ("Data Confidence", "Feeds kitne fresh/complete hain. Low confidence par signal ko trust kam dena chahiye; core engine stale critical data par DATA WAIT use karta hai."),
    ("1m / 3m / 5m Flow", "Same option activity ko short windows me compare karta hai—OI, premium aur volume movement se pressure/participation samajhne ke liye."),
    ("Persistence / Maturity", "Signal ek naya spike hai ya kuch minutes se sustain ho raha hai. Sustained evidence usually isolated spike se zyada stable hota hai."),
    ("Big Player", "Unusual aligned option-flow/activity evidence. Isko independent guarantee nahi samjho; direction, persistence aur price confirmation ke saath dekho."),
    ("CE / PE Wall", "Strike jahan relatively strong option OI/structure barrier dikhta hai. Wall toot bhi sakti hai; price + flow confirmation zaroori hai."),
    ("Cluster", "Nearby strikes ka grouped concentration. Single strike se broader zone context deta hai."),
    ("Greeks", "Delta price sensitivity, Theta time decay, Gamma delta-change sensitivity, Vega IV sensitivity. Board par compact values context ke liye hain."),
    ("IV Δ", "Implied Volatility ka saved-history change. Load IV Δ button par hi 1m/3m/5m compare hota hai; volatility-point change hai, One Brain score ya profit probability nahi."),
    ("Alert Latency", "Alert generate hone aur delivery complete hone ke beech ka time. Diagnostics delivery path ko audit karta hai, trading engine ko nahi."),
    ("Replay / Post-market Review", "Recorded snapshots se baad me dekhta hai ki move signal se pehle/baad aaya aur Big Player confirmation kitni der me hui."),
    ("Validation Lab", "Recorded One-Brain actions ko 5m/15m/30m NIFTY spot outcomes ke against walk-forward aur regime/readiness buckets me review karta hai; auto tuning nahi karta."),
]


def render_help_guide() -> None:
    st.caption(
        "Quick guide only — is panel ko kholne se koi broker/API call, indicator calculation ya One Brain recomputation nahi hota."
    )
    query = st.text_input(
        "Find a term",
        placeholder="e.g. Big Player, Data Confidence, Wall",
        key="one_brain_help_search",
        help="Sirf is glossary ko filter karta hai.",
    ).strip().lower()
    rows = GLOSSARY
    if query:
        rows = [item for item in rows if query in item[0].lower() or query in item[1].lower()]
    if not rows:
        st.info("Is term ka quick guide entry nahi mila.")
        return
    for title, meaning in rows:
        st.markdown(f"**{title}**")
        st.caption(meaning)
