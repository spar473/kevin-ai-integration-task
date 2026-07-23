"""Safe, atomic JSON persistence for validated Pydantic models."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.models import (
    CandidateEvaluation,
    ClarificationQuestion,
    HiringPack,
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
    hiring_pack: HiringPack | None = None
    candidate_evaluation: CandidateEvaluation | None = None


def append_audit_event(
    audit_log: AuditLog,
    event_type: AuditEventType,
    *,
    role: RoleSpecification,
    actor: str,
    metadata: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
    artifact_model: str | None = None,
    artifact_prompt_version: str | None = None,
    inherit_role_audit: bool = True,
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
        model=role.audit.model if inherit_role_audit else artifact_model,
        prompt_version=(
            role.audit.prompt_version
            if inherit_role_audit
            else artifact_prompt_version
        ),
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


def _hiring_pack_content_hash(pack: HiringPack) -> str:
    content = {
        "job_description": pack.job_description.model_dump(mode="json"),
        "screening_questions": [
            item.model_dump(mode="json") for item in pack.screening_questions
        ],
        "human_review_guidance": pack.human_review_guidance,
    }
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def record_hiring_pack_generated(
    audit_log: AuditLog,
    *,
    role: RoleSpecification,
    hiring_pack: HiringPack,
) -> AuditLog:
    """Record one validated, already-persisted hiring-pack generation."""
    provenance = hiring_pack.provenance
    if (
        provenance.source_role_id != role.role_id
        or provenance.source_role_version != role.version
    ):
        raise AuditIntegrityError(
            "Hiring-pack generation does not trace to the supplied role version."
        )
    return append_audit_event(
        audit_log,
        AuditEventType.HIRING_PACK_GENERATED,
        role=role,
        actor=provenance.generated_by,
        occurred_at=provenance.generated_at,
        inherit_role_audit=False,
        artifact_model=provenance.model,
        artifact_prompt_version=provenance.prompt_version,
        metadata={
            "hiring_pack_id": hiring_pack.hiring_pack_id,
            "hiring_pack_version": hiring_pack.version,
            "source_role_version": provenance.source_role_version,
            "provider": provenance.provider,
            "reference_files": [
                item.filename for item in provenance.reference_files
            ],
            "question_count": len(hiring_pack.screening_questions),
        },
    )


def record_hiring_pack_edited(
    audit_log: AuditLog,
    *,
    role: RoleSpecification,
    previous_pack: HiringPack,
    updated_pack: HiringPack,
    changed_fields: list[str],
) -> AuditLog:
    """Record one meaningful, validated human edit using content hashes."""
    if not changed_fields:
        raise AuditIntegrityError("A no-op hiring-pack edit cannot be audited.")
    if (
        previous_pack.hiring_pack_id != updated_pack.hiring_pack_id
        or updated_pack.parent_version != previous_pack.version
        or updated_pack.version != previous_pack.version + 1
        or not updated_pack.human_edited
        or updated_pack.last_edited_by is None
        or updated_pack.last_edited_at is None
    ):
        raise AuditIntegrityError("Hiring-pack edit version metadata is invalid.")
    if (
        updated_pack.provenance.source_role_id != role.role_id
        or updated_pack.provenance.source_role_version != role.version
    ):
        raise AuditIntegrityError(
            "Hiring-pack edit does not trace to the supplied role version."
        )
    before_hash = _hiring_pack_content_hash(previous_pack)
    after_hash = _hiring_pack_content_hash(updated_pack)
    if before_hash == after_hash:
        raise AuditIntegrityError("A no-op hiring-pack edit cannot be audited.")
    return append_audit_event(
        audit_log,
        AuditEventType.HIRING_PACK_EDITED,
        role=role,
        actor=updated_pack.last_edited_by,
        occurred_at=updated_pack.last_edited_at,
        inherit_role_audit=False,
        artifact_model=updated_pack.provenance.model,
        artifact_prompt_version=updated_pack.provenance.prompt_version,
        metadata={
            "hiring_pack_id": updated_pack.hiring_pack_id,
            "hiring_pack_version": updated_pack.version,
            "previous_hiring_pack_version": previous_pack.version,
            "edited_fields": changed_fields,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
        },
    )


def _candidate_evaluation_content_hash(evaluation: CandidateEvaluation) -> str:
    content = {
        "evidence_items": [
            item.model_dump(mode="json") for item in evaluation.evidence_items
        ],
        "assessments": [
            item.model_dump(mode="json") for item in evaluation.assessments
        ],
        "reviewer_guidance": evaluation.reviewer_guidance,
    }
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def record_candidate_evaluation_generated(
    audit_log: AuditLog,
    *,
    role: RoleSpecification,
    evaluation: CandidateEvaluation,
) -> AuditLog:
    """Record one fully validated and already-persistable evaluation version."""
    response_set = evaluation.source_response_set
    if (
        not evaluation.evaluation_id
        or evaluation.evaluated_at is None
        or evaluation.evaluated_by is None
        or response_set is None
        or evaluation.role_id != role.role_id
        or evaluation.role_version != role.version
    ):
        raise AuditIntegrityError(
            "Candidate evaluation provenance is incomplete or mismatched."
        )
    return append_audit_event(
        audit_log,
        AuditEventType.ASSESSMENT_GENERATED,
        role=role,
        actor=evaluation.evaluated_by,
        occurred_at=evaluation.evaluated_at,
        inherit_role_audit=False,
        artifact_model=evaluation.model,
        artifact_prompt_version=evaluation.prompt_version,
        metadata={
            "candidate_id": evaluation.candidate_id,
            "evaluation_id": evaluation.evaluation_id,
            "evaluation_version": evaluation.version,
            "source_role_id": evaluation.role_id,
            "source_role_version": evaluation.role_version,
            "source_hiring_pack_id": evaluation.hiring_pack_id,
            "source_hiring_pack_version": evaluation.hiring_pack_version,
            "response_count": len(response_set.responses),
            "evidence_count": len(evaluation.evidence_items),
            "assessment_count": len(evaluation.assessments),
            "provider": evaluation.provider,
            "prompt_injection_detected": evaluation.prompt_injection_detected,
            "validation_outcome": "passed",
        },
    )


def record_candidate_evaluation_edited(
    audit_log: AuditLog,
    *,
    role: RoleSpecification,
    previous_evaluation: CandidateEvaluation,
    updated_evaluation: CandidateEvaluation,
    changed_fields: list[str],
) -> AuditLog:
    """Record one meaningful human review without retaining candidate answers."""
    if not changed_fields:
        raise AuditIntegrityError("A no-op candidate review cannot be audited.")
    if (
        not updated_evaluation.evaluation_id
        or previous_evaluation.evaluation_id != updated_evaluation.evaluation_id
        or updated_evaluation.version != previous_evaluation.version + 1
        or updated_evaluation.parent_version != previous_evaluation.version
        or not updated_evaluation.human_edited
        or updated_evaluation.last_edited_by is None
        or updated_evaluation.last_edited_at is None
    ):
        raise AuditIntegrityError("Candidate review version metadata is invalid.")
    if (
        updated_evaluation.role_id != role.role_id
        or updated_evaluation.role_version != role.version
        or updated_evaluation.hiring_pack_id
        != previous_evaluation.hiring_pack_id
        or updated_evaluation.hiring_pack_version
        != previous_evaluation.hiring_pack_version
    ):
        raise AuditIntegrityError(
            "Candidate review does not preserve source role and pack versions."
        )
    before_hash = _candidate_evaluation_content_hash(previous_evaluation)
    after_hash = _candidate_evaluation_content_hash(updated_evaluation)
    if before_hash == after_hash:
        raise AuditIntegrityError("A no-op candidate review cannot be audited.")
    return append_audit_event(
        audit_log,
        AuditEventType.HUMAN_REVIEW_RECORDED,
        role=role,
        actor=updated_evaluation.last_edited_by,
        occurred_at=updated_evaluation.last_edited_at,
        inherit_role_audit=False,
        artifact_model=updated_evaluation.model,
        artifact_prompt_version=updated_evaluation.prompt_version,
        metadata={
            "candidate_id": updated_evaluation.candidate_id,
            "evaluation_id": updated_evaluation.evaluation_id,
            "evaluation_version": updated_evaluation.version,
            "previous_evaluation_version": previous_evaluation.version,
            "source_role_version": updated_evaluation.role_version,
            "source_hiring_pack_id": updated_evaluation.hiring_pack_id,
            "source_hiring_pack_version": updated_evaluation.hiring_pack_version,
            "edited_fields": changed_fields,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
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
    _HIRING_PACK_FILE = "hiring_pack.json"
    _CANDIDATE_EVALUATION_FILE = "candidate_evaluation.json"
    _VERSIONS_FOLDER = "role_versions"
    _HIRING_PACK_VERSIONS_FOLDER = "hiring_pack_versions"
    _CANDIDATE_EVALUATIONS_FOLDER = "candidate_evaluations"

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
        hiring_pack: HiringPack | None = None,
        candidate_evaluation: CandidateEvaluation | None = None,
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
        if hiring_pack is not None:
            if (
                hiring_pack.provenance.source_role_id != role.role_id
                or hiring_pack.provenance.source_role_version > role.version
            ):
                raise StorageValidationError(
                    "Hiring pack does not trace to this session's role history."
                )
            self.save_hiring_pack(session_id, hiring_pack)
        if candidate_evaluation is not None:
            if (
                candidate_evaluation.role_id != role.role_id
                or candidate_evaluation.role_version > role.version
            ):
                raise StorageValidationError(
                    "Candidate evaluation does not trace to this session's role history."
                )
            self.save_candidate_evaluation(session_id, candidate_evaluation)
        self.storage.save(audit_path, audit_log)
        return self.root / folder

    def save_hiring_pack(
        self, session_id: str, hiring_pack: HiringPack
    ) -> Path:
        """Persist the current pack and retain every prior pack version."""
        folder = self._folder_name(session_id)
        if (
            hiring_pack.source_session_id is not None
            and hiring_pack.source_session_id != session_id
        ):
            raise StorageValidationError(
                "Hiring pack belongs to a different session."
            )
        pack_path = f"{folder}/{self._HIRING_PACK_FILE}"
        try:
            existing_pack = self.storage.load(pack_path, HiringPack)
        except MissingStorageFile:
            existing_pack = None
        if existing_pack is not None:
            if existing_pack.hiring_pack_id != hiring_pack.hiring_pack_id:
                raise AuditIntegrityError(
                    "A persisted hiring pack cannot be replaced with a new ID."
                )
            if existing_pack.version == hiring_pack.version:
                if existing_pack != hiring_pack:
                    raise AuditIntegrityError(
                        "A hiring-pack version cannot be rewritten."
                    )
                return self.storage._safe_path(pack_path)
            if (
                hiring_pack.version != existing_pack.version + 1
                or hiring_pack.parent_version != existing_pack.version
            ):
                raise AuditIntegrityError(
                    "Hiring-pack versions must be appended sequentially."
                )
            self.storage.save(
                (
                    f"{folder}/{self._HIRING_PACK_VERSIONS_FOLDER}/"
                    f"hiring_pack_v{existing_pack.version}.json"
                ),
                existing_pack,
            )
        self.storage.save(pack_path, hiring_pack)
        self.storage.save(
            (
                f"{folder}/{self._HIRING_PACK_VERSIONS_FOLDER}/"
                f"hiring_pack_v{hiring_pack.version}.json"
            ),
            hiring_pack,
        )
        return self.storage._safe_path(pack_path)

    def save_audit_log(self, session_id: str, audit_log: AuditLog) -> Path:
        """Persist only an append-only audit continuation for a session."""
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
        return self.storage.save(audit_path, audit_log)

    def save_candidate_evaluation(
        self,
        session_id: str,
        evaluation: CandidateEvaluation,
    ) -> Path:
        """Persist a current pointer and immutable per-evaluation versions."""
        folder = self._folder_name(session_id)
        if not evaluation.evaluation_id:
            raise StorageValidationError("Candidate evaluation ID is required.")
        evaluation_path = f"{folder}/{self._CANDIDATE_EVALUATION_FILE}"
        version_path = (
            f"{folder}/{self._CANDIDATE_EVALUATIONS_FOLDER}/"
            f"{evaluation.evaluation_id}/"
            f"candidate_evaluation_v{evaluation.version}.json"
        )
        try:
            existing_version = self.storage.load(version_path, CandidateEvaluation)
        except MissingStorageFile:
            existing_version = None
        if existing_version is not None:
            if existing_version != evaluation:
                raise AuditIntegrityError(
                    "A candidate-evaluation version cannot be rewritten."
                )
            self.storage.save(evaluation_path, evaluation)
            return self.storage._safe_path(evaluation_path)

        try:
            current = self.storage.load(evaluation_path, CandidateEvaluation)
        except MissingStorageFile:
            current = None
        if current is not None and current.evaluation_id == evaluation.evaluation_id:
            if current.version == evaluation.version:
                if current != evaluation:
                    raise AuditIntegrityError(
                        "A candidate-evaluation version cannot be rewritten."
                    )
                return self.storage._safe_path(evaluation_path)
            if (
                evaluation.version != current.version + 1
                or evaluation.parent_version != current.version
            ):
                raise AuditIntegrityError(
                    "Candidate-evaluation versions must be appended sequentially."
                )
        self.storage.save(evaluation_path, evaluation)
        self.storage.save(version_path, evaluation)
        return self.storage._safe_path(evaluation_path)

    def save_candidate_evaluation_and_audit(
        self,
        session_id: str,
        evaluation: CandidateEvaluation,
        audit_log: AuditLog,
    ) -> Path:
        """Save evaluation before audit and roll back if the audit write fails.

        Each JSON replacement remains atomic. The scoped rollback covers
        in-process failures between the two files so a successful assessment is
        not retained without its matching append-only event.
        """
        folder = self._folder_name(session_id)
        if not evaluation.evaluation_id:
            raise StorageValidationError("Candidate evaluation ID is required.")
        current_relative = f"{folder}/{self._CANDIDATE_EVALUATION_FILE}"
        version_relative = (
            f"{folder}/{self._CANDIDATE_EVALUATIONS_FOLDER}/"
            f"{evaluation.evaluation_id}/"
            f"candidate_evaluation_v{evaluation.version}.json"
        )
        try:
            previous_current = self.storage.load(
                current_relative, CandidateEvaluation
            )
        except MissingStorageFile:
            previous_current = None
        version_path = self.storage._safe_path(version_relative)
        version_preexisted = version_path.is_file()

        evaluation_path = self.save_candidate_evaluation(session_id, evaluation)
        try:
            self.save_audit_log(session_id, audit_log)
        except Exception as audit_error:
            rollback_errors: list[Exception] = []
            try:
                if previous_current is None:
                    current_path = self.storage._safe_path(current_relative)
                    if current_path.is_file():
                        current_path.unlink()
                else:
                    self.storage.save(current_relative, previous_current)
            except (OSError, StorageError) as exc:
                rollback_errors.append(exc)
            try:
                if not version_preexisted and version_path.is_file():
                    version_path.unlink()
            except OSError as exc:
                rollback_errors.append(exc)
            if rollback_errors:
                raise StorageError(
                    "Audit persistence failed and evaluation rollback was incomplete."
                ) from audit_error
            raise
        return evaluation_path

    def load_candidate_evaluation_version(
        self,
        session_id: str,
        evaluation_id: str,
        version: int,
    ) -> CandidateEvaluation:
        """Load one immutable historical evaluation version."""
        if not self._SESSION_ID_PATTERN.fullmatch(evaluation_id):
            raise StoragePathError(
                "Candidate evaluation id contains unsupported characters."
            )
        folder = self._folder_name(session_id)
        return self.storage.load(
            (
                f"{folder}/{self._CANDIDATE_EVALUATIONS_FOLDER}/"
                f"{evaluation_id}/candidate_evaluation_v{version}.json"
            ),
            CandidateEvaluation,
        )

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
        try:
            hiring_pack = self.storage.load(
                f"{folder}/{self._HIRING_PACK_FILE}", HiringPack
            )
        except MissingStorageFile:
            hiring_pack = None
        try:
            candidate_evaluation = self.storage.load(
                f"{folder}/{self._CANDIDATE_EVALUATION_FILE}",
                CandidateEvaluation,
            )
        except MissingStorageFile:
            candidate_evaluation = None
        if (
            history.session_id != session_id
            or audit_log.session_id != session_id
            or history.role_id != role.role_id
            or history.role_version != role.version
        ):
            raise StorageValidationError(
                "Stored session files do not reference the same role version."
            )
        if hiring_pack is not None and (
            hiring_pack.provenance.source_role_id != role.role_id
            or hiring_pack.provenance.source_role_version > role.version
            or (
                hiring_pack.source_session_id is not None
                and hiring_pack.source_session_id != session_id
            )
        ):
            raise StorageValidationError(
                "Stored hiring pack does not trace to the session role history."
            )
        if candidate_evaluation is not None and (
            candidate_evaluation.role_id != role.role_id
            or candidate_evaluation.role_version > role.version
            or hiring_pack is None
            or candidate_evaluation.hiring_pack_id != hiring_pack.hiring_pack_id
            or candidate_evaluation.hiring_pack_version is None
            or candidate_evaluation.hiring_pack_version > hiring_pack.version
        ):
            raise StorageValidationError(
                "Stored candidate evaluation does not trace to session role and "
                "hiring-pack history."
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
            hiring_pack=hiring_pack,
            candidate_evaluation=candidate_evaluation,
        )
