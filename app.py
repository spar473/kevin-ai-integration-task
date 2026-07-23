"""Restrained Streamlit shell for the ZURU Talent Copilot prototype.

All business logic lives in ``src/``. This file only renders state and wires
user actions to ``src.discovery.take_discovery_turn`` and the deterministic
helpers in ``src.workflow`` -- it computes nothing itself.
"""

from __future__ import annotations

import platform
import uuid

import streamlit as st
from pydantic import ValidationError

from src.config import Settings
from src.discovery import take_discovery_turn
from src.llm_client import LLMClientError, OpenRouterClient
from src.models import (
    BasicRoleInfo,
    DiscoverySemanticValidationError,
    EmploymentType,
    RequirementPriority,
    RoleFamily,
    RoleLevel,
    RoleSpecification,
)
from src.workflow import WorkflowState, approval_blockers


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


def _settings() -> Settings | None:
    try:
        return Settings.from_env()
    except ValidationError:
        return None


def _human_label(value: str) -> str:
    return value.replace("_", " ").title()


with tabs[0]:
    st.subheader("Define Role")

    settings = _settings()
    api_ready = bool(settings and settings.api_key_configured and settings.openrouter_model)
    if not api_ready:
        st.warning(
            "OpenRouter is not configured. Set OPENROUTER_API_KEY and OPENROUTER_MODEL "
            "in .env to start a discovery conversation. See the System Status tab."
        )

    st.session_state.setdefault("workflow_state", None)
    st.session_state.setdefault("discovery_error", None)

    conversation_column, summary_column = st.columns([3, 2])

    with conversation_column:
        if st.session_state["workflow_state"] is None:
            st.markdown("**Step 1 -- basic role information**")
            with st.form("role_setup_form"):
                title = st.text_input("Role title", placeholder="Marketing Intern")
                role_family = st.selectbox(
                    "Role family",
                    options=list(RoleFamily),
                    format_func=lambda item: _human_label(item.value),
                )
                role_level = st.selectbox(
                    "Role level",
                    options=list(RoleLevel),
                    format_func=lambda item: _human_label(item.value),
                )
                employment_type = st.selectbox(
                    "Employment type",
                    options=list(EmploymentType),
                    format_func=lambda item: _human_label(item.value),
                )
                initial_statement = st.text_area(
                    "Describe the role in your own words",
                    placeholder=(
                        "We need a Marketing Intern for summer. They should be "
                        "creative and good with social media..."
                    ),
                    height=120,
                )
                submitted = st.form_submit_button(
                    "Start discovery", disabled=not api_ready
                )

            if submitted:
                if not title.strip() or not initial_statement.strip():
                    st.error("Role title and an initial description are both required.")
                else:
                    role = RoleSpecification(
                        role_id=f"role_{uuid.uuid4().hex[:8]}",
                        basic_info=BasicRoleInfo(
                            title=title.strip(),
                            role_family=role_family,
                            role_level=role_level,
                            employment_type=employment_type,
                            initial_manager_statement=initial_statement.strip(),
                        ),
                    )
                    state = WorkflowState(role_specification=role)
                    client = OpenRouterClient(settings)
                    try:
                        state = take_discovery_turn(
                            state=state,
                            manager_message=initial_statement.strip(),
                            llm_client=client,
                        )
                    except (LLMClientError, DiscoverySemanticValidationError) as exc:
                        st.session_state["discovery_error"] = str(exc)
                    else:
                        st.session_state["workflow_state"] = state
                        st.session_state["discovery_error"] = None
                    st.rerun()
        else:
            state: WorkflowState = st.session_state["workflow_state"]
            st.markdown(f"**Current stage:** {_human_label(state.current_stage.value)}")

            if state.current_question is not None:
                st.info(state.current_question.question)
                with st.form("discovery_answer_form", clear_on_submit=True):
                    answer = st.text_area("Your answer", height=100)
                    answered = st.form_submit_button(
                        "Send", disabled=not api_ready
                    )
                if answered:
                    if not answer.strip():
                        st.error("Enter an answer before sending.")
                    else:
                        client = OpenRouterClient(settings)
                        try:
                            new_state = take_discovery_turn(
                                state=state, manager_message=answer.strip(), llm_client=client
                            )
                        except (LLMClientError, DiscoverySemanticValidationError) as exc:
                            st.session_state["discovery_error"] = str(exc)
                        else:
                            st.session_state["workflow_state"] = new_state
                            st.session_state["discovery_error"] = None
                        st.rerun()
            else:
                st.success("No open question -- see the readiness summary for next steps.")

            if st.button("Start over"):
                st.session_state["workflow_state"] = None
                st.session_state["discovery_error"] = None
                st.rerun()

        if st.session_state["discovery_error"]:
            st.error(
                "The discovery turn could not be completed: "
                f"{st.session_state['discovery_error']}"
            )
            st.caption("Nothing was lost -- the role specification was not updated.")

    with summary_column:
        st.markdown("**Role summary**")
        state = st.session_state["workflow_state"]
        if state is None:
            st.caption("Start discovery to see the structured role specification here.")
        else:
            role = state.role_specification
            st.write(f"**{role.basic_info.title}**")
            st.caption(
                f"{_human_label(role.basic_info.role_family.value)} - "
                f"{_human_label(role.basic_info.role_level.value)} - "
                f"{_human_label(role.basic_info.employment_type.value)}"
            )

            must_haves = [
                item for item in role.requirements
                if item.priority is RequirementPriority.MUST_HAVE
            ]
            preferred = [
                item for item in role.requirements
                if item.priority is not RequirementPriority.MUST_HAVE
            ]

            st.markdown(f"**Must-have requirements ({len(must_haves)})**")
            for item in must_haves:
                st.markdown(f"- {item.name}")
            st.markdown(f"**Preferred / optional requirements ({len(preferred)})**")
            for item in preferred:
                st.markdown(f"- {item.name}")

            if role.open_ambiguities:
                st.markdown(f"**Open ambiguities ({len(role.open_ambiguities)})**")
                for item in role.open_ambiguities:
                    st.markdown(f"- {item.description}")

            if role.open_assumptions:
                st.markdown(f"**Unconfirmed assumptions ({len(role.open_assumptions)})**")
                for item in role.open_assumptions:
                    st.markdown(f"- {item.statement}")

            blockers = approval_blockers(role)
            st.markdown(f"**Approval blockers ({len(blockers)})**")
            if blockers:
                for blocker in blockers:
                    st.markdown(f"- {blocker}")
            else:
                st.caption("No blockers remaining.")

notices = {
    "Review Role": "Human review and approval controls are not implemented yet.",
    "Hiring Pack": "Job description and screening-pack generation are not implemented yet.",
    "Candidate Evidence": "Candidate evidence evaluation is not implemented yet.",
}

for tab, (name, notice) in zip(tabs[1:4], notices.items(), strict=True):
    with tab:
        st.subheader(name)
        st.info(notice)

with tabs[4]:
    st.subheader("System Status")
    if settings is None:
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
    if not api_ready:
        st.warning("OpenRouter calls are disabled until configuration is complete.")
