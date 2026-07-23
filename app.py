"""Restrained Streamlit shell for the ZURU Talent Copilot prototype.

All business logic lives in ``src/``. This file only renders state and wires
user actions to discovery, readiness, and workflow helpers -- it computes
nothing itself.
"""

from __future__ import annotations

import platform
import uuid
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from src.config import Settings
from src.discovery import (
    delete_requirement,
    edit_requirement,
    take_discovery_turn,
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
    DiscoverySemanticValidationError,
    EmploymentType,
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
    *, hiring_pack: HiringPack, audit_log: AuditLog
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
    )


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
                st.session_state["hiring_pack"] = result.hiring_pack
                st.session_state["audit_log"] = result.audit_log
                try:
                    _save_active_session(
                        hiring_pack=result.hiring_pack,
                        audit_log=result.audit_log,
                    )
                except StorageError as exc:
                    st.warning(
                        "The pack and audit event were saved, but the full session "
                        f"snapshot could not be refreshed: {exc}"
                    )
                st.rerun()

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
                            st.session_state["hiring_pack"] = result.hiring_pack
                            st.session_state["audit_log"] = result.audit_log
                            try:
                                _save_active_session(
                                    hiring_pack=result.hiring_pack,
                                    audit_log=result.audit_log,
                                )
                            except StorageError as exc:
                                st.warning(
                                    "The edit and audit event were saved, but the "
                                    f"full session snapshot was not refreshed: {exc}"
                                )
                            st.rerun()

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
                                st.session_state["hiring_pack"] = result.hiring_pack
                                st.session_state["audit_log"] = result.audit_log
                                try:
                                    _save_active_session(
                                        hiring_pack=result.hiring_pack,
                                        audit_log=result.audit_log,
                                    )
                                except StorageError as exc:
                                    st.warning(
                                        "The edit and audit event were saved, but "
                                        "the full session snapshot was not refreshed: "
                                        f"{exc}"
                                    )
                                st.rerun()

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
                        st.session_state["hiring_pack"] = result.hiring_pack
                        st.session_state["audit_log"] = result.audit_log
                        try:
                            _save_active_session(
                                hiring_pack=result.hiring_pack,
                                audit_log=result.audit_log,
                            )
                        except StorageError as exc:
                            st.warning(
                                "The edit and audit event were saved, but the "
                                f"full session snapshot was not refreshed: {exc}"
                            )
                        st.rerun()

with tabs[3]:
    st.subheader("Candidate Evidence")
    st.info("Candidate evidence evaluation is reserved for Phase 7 and is not implemented.")

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
