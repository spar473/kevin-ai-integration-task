"""Restrained Streamlit shell for the ZURU Talent Copilot prototype.

All business logic lives in ``src/``. This file only renders state and wires
user actions to discovery, readiness, and workflow helpers -- it computes
nothing itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
import platform
import uuid
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from src.config import Settings
from src.discovery import (
    ResponsibilityNotFoundError,
    SuccessOutcomeNotFoundError,
    ZuruDnaBehaviourNotFoundError,
    add_responsibility,
    add_success_outcome,
    add_zuru_dna_behaviour,
    delete_requirement,
    delete_responsibility,
    delete_success_outcome,
    delete_zuru_dna_behaviour,
    edit_assessment_plan,
    edit_business_need,
    edit_constraints,
    edit_requirement,
    edit_responsibility,
    edit_success_outcome,
    edit_zuru_dna_behaviour,
    take_discovery_turn,
)
from src.evaluation import (
    CandidateEvaluationValidationError,
    EvaluationBlockedError,
    NoCandidateEvaluationChangesError,
    edit_and_persist_candidate_evaluation,
    evaluate_and_persist_candidate,
)
from src.generation import (
    GenerationBlockedError,
    HiringPackValidationError,
    NoHiringPackChangesError,
    ReferenceLoadError,
    edit_and_persist_hiring_pack,
    generate_and_persist_hiring_pack,
    generation_blockers,
    hiring_pack_is_stale,
)
from src.llm_client import LLMClientError, OpenRouterClient
from src.models import (
    ApprovalSection,
    BasicRoleInfo,
    CandidateEvaluation,
    CandidateQuestionResponse,
    CandidateResponseSet,
    DiscoverySemanticValidationError,
    EmploymentType,
    EvidenceQuality,
    HiringPack,
    JobDescription,
    JobDescriptionCriterion,
    RequirementPriority,
    RoleFamily,
    RoleLevel,
    RoleSpecification,
    RubricAnchor,
    ScreeningQuestion,
    ZuruDnaSelection,
)
from src.readiness import (
    REQUIRED_APPROVAL_SECTIONS,
    ApprovalBlockedError,
    ContradictionNotFoundError,
    approve_role,
    approval_blockers,
    evaluate_role_quality,
    refresh_role_quality,
    resolve_contradiction,
)
from src.storage import (
    AuditEventType,
    AuditLog,
    DiscoveryMessage,
    SessionStore,
    StorageError,
    append_audit_event,
    record_discovery_turn,
    record_human_review,
    record_new_contradictions,
    record_requirement_edit,
    record_role_section_edit,
)
from src.workflow import WorkflowState, advance_workflow


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


SESSION_STORE = SessionStore(
    Path(__file__).resolve().parent / "data" / "sessions"
)
REFERENCE_DIRECTORY = Path(__file__).resolve().parent / "files"


def _current_audit_log(role: RoleSpecification) -> AuditLog:
    """Return the active log, initialising legacy in-memory UI state if needed."""
    session_id = st.session_state["session_id"] or uuid.uuid4().hex
    audit_log = st.session_state["audit_log"]
    if audit_log is None:
        audit_log = append_audit_event(
            AuditLog(session_id=session_id),
            AuditEventType.SESSION_CREATED,
            role=role,
            actor="system",
        )
    st.session_state["session_id"] = session_id
    st.session_state["audit_log"] = audit_log
    return audit_log


def _nonempty_lines(value: str) -> list[str]:
    """Normalise a multi-line editor into deliberate non-empty list items."""
    return [line.strip() for line in value.splitlines() if line.strip()]


def _save_active_session(
    *,
    hiring_pack: HiringPack,
    audit_log: AuditLog,
    candidate_evaluation: CandidateEvaluation | None = None,
) -> None:
    """Persist the full UI snapshot after a pack or audit file has been saved."""
    state = st.session_state["workflow_state"]
    session_id = st.session_state["session_id"]
    if state is None or not session_id:
        raise StorageError("The active session is unavailable.")
    SESSION_STORE.save_session(
        session_id=session_id,
        workflow_state=state,
        messages=st.session_state["discovery_messages"],
        audit_log=audit_log,
        hiring_pack=hiring_pack,
        candidate_evaluation=(
            candidate_evaluation
            if candidate_evaluation is not None
            else st.session_state.get("candidate_evaluation")
        ),
    )


def _persist_result(
    *,
    hiring_pack: HiringPack,
    audit_log: AuditLog,
    candidate_evaluation: CandidateEvaluation | None = None,
) -> None:
    """Accept a persisted artefact and best-effort refresh the session snapshot."""
    if candidate_evaluation is None:
        st.session_state["hiring_pack"] = hiring_pack
    else:
        st.session_state["candidate_evaluation"] = candidate_evaluation
    st.session_state["audit_log"] = audit_log
    try:
        _save_active_session(
            hiring_pack=hiring_pack,
            audit_log=audit_log,
            candidate_evaluation=candidate_evaluation,
        )
    except StorageError as exc:
        st.warning(
            "The result and audit event were saved, but the full session "
            f"snapshot could not be refreshed: {exc}"
        )
    st.rerun()


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
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("discovery_messages", [])
    st.session_state.setdefault("audit_log", None)
    st.session_state.setdefault("hiring_pack", None)
    st.session_state.setdefault("candidate_evaluation", None)

    with st.expander("Save or reload session"):
        current_state = st.session_state["workflow_state"]
        save_column, load_column = st.columns(2)
        with save_column:
            if st.button(
                "Save current session",
                disabled=current_state is None,
                use_container_width=True,
            ):
                session_id = st.session_state["session_id"] or uuid.uuid4().hex
                audit_log = st.session_state["audit_log"]
                if audit_log is None:
                    audit_log = append_audit_event(
                        AuditLog(session_id=session_id),
                        AuditEventType.SESSION_CREATED,
                        role=current_state.role_specification,
                        actor="system",
                    )
                try:
                    with st.spinner("Saving validated session files..."):
                        saved_folder = SESSION_STORE.save_session(
                            session_id=session_id,
                            workflow_state=current_state,
                            messages=st.session_state["discovery_messages"],
                            audit_log=audit_log,
                            hiring_pack=st.session_state["hiring_pack"],
                            candidate_evaluation=st.session_state[
                                "candidate_evaluation"
                            ],
                        )
                except StorageError as exc:
                    st.error(f"Session could not be saved: {exc}")
                else:
                    st.session_state["session_id"] = session_id
                    st.session_state["audit_log"] = audit_log
                    st.success(f"Saved {saved_folder.name}.")

        with load_column:
            try:
                available_sessions = SESSION_STORE.list_sessions()
            except OSError as exc:
                available_sessions = []
                st.error(f"Saved sessions could not be listed: {exc}")
            selected_session = st.selectbox(
                "Saved session",
                options=available_sessions,
                placeholder="No saved sessions",
                disabled=not available_sessions,
            )
            if st.button(
                "Reload selected session",
                disabled=not selected_session,
                use_container_width=True,
            ):
                try:
                    with st.spinner("Loading and validating session files..."):
                        snapshot = SESSION_STORE.load_session(selected_session)
                except StorageError as exc:
                    st.error(f"Session could not be loaded: {exc}")
                else:
                    st.session_state["session_id"] = snapshot.session_id
                    st.session_state["workflow_state"] = snapshot.workflow_state
                    st.session_state["discovery_messages"] = snapshot.messages
                    st.session_state["audit_log"] = snapshot.audit_log
                    st.session_state["hiring_pack"] = snapshot.hiring_pack
                    st.session_state["candidate_evaluation"] = (
                        snapshot.candidate_evaluation
                    )
                    st.session_state["discovery_error"] = None
                    st.rerun()

    conversation_column, summary_column = st.columns([3, 2])

    with conversation_column:
        if st.session_state["workflow_state"] is None:
            st.markdown("**Step 1 -- basic role information**")
            with st.form("role_setup_form"):
                title = st.text_input("Role title", placeholder="Marketing Intern")
                division = st.text_input("Division", placeholder="ZURU Toys")
                team = st.text_input("Team", placeholder="Growth Marketing")
                location = st.text_input("Location", placeholder="Auckland")
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
                            division=division.strip() or None,
                            team=team.strip() or None,
                            location=location.strip() or None,
                            initial_manager_statement=initial_statement.strip(),
                        ),
                    )
                    initial_state = WorkflowState(role_specification=role)
                    client = OpenRouterClient(settings)
                    try:
                        state = take_discovery_turn(
                            state=initial_state,
                            manager_message=initial_statement.strip(),
                            llm_client=client,
                        )
                    except (LLMClientError, DiscoverySemanticValidationError) as exc:
                        st.session_state["discovery_error"] = str(exc)
                    else:
                        session_id = uuid.uuid4().hex
                        audit_log = append_audit_event(
                            AuditLog(session_id=session_id),
                            AuditEventType.SESSION_CREATED,
                            role=initial_state.role_specification,
                            actor="system",
                        )
                        audit_log = record_discovery_turn(
                            audit_log,
                            previous_state=initial_state,
                            updated_state=state,
                            manager_answer=initial_statement.strip(),
                        )
                        messages = [
                            DiscoveryMessage(
                                role="manager",
                                content=initial_statement.strip(),
                            )
                        ]
                        if state.current_question is not None:
                            messages.append(
                                DiscoveryMessage(
                                    role="assistant",
                                    content=state.current_question.question,
                                )
                            )
                        st.session_state["session_id"] = session_id
                        st.session_state["workflow_state"] = state
                        st.session_state["discovery_messages"] = messages
                        st.session_state["audit_log"] = audit_log
                        st.session_state["hiring_pack"] = None
                        st.session_state["candidate_evaluation"] = None
                        st.session_state["discovery_error"] = None
                    st.rerun()
        else:
            state: WorkflowState = st.session_state["workflow_state"]
            st.markdown(f"**Current stage:** {_human_label(state.current_stage.value)}")

            messages = st.session_state["discovery_messages"]
            for message in messages:
                chat_role = "user" if message.role == "manager" else "assistant"
                with st.chat_message(chat_role):
                    st.write(message.content)

            if state.current_question is not None:
                if not messages or messages[-1].content != state.current_question.question:
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
                                state=state,
                                manager_message=answer.strip(),
                                llm_client=client,
                            )
                        except (LLMClientError, DiscoverySemanticValidationError) as exc:
                            st.session_state["discovery_error"] = str(exc)
                        else:
                            audit_log = record_discovery_turn(
                                _current_audit_log(state.role_specification),
                                previous_state=state,
                                updated_state=new_state,
                                manager_answer=answer.strip(),
                            )
                            updated_messages = [
                                *messages,
                                DiscoveryMessage(
                                    role="manager",
                                    content=answer.strip(),
                                ),
                            ]
                            if new_state.current_question is not None:
                                updated_messages.append(
                                    DiscoveryMessage(
                                        role="assistant",
                                        content=new_state.current_question.question,
                                    )
                                )
                            st.session_state["workflow_state"] = new_state
                            st.session_state["discovery_messages"] = updated_messages
                            st.session_state["audit_log"] = audit_log
                            st.session_state["discovery_error"] = None
                        st.rerun()
            else:
                st.success("No open question -- see the readiness summary for next steps.")

            if st.button("Start over"):
                st.session_state["workflow_state"] = None
                st.session_state["discovery_error"] = None
                st.session_state["session_id"] = None
                st.session_state["discovery_messages"] = []
                st.session_state["audit_log"] = None
                st.session_state["hiring_pack"] = None
                st.session_state["candidate_evaluation"] = None
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

            if role.requirements:
                st.markdown("**Edit requirements**")
            for item in role.requirements:
                with st.expander(
                    f"{item.name} · {_human_label(item.priority.value)}"
                ):
                    st.caption(f"Manager source: “{item.source_statement}”")
                    with st.form(
                        f"edit_requirement_{role.version}_{item.requirement_id}"
                    ):
                        edited_name = st.text_input(
                            "Requirement name",
                            value=item.name,
                        )
                        edited_description = st.text_area(
                            "Description",
                            value=item.description or "",
                        )
                        priorities = list(RequirementPriority)
                        edited_priority = st.selectbox(
                            "Priority",
                            options=priorities,
                            index=priorities.index(item.priority),
                            format_func=lambda value: _human_label(value.value),
                        )
                        edited_rationale = st.text_area(
                            "Business rationale",
                            value=item.business_rationale or "",
                        )
                        edit_submitted = st.form_submit_button("Save changes")

                    if edit_submitted:
                        try:
                            updated_role = edit_requirement(
                                role,
                                item.requirement_id,
                                name=edited_name,
                                description=edited_description,
                                priority=edited_priority,
                                business_rationale=edited_rationale,
                            )
                        except (ValueError, ValidationError) as exc:
                            st.error(f"Requirement could not be updated: {exc}")
                        else:
                            audit_log = record_requirement_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                requirement_id=item.requirement_id,
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()

                    delete_confirmed = st.checkbox(
                        "Confirm deletion from the current role version",
                        key=(
                            f"confirm_delete_{role.version}_"
                            f"{item.requirement_id}"
                        ),
                    )
                    if st.button(
                        "Delete requirement",
                        key=f"delete_{role.version}_{item.requirement_id}",
                        disabled=not delete_confirmed,
                    ):
                        updated_role = delete_requirement(
                            role, item.requirement_id
                        )
                        audit_log = record_requirement_edit(
                            _current_audit_log(role),
                            previous_role=role,
                            updated_role=updated_role,
                            requirement_id=item.requirement_id,
                            deleted=True,
                        )
                        st.session_state["workflow_state"] = state.model_copy(
                            update={"role_specification": updated_role}
                        )
                        st.session_state["audit_log"] = audit_log
                        st.rerun()

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

with tabs[1]:
    st.subheader("Review Role")
    state = st.session_state["workflow_state"]
    if state is None:
        st.info("Define a role before reviewing readiness and approval.")
    else:
        previous_role = state.role_specification
        role = refresh_role_quality(previous_role)
        if role != previous_role:
            audit_log = record_new_contradictions(
                _current_audit_log(previous_role),
                previous_role=previous_role,
                updated_role=role,
            )
            state = state.model_copy(update={"role_specification": role})
            st.session_state["workflow_state"] = state
            st.session_state["audit_log"] = audit_log
        report = evaluate_role_quality(role)

        score_column, status_column = st.columns(2)
        score_column.metric("Readiness", f"{report.readiness.score}/100")
        status_column.metric(
            "Interpretation", report.readiness.interpretation.title()
        )
        st.caption(
            "This is a deterministic product heuristic, not a rating of the "
            "manager or a validated psychometric measure."
        )

        st.markdown("**Readiness dimensions**")
        st.dataframe(
            [
                {
                    "Dimension": item.label,
                    "Points": f"{item.earned_points}/{item.weight}",
                    "Explanation": item.explanation,
                }
                for item in report.readiness.dimensions
            ],
            hide_index=True,
            use_container_width=True,
        )

        st.markdown(f"**Approval blockers ({len(report.blockers)})**")
        if report.blockers:
            for blocker in report.blockers:
                st.error(blocker)
        else:
            st.caption("No non-overridable blockers remain.")

        st.markdown("### Complete missing role sections")
        st.caption(
            "Discovery only extracts requirements, assumptions, and "
            "ambiguities from the conversation. The sections below are "
            "business decisions a manager states directly -- confirm them "
            "here to clear the blockers above."
        )

        with st.expander("Business need", expanded=bool(report.blockers)):
            with st.form(f"business_need_{role.version}"):
                problem = st.text_area(
                    "Business problem", value=role.business_need.problem or ""
                )
                why_now = st.text_input(
                    "Why now", value=role.business_need.why_now or ""
                )
                cost_of_vacancy = st.text_input(
                    "Cost of leaving this vacant",
                    value=role.business_need.cost_of_vacancy or "",
                )
                is_replacement = st.checkbox(
                    "This role replaces someone who left",
                    value=bool(role.business_need.is_replacement),
                )
                business_need_submitted = st.form_submit_button("Save business need")
            if business_need_submitted:
                updated_role = edit_business_need(
                    role,
                    problem=problem,
                    why_now=why_now,
                    cost_of_vacancy=cost_of_vacancy,
                    is_replacement=is_replacement,
                )
                audit_log = record_role_section_edit(
                    _current_audit_log(role),
                    previous_role=role,
                    updated_role=updated_role,
                    section="business_need",
                )
                st.session_state["workflow_state"] = state.model_copy(
                    update={"role_specification": updated_role}
                )
                st.session_state["audit_log"] = audit_log
                st.rerun()

        with st.expander(
            f"Success outcomes ({len(role.success_outcomes)})",
            expanded=not role.success_outcomes,
        ):
            for item in role.success_outcomes:
                with st.container(border=True):
                    st.caption(item.outcome_id)
                    with st.form(f"edit_outcome_{role.version}_{item.outcome_id}"):
                        description = st.text_area(
                            "Description", value=item.description
                        )
                        time_horizon = st.text_input(
                            "Time horizon", value=item.time_horizon or ""
                        )
                        measure = st.text_input("Measure", value=item.measure or "")
                        priorities = list(RequirementPriority)
                        priority = st.selectbox(
                            "Priority",
                            options=priorities,
                            index=priorities.index(item.priority),
                            format_func=lambda value: _human_label(value.value),
                            key=f"outcome_priority_{role.version}_{item.outcome_id}",
                        )
                        outcome_submitted = st.form_submit_button("Save outcome")
                    if outcome_submitted:
                        try:
                            updated_role = edit_success_outcome(
                                role,
                                item.outcome_id,
                                description=description,
                                time_horizon=time_horizon,
                                measure=measure,
                                priority=priority,
                            )
                        except (ValueError, ValidationError) as exc:
                            st.error(f"Outcome could not be updated: {exc}")
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="success_outcomes",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()
                    if st.button(
                        "Delete outcome",
                        key=f"delete_outcome_{role.version}_{item.outcome_id}",
                    ):
                        try:
                            updated_role = delete_success_outcome(
                                role, item.outcome_id
                            )
                        except SuccessOutcomeNotFoundError as exc:
                            st.error(str(exc))
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="success_outcomes",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()

            st.markdown("**Add an outcome**")
            with st.form(f"add_outcome_{role.version}"):
                new_description = st.text_area("Description", key="new_outcome_description")
                new_time_horizon = st.text_input(
                    "Time horizon", placeholder="First 90 days", key="new_outcome_horizon"
                )
                new_measure = st.text_input(
                    "Measure", placeholder="Three approved posts a week", key="new_outcome_measure"
                )
                new_priority = st.selectbox(
                    "Priority",
                    options=list(RequirementPriority),
                    format_func=lambda value: _human_label(value.value),
                    key="new_outcome_priority",
                )
                add_outcome_submitted = st.form_submit_button("Add outcome")
            if add_outcome_submitted:
                if not new_description.strip():
                    st.error("An outcome description is required.")
                else:
                    updated_role = add_success_outcome(
                        role,
                        description=new_description,
                        time_horizon=new_time_horizon,
                        measure=new_measure,
                        priority=new_priority,
                    )
                    audit_log = record_role_section_edit(
                        _current_audit_log(role),
                        previous_role=role,
                        updated_role=updated_role,
                        section="success_outcomes",
                    )
                    st.session_state["workflow_state"] = state.model_copy(
                        update={"role_specification": updated_role}
                    )
                    st.session_state["audit_log"] = audit_log
                    st.rerun()

        with st.expander(
            f"Responsibilities ({len(role.responsibilities)})",
            expanded=not role.responsibilities,
        ):
            for item in role.responsibilities:
                with st.container(border=True):
                    st.caption(item.responsibility_id)
                    with st.form(
                        f"edit_responsibility_{role.version}_{item.responsibility_id}"
                    ):
                        description = st.text_area(
                            "Description", value=item.description
                        )
                        frequency = st.text_input(
                            "Frequency", value=item.frequency or ""
                        )
                        ownership_level = st.text_input(
                            "Ownership level", value=item.ownership_level or ""
                        )
                        priority_options = [None, *RequirementPriority]
                        responsibility_priority = st.selectbox(
                            "Priority",
                            options=priority_options,
                            index=priority_options.index(item.priority),
                            format_func=lambda value: (
                                "Unset" if value is None else _human_label(value.value)
                            ),
                            key=(
                                f"responsibility_priority_{role.version}_"
                                f"{item.responsibility_id}"
                            ),
                        )
                        responsibility_submitted = st.form_submit_button(
                            "Save responsibility"
                        )
                    if responsibility_submitted:
                        try:
                            updated_role = edit_responsibility(
                                role,
                                item.responsibility_id,
                                description=description,
                                frequency=frequency,
                                ownership_level=ownership_level,
                                priority=responsibility_priority,
                            )
                        except (ValueError, ValidationError) as exc:
                            st.error(f"Responsibility could not be updated: {exc}")
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="responsibilities",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()
                    if st.button(
                        "Delete responsibility",
                        key=(
                            f"delete_responsibility_{role.version}_"
                            f"{item.responsibility_id}"
                        ),
                    ):
                        try:
                            updated_role = delete_responsibility(
                                role, item.responsibility_id
                            )
                        except ResponsibilityNotFoundError as exc:
                            st.error(str(exc))
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="responsibilities",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()

            st.markdown("**Add a responsibility**")
            with st.form(f"add_responsibility_{role.version}"):
                new_resp_description = st.text_area(
                    "Description", key="new_responsibility_description"
                )
                new_resp_frequency = st.text_input(
                    "Frequency", placeholder="Weekly", key="new_responsibility_frequency"
                )
                new_resp_ownership = st.text_input(
                    "Ownership level",
                    placeholder="Shared",
                    key="new_responsibility_ownership",
                )
                new_resp_priority = st.selectbox(
                    "Priority",
                    options=[None, *RequirementPriority],
                    format_func=lambda value: (
                        "Unset" if value is None else _human_label(value.value)
                    ),
                    key="new_responsibility_priority",
                )
                add_responsibility_submitted = st.form_submit_button(
                    "Add responsibility"
                )
            if add_responsibility_submitted:
                if not new_resp_description.strip():
                    st.error("A responsibility description is required.")
                else:
                    updated_role = add_responsibility(
                        role,
                        description=new_resp_description,
                        frequency=new_resp_frequency,
                        ownership_level=new_resp_ownership,
                        priority=new_resp_priority,
                    )
                    audit_log = record_role_section_edit(
                        _current_audit_log(role),
                        previous_role=role,
                        updated_role=updated_role,
                        section="responsibilities",
                    )
                    st.session_state["workflow_state"] = state.model_copy(
                        update={"role_specification": updated_role}
                    )
                    st.session_state["audit_log"] = audit_log
                    st.rerun()

        with st.expander("Constraints and logistics", expanded=False):
            with st.form(f"constraints_{role.version}"):
                country = st.text_input("Country", value=role.constraints.country or "")
                location = st.text_input(
                    "Location", value=role.constraints.location or ""
                )
                work_arrangement = st.text_input(
                    "Work arrangement",
                    value=role.constraints.work_arrangement or "",
                )
                work_rights = st.text_input(
                    "Work rights", value=role.constraints.work_rights or ""
                )
                weekly_hours = st.text_input(
                    "Weekly hours", value=role.constraints.weekly_hours or ""
                )
                travel = st.text_input("Travel", value=role.constraints.travel or "")
                languages = st.text_area(
                    "Languages (one per line)",
                    value="\n".join(role.constraints.languages),
                )
                jurisdiction_notes = st.text_area(
                    "Jurisdiction notes (one per line)",
                    value="\n".join(role.constraints.jurisdiction_notes),
                )
                constraints_submitted = st.form_submit_button("Save constraints")
            if constraints_submitted:
                updated_role = edit_constraints(
                    role,
                    country=country,
                    location=location,
                    work_arrangement=work_arrangement,
                    work_rights=work_rights,
                    weekly_hours=weekly_hours,
                    travel=travel,
                    languages=_nonempty_lines(languages),
                    jurisdiction_notes=_nonempty_lines(jurisdiction_notes),
                )
                audit_log = record_role_section_edit(
                    _current_audit_log(role),
                    previous_role=role,
                    updated_role=updated_role,
                    section="constraints",
                )
                st.session_state["workflow_state"] = state.model_copy(
                    update={"role_specification": updated_role}
                )
                st.session_state["audit_log"] = audit_log
                st.rerun()

        with st.expander("Assessment plan", expanded=not role.assessment_methods):
            with st.form(f"assessment_plan_{role.version}"):
                assessment_methods = st.text_area(
                    "Assessment methods (one per line)",
                    value="\n".join(role.assessment_methods),
                    placeholder="Structured screening questions",
                )
                decision_owner = st.text_input(
                    "Decision owner", value=role.decision_owner or ""
                )
                assessment_submitted = st.form_submit_button("Save assessment plan")
            if assessment_submitted:
                updated_role = edit_assessment_plan(
                    role,
                    assessment_methods=_nonempty_lines(assessment_methods),
                    decision_owner=decision_owner,
                )
                audit_log = record_role_section_edit(
                    _current_audit_log(role),
                    previous_role=role,
                    updated_role=updated_role,
                    section="assessment_plan",
                )
                st.session_state["workflow_state"] = state.model_copy(
                    update={"role_specification": updated_role}
                )
                st.session_state["audit_log"] = audit_log
                st.rerun()

        with st.expander(
            f"ZURU DNA behaviours ({len(role.zuru_dna_behaviours)})",
            expanded=not role.zuru_dna_behaviours,
        ):
            st.caption(
                "The six ZURU DNA values: Good Humans Only, Collaboration, "
                "Radical Candour, Overprepare and Win, Shift the Needle, "
                "Compounding Improvement."
            )
            for dna_index, item in enumerate(role.zuru_dna_behaviours):
                with st.container(border=True):
                    with st.form(f"edit_dna_{role.version}_{dna_index}"):
                        value = st.text_input("ZURU DNA value", value=item.value)
                        role_behaviour = st.text_area(
                            "Role-relevant behaviour", value=item.role_behaviour
                        )
                        scenario = st.text_input(
                            "Scenario", value=item.scenario or ""
                        )
                        evidence_method = st.text_input(
                            "Evidence method", value=item.evidence_method or ""
                        )
                        dna_submitted = st.form_submit_button("Save behaviour")
                    if dna_submitted:
                        try:
                            updated_role = edit_zuru_dna_behaviour(
                                role,
                                dna_index,
                                value=value,
                                role_behaviour=role_behaviour,
                                scenario=scenario,
                                evidence_method=evidence_method,
                            )
                        except (ValueError, ValidationError) as exc:
                            st.error(f"ZURU DNA behaviour could not be updated: {exc}")
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="zuru_dna_behaviours",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()
                    if st.button(
                        "Delete behaviour",
                        key=f"delete_dna_{role.version}_{dna_index}",
                    ):
                        try:
                            updated_role = delete_zuru_dna_behaviour(role, dna_index)
                        except ZuruDnaBehaviourNotFoundError as exc:
                            st.error(str(exc))
                        else:
                            audit_log = record_role_section_edit(
                                _current_audit_log(role),
                                previous_role=role,
                                updated_role=updated_role,
                                section="zuru_dna_behaviours",
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": updated_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()

            st.markdown("**Add a ZURU DNA behaviour**")
            with st.form(f"add_dna_{role.version}"):
                new_dna_value = st.text_input(
                    "ZURU DNA value", placeholder="Collaboration", key="new_dna_value"
                )
                new_dna_behaviour = st.text_area(
                    "Role-relevant behaviour", key="new_dna_behaviour"
                )
                new_dna_scenario = st.text_input("Scenario", key="new_dna_scenario")
                new_dna_evidence = st.text_input(
                    "Evidence method", key="new_dna_evidence"
                )
                add_dna_submitted = st.form_submit_button("Add behaviour")
            if add_dna_submitted:
                if not new_dna_value.strip() or not new_dna_behaviour.strip():
                    st.error("A ZURU DNA value and role-relevant behaviour are required.")
                else:
                    updated_role = add_zuru_dna_behaviour(
                        role,
                        value=new_dna_value,
                        role_behaviour=new_dna_behaviour,
                        scenario=new_dna_scenario,
                        evidence_method=new_dna_evidence,
                    )
                    audit_log = record_role_section_edit(
                        _current_audit_log(role),
                        previous_role=role,
                        updated_role=updated_role,
                        section="zuru_dna_behaviours",
                    )
                    st.session_state["workflow_state"] = state.model_copy(
                        update={"role_specification": updated_role}
                    )
                    st.session_state["audit_log"] = audit_log
                    st.rerun()

        if report.contradictions:
            st.markdown(f"**Contradictions ({len(report.contradictions)})**")
            for contradiction in report.contradictions:
                if contradiction.resolved:
                    st.write(
                        f"- **{contradiction.severity.title()} / resolved:** "
                        f"{contradiction.description}"
                    )
                    st.caption(
                        f"Resolved by {contradiction.resolved_by}: "
                        f"{contradiction.resolution}"
                    )
                    continue
                st.write(
                    f"- **{contradiction.severity.title()} / unresolved:** "
                    f"{contradiction.description}"
                )
                with st.expander("Mark resolved"):
                    with st.form(
                        f"resolve_{role.version}_{contradiction.contradiction_id}"
                    ):
                        resolver = st.text_input("Resolved by", key=(
                            f"resolver_{role.version}_{contradiction.contradiction_id}"
                        ))
                        resolution_note = st.text_area(
                            "Resolution note",
                            key=(
                                f"resolution_{role.version}_"
                                f"{contradiction.contradiction_id}"
                            ),
                        )
                        resolve_submitted = st.form_submit_button("Mark resolved")
                    if resolve_submitted:
                        try:
                            resolved_role = resolve_contradiction(
                                role,
                                contradiction.contradiction_id,
                                resolved_by=resolver,
                                resolution=resolution_note,
                            )
                        except (ValueError, ContradictionNotFoundError) as exc:
                            st.error(f"Contradiction could not be resolved: {exc}")
                        else:
                            audit_log = append_audit_event(
                                _current_audit_log(role),
                                AuditEventType.HUMAN_REVIEW_RECORDED,
                                role=resolved_role,
                                actor=resolver.strip() or "manager",
                                metadata={
                                    "action": "contradiction_resolved",
                                    "contradiction_id": contradiction.contradiction_id,
                                },
                            )
                            st.session_state["workflow_state"] = state.model_copy(
                                update={"role_specification": resolved_role}
                            )
                            st.session_state["audit_log"] = audit_log
                            st.rerun()

        with st.expander(
            f"Quality warnings ({len(report.warnings)})", expanded=bool(report.warnings)
        ):
            if report.vague_phrases:
                st.markdown("**Vague language**")
                for flag in report.vague_phrases:
                    st.write(f"- **{flag.phrase}** ({flag.source})")
                    st.caption(
                        f"{flag.why_untestable} Clarify: {flag.clarification}"
                    )
            if report.excessive_requirements:
                st.markdown("**Requirement calibration**")
                for issue in report.excessive_requirements:
                    st.write(f"- {issue.message}")
                    st.caption(issue.clarification)
            if not report.warnings:
                st.caption("No acknowledgement warnings.")

        if role.human_approved:
            st.success(
                f"Approved by {role.approved_by} at "
                f"{role.approved_at.isoformat() if role.approved_at else 'unknown time'}."
            )
            st.caption(
                "Explicitly approved: "
                + ", ".join(
                    _human_label(section.value)
                    for section in role.approved_sections
                )
            )
        else:
            st.markdown("**Explicit manager approval**")
            st.caption(
                "Warnings may be acknowledged, but remain in the role's quality log. "
                "High or critical contradictions cannot be overridden."
            )
            with st.form(f"role_approval_{role.role_id}_{role.version}"):
                approver = st.text_input("Approver name")
                section_checks: dict[ApprovalSection, bool] = {}
                for section in REQUIRED_APPROVAL_SECTIONS:
                    section_checks[section] = st.checkbox(
                        f"I approve: {_human_label(section.value)}"
                    )

                warning_checks: dict[str, bool] = {}
                if report.warnings:
                    st.markdown("**Warning acknowledgements**")
                    for warning in report.warnings:
                        warning_checks[warning.warning_id] = st.checkbox(
                            f"Acknowledge: {warning.message}"
                        )
                approval_submitted = st.form_submit_button("Approve role")

            if approval_submitted:
                confirmed_sections = {
                    section
                    for section, checked in section_checks.items()
                    if checked
                }
                acknowledged_warning_ids = {
                    warning_id
                    for warning_id, checked in warning_checks.items()
                    if checked
                }
                try:
                    approved_role = approve_role(
                        role,
                        approver=approver,
                        confirmed_sections=confirmed_sections,
                        acknowledged_warning_ids=acknowledged_warning_ids,
                    )
                except ApprovalBlockedError as exc:
                    for reason in exc.reasons:
                        st.error(reason)
                else:
                    approved_state = state.model_copy(
                        update={"role_specification": approved_role}
                    )
                    advanced_state = advance_workflow(approved_state)
                    audit_log = record_human_review(
                        _current_audit_log(role),
                        role=approved_role,
                    )
                    if state.current_stage is not advanced_state.current_stage:
                        audit_log = append_audit_event(
                            audit_log,
                            AuditEventType.STAGE_CHANGED,
                            role=approved_role,
                            actor=approved_role.approved_by or "manager",
                            metadata={
                                "from": state.current_stage.value,
                                "to": advanced_state.current_stage.value,
                            },
                        )
                    st.session_state["workflow_state"] = advanced_state
                    st.session_state["audit_log"] = audit_log
                    st.rerun()

        audit_log = st.session_state["audit_log"]
        if audit_log is not None:
            with st.expander(f"Audit log ({len(audit_log.events)} events)"):
                st.dataframe(
                    [
                        {
                            "Time": event.timestamp.isoformat(),
                            "Event": _human_label(event.event_type.value),
                            "Actor": event.actor,
                            "Role version": event.role_version,
                            "Approved": event.approval,
                        }
                        for event in audit_log.events
                    ],
                    hide_index=True,
                    use_container_width=True,
                )

with tabs[2]:
    st.subheader("Hiring Pack")
    state = st.session_state["workflow_state"]
    if state is None:
        st.info("Define and approve a role before generating a hiring pack.")
    else:
        role = state.role_specification
        blockers = generation_blockers(role)
        if blockers:
            st.warning("Generation is unavailable until the approved-role contract is complete.")
            for blocker in blockers:
                st.write(f"- {blocker}")
        else:
            st.success(
                f"Role version {role.version} is approved by "
                f"{role.approved_by or 'the hiring manager'}."
            )

        pack: HiringPack | None = st.session_state["hiring_pack"]
        if pack is not None and hiring_pack_is_stale(pack, role):
            st.warning(
                "This hiring pack traces to an older role version. Re-approve the "
                "current role, then regenerate before editing or using the pack."
            )

        generation_actor = st.text_input(
            "Generation actor",
            value=role.approved_by or "",
            key=f"generation_actor_{role.role_id}_{role.version}",
            help="Recorded in hiring-pack provenance and the append-only audit log.",
        )
        generation_disabled = bool(blockers) or not api_ready or not generation_actor.strip()
        generation_label = "Regenerate hiring pack" if pack is not None else "Generate hiring pack"
        if st.button(
            generation_label,
            disabled=generation_disabled,
            type="primary",
        ):
            client = OpenRouterClient(settings)
            session_id = st.session_state["session_id"] or uuid.uuid4().hex
            st.session_state["session_id"] = session_id
            try:
                with st.spinner("Generating and validating the hiring pack..."):
                    result = generate_and_persist_hiring_pack(
                        role=role,
                        llm_client=client,
                        reference_directory=REFERENCE_DIRECTORY,
                        actor=generation_actor,
                        session_id=session_id,
                        session_store=SESSION_STORE,
                        audit_log=_current_audit_log(role),
                        existing_pack=pack,
                    )
            except (
                GenerationBlockedError,
                HiringPackValidationError,
                LLMClientError,
                ReferenceLoadError,
                StorageError,
                ValueError,
            ) as exc:
                st.error(f"Hiring-pack generation could not be completed: {exc}")
                st.caption("No unvalidated pack or generation audit event was accepted.")
            else:
                _persist_result(
                    hiring_pack=result.hiring_pack,
                    audit_log=result.audit_log,
                )

        pack = st.session_state["hiring_pack"]
        if pack is not None:
            provenance = pack.provenance
            metadata_columns = st.columns(3)
            metadata_columns[0].metric("Pack version", pack.version)
            metadata_columns[1].metric(
                "Source role version", provenance.source_role_version
            )
            metadata_columns[2].metric(
                "Human edited", "Yes" if pack.human_edited else "No"
            )
            st.caption(
                f"Generated {provenance.generated_at.isoformat()} by "
                f"{provenance.generated_by}; model "
                f"{provenance.model or 'not reported'}; prompt "
                f"{provenance.prompt_version}."
            )
            st.caption(
                "Local references: "
                + ", ".join(
                    reference.filename for reference in provenance.reference_files
                )
            )
            if pack.last_edited_by and pack.last_edited_at:
                st.caption(
                    f"Last edited {pack.last_edited_at.isoformat()} by "
                    f"{pack.last_edited_by}."
                )

            jd = pack.job_description
            st.markdown("---")
            st.markdown(f"## {jd.title}")
            st.caption(jd.location)
            st.markdown("**Role purpose**")
            st.write(jd.purpose)
            st.markdown("**Business impact**")
            st.write(jd.business_impact)
            for label, values in (
                ("Responsibilities", jd.responsibilities),
                ("Measurable outcomes", jd.outcomes),
            ):
                st.markdown(f"**{label}**")
                for value in values:
                    st.write(f"- {value}")

            requirement_by_id = {
                item.requirement_id: item for item in role.requirements
            }
            st.markdown("**Must-have criteria**")
            for criterion in jd.must_have_criteria:
                st.write(f"- {criterion.text}")
                st.caption(
                    f"{criterion.requirement_id} — "
                    f"{requirement_by_id[criterion.requirement_id].name}"
                )
            st.markdown("**Preferred criteria**")
            if jd.preferred_criteria:
                for criterion in jd.preferred_criteria:
                    st.write(f"- {criterion.text}")
                    st.caption(
                        f"{criterion.requirement_id} — "
                        f"{requirement_by_id[criterion.requirement_id].name}"
                    )
            else:
                st.caption("No approved preferred criteria.")

            st.markdown("**Relevant ZURU DNA behaviours**")
            for behaviour in jd.zuru_dna_behaviours:
                st.write(f"- **{behaviour.value}:** {behaviour.role_behaviour}")
            st.markdown("**Logistics and eligibility**")
            for value in jd.logistics:
                st.write(f"- {value}")
            st.markdown("**Assessment expectations**")
            for value in jd.assessment_expectations:
                st.write(f"- {value}")

            if not hiring_pack_is_stale(pack, role):
                with st.expander("Edit job description"):
                    with st.form(f"edit_jd_{pack.hiring_pack_id}_{pack.version}"):
                        jd_editor = st.text_input("Editor name")
                        edited_title = st.text_input("Title", value=jd.title)
                        edited_location = st.text_input("Location", value=jd.location)
                        edited_purpose = st.text_area("Role purpose", value=jd.purpose)
                        edited_impact = st.text_area(
                            "Business impact", value=jd.business_impact
                        )
                        edited_responsibilities = st.text_area(
                            "Responsibilities — one per line",
                            value="\n".join(jd.responsibilities),
                        )
                        edited_outcomes = st.text_area(
                            "Measurable outcomes — one per line",
                            value="\n".join(jd.outcomes),
                        )
                        edited_must_text: list[str] = []
                        for criterion in jd.must_have_criteria:
                            edited_must_text.append(
                                st.text_area(
                                    f"Must-have {criterion.requirement_id}",
                                    value=criterion.text,
                                )
                            )
                        edited_preferred_text: list[str] = []
                        for criterion in jd.preferred_criteria:
                            edited_preferred_text.append(
                                st.text_area(
                                    f"Preferred {criterion.requirement_id}",
                                    value=criterion.text,
                                )
                            )
                        edited_dna_values: list[str] = []
                        edited_dna_behaviours: list[str] = []
                        for index, behaviour in enumerate(jd.zuru_dna_behaviours):
                            edited_dna_values.append(
                                st.text_input(
                                    f"DNA value {index + 1}",
                                    value=behaviour.value,
                                )
                            )
                            edited_dna_behaviours.append(
                                st.text_area(
                                    f"Observable DNA behaviour {index + 1}",
                                    value=behaviour.role_behaviour,
                                )
                            )
                        edited_logistics = st.text_area(
                            "Logistics — one per line",
                            value="\n".join(jd.logistics),
                        )
                        edited_assessment = st.text_area(
                            "Assessment expectations — one per line",
                            value="\n".join(jd.assessment_expectations),
                        )
                        save_jd = st.form_submit_button(
                            "Save JD changes",
                            disabled=not jd_editor.strip(),
                        )

                    if save_jd:
                        try:
                            updated_jd = JobDescription(
                                title=edited_title,
                                location=edited_location,
                                purpose=edited_purpose,
                                business_impact=edited_impact,
                                responsibilities=_nonempty_lines(
                                    edited_responsibilities
                                ),
                                outcomes=_nonempty_lines(edited_outcomes),
                                must_have_criteria=[
                                    JobDescriptionCriterion(
                                        requirement_id=criterion.requirement_id,
                                        text=text,
                                    )
                                    for criterion, text in zip(
                                        jd.must_have_criteria,
                                        edited_must_text,
                                        strict=True,
                                    )
                                ],
                                preferred_criteria=[
                                    JobDescriptionCriterion(
                                        requirement_id=criterion.requirement_id,
                                        text=text,
                                    )
                                    for criterion, text in zip(
                                        jd.preferred_criteria,
                                        edited_preferred_text,
                                        strict=True,
                                    )
                                ],
                                zuru_dna_behaviours=[
                                    ZuruDnaSelection(
                                        value=value,
                                        role_behaviour=behaviour,
                                    )
                                    for value, behaviour in zip(
                                        edited_dna_values,
                                        edited_dna_behaviours,
                                        strict=True,
                                    )
                                ],
                                logistics=_nonempty_lines(edited_logistics),
                                assessment_expectations=_nonempty_lines(
                                    edited_assessment
                                ),
                            )
                            result = edit_and_persist_hiring_pack(
                                hiring_pack=pack,
                                role=role,
                                editor=jd_editor,
                                session_id=st.session_state["session_id"],
                                session_store=SESSION_STORE,
                                audit_log=_current_audit_log(role),
                                job_description=updated_jd,
                            )
                        except NoHiringPackChangesError:
                            st.info("No JD content changed, so no edit event was recorded.")
                        except ValidationError:
                            st.error(
                                "JD changes were not saved. Complete every required "
                                "section and keep it aligned to the approved role."
                            )
                        except (
                            GenerationBlockedError,
                            HiringPackValidationError,
                            StorageError,
                            ValueError,
                        ) as exc:
                            st.error(f"JD changes were not saved: {exc}")
                        else:
                            _persist_result(
                                hiring_pack=result.hiring_pack,
                                audit_log=result.audit_log,
                            )

            st.markdown("---")
            st.markdown(f"## Screening questions ({len(pack.screening_questions)})")
            for question_index, item in enumerate(pack.screening_questions):
                mapped_labels = [
                    f"{requirement_id} — {requirement_by_id[requirement_id].name}"
                    for requirement_id in item.requirement_ids
                ]
                with st.expander(
                    f"{item.question_id}: {item.question}",
                    expanded=question_index == 0,
                ):
                    st.markdown("**Mapped requirements**")
                    for label in mapped_labels:
                        st.write(f"- {label}")
                    st.markdown("**Purpose**")
                    st.write(item.purpose)
                    st.markdown("**Expected evidence**")
                    for value in item.expected_evidence:
                        st.write(f"- {value}")
                    st.markdown("**Anchored rubric**")
                    st.dataframe(
                        [
                            {
                                "Score": anchor.score,
                                "Observable evidence": anchor.description,
                            }
                            for anchor in item.rubric
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                    flag_columns = st.columns(2)
                    with flag_columns[0]:
                        st.markdown("**Green flags**")
                        for value in item.green_flags:
                            st.write(f"- {value}")
                    with flag_columns[1]:
                        st.markdown("**Red flags**")
                        for value in item.red_flags:
                            st.write(f"- {value}")
                    st.markdown("**Human follow-up**")
                    st.write(item.follow_up)

                    if not hiring_pack_is_stale(pack, role):
                        with st.form(
                            f"edit_question_{pack.hiring_pack_id}_"
                            f"{pack.version}_{item.question_id}"
                        ):
                            question_editor = st.text_input("Editor name")
                            edited_question = st.text_area(
                                "Question", value=item.question
                            )
                            edited_mappings = st.multiselect(
                                "Mapped requirement IDs",
                                options=list(requirement_by_id),
                                default=item.requirement_ids,
                                format_func=lambda requirement_id: (
                                    f"{requirement_id} — "
                                    f"{requirement_by_id[requirement_id].name}"
                                ),
                            )
                            edited_purpose = st.text_area(
                                "Purpose", value=item.purpose
                            )
                            edited_evidence = st.text_area(
                                "Expected evidence — one per line",
                                value="\n".join(item.expected_evidence),
                            )
                            edited_anchor_text: list[str] = []
                            for anchor in item.rubric:
                                edited_anchor_text.append(
                                    st.text_area(
                                        f"Score {anchor.score} anchor",
                                        value=anchor.description,
                                    )
                                )
                            edited_green = st.text_area(
                                "Green flags — one per line",
                                value="\n".join(item.green_flags),
                            )
                            edited_red = st.text_area(
                                "Red flags — one per line",
                                value="\n".join(item.red_flags),
                            )
                            edited_follow_up = st.text_area(
                                "Follow-up", value=item.follow_up
                            )
                            save_question = st.form_submit_button(
                                "Save question changes",
                                disabled=not question_editor.strip(),
                            )

                        if save_question:
                            try:
                                updated_question = ScreeningQuestion(
                                    question_id=item.question_id,
                                    question=edited_question,
                                    requirement_ids=edited_mappings,
                                    purpose=edited_purpose,
                                    expected_evidence=_nonempty_lines(
                                        edited_evidence
                                    ),
                                    rubric=[
                                        RubricAnchor(
                                            score=score,
                                            description=description,
                                        )
                                        for score, description in enumerate(
                                            edited_anchor_text
                                        )
                                    ],
                                    green_flags=_nonempty_lines(edited_green),
                                    red_flags=_nonempty_lines(edited_red),
                                    follow_up=edited_follow_up,
                                )
                                updated_questions = list(pack.screening_questions)
                                updated_questions[question_index] = updated_question
                                result = edit_and_persist_hiring_pack(
                                    hiring_pack=pack,
                                    role=role,
                                    editor=question_editor,
                                    session_id=st.session_state["session_id"],
                                    session_store=SESSION_STORE,
                                    audit_log=_current_audit_log(role),
                                    screening_questions=updated_questions,
                                )
                            except NoHiringPackChangesError:
                                st.info(
                                    "No question content changed, so no edit event "
                                    "was recorded."
                                )
                            except ValidationError:
                                st.error(
                                    "Question changes were not saved. Keep at least "
                                    "one valid mapping, all six anchors, and non-empty "
                                    "red and green flags."
                                )
                            except (
                                GenerationBlockedError,
                                HiringPackValidationError,
                                StorageError,
                                ValueError,
                            ) as exc:
                                st.error(f"Question changes were not saved: {exc}")
                            else:
                                _persist_result(
                                    hiring_pack=result.hiring_pack,
                                    audit_log=result.audit_log,
                                )

            st.markdown("**Human-review guidance**")
            for value in pack.human_review_guidance:
                st.write(f"- {value}")
            if not hiring_pack_is_stale(pack, role):
                with st.form(
                    f"edit_guidance_{pack.hiring_pack_id}_{pack.version}"
                ):
                    guidance_editor = st.text_input("Guidance editor")
                    edited_guidance = st.text_area(
                        "Guidance — one item per line",
                        value="\n".join(pack.human_review_guidance),
                    )
                    save_guidance = st.form_submit_button(
                        "Save guidance changes",
                        disabled=not guidance_editor.strip(),
                    )
                if save_guidance:
                    try:
                        result = edit_and_persist_hiring_pack(
                            hiring_pack=pack,
                            role=role,
                            editor=guidance_editor,
                            session_id=st.session_state["session_id"],
                            session_store=SESSION_STORE,
                            audit_log=_current_audit_log(role),
                            human_review_guidance=_nonempty_lines(edited_guidance),
                        )
                    except NoHiringPackChangesError:
                        st.info(
                            "No guidance changed, so no edit event was recorded."
                        )
                    except ValidationError:
                        st.error("At least one human-review guidance item is required.")
                    except (
                        GenerationBlockedError,
                        HiringPackValidationError,
                        StorageError,
                        ValueError,
                    ) as exc:
                        st.error(f"Guidance changes were not saved: {exc}")
                    else:
                        _persist_result(
                            hiring_pack=result.hiring_pack,
                            audit_log=result.audit_log,
                        )

with tabs[3]:
    st.subheader("Candidate Evidence")
    state = st.session_state["workflow_state"]
    pack: HiringPack | None = st.session_state["hiring_pack"]
    evaluation: CandidateEvaluation | None = st.session_state[
        "candidate_evaluation"
    ]
    if state is None:
        st.info("Define and approve a role before evaluating candidate evidence.")
    elif pack is None:
        role = state.role_specification
        st.info(
            f"Selected role: {role.basic_info.title or role.role_id} "
            f"(version {role.version})."
        )
        st.warning("Generate a hiring pack before collecting candidate responses.")
    else:
        role = state.role_specification
        role_columns = st.columns(3)
        role_columns[0].metric(
            "Selected role", role.basic_info.title or role.role_id
        )
        role_columns[1].metric(
            "Approval", "Approved" if role.human_approved else "Not approved"
        )
        role_columns[2].metric(
            "Hiring pack", f"{pack.hiring_pack_id} · v{pack.version}"
        )
        pack_stale = hiring_pack_is_stale(pack, role)
        if pack_stale:
            st.warning(
                "This hiring pack is stale for the current role version. Candidate "
                "evaluation is blocked until the matching approved role snapshot is "
                "loaded or the pack is regenerated."
            )
        elif not role.human_approved:
            st.warning("The selected role is not approved, so evaluation is blocked.")
        else:
            st.success(
                f"Role version {role.version} and hiring-pack version "
                f"{pack.version} are eligible for evidence review."
            )

        existing_source = (
            evaluation.source_response_set
            if evaluation is not None
            and evaluation.source_response_set is not None
            and evaluation.role_id == role.role_id
            and evaluation.role_version == role.version
            and evaluation.hiring_pack_id == pack.hiring_pack_id
            and evaluation.hiring_pack_version == pack.version
            else None
        )
        existing_answers = (
            {
                response.question_id: response.answer_text
                for response in existing_source.responses
            }
            if existing_source is not None
            else {}
        )
        with st.form(
            f"candidate_responses_{role.role_id}_{role.version}_"
            f"{pack.hiring_pack_id}_{pack.version}"
        ):
            candidate_id = st.text_input(
                "Anonymous candidate identifier",
                value=(
                    existing_source.candidate_id
                    if existing_source is not None
                    else ""
                ),
                help=(
                    "Use a pseudonymous reference, not a candidate name or email "
                    "address."
                ),
            )
            evaluation_actor = st.text_input(
                "Evaluation actor",
                value=role.approved_by or "",
                help="Recorded in evaluation provenance and the append-only audit log.",
            )
            response_values: list[tuple[ScreeningQuestion, str]] = []
            st.markdown("**Screening responses**")
            for question in pack.screening_questions:
                st.markdown(f"**{question.question_id}: {question.question}**")
                st.caption(
                    "Mapped requirements: " + ", ".join(question.requirement_ids)
                )
                answer = st.text_area(
                    f"Candidate response for {question.question_id}",
                    value=existing_answers.get(question.question_id, ""),
                    key=(
                        f"candidate_answer_{pack.hiring_pack_id}_{pack.version}_"
                        f"{question.question_id}"
                    ),
                    label_visibility="collapsed",
                )
                response_values.append((question, answer))
            evaluate_submitted = st.form_submit_button(
                "Run evidence evaluation",
                type="primary",
                disabled=(
                    pack_stale
                    or not role.human_approved
                    or not api_ready
                    or not candidate_id.strip()
                    or not evaluation_actor.strip()
                ),
            )

        if not api_ready:
            st.caption(
                "OpenRouter must be configured to run a new evaluation. Existing "
                "persisted evaluations remain reviewable without a provider call."
            )

        if evaluate_submitted:
            response_by_question = (
                {
                    response.question_id: response
                    for response in existing_source.responses
                }
                if existing_source is not None
                else {}
            )
            responses = [
                CandidateQuestionResponse(
                    response_id=(
                        response_by_question[question.question_id].response_id
                        if question.question_id in response_by_question
                        else f"response_{question.question_id}"
                    ),
                    question_id=question.question_id,
                    answer_text=answer,
                )
                for question, answer in response_values
            ]
            same_source_content = (
                existing_source is not None
                and candidate_id.strip() == existing_source.candidate_id
                and [
                    (item.question_id, item.answer_text)
                    for item in responses
                ]
                == [
                    (item.question_id, item.answer_text)
                    for item in existing_source.responses
                ]
            )
            response_set = CandidateResponseSet(
                response_set_id=(
                    existing_source.response_set_id
                    if same_source_content and existing_source is not None
                    else f"response_set_{uuid.uuid4().hex}"
                ),
                candidate_id=candidate_id.strip(),
                source_role_id=role.role_id,
                source_role_version=role.version,
                source_hiring_pack_id=pack.hiring_pack_id,
                source_hiring_pack_version=pack.version,
                submitted_at=(
                    existing_source.submitted_at
                    if same_source_content and existing_source is not None
                    else datetime.now(UTC)
                ),
                responses=responses,
            )
            client = OpenRouterClient(settings)
            session_id = st.session_state["session_id"] or uuid.uuid4().hex
            st.session_state["session_id"] = session_id
            try:
                with st.spinner(
                    "Extracting evidence and validating requirement assessments..."
                ):
                    result = evaluate_and_persist_candidate(
                        role=role,
                        hiring_pack=pack,
                        response_set=response_set,
                        llm_client=client,
                        actor=evaluation_actor,
                        session_id=session_id,
                        session_store=SESSION_STORE,
                        audit_log=_current_audit_log(role),
                        existing_evaluation=(
                            evaluation if same_source_content else None
                        ),
                    )
            except (
                CandidateEvaluationValidationError,
                EvaluationBlockedError,
                LLMClientError,
                StorageError,
                ValidationError,
                ValueError,
            ) as exc:
                st.error(f"Candidate evaluation could not be completed: {exc}")
                st.caption(
                    "No partial or unvalidated evaluation and no successful "
                    "assessment event were accepted."
                )
            else:
                _persist_result(
                    hiring_pack=pack,
                    audit_log=result.audit_log,
                    candidate_evaluation=result.evaluation,
                )

        evaluation = st.session_state["candidate_evaluation"]
        if evaluation is not None:
            source_is_current = (
                evaluation.role_id == role.role_id
                and evaluation.role_version == role.version
                and evaluation.hiring_pack_id == pack.hiring_pack_id
                and evaluation.hiring_pack_version == pack.version
            )
            if not source_is_current:
                st.warning(
                    "The displayed evaluation is historical. It remains readable, "
                    "but review edits are disabled because its source role or hiring "
                    "pack is not current."
                )
            st.markdown("---")
            st.markdown(
                f"## Evaluation for {evaluation.candidate_id} "
                f"(version {evaluation.version})"
            )
            provenance_columns = st.columns(4)
            provenance_columns[0].metric(
                "Overall confidence",
                (
                    f"{evaluation.overall_confidence:.0%}"
                    if evaluation.overall_confidence is not None
                    else "Not available"
                ),
            )
            provenance_columns[1].metric(
                "Requirement coverage",
                (
                    f"{evaluation.requirement_coverage:.0%}"
                    if evaluation.requirement_coverage is not None
                    else "Not available"
                ),
            )
            provenance_columns[2].metric(
                "Must-have coverage",
                (
                    f"{evaluation.must_have_coverage:.0%}"
                    if evaluation.must_have_coverage is not None
                    else "Not available"
                ),
            )
            provenance_columns[3].metric(
                "Review routing",
                (
                    _human_label(evaluation.routing.value)
                    if evaluation.routing is not None
                    else "Human review"
                ),
            )
            evaluated_label = (
                evaluation.evaluated_at.isoformat()
                if evaluation.evaluated_at
                else "at an unreported time"
            )
            st.caption(
                f"Source role {evaluation.role_id} v{evaluation.role_version}; "
                f"hiring pack {evaluation.hiring_pack_id} "
                f"v{evaluation.hiring_pack_version}; evaluated "
                f"{evaluated_label} "
                f"by {evaluation.evaluated_by or 'an unreported actor'}; model "
                f"{evaluation.model or 'not reported'}; prompt "
                f"{evaluation.prompt_version or 'not reported'}."
            )
            if evaluation.prompt_injection_detected:
                st.warning(
                    "Instruction-like candidate text was detected and isolated as "
                    "untrusted data. It did not change the schema, IDs, rubric, or "
                    "missing-evidence rules, and confidence was reduced."
                )
            if evaluation.human_edited and evaluation.last_edited_at:
                st.caption(
                    f"Last human review edit: "
                    f"{evaluation.last_edited_at.isoformat()} by "
                    f"{evaluation.last_edited_by}."
                )

            st.markdown("### Extracted evidence")
            if not evaluation.evidence_items:
                st.caption("No requirement-relevant evidence was extracted.")
            for evidence in evaluation.evidence_items:
                with st.expander(
                    f"{evidence.evidence_id} · {evidence.requirement_id} · "
                    f"{_human_label(evidence.evidence_quality.value)}"
                ):
                    st.markdown(f"> {evidence.quote}")
                    st.caption(
                        f"Source: {evidence.source_id}; question: "
                        f"{evidence.source_question_id or 'supporting evidence'}; "
                        f"type: {_human_label(evidence.evidence_type.value)}; "
                        f"ownership: {_human_label(evidence.ownership.value)}; "
                        f"verification: "
                        f"{_human_label(evidence.verification_status.value)}."
                    )
                    st.write(evidence.evaluator_explanation or "")

            st.markdown("### Requirement assessments")
            for assessment_index, assessment in enumerate(
                evaluation.assessments
            ):
                priority = (
                    _human_label(assessment.requirement_priority.value)
                    if assessment.requirement_priority is not None
                    else "Unspecified"
                )
                with st.expander(
                    f"{assessment.requirement_id}: "
                    f"{assessment.requirement_label or 'Requirement'} · "
                    f"{assessment.score}/5 · {priority}",
                    expanded=assessment_index == 0,
                ):
                    assessment_columns = st.columns(3)
                    assessment_columns[0].metric("Score", f"{assessment.score}/5")
                    assessment_columns[1].metric(
                        "Evidence quality",
                        _human_label(assessment.evidence_quality.value),
                    )
                    assessment_columns[2].metric(
                        "Confidence", f"{assessment.confidence:.0%}"
                    )
                    st.caption(
                        "Mapped questions: "
                        + ", ".join(assessment.relevant_question_ids)
                    )
                    st.markdown("**Matched rubric anchor**")
                    st.write(
                        f"{assessment.rubric_question_id}: "
                        f"{assessment.rubric_anchor}"
                    )
                    for label, values in (
                        ("Strengths", assessment.strengths),
                        ("Concerns", assessment.concerns),
                        ("Missing evidence", assessment.missing_evidence),
                        (
                            "Contradictory evidence",
                            assessment.contradictory_evidence,
                        ),
                    ):
                        st.markdown(f"**{label}**")
                        if values:
                            for value in values:
                                st.write(f"- {value}")
                        else:
                            st.caption("None recorded.")
                    st.markdown("**Reviewer explanation**")
                    st.write(
                        assessment.reviewer_explanation
                        or assessment.reasoning_summary
                        or "No explanation supplied."
                    )
                    st.markdown("**Recommended follow-up**")
                    st.write(assessment.human_follow_up or "No follow-up supplied.")

                    if source_is_current:
                        with st.form(
                            f"review_assessment_{evaluation.evaluation_id}_"
                            f"{evaluation.version}_{assessment.requirement_id}"
                        ):
                            reviewer = st.text_input("Reviewer name")
                            reviewed_score = st.slider(
                                "Reviewed score",
                                min_value=0,
                                max_value=5,
                                value=assessment.score,
                            )
                            quality_options = list(EvidenceQuality)
                            reviewed_quality = st.selectbox(
                                "Reviewed evidence quality",
                                options=quality_options,
                                index=quality_options.index(
                                    assessment.evidence_quality
                                ),
                                format_func=lambda item: _human_label(item.value),
                            )
                            reviewed_confidence = st.slider(
                                "Reviewed confidence",
                                min_value=0.0,
                                max_value=1.0,
                                value=float(assessment.confidence),
                                step=0.05,
                                help=(
                                    "Confidence is certainty in the evidence "
                                    "assessment, not candidate quality."
                                ),
                            )
                            reviewed_strengths = st.text_area(
                                "Strengths — one per line",
                                value="\n".join(assessment.strengths),
                            )
                            reviewed_concerns = st.text_area(
                                "Concerns — one per line",
                                value="\n".join(assessment.concerns),
                            )
                            reviewed_missing = st.text_area(
                                "Missing evidence — one per line",
                                value="\n".join(assessment.missing_evidence),
                            )
                            reviewed_contradictions = st.text_area(
                                "Contradictions — one per line",
                                value="\n".join(
                                    assessment.contradictory_evidence
                                ),
                            )
                            reviewed_follow_up = st.text_area(
                                "Recommended follow-up",
                                value=assessment.human_follow_up or "",
                            )
                            reviewed_explanation = st.text_area(
                                "Reviewer-facing explanation",
                                value=(
                                    assessment.reviewer_explanation
                                    or assessment.reasoning_summary
                                    or ""
                                ),
                            )
                            evidence_relevance: dict[str, float] = {}
                            evidence_qualities: dict[str, EvidenceQuality] = {}
                            for evidence_id in assessment.evidence_item_ids:
                                evidence_item = next(
                                    item
                                    for item in evaluation.evidence_items
                                    if item.evidence_id == evidence_id
                                )
                                evidence_relevance[evidence_id] = st.slider(
                                    f"Relevance · {evidence_id}",
                                    min_value=0.0,
                                    max_value=1.0,
                                    value=float(evidence_item.relevance or 0.0),
                                    step=0.05,
                                )
                                evidence_qualities[evidence_id] = st.selectbox(
                                    f"Evidence quality · {evidence_id}",
                                    options=quality_options,
                                    index=quality_options.index(
                                        evidence_item.evidence_quality
                                    ),
                                    format_func=lambda item: _human_label(
                                        item.value
                                    ),
                                )
                            save_review = st.form_submit_button(
                                "Save human review",
                                disabled=not reviewer.strip(),
                            )

                        if save_review:
                            updated_assessments = list(evaluation.assessments)
                            updated_assessments[assessment_index] = (
                                assessment.model_copy(
                                    update={
                                        "score": reviewed_score,
                                        "confidence": reviewed_confidence,
                                        "evidence_quality": reviewed_quality,
                                        "strengths": _nonempty_lines(
                                            reviewed_strengths
                                        ),
                                        "concerns": _nonempty_lines(
                                            reviewed_concerns
                                        ),
                                        "missing_evidence": _nonempty_lines(
                                            reviewed_missing
                                        ),
                                        "contradictory_evidence": (
                                            _nonempty_lines(
                                                reviewed_contradictions
                                            )
                                        ),
                                        "human_follow_up": (
                                            reviewed_follow_up.strip() or None
                                        ),
                                        "reasoning_summary": (
                                            reviewed_explanation.strip() or None
                                        ),
                                        "reviewer_explanation": (
                                            reviewed_explanation.strip() or None
                                        ),
                                    }
                                )
                            )
                            updated_evidence = list(evaluation.evidence_items)
                            for evidence_index, evidence_item in enumerate(
                                updated_evidence
                            ):
                                if evidence_item.evidence_id in evidence_relevance:
                                    updated_evidence[evidence_index] = (
                                        evidence_item.model_copy(
                                            update={
                                                "relevance": evidence_relevance[
                                                    evidence_item.evidence_id
                                                ],
                                                "evidence_quality": (
                                                    evidence_qualities[
                                                        evidence_item.evidence_id
                                                    ]
                                                ),
                                            }
                                        )
                                    )
                            try:
                                result = edit_and_persist_candidate_evaluation(
                                    evaluation=evaluation,
                                    role=role,
                                    hiring_pack=pack,
                                    editor=reviewer,
                                    session_id=st.session_state["session_id"],
                                    session_store=SESSION_STORE,
                                    audit_log=_current_audit_log(role),
                                    evidence_items=updated_evidence,
                                    assessments=updated_assessments,
                                )
                            except NoCandidateEvaluationChangesError:
                                st.info(
                                    "No review content changed, so no audit event "
                                    "was recorded."
                                )
                            except (
                                CandidateEvaluationValidationError,
                                StorageError,
                                ValidationError,
                                ValueError,
                            ) as exc:
                                st.error(f"Human review was not saved: {exc}")
                            else:
                                _persist_result(
                                    hiring_pack=pack,
                                    audit_log=result.audit_log,
                                    candidate_evaluation=result.evaluation,
                                )

            st.markdown("### Overall reviewer guidance")
            for guidance in evaluation.reviewer_guidance:
                st.write(f"- {guidance}")
            if evaluation.human_follow_ups:
                st.markdown("**Recommended follow-up questions**")
                for follow_up in evaluation.human_follow_ups:
                    st.write(f"- {follow_up}")
            if evaluation.contradictions:
                st.markdown("**Cross-response contradictions**")
                for contradiction in evaluation.contradictions:
                    st.write(f"- {contradiction}")

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
