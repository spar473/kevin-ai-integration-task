"""Pydantic domain models for role discovery and candidate evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class DomainModel(BaseModel):
    """Base model with strict field names and predictable JSON behaviour."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoleFamily(str, Enum):
    """Broad role families used to guide later discovery."""

    MARKETING = "marketing"
    CREATIVE = "creative"
    TECHNICAL = "technical"
    PRODUCT = "product"
    COMMERCIAL = "commercial"
    OPERATIONS = "operations"
    PEOPLE = "people"
    EXECUTIVE = "executive"
    OTHER = "other"


class RoleLevel(str, Enum):
    """Role seniority bands."""

    INTERN = "intern"
    ENTRY = "entry"
    INTERMEDIATE = "intermediate"
    SENIOR = "senior"
    LEAD = "lead"
    EXECUTIVE = "executive"


class EmploymentType(str, Enum):
    """Supported employment arrangements."""

    INTERNSHIP = "internship"
    FIXED_TERM = "fixed_term"
    PERMANENT = "permanent"
    CONTRACT = "contract"
    CASUAL = "casual"


class RequirementCategory(str, Enum):
    """Categories used to group job requirements."""

    TECHNICAL = "technical"
    DOMAIN = "domain"
    BEHAVIOURAL = "behavioural"
    LOGISTICAL = "logistical"
    LEGAL = "legal"
    OTHER = "other"


class RequirementPriority(str, Enum):
    """Business priority of a requirement."""

    MUST_HAVE = "must_have"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class ProficiencyLevel(str, Enum):
    """Expected capability level."""

    AWARENESS = "awareness"
    WORKING = "working"
    INDEPENDENT = "independent"
    ADVANCED = "advanced"
    EXPERT = "expert"


class Learnability(str, Enum):
    """When a capability needs to be available."""

    DAY_ONE = "day_one"
    WITHIN_30_DAYS = "within_30_days"
    WITHIN_90_DAYS = "within_90_days"
    LONGER_TERM = "longer_term"


class WorkflowStage(str, Enum):
    """Ordered role-discovery workflow stages."""

    BASIC_INFO = "basic_info"
    BUSINESS_NEED = "business_need"
    SUCCESS_OUTCOMES = "success_outcomes"
    RESPONSIBILITIES = "responsibilities"
    DAY_ONE_REQUIREMENTS = "day_one_requirements"
    LEARNABLE_REQUIREMENTS = "learnable_requirements"
    BEHAVIOURAL_REQUIREMENTS = "behavioural_requirements"
    CONSTRAINTS = "constraints"
    ASSESSMENT_PLAN = "assessment_plan"
    QUALITY_REVIEW = "quality_review"
    MANAGER_APPROVAL = "manager_approval"
    COMPLETE = "complete"


class DiscoveryProgressRecommendation(str, Enum):
    """Non-binding model advice interpreted by application-owned transitions."""

    STAY = "stay"
    ADVANCE = "advance"


class CandidateSourceType(str, Enum):
    """Provenance labels for candidate evidence."""

    SCREENING_RESPONSE = "screening_response"
    CV = "cv"
    PORTFOLIO_SUMMARY = "portfolio_summary"
    TASK_RESPONSE = "task_response"
    REVIEWER_NOTE = "reviewer_note"


class ReviewStatus(str, Enum):
    """Human review state without an automated hiring outcome."""

    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class ApprovalSection(str, Enum):
    """Role areas a manager must explicitly confirm before approval."""

    BUSINESS_PURPOSE = "business_purpose"
    OUTCOMES = "outcomes"
    MUST_HAVES = "must_haves"
    BEHAVIOURAL_CRITERIA = "behavioural_criteria"
    KEY_CONSTRAINTS = "key_constraints"


class BasicRoleInfo(DomainModel):
    """Known basic facts about a role; discovery may leave fields unknown."""

    title: str | None = None
    role_family: RoleFamily | None = None
    role_level: RoleLevel | None = None
    employment_type: EmploymentType | None = None
    division: str | None = None
    team: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    reporting_to: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    initial_manager_statement: str | None = None


class BusinessNeed(DomainModel):
    """Business reason for opening the role."""

    problem: str | None = None
    why_now: str | None = None
    cost_of_vacancy: str | None = None
    is_replacement: bool | None = None


class SuccessOutcome(DomainModel):
    """An observable outcome expected from the role."""

    outcome_id: NonEmptyText
    description: NonEmptyText
    time_horizon: str | None = None
    measure: str | None = None
    priority: RequirementPriority = RequirementPriority.MUST_HAVE
    source_statement: str | None = None
    confidence: Confidence | None = None


class Responsibility(DomainModel):
    """A recurring responsibility with optional ownership detail."""

    responsibility_id: NonEmptyText
    description: NonEmptyText
    frequency: str | None = None
    ownership_level: str | None = None
    priority: RequirementPriority | None = None
    source_statement: str | None = None
    confidence: Confidence | None = None


class Requirement(DomainModel):
    """A traceable role requirement that preserves the manager's wording."""

    requirement_id: NonEmptyText
    category: RequirementCategory
    name: NonEmptyText
    description: str | None = None
    priority: RequirementPriority
    proficiency: ProficiencyLevel | None = None
    learnability: Learnability | None = None
    accepted_equivalents: list[str] = Field(default_factory=list)
    business_rationale: str | None = None
    evidence_methods: list[str] = Field(default_factory=list)
    source_statement: NonEmptyText
    source_turn_id: str | None = None
    confidence: Confidence | None = None
    requires_confirmation: bool = True
    approved_by_human: bool = False

    @field_validator("source_statement")
    @classmethod
    def source_must_contain_text(cls, value: str) -> str:
        """Reject source statements consisting only of whitespace."""
        if not value.strip():
            raise ValueError("source_statement must not be empty")
        return value

    @model_validator(mode="after")
    def must_have_requires_rationale(self) -> Requirement:
        """Require an explicit business reason for every must-have."""
        if self.priority is RequirementPriority.MUST_HAVE and not (
            self.business_rationale and self.business_rationale.strip()
        ):
            raise ValueError("must-have requirements need a business_rationale")
        return self


class ZuruDnaBehaviour(DomainModel):
    """A role-relevant observable behaviour linked to a ZURU DNA value."""

    value: NonEmptyText
    role_behaviour: NonEmptyText
    scenario: str | None = None
    evidence_method: str | None = None
    source_statement: str | None = None
    approved_by_human: bool = False


class RoleConstraints(DomainModel):
    """Logistical or jurisdictional facts, all optional during discovery."""

    country: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    work_rights: str | None = None
    weekly_hours: str | None = None
    travel: str | None = None
    languages: list[str] = Field(default_factory=list)
    jurisdiction_notes: list[str] = Field(default_factory=list)


class Contradiction(DomainModel):
    """A conflict that must remain visible until human resolution."""

    contradiction_id: NonEmptyText
    description: NonEmptyText
    severity: Annotated[str, Field(pattern="^(low|medium|high|critical)$")]
    source_statements: list[str] = Field(min_length=1)
    resolution: str | None = None
    resolved: bool = False
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class WarningAcknowledgement(DomainModel):
    """Human acknowledgement of a warning that remains visible in the log."""

    warning_id: NonEmptyText
    warning: NonEmptyText
    acknowledged_by: NonEmptyText
    acknowledged_at: datetime


class RoleQuality(DomainModel):
    """Deterministic quality results and unresolved issues."""

    readiness_score: Annotated[int, Field(ge=0, le=100)] | None = None
    critical_missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    warning_acknowledgements: list[WarningAcknowledgement] = Field(
        default_factory=list
    )


class AuditMetadata(DomainModel):
    """Non-secret provenance metadata for an artefact."""

    schema_version: NonEmptyText = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str | None = None
    prompt_version: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class ClarificationQuestion(DomainModel):
    """A single high-value question proposed for the manager."""

    question_id: NonEmptyText
    question: NonEmptyText
    target_stage: WorkflowStage
    purpose: str | None = None


class DiscoveryAssumption(DomainModel):
    """An inference that remains explicitly subject to manager confirmation."""

    assumption_id: NonEmptyText
    statement: NonEmptyText
    source_statement: NonEmptyText
    requires_confirmation: bool


class UnresolvedAmbiguity(DomainModel):
    """A missing or vague detail preserved with its source wording."""

    ambiguity_id: NonEmptyText
    description: NonEmptyText
    source_statement: NonEmptyText
    why_confirmation_is_needed: NonEmptyText


class RoleSpecification(DomainModel):
    """Shared source of truth for role discovery and later artefacts."""

    role_id: NonEmptyText
    version: Annotated[int, Field(ge=1)] = 1
    parent_version: Annotated[int, Field(ge=1)] | None = None
    review_status: ReviewStatus = ReviewStatus.DRAFT
    basic_info: BasicRoleInfo = Field(default_factory=BasicRoleInfo)
    business_need: BusinessNeed = Field(default_factory=BusinessNeed)
    success_outcomes: list[SuccessOutcome] = Field(default_factory=list)
    responsibilities: list[Responsibility] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    open_assumptions: list[DiscoveryAssumption] = Field(default_factory=list)
    open_ambiguities: list[UnresolvedAmbiguity] = Field(default_factory=list)
    zuru_dna_behaviours: list[ZuruDnaBehaviour] = Field(default_factory=list)
    constraints: RoleConstraints = Field(default_factory=RoleConstraints)
    assessment_methods: list[str] = Field(default_factory=list)
    decision_owner: str | None = None
    quality: RoleQuality = Field(default_factory=RoleQuality)
    audit: AuditMetadata = Field(default_factory=AuditMetadata)
    human_approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
    approved_sections: list[ApprovalSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def parent_version_must_precede_version(self) -> RoleSpecification:
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("parent_version must be lower than version")
        return self


def _source_statements_overlap(first: str, second: str) -> bool:
    """Return whether two source statements refer to the same unresolved wording."""
    left = first.strip().lower()
    right = second.strip().lower()
    if not left or not right:
        return False
    return left == right or left in right or right in left


_HEDGE_PHRASES: tuple[str, ...] = (
    "unspecified",
    "not specified",
    "not yet specified",
    "unresolved",
    "unclear",
    "undefined",
    "not defined",
    "not yet defined",
    "unknown",
    "not yet known",
    "tbd",
    "to be determined",
    "not confirmed",
    "unconfirmed",
)


def _admits_unresolved_scope(*texts: str) -> bool:
    """Return whether any text admits, in its own words, that scope is still open.

    This is a self-contained backstop: it does not depend on a matching
    ``ambiguities`` entry existing at all, only on the requirement's own
    wording. A requirement whose own description hedges this way cannot
    honestly be ``must_have`` yet, regardless of what else the turn extracted.
    """
    combined = " ".join(text.lower() for text in texts if text)
    return any(phrase in combined for phrase in _HEDGE_PHRASES)


class DiscoverySemanticValidationError(ValueError):
    """Safe semantic failure containing locations and codes, never values."""

    def __init__(self, issues: list[tuple[str, str]]) -> None:
        super().__init__("Discovery extraction failed semantic validation.")
        self.issues = tuple(issues)


class ProviderDiscoveryModel(BaseModel):
    """Provider contract base kept deliberately structural and compact."""

    model_config = ConfigDict(extra="forbid", strict=True)
    inline_provider_schema: ClassVar[bool] = True


class DiscoveryRequirementExtraction(ProviderDiscoveryModel):
    """Compact source-backed requirement proposed by the model."""

    category: str = Field(
        json_schema_extra={"enum": [item.value for item in RequirementCategory]}
    )
    name: str
    description: str
    priority: str = Field(
        json_schema_extra={"enum": [item.value for item in RequirementPriority]}
    )
    rationale: str
    source_statement: str


class DiscoveryAssumptionExtraction(ProviderDiscoveryModel):
    """Compact inference that remains unconfirmed after mapping."""

    statement: str
    source_statement: str


class DiscoveryAmbiguityExtraction(ProviderDiscoveryModel):
    """Compact unresolved detail with source traceability."""

    description: str
    source_statement: str
    why_confirmation_is_needed: str


class DiscoveryContradictionExtraction(ProviderDiscoveryModel):
    """Compact possible conflict between source statements."""

    description: str
    source_statements: list[str]


class DiscoveryExtractionResponse(ProviderDiscoveryModel):
    """Minimal provider-facing contract for one discovery extraction."""

    incremental_requirements: list[DiscoveryRequirementExtraction]
    assumptions: list[DiscoveryAssumptionExtraction]
    ambiguities: list[DiscoveryAmbiguityExtraction]
    possible_contradictions: list[DiscoveryContradictionExtraction]
    next_question: str
    stage_recommendation: str = Field(
        json_schema_extra={
            "enum": [
                DiscoveryProgressRecommendation.STAY.value,
                DiscoveryProgressRecommendation.ADVANCE.value,
            ]
        }
    )

    def semantic_issues(self) -> list[tuple[str, str]]:
        """Return safe semantic locations and codes without generated values."""
        issues: list[tuple[str, str]] = []

        def require_text(location: str, value: str) -> None:
            if not value.strip():
                issues.append((location, "empty_string"))

        categories = {item.value for item in RequirementCategory}
        priorities = {item.value for item in RequirementPriority}
        recommendations = {item.value for item in DiscoveryProgressRecommendation}
        ambiguity_sources = [item.source_statement for item in self.ambiguities]
        for index, requirement in enumerate(self.incremental_requirements):
            prefix = f"incremental_requirements.{index}"
            for field_name in (
                "name",
                "description",
                "rationale",
                "source_statement",
            ):
                require_text(f"{prefix}.{field_name}", getattr(requirement, field_name))
            if requirement.category not in categories:
                issues.append((f"{prefix}.category", "unsupported_category"))
            if requirement.priority not in priorities:
                issues.append((f"{prefix}.priority", "unsupported_priority"))
            if requirement.priority == RequirementPriority.MUST_HAVE.value:
                if any(
                    _source_statements_overlap(
                        requirement.source_statement, ambiguity_source
                    )
                    for ambiguity_source in ambiguity_sources
                ):
                    issues.append(
                        (
                            f"{prefix}.priority",
                            "must_have_conflicts_with_unresolved_ambiguity",
                        )
                    )
                if _admits_unresolved_scope(requirement.name, requirement.description):
                    issues.append(
                        (
                            f"{prefix}.priority",
                            "must_have_admits_unresolved_scope",
                        )
                    )
        for index, assumption in enumerate(self.assumptions):
            prefix = f"assumptions.{index}"
            require_text(f"{prefix}.statement", assumption.statement)
            require_text(f"{prefix}.source_statement", assumption.source_statement)
        for index, ambiguity in enumerate(self.ambiguities):
            prefix = f"ambiguities.{index}"
            require_text(f"{prefix}.description", ambiguity.description)
            require_text(f"{prefix}.source_statement", ambiguity.source_statement)
            require_text(
                f"{prefix}.why_confirmation_is_needed",
                ambiguity.why_confirmation_is_needed,
            )
        for index, contradiction in enumerate(self.possible_contradictions):
            prefix = f"possible_contradictions.{index}"
            require_text(f"{prefix}.description", contradiction.description)
            if len(contradiction.source_statements) < 2:
                issues.append(
                    (f"{prefix}.source_statements", "insufficient_source_statements")
                )
            for source_index, statement in enumerate(
                contradiction.source_statements
            ):
                require_text(
                    f"{prefix}.source_statements.{source_index}", statement
                )
        require_text("next_question", self.next_question)
        if (
            self.next_question.count("?") != 1
            or "\n" in self.next_question
            or "\r" in self.next_question
        ):
            issues.append(("next_question", "invalid_single_question"))
        require_text("stage_recommendation", self.stage_recommendation)
        if self.stage_recommendation not in recommendations:
            issues.append(("stage_recommendation", "unsupported_stage"))
        return issues

    def validate_semantics(self) -> None:
        """Raise a value-free error when application constraints fail."""
        issues = self.semantic_issues()
        if issues:
            raise DiscoverySemanticValidationError(issues)

    def to_discovery_turn_result(
        self,
        *,
        current_stage: WorkflowStage,
        source_turn_id: str = "initial_manager_statement",
    ) -> DiscoveryTurnResult:
        """Add deterministic domain fields after provider validation."""
        self.validate_semantics()
        from src.workflow import progress_recommendation_stage

        target_stage = progress_recommendation_stage(
            current_stage,
            DiscoveryProgressRecommendation(self.stage_recommendation),
        )
        requirements = [
            Requirement(
                requirement_id=f"requirement_{index:03d}",
                category=RequirementCategory(item.category),
                name=item.name,
                description=item.description,
                priority=RequirementPriority(item.priority),
                proficiency=None,
                learnability=None,
                accepted_equivalents=[],
                business_rationale=item.rationale,
                evidence_methods=[],
                source_statement=item.source_statement,
                source_turn_id=source_turn_id,
                confidence=None,
                requires_confirmation=True,
                approved_by_human=False,
            )
            for index, item in enumerate(self.incremental_requirements, start=1)
        ]
        assumptions = [
            DiscoveryAssumption(
                assumption_id=f"assumption_{index:03d}",
                statement=item.statement,
                source_statement=item.source_statement,
                requires_confirmation=True,
            )
            for index, item in enumerate(self.assumptions, start=1)
        ]
        ambiguities = [
            UnresolvedAmbiguity(
                ambiguity_id=f"ambiguity_{index:03d}",
                description=item.description,
                source_statement=item.source_statement,
                why_confirmation_is_needed=item.why_confirmation_is_needed,
            )
            for index, item in enumerate(self.ambiguities, start=1)
        ]
        contradictions = [
            Contradiction(
                contradiction_id=f"contradiction_{index:03d}",
                description=item.description,
                severity=_classify_discovery_contradiction(
                    item.description, item.source_statements
                ),
                source_statements=list(item.source_statements),
                resolution=None,
                resolved=False,
            )
            for index, item in enumerate(self.possible_contradictions, start=1)
        ]
        return DiscoveryTurnResult(
            extracted_requirements=requirements,
            assumptions=assumptions,
            ambiguities=ambiguities,
            contradictions=contradictions,
            next_question=ClarificationQuestion(
                question_id="question_001",
                question=self.next_question,
                target_stage=target_stage,
                purpose=None,
            ),
            confidence=None,
        )


def _classify_discovery_contradiction(
    description: str, source_statements: list[str]
) -> str:
    """Classify a provider-suggested conflict using deterministic rules."""
    from src.readiness import classify_contradiction_severity

    provisional = Contradiction(
        contradiction_id="provisional",
        description=description,
        severity="low",
        source_statements=source_statements,
    )
    return classify_contradiction_severity(provisional)


class DiscoveryTurnResult(DomainModel):
    """Small structured result for one discovery extraction turn."""

    extracted_requirements: list[Requirement] = Field(default_factory=list)
    assumptions: list[DiscoveryAssumption] = Field(default_factory=list)
    ambiguities: list[UnresolvedAmbiguity] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    next_question: ClarificationQuestion
    confidence: Confidence | None = None


class JobDescriptionCriterion(DomainModel):
    """One JD criterion tied to an approved role requirement."""

    requirement_id: NonEmptyText
    text: NonEmptyText


class ZuruDnaSelection(DomainModel):
    """A reference-grounded DNA value expressed as observable role behaviour."""

    value: NonEmptyText
    role_behaviour: NonEmptyText


class JobDescription(DomainModel):
    """Structured job description that remains editable section by section."""

    title: NonEmptyText
    location: NonEmptyText
    purpose: NonEmptyText
    business_impact: NonEmptyText
    responsibilities: list[NonEmptyText] = Field(min_length=1)
    outcomes: list[NonEmptyText] = Field(min_length=1)
    must_have_criteria: list[JobDescriptionCriterion] = Field(min_length=1)
    preferred_criteria: list[JobDescriptionCriterion]
    zuru_dna_behaviours: list[ZuruDnaSelection] = Field(min_length=1)
    logistics: list[NonEmptyText] = Field(min_length=1)
    assessment_expectations: list[NonEmptyText] = Field(min_length=1)


class RubricAnchor(DomainModel):
    """Observable evidence associated with one score on the documented scale."""

    score: Annotated[int, Field(ge=0, le=5)]
    description: NonEmptyText

    @field_validator("description")
    @classmethod
    def description_must_be_evidence_based(cls, value: str) -> str:
        normalised = " ".join(value.casefold().split()).rstrip(".")
        if normalised.removeprefix("an ").removeprefix("a ") in {
            "bad answer",
            "poor answer",
            "weak answer",
            "average answer",
            "good answer",
            "strong answer",
            "excellent answer",
        }:
            raise ValueError(
                "rubric descriptions must state observable evidence"
            )
        return value


class ScreeningQuestion(DomainModel):
    """A traceable screening question with evidence-based assessment guidance."""

    question_id: NonEmptyText
    question: NonEmptyText
    requirement_ids: list[NonEmptyText] = Field(min_length=1)
    purpose: NonEmptyText
    expected_evidence: list[NonEmptyText] = Field(min_length=1)
    rubric: list[RubricAnchor] = Field(min_length=6, max_length=6)
    green_flags: list[NonEmptyText] = Field(min_length=1)
    red_flags: list[NonEmptyText] = Field(min_length=1)
    follow_up: NonEmptyText

    @model_validator(mode="after")
    def mappings_and_rubric_must_be_complete(self) -> ScreeningQuestion:
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("requirement_ids must be unique within a question")
        scores = [anchor.score for anchor in self.rubric]
        if len(scores) != len(set(scores)):
            raise ValueError("rubric scores must be unique")
        if scores != list(range(6)):
            raise ValueError("rubric anchors must be ordered scores 0 through 5")
        return self


def _question_ids_are_unique(questions: list[ScreeningQuestion]) -> bool:
    identifiers = [question.question_id for question in questions]
    return len(identifiers) == len(set(identifiers))


class ProviderGenerationModel(BaseModel):
    """Strict provider-facing base for hiring-pack structured output."""

    model_config = ConfigDict(extra="forbid", strict=True)
    inline_provider_schema: ClassVar[bool] = True


class HiringPackDraft(ProviderGenerationModel):
    """Provider output before application-owned provenance and validation."""

    job_description: JobDescription
    screening_questions: list[ScreeningQuestion] = Field(
        min_length=5, max_length=7
    )
    human_review_guidance: list[NonEmptyText] = Field(min_length=1)

    @model_validator(mode="after")
    def question_ids_must_be_unique(self) -> HiringPackDraft:
        if not _question_ids_are_unique(self.screening_questions):
            raise ValueError("screening question IDs must be unique")
        return self


class ReferenceFileProvenance(DomainModel):
    """Immutable identity and extraction details for one local reference file."""

    filename: NonEmptyText
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    byte_size: Annotated[int, Field(gt=0)]
    category: Annotated[str, Field(pattern="^(zuru_dna|example_jd)$")]
    extraction_method: NonEmptyText


class HiringPackProvenance(DomainModel):
    """Generation metadata without retaining full prompts or artefacts."""

    source_role_id: NonEmptyText
    source_role_version: Annotated[int, Field(ge=1)]
    generated_at: datetime
    generated_by: NonEmptyText
    model: str | None = None
    provider: str | None = None
    prompt_version: NonEmptyText
    reference_files: list[ReferenceFileProvenance] = Field(min_length=2)
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    total_tokens: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def references_must_cover_dna_and_jd(self) -> HiringPackProvenance:
        filenames = [item.filename for item in self.reference_files]
        if len(filenames) != len(set(filenames)):
            raise ValueError("reference filenames must be unique")
        categories = {item.category for item in self.reference_files}
        if categories != {"zuru_dna", "example_jd"}:
            raise ValueError("reference files must include ZURU DNA and an example JD")
        return self


class HiringPack(DomainModel):
    """Versioned hiring artefact generated from one approved role snapshot."""

    schema_version: str = "1.0"
    hiring_pack_id: NonEmptyText
    version: Annotated[int, Field(ge=1)] = 1
    parent_version: Annotated[int, Field(ge=1)] | None = None
    source_session_id: NonEmptyText | None = None
    provenance: HiringPackProvenance
    job_description: JobDescription
    screening_questions: list[ScreeningQuestion] = Field(
        min_length=5, max_length=7
    )
    human_review_guidance: list[NonEmptyText] = Field(min_length=1)
    human_edited: bool = False
    last_edited_by: NonEmptyText | None = None
    last_edited_at: datetime | None = None

    @model_validator(mode="after")
    def version_questions_and_edit_metadata_are_consistent(self) -> HiringPack:
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("parent_version must be lower than version")
        if not _question_ids_are_unique(self.screening_questions):
            raise ValueError("screening question IDs must be unique")
        has_edit_metadata = (
            self.last_edited_by is not None or self.last_edited_at is not None
        )
        if self.human_edited and (
            self.last_edited_by is None or self.last_edited_at is None
        ):
            raise ValueError("human-edited packs require editor and timestamp")
        if not self.human_edited and has_edit_metadata:
            raise ValueError("unedited packs cannot contain edit metadata")
        return self


class EvidenceItem(DomainModel):
    """Candidate evidence with explicit source provenance."""

    evidence_id: NonEmptyText
    requirement_id: NonEmptyText
    source_type: CandidateSourceType
    source_id: NonEmptyText
    quote: NonEmptyText
    location: str | None = None
    direct: bool = True
    relevance: Confidence | None = None


class RequirementAssessment(DomainModel):
    """Evidence-based assessment of one approved requirement."""

    requirement_id: NonEmptyText
    score: int
    scale_max: Annotated[int, Field(gt=0)] = 5
    confidence: Confidence
    evidence: list[EvidenceItem] = Field(default_factory=list)
    reasoning_summary: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    human_follow_up: str | None = None
    review_required: bool = True

    @model_validator(mode="after")
    def score_must_fit_scale(self) -> RequirementAssessment:
        """Ensure the criterion score falls within its declared scale."""
        if self.score < 0 or self.score > self.scale_max:
            raise ValueError("score must be between 0 and scale_max")
        return self


class CandidateEvaluation(DomainModel):
    """Human-review support that intentionally contains no hiring decision."""

    candidate_id: NonEmptyText
    role_id: NonEmptyText
    role_version: Annotated[int, Field(ge=1)] = 1
    assessments: list[RequirementAssessment] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    human_follow_ups: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    audit: AuditMetadata = Field(default_factory=AuditMetadata)
