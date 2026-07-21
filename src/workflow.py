"""Deterministic discovery workflow rules with no model calls."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.models import (
    Learnability,
    RequirementPriority,
    RoleSpecification,
    WorkflowStage,
)


DISCOVERY_STAGES: tuple[WorkflowStage, ...] = tuple(WorkflowStage)


class WorkflowState(BaseModel):
    """Current role state plus explicit confirmations for optional sections."""

    model_config = ConfigDict(extra="forbid")

    role_specification: RoleSpecification
    current_stage: WorkflowStage = WorkflowStage.BASIC_INFO
    confirmed_stages: set[WorkflowStage] = Field(default_factory=set)


def approval_blockers(role: RoleSpecification) -> list[str]:
    """Return critical gaps that prevent manager approval."""
    blockers: list[str] = []
    basic = role.basic_info
    if not basic.title:
        blockers.append("Role title is missing.")
    if basic.role_family is None:
        blockers.append("Role family is missing.")
    if basic.role_level is None:
        blockers.append("Role level is missing.")
    if basic.employment_type is None:
        blockers.append("Employment type is missing.")
    if not role.business_need.problem:
        blockers.append("Business need is missing.")
    if not role.success_outcomes:
        blockers.append("At least one success outcome is required.")
    if not role.responsibilities:
        blockers.append("At least one responsibility is required.")
    if not any(
        item.priority is RequirementPriority.MUST_HAVE
        for item in role.requirements
    ):
        blockers.append("At least one justified must-have requirement is required.")
    if not role.assessment_methods:
        blockers.append("At least one assessment method is required.")
    if not role.decision_owner:
        blockers.append("A human decision owner is required.")
    if any(
        not item.resolved and item.severity in {"high", "critical"}
        for item in role.quality.contradictions
    ):
        blockers.append("High or critical contradictions must be resolved.")
    blockers.extend(
        f"Critical field remains missing: {field_name}"
        for field_name in role.quality.critical_missing_fields
    )
    return blockers


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

