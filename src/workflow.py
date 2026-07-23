"""Deterministic discovery workflow rules with no model calls."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.models import (
    ClarificationQuestion,
    DiscoveryProgressRecommendation,
    Learnability,
    RequirementPriority,
    RoleSpecification,
    WorkflowStage,
)
from src.readiness import approval_blockers


DISCOVERY_STAGES: tuple[WorkflowStage, ...] = tuple(WorkflowStage)


class DiscoveryTransitionUnavailable(ValueError):
    """Raised when a progress recommendation has no deterministic transition."""

    def __init__(self) -> None:
        super().__init__("Discovery progress transition is unavailable.")


def progress_recommendation_stage(
    current_stage: WorkflowStage,
    recommendation: DiscoveryProgressRecommendation,
) -> WorkflowStage:
    """Resolve stay or advance using the application-owned stage order."""
    if recommendation is DiscoveryProgressRecommendation.STAY:
        return current_stage
    current_index = DISCOVERY_STAGES.index(current_stage)
    if current_index + 1 >= len(DISCOVERY_STAGES):
        raise DiscoveryTransitionUnavailable()
    return DISCOVERY_STAGES[current_index + 1]


class WorkflowState(BaseModel):
    """Current role state plus explicit confirmations for optional sections."""

    model_config = ConfigDict(extra="forbid")

    role_specification: RoleSpecification
    current_stage: WorkflowStage = WorkflowStage.BASIC_INFO
    confirmed_stages: set[WorkflowStage] = Field(default_factory=set)
    current_question: ClarificationQuestion | None = None


def stage_is_complete(state: WorkflowState, stage: WorkflowStage) -> bool:
    """Evaluate the minimum deterministic completion rule for a stage."""
    role = state.role_specification
    basic = role.basic_info

    if stage is WorkflowStage.BASIC_INFO:
        return all(
            (
                basic.title,
                basic.role_family,
                basic.role_level,
                basic.employment_type,
            )
        )
    if stage is WorkflowStage.BUSINESS_NEED:
        return bool(role.business_need.problem)
    if stage is WorkflowStage.SUCCESS_OUTCOMES:
        return bool(role.success_outcomes)
    if stage is WorkflowStage.RESPONSIBILITIES:
        return bool(role.responsibilities)
    if stage is WorkflowStage.DAY_ONE_REQUIREMENTS:
        return any(
            requirement.priority is RequirementPriority.MUST_HAVE
            and requirement.learnability in {None, Learnability.DAY_ONE}
            for requirement in role.requirements
        )
    if stage is WorkflowStage.LEARNABLE_REQUIREMENTS:
        return stage in state.confirmed_stages or any(
            requirement.learnability not in {None, Learnability.DAY_ONE}
            for requirement in role.requirements
        )
    if stage is WorkflowStage.BEHAVIOURAL_REQUIREMENTS:
        return stage in state.confirmed_stages or bool(role.zuru_dna_behaviours)
    if stage is WorkflowStage.CONSTRAINTS:
        constraints = role.constraints
        return stage in state.confirmed_stages or any(
            (
                constraints.country,
                constraints.location,
                constraints.work_arrangement,
                constraints.work_rights,
                constraints.weekly_hours,
            )
        )
    if stage is WorkflowStage.ASSESSMENT_PLAN:
        return bool(role.assessment_methods and role.decision_owner)
    if stage is WorkflowStage.QUALITY_REVIEW:
        return not approval_blockers(role)
    if stage is WorkflowStage.MANAGER_APPROVAL:
        return not approval_blockers(role) and role.human_approved
    if stage is WorkflowStage.COMPLETE:
        return not approval_blockers(role) and role.human_approved
    return False


def next_incomplete_stage(state: WorkflowState) -> WorkflowStage:
    """Return the first incomplete stage in the fixed discovery order."""
    for stage in DISCOVERY_STAGES:
        if not stage_is_complete(state, stage):
            return stage
    return WorkflowStage.COMPLETE


def advance_workflow(state: WorkflowState) -> WorkflowState:
    """Return a copied state positioned at its next incomplete stage."""
    return state.model_copy(update={"current_stage": next_incomplete_stage(state)})


def resolve_next_stage(
    state: WorkflowState, recommended_stage: WorkflowStage
) -> WorkflowStage:
    """Gate a model-recommended stage behind deterministic stage completeness.

    ``recommended_stage`` is the mechanical stay/advance mapping already produced
    by :func:`progress_recommendation_stage`. The model's "advance" is only
    non-binding routing advice: the application blocks it whenever the current
    stage's own minimum information is still incomplete, so a role can never
    progress past a stage with missing critical fields no matter what the model
    recommends.
    """
    if recommended_stage == state.current_stage:
        return state.current_stage
    if not stage_is_complete(state, state.current_stage):
        return state.current_stage
    return recommended_stage
