"""Restrained Streamlit shell for the ZURU Talent Copilot prototype."""

from __future__ import annotations

import platform

import streamlit as st
from pydantic import ValidationError

from src.config import Settings


st.set_page_config(page_title="ZURU Talent Copilot", layout="wide")

st.title("ZURU Talent Copilot")
st.caption(
    "A human-led decision-support prototype for role definition, hiring-pack "
    "preparation, and candidate evidence review. It does not make final hiring decisions."
)

tabs = st.tabs(
    [
        "Define Role",
        "Review Role",
        "Hiring Pack",
        "Candidate Evidence",
        "System Status",
    ]
)

notices = {
    "Define Role": "The state-controlled discovery experience is not implemented yet.",
    "Review Role": "Human review and approval controls are not implemented yet.",
    "Hiring Pack": "Job description and screening-pack generation are not implemented yet.",
    "Candidate Evidence": "Candidate evidence evaluation is not implemented yet.",
}

for tab, (name, notice) in zip(tabs[:4], notices.items(), strict=True):
    with tab:
        st.subheader(name)
        st.info(notice)

with tabs[4]:
    st.subheader("System Status")
    try:
        settings = Settings.from_env()
    except ValidationError:
        st.error("Configuration is invalid. Check non-secret environment settings.")
    else:
        status = {
            "Application environment": settings.app_env,
            "OpenRouter base URL": str(settings.openrouter_base_url),
            "OpenRouter model": settings.openrouter_model or "not configured",
            "API key configured": settings.api_key_configured,
            "Python version": platform.python_version(),
        }
        st.json(status)
    st.warning("OpenRouter calls are disabled in this setup shell.")

