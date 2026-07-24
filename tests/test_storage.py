"""Safe JSON storage tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.models import (
    BasicRoleInfo,
    EmploymentType,
    RoleFamily,
    RoleLevel,
    RoleSpecification,
    WorkflowStage,
)
from src.storage import (
    AuditEventType,
    AuditIntegrityError,
    AuditLog,
    DiscoveryMessage,
    JsonStorage,
    MalformedJsonError,
    MissingStorageFile,
    SessionStore,
    StoragePathError,
    StorageSecurityError,
    StorageValidationError,
    append_audit_event,
    record_discovery_turn,
    record_role_section_edit,
)
from src.workflow import WorkflowState


def test_save_load_round_trip(tmp_path: Path) -> None:
    storage = JsonStorage(tmp_path)
    role = RoleSpecification(
        role_id="role_001",
        basic_info=BasicRoleInfo(title="Marketing Intern"),
    )

    saved_path = storage.save("session/role.json", role)
    restored = storage.load("session/role.json", RoleSpecification)

    assert saved_path.is_file()
    assert restored == role
    assert saved_path.read_text(encoding="utf-8").endswith("\n")


def test_malformed_json_raises_clear_error(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(MalformedJsonError):
        JsonStorage(tmp_path).load("broken.json", RoleSpecification)


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MissingStorageFile):
        JsonStorage(tmp_path).load("missing.json", RoleSpecification)


def test_schema_validation_failure_is_wrapped(tmp_path: Path) -> None:
    (tmp_path / "invalid.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StorageValidationError):
        JsonStorage(tmp_path).load("invalid.json", RoleSpecification)


@pytest.mark.parametrize("unsafe_path", ["../escape.json", "nested/../../escape.json"])
def test_path_traversal_is_prevented(tmp_path: Path, unsafe_path: str) -> None:
    with pytest.raises(StoragePathError):
        JsonStorage(tmp_path).save(
            unsafe_path, RoleSpecification(role_id="role_001")
        )


def test_audit_event_enum_matches_the_documented_phase_9_event_list() -> None:
    assert {item.value for item in AuditEventType} == {
        "session_created",
        "manager_answer",
        "ai_update",
        "manager_edit",
        "stage_changed",
        "contradiction_flagged",
        "requirement_approved",
        "hiring_pack_generated",
        "hiring_pack_edited",
        "candidate_evidence_added",
        "assessment_generated",
        "human_review_recorded",
    }


def versioned_role() -> RoleSpecification:
    return RoleSpecification(
        role_id="role_001",
        version=3,
        parent_version=2,
        basic_info=BasicRoleInfo(
            title="Marketing Intern",
            role_family=RoleFamily.MARKETING,
            role_level=RoleLevel.INTERN,
            employment_type=EmploymentType.INTERNSHIP,
        ),
    )


def test_append_audit_event_records_version_approval_and_chronology() -> None:
    timestamp = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    role = versioned_role()
    log = AuditLog(session_id="abc123")

    log = append_audit_event(
        log,
        AuditEventType.SESSION_CREATED,
        role=role,
        actor="system",
        occurred_at=timestamp,
    )
    log = append_audit_event(
        log,
        AuditEventType.MANAGER_ANSWER,
        role=role,
        actor="manager",
        metadata={"character_count": 42},
        occurred_at=timestamp + timedelta(seconds=1),
    )

    assert [event.event_id for event in log.events] == ["event_0001", "event_0002"]
    assert [event.event_type for event in log.events] == [
        AuditEventType.SESSION_CREATED,
        AuditEventType.MANAGER_ANSWER,
    ]
    assert log.events[1].role_version == 3
    assert log.events[1].parent_version == 2
    assert log.events[1].approval is False
    assert log.events[0].timestamp < log.events[1].timestamp


def test_append_audit_event_rejects_out_of_order_timestamp() -> None:
    timestamp = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    log = append_audit_event(
        AuditLog(session_id="abc123"),
        AuditEventType.SESSION_CREATED,
        role=versioned_role(),
        actor="system",
        occurred_at=timestamp,
    )

    with pytest.raises(AuditIntegrityError, match="chronological"):
        append_audit_event(
            log,
            AuditEventType.MANAGER_ANSWER,
            role=versioned_role(),
            actor="manager",
            occurred_at=timestamp - timedelta(seconds=1),
        )


def test_discovery_audit_records_metadata_without_manager_answer_text() -> None:
    previous = WorkflowState(
        role_specification=versioned_role(),
        current_stage=WorkflowStage.BASIC_INFO,
    )
    updated_role = versioned_role().model_copy(
        update={"version": 4, "parent_version": 3}
    )
    updated = WorkflowState(
        role_specification=updated_role,
        current_stage=WorkflowStage.BUSINESS_NEED,
    )

    log = record_discovery_turn(
        AuditLog(session_id="abc123"),
        previous_state=previous,
        updated_state=updated,
        manager_answer="Confidential manager wording should not be copied.",
    )

    assert [event.event_type for event in log.events] == [
        AuditEventType.MANAGER_ANSWER,
        AuditEventType.AI_UPDATE,
        AuditEventType.STAGE_CHANGED,
    ]
    assert log.events[0].metadata == {"character_count": 50}
    assert "Confidential manager wording" not in log.model_dump_json()


def test_record_role_section_edit_captures_section_and_previous_version() -> None:
    previous_role = versioned_role()
    updated_role = previous_role.model_copy(
        update={"version": 4, "parent_version": 3}
    )

    log = record_role_section_edit(
        AuditLog(session_id="abc123"),
        previous_role=previous_role,
        updated_role=updated_role,
        section="business_need",
    )

    assert [event.event_type for event in log.events] == [AuditEventType.MANAGER_EDIT]
    assert log.events[0].metadata == {
        "section": "business_need",
        "previous_role_version": 3,
    }
    assert log.events[0].role_version == 4


def test_session_store_preserves_workflow_messages_and_audit_log(
    tmp_path: Path,
) -> None:
    role = versioned_role()
    state = WorkflowState(
        role_specification=role,
        current_stage=WorkflowStage.BUSINESS_NEED,
    )
    timestamp = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    messages = [
        DiscoveryMessage(
            role="manager",
            content="We need a Marketing Intern.",
            timestamp=timestamp,
        )
    ]
    audit_log = append_audit_event(
        AuditLog(session_id="abc123"),
        AuditEventType.SESSION_CREATED,
        role=role,
        actor="system",
        occurred_at=timestamp,
    )
    store = SessionStore(tmp_path)

    folder = store.save_session(
        session_id="abc123",
        workflow_state=state,
        messages=messages,
        audit_log=audit_log,
    )
    restored = store.load_session("abc123")

    assert {path.name for path in folder.iterdir()} == {
        "role_specification.json",
        "discovery_history.json",
        "audit_log.json",
        "role_versions",
    }
    assert (
        folder / "role_versions" / "role_specification_v3.json"
    ).is_file()
    assert restored.workflow_state == state
    assert restored.messages == messages
    assert restored.audit_log == audit_log
    assert store.list_sessions() == ["abc123"]


def test_session_store_refuses_to_rewrite_existing_audit_history(
    tmp_path: Path,
) -> None:
    role = versioned_role()
    state = WorkflowState(role_specification=role)
    timestamp = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    first_log = append_audit_event(
        AuditLog(session_id="abc123"),
        AuditEventType.SESSION_CREATED,
        role=role,
        actor="system",
        occurred_at=timestamp,
    )
    full_log = append_audit_event(
        first_log,
        AuditEventType.MANAGER_ANSWER,
        role=role,
        actor="manager",
        occurred_at=timestamp + timedelta(seconds=1),
    )
    store = SessionStore(tmp_path)
    store.save_session(
        session_id="abc123",
        workflow_state=state,
        messages=[],
        audit_log=full_log,
    )

    with pytest.raises(AuditIntegrityError, match="append-only"):
        store.save_session(
            session_id="abc123",
            workflow_state=state,
            messages=[],
            audit_log=first_log,
        )


def test_session_store_retains_previous_role_versions(tmp_path: Path) -> None:
    role_v3 = versioned_role()
    state_v3 = WorkflowState(role_specification=role_v3)
    log = append_audit_event(
        AuditLog(session_id="abc123"),
        AuditEventType.SESSION_CREATED,
        role=role_v3,
        actor="system",
    )
    store = SessionStore(tmp_path)
    store.save_session(
        session_id="abc123",
        workflow_state=state_v3,
        messages=[],
        audit_log=log,
    )
    role_v4 = role_v3.model_copy(update={"version": 4, "parent_version": 3})
    state_v4 = state_v3.model_copy(update={"role_specification": role_v4})
    log = append_audit_event(
        log,
        AuditEventType.MANAGER_EDIT,
        role=role_v4,
        actor="manager",
    )

    folder = store.save_session(
        session_id="abc123",
        workflow_state=state_v4,
        messages=[],
        audit_log=log,
    )

    assert (
        folder / "role_versions" / "role_specification_v3.json"
    ).is_file()
    assert (
        folder / "role_versions" / "role_specification_v4.json"
    ).is_file()


def test_secret_bearing_audit_metadata_is_not_written(tmp_path: Path) -> None:
    log = append_audit_event(
        AuditLog(session_id="abc123"),
        AuditEventType.SESSION_CREATED,
        role=versioned_role(),
        actor="system",
        metadata={"authorization": "Bearer synthetic-secret"},
    )

    with pytest.raises(StorageSecurityError):
        JsonStorage(tmp_path).save("audit_log.json", log)
