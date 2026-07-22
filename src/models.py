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


class RoleQuality(DomainModel):
    """Deterministic quality results and unresolved issues."""

    readiness_score: Annotated[int, Field(ge=0, le=100)] | None = None
    critical_missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AuditMetadata(DomainModel):
    """Non-secret provenance metadata for an artefact."""

    schema_version: str = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str | None = None
    prompt_version: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class RoleSpecification(DomainModel):
    """Shared source of truth for role discovery and later artefacts."""

    role_id: NonEmptyText
    version: Annotated[int, Field(ge=1)] = 1
    review_status: ReviewStatus = ReviewStatus.DRAFT
    basic_info: BasicRoleInfo = Field(default_factory=BasicRoleInfo)
    business_need: BusinessNeed = Field(default_factory=BusinessNeed)
    success_outcomes: list[SuccessOutcome] = Field(default_factory=list)
    responsibilities: list[Responsibility] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    zuru_dna_behaviours: list[ZuruDnaBehaviour] = Field(default_factory=list)
    constraints: RoleConstraints = Field(default_factory=RoleConstraints)
    assessment_methods: list[str] = Field(default_factory=list)
    decision_owner: str | None = None
    quality: RoleQuality = Field(default_factory=RoleQuality)
    audit: AuditMetadata = Field(default_factory=AuditMetadata)
    human_approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None


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
                severity="low",
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


class DiscoveryTurnResult(DomainModel):
    """Small structured result for one discovery extraction turn."""

    extracted_requirements: list[Requirement] = Field(default_factory=list)
    assumptions: list[DiscoveryAssumption] = Field(default_factory=list)
    ambiguities: list[UnresolvedAmbiguity] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    next_question: ClarificationQuestion
    confidence: Confidence | None = None


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
