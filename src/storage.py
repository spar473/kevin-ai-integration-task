"""Safe, atomic JSON persistence for validated Pydantic models."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.models import (
    ClarificationQuestion,
    RoleSpecification,
    WorkflowStage,
)
from src.workflow import WorkflowState


ModelT = TypeVar("ModelT", bound=BaseModel)


class StorageError(RuntimeError):
    """Base error for JSON persistence failures."""


class StoragePathError(StorageError):
    """Raised when a path escapes the configured storage root."""


class MissingStorageFile(StorageError):
    """Raised when a requested JSON file does not exist."""


class MalformedJsonError(StorageError):
    """Raised when a stored file does not contain valid JSON."""


class StorageValidationError(StorageError):
    """Raised when stored JSON does not match its expected schema."""


class StorageSecurityError(StorageError):
    """Raised when a payload appears to contain secret material."""


class AuditIntegrityError(StorageError):
    """Raised when audit history is reordered, truncated, or rewritten."""


class StorageModel(BaseModel):
    """Strict base for persisted session and audit records."""

    model_config = ConfigDict(extra="forbid")


class AuditEventType(str, Enum):
    """Append-only event types specified by docs/02 Phase 9."""

    SESSION_CREATED = "session_created"
    MANAGER_ANSWER = "manager_answer"
    AI_UPDATE = "ai_update"
    MANAGER_EDIT = "manager_edit"
    STAGE_CHANGED = "stage_changed"
    CONTRADICTION_FLAGGED = "contradiction_flagged"
    REQUIREMENT_APPROVED = "requirement_approved"
    HIRING_PACK_GENERATED = "hiring_pack_generated"
    HIRING_PACK_EDITED = "hiring_pack_edited"
    CANDIDATE_EVIDENCE_ADDED = "candidate_evidence_added"
    ASSESSMENT_GENERATED = "assessment_generated"
    HUMAN_REVIEW_RECORDED = "human_review_recorded"


class AuditEvent(StorageModel):
    """Safe provenance metadata for one user or system action."""

    event_id: str
    event_type: AuditEventType
    timestamp: datetime
    actor: str
    role_id: str
    role_version: int
    parent_version: int | None = None
    approval: bool
    model: str | None = None
    prompt_version: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AuditLog(StorageModel):
    """Versioned append-only audit sequence for one prototype session."""

    schema_version: str = "1.0"
    session_id: str
    events: list[AuditEvent] = Field(default_factory=list)


class DiscoveryMessage(StorageModel):
    """One saved role-discovery conversation message."""

    role: Literal["manager", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiscoveryHistory(StorageModel):
    """Persisted workflow fields that are not part of the role specification."""

    schema_version: str = "1.0"
    session_id: str
    role_id: str
    role_version: int
    current_stage: WorkflowStage
    confirmed_stages: list[WorkflowStage] = Field(default_factory=list)
    current_question: ClarificationQuestion | None = None
    messages: list[DiscoveryMessage] = Field(default_factory=list)


class SessionSnapshot(StorageModel):
    """Validated in-memory result of loading a saved session."""

    session_id: str
    workflow_state: WorkflowState
    messages: list[DiscoveryMessage]
    audit_log: AuditLog


def append_audit_event(
    audit_log: AuditLog,
    event_type: AuditEventType,
    *,
    role: RoleSpecification,
    actor: str,
    metadata: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> AuditLog:
    """Return a copied log with one chronological, version-aware event."""
    timestamp = occurred_at or datetime.now(UTC)
    if audit_log.events and timestamp < audit_log.events[-1].timestamp:
        raise AuditIntegrityError("Audit events must remain chronological.")
    event = AuditEvent(
        event_id=f"event_{len(audit_log.events) + 1:04d}",
        event_type=event_type,
        timestamp=timestamp,
        actor=actor.strip() or "unknown",
        role_id=role.role_id,
        role_version=role.version,
        parent_version=role.parent_version,
        approval=role.human_approved,
        model=role.audit.model,
        prompt_version=role.audit.prompt_version,
        metadata=metadata or {},
    )
    return audit_log.model_copy(update={"events": [*audit_log.events, event]})


def record_discovery_turn(
    audit_log: AuditLog,
    *,
    previous_state: WorkflowState,
    updated_state: WorkflowState,
    manager_answer: str,
) -> AuditLog:
    """Record a successful discovery turn without logging the answer text."""
    previous_role = previous_state.role_specification
    updated_role = updated_state.role_specification
    result = append_audit_event(
        audit_log,
        AuditEventType.MANAGER_ANSWER,
        role=previous_role,
        actor="manager",
        metadata={"character_count": len(manager_answer)},
    )
    result = append_audit_event(
        result,
        AuditEventType.AI_UPDATE,
        role=updated_role,
        actor="system",
        metadata={
            "requirements_added": max(
                0, len(updated_role.requirements) - len(previous_role.requirements)
            ),
            "role_version": updated_role.version,
        },
    )
    if previous_state.current_stage is not updated_state.current_stage:
        result = append_audit_event(
            result,
            AuditEventType.STAGE_CHANGED,
            role=updated_role,
            actor="system",
            metadata={
                "from": previous_state.current_stage.value,
                "to": updated_state.current_stage.value,
            },
        )
    known_contradictions = {
        item.contradiction_id for item in previous_role.quality.contradictions
    }
    for contradiction in updated_role.quality.contradictions:
        if contradiction.contradiction_id in known_contradictions:
            continue
        result = append_audit_event(
            result,
            AuditEventType.CONTRADICTION_FLAGGED,
            role=updated_role,
            actor="system",
            metadata={
                "contradiction_id": contradiction.contradiction_id,
                "severity": contradiction.severity,
            },
        )
    return result


def record_requirement_edit(
    audit_log: AuditLog,
    *,
    previous_role: RoleSpecification,
    updated_role: RoleSpecification,
    requirement_id: str,
    changed_fields: list[str] | None = None,
    deleted: bool = False,
) -> AuditLog:
    """Record a manager edit and, when retained, explicit requirement approval."""
    if changed_fields is None:
        if deleted:
            changed_fields = ["deleted"]
        else:
            previous = next(
                item
                for item in previous_role.requirements
                if item.requirement_id == requirement_id
            )
            updated = next(
                item
                for item in updated_role.requirements
                if item.requirement_id == requirement_id
            )
            compared_fields = (
                "name",
                "description",
                "priority",
                "business_rationale",
            )
            changed_fields = [
                field_name
                for field_name in compared_fields
                if getattr(previous, field_name) != getattr(updated, field_name)
            ]
    result = append_audit_event(
        audit_log,
        AuditEventType.MANAGER_EDIT,
        role=updated_role,
        actor="manager",
        metadata={
            "requirement_id": requirement_id,
            "changed_fields": changed_fields,
            "deleted": deleted,
            "previous_role_version": previous_role.version,
        },
    )
    if not deleted:
        result = append_audit_event(
            result,
            AuditEventType.REQUIREMENT_APPROVED,
            role=updated_role,
            actor="manager",
            metadata={"requirement_id": requirement_id},
        )
    return result


def record_new_contradictions(
    audit_log: AuditLog,
    *,
    previous_role: RoleSpecification,
    updated_role: RoleSpecification,
) -> AuditLog:
    """Record newly materialised contradictions without duplicating old events."""
    known_ids = {
        item.contradiction_id for item in previous_role.quality.contradictions
    }
    result = audit_log
    for contradiction in updated_role.quality.contradictions:
        if contradiction.contradiction_id in known_ids:
            continue
        result = append_audit_event(
            result,
            AuditEventType.CONTRADICTION_FLAGGED,
            role=updated_role,
            actor="system",
            metadata={
                "contradiction_id": contradiction.contradiction_id,
                "severity": contradiction.severity,
            },
        )
    return result


def record_human_review(
    audit_log: AuditLog,
    *,
    role: RoleSpecification,
) -> AuditLog:
    """Record explicit role approval without duplicating role content."""
    result = append_audit_event(
        audit_log,
        AuditEventType.REQUIREMENT_APPROVED,
        role=role,
        actor=role.approved_by or "manager",
        metadata={
            "requirement_ids": [
                item.requirement_id for item in role.requirements
            ]
        },
    )
    return append_audit_event(
        result,
        AuditEventType.HUMAN_REVIEW_RECORDED,
        role=role,
        actor=role.approved_by or "manager",
        metadata={
            "approved_sections": [
                section.value for section in role.approved_sections
            ]
        },
    )


class JsonStorage:
    """Persist Pydantic models beneath one configured directory."""

    _SECRET_KEYS = {
        "api_key",
        "openrouter_api_key",
        "authorization",
        "access_token",
        "secret",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _safe_path(self, relative_path: str | Path) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise StoragePathError("Storage paths must be relative.")
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise StoragePathError("Storage path escapes the configured directory.")
        return candidate

    @classmethod
    def _contains_secret(cls, value: object) -> bool:
        if isinstance(value, dict):
            if any(str(key).lower() in cls._SECRET_KEYS for key in value):
                return True
            return any(cls._contains_secret(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_secret(item) for item in value)
        return False

    def save(self, relative_path: str | Path, model: BaseModel) -> Path:
        """Atomically save a model as indented UTF-8 JSON."""
        destination = self._safe_path(relative_path)
        payload = model.model_dump(mode="json")
        if self._contains_secret(payload):
            raise StorageSecurityError("Refusing to persist a secret-bearing payload.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return destination

    def load(self, relative_path: str | Path, model_type: type[ModelT]) -> ModelT:
        """Load JSON and validate it as the requested Pydantic model."""
        source = self._safe_path(relative_path)
        if not source.is_file():
            raise MissingStorageFile(f"Stored JSON file was not found: {relative_path}")
        try:
            raw = source.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedJsonError(f"Malformed JSON in {relative_path}") from exc
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise StorageValidationError(
                f"Stored JSON failed {model_type.__name__} validation: {relative_path}"
            ) from exc


class SessionStore:
    """Save and reload validated discovery sessions under ``session_<id>``."""

    _SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    _ROLE_FILE = "role_specification.json"
    _HISTORY_FILE = "discovery_history.json"
    _AUDIT_FILE = "audit_log.json"
    _VERSIONS_FOLDER = "role_versions"

    def __init__(self, root: Path) -> None:
        self.storage = JsonStorage(root)

    @property
    def root(self) -> Path:
        return self.storage.root

    @classmethod
    def _validate_session_id(cls, session_id: str) -> str:
        if not cls._SESSION_ID_PATTERN.fullmatch(session_id):
            raise StoragePathError("Session id contains unsupported characters.")
        return session_id

    @classmethod
    def _folder_name(cls, session_id: str) -> str:
        return f"session_{cls._validate_session_id(session_id)}"

    def list_sessions(self) -> list[str]:
        """Return complete saved session ids, newest folder first."""
        if not self.root.is_dir():
            return []
        complete: list[Path] = []
        required = {self._ROLE_FILE, self._HISTORY_FILE, self._AUDIT_FILE}
        for folder in self.root.iterdir():
            if (
                folder.is_dir()
                and folder.name.startswith("session_")
                and required.issubset({item.name for item in folder.iterdir()})
            ):
                complete.append(folder)
        complete.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return [item.name.removeprefix("session_") for item in complete]

    def save_session(
        self,
        *,
        session_id: str,
        workflow_state: WorkflowState,
        messages: list[DiscoveryMessage],
        audit_log: AuditLog,
    ) -> Path:
        """Atomically save each validated session file and preserve audit prefix."""
        folder = self._folder_name(session_id)
        if audit_log.session_id != session_id:
            raise AuditIntegrityError("Audit log belongs to a different session.")
        audit_path = f"{folder}/{self._AUDIT_FILE}"
        try:
            existing_log = self.storage.load(audit_path, AuditLog)
        except MissingStorageFile:
            existing_log = None
        if existing_log is not None:
            prefix = audit_log.events[: len(existing_log.events)]
            if (
                len(audit_log.events) < len(existing_log.events)
                or prefix != existing_log.events
            ):
                raise AuditIntegrityError("Audit log must remain append-only.")

        role = workflow_state.role_specification
        if (
            not audit_log.events
            or audit_log.events[-1].role_version != role.version
        ):
            raise AuditIntegrityError(
                "Latest audit event must trace to the saved role version."
            )
        history = DiscoveryHistory(
            session_id=session_id,
            role_id=role.role_id,
            role_version=role.version,
            current_stage=workflow_state.current_stage,
            confirmed_stages=sorted(
                workflow_state.confirmed_stages, key=lambda item: item.value
            ),
            current_question=workflow_state.current_question,
            messages=messages,
        )
        role_path = f"{folder}/{self._ROLE_FILE}"
        try:
            existing_role = self.storage.load(role_path, RoleSpecification)
        except MissingStorageFile:
            existing_role = None
        if existing_role is not None and existing_role.version != role.version:
            self.storage.save(
                (
                    f"{folder}/{self._VERSIONS_FOLDER}/"
                    f"role_specification_v{existing_role.version}.json"
                ),
                existing_role,
            )
        self.storage.save(f"{folder}/{self._ROLE_FILE}", role)
        self.storage.save(
            (
                f"{folder}/{self._VERSIONS_FOLDER}/"
                f"role_specification_v{role.version}.json"
            ),
            role,
        )
        self.storage.save(f"{folder}/{self._HISTORY_FILE}", history)
        self.storage.save(audit_path, audit_log)
        return self.root / folder

    def load_session(self, session_id: str) -> SessionSnapshot:
        """Load all required files and reject cross-version inconsistencies."""
        folder = self._folder_name(session_id)
        role = self.storage.load(
            f"{folder}/{self._ROLE_FILE}", RoleSpecification
        )
        history = self.storage.load(
            f"{folder}/{self._HISTORY_FILE}", DiscoveryHistory
        )
        audit_log = self.storage.load(f"{folder}/{self._AUDIT_FILE}", AuditLog)
        if (
            history.session_id != session_id
            or audit_log.session_id != session_id
            or history.role_id != role.role_id
            or history.role_version != role.version
        ):
            raise StorageValidationError(
                "Stored session files do not reference the same role version."
            )
        state = WorkflowState(
            role_specification=role,
            current_stage=history.current_stage,
            confirmed_stages=set(history.confirmed_stages),
            current_question=history.current_question,
        )
        return SessionSnapshot(
            session_id=session_id,
            workflow_state=state,
            messages=history.messages,
            audit_log=audit_log,
        )
