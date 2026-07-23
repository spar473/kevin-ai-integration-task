"""Deterministic discovery state-machine tests: no model calls involved.

Covers the Phase 3 acceptance criteria: normal progression, blocked progression
on missing critical fields, and blocked manager approval. Tests for applying a
manager's correction onto a ``RoleSpecification`` live in ``test_discovery.py``,
since that update-application logic belongs to ``src/discovery.py``, not the
state machine itself.
"""

from __future__ import annotations

import pytest

from src.models import (
    BasicRoleInfo,
    BusinessNeed,
    Contradiction,
    DiscoveryProgressRecommendation,
    EmploymentType,
    Learnability,
    Requirement,
    RequirementCategory,
    RequirementPriority,
    Responsibility,
    RoleFamily,
    RoleLevel,
    RoleQuality,
    RoleSpecification,
    SuccessOutcome,
    WorkflowStage,
    ZuruDnaBehaviour,
)
from src.workflow import (
    DISCOVERY_STAGES,
    DiscoveryTransitionUnavailable,
    WorkflowState,
    advance_workflow,
    approval_blockers,
    next_incomplete_stage,
    progress_recommendation_stage,
    resolve_next_stage,
    stage_is_complete,
)


def empty_role() -> RoleSpecification:
    """Return the minimum valid role: a role_id and nothing else known yet."""
    return RoleSpecification(role_id="role_001")


def role_with_basic_info() -> RoleSpecification:
    role = empty_role()
    return role.model_copy(
        update={
            "basic_info": BasicRoleInfo(
                title="Marketing Intern",
                role_family=RoleFamily.MARKETING,
                role_level=RoleLevel.INTERN,
                employment_type=EmploymentType.INTERNSHIP,
            )
        }
    )


def fully_populated_role(*, human_approved: bool = False) -> RoleSpecification:
    """Return a role complete enough to clear every deterministic gap."""
    role = role_with_basic_info()
    return role.model_copy(
        update={
            "business_need": BusinessNeed(problem="Support summer campaign delivery."),
            "success_outcomes": [
                SuccessOutcome(
                    outcome_id="outcome_001",
                    description="Ship four TikTok campaign posts.",
                )
            ],
            "responsibilities": [
                Responsibility(
                    responsibility_id="responsibility_001",
                    description="Support weekly campaign planning.",
                )
            ],
            "requirements": [
                Requirement(
                    requirement_id="requirement_001",
                    category=RequirementCategory.DOMAIN,
                    name="Social content awareness",
                    priority=RequirementPriority.MUST_HAVE,
                    learnability=Learnability.DAY_ONE,
                    business_rationale="The intern posts directly to TikTok.",
                    source_statement="They'll work with the team on TikTok stuff.",
                ),
                Requirement(
                    requirement_id="requirement_002",
                    category=RequirementCategory.TECHNICAL,
                    name="Campaign reporting tools",
                    priority=RequirementPriority.PREFERRED,
                    learnability=Learnability.WITHIN_30_DAYS,
                    source_statement="Help with campaigns.",
                ),
            ],
            "zuru_dna_behaviours": [
                ZuruDnaBehaviour(
                    value="Collaboration",
                    role_behaviour="Shares TikTok drafts early for team feedback.",
                )
            ],
            "constraints": role.constraints.model_copy(
                update={"work_arrangement": "Hybrid, three days in office."}
            ),
            "assessment_methods": ["Screening response"],
            "decision_owner": "Hiring Manager",
            "human_approved": human_approved,
        }
    )


# ---------------------------------------------------------------------------
# Normal progression
# ---------------------------------------------------------------------------


def test_workflow_state_begins_at_basic_info() -> None:
    state = WorkflowState(role_specification=empty_role())

    assert state.current_stage is WorkflowStage.BASIC_INFO


def test_next_incomplete_stage_is_basic_info_for_an_empty_role() -> None:
    state = WorkflowState(role_specification=empty_role())

    assert next_incomplete_stage(state) is WorkflowStage.BASIC_INFO


def test_next_incomplete_stage_advances_once_basic_info_is_known() -> None:
    state = WorkflowState(role_specification=role_with_basic_info())

    assert next_incomplete_stage(state) is WorkflowStage.BUSINESS_NEED


def test_advance_workflow_reaches_manager_approval_for_a_complete_role() -> None:
    """A role with zero approval blockers clears QUALITY_REVIEW automatically,
    since that stage's own completeness rule is "no blockers remain"."""
    state = WorkflowState(role_specification=fully_populated_role())

    advanced = advance_workflow(state)

    assert advanced.current_stage is WorkflowStage.MANAGER_APPROVAL
    assert stage_is_complete(state, WorkflowStage.QUALITY_REVIEW) is True


def test_stage_order_matches_documented_sequence() -> None:
    assert DISCOVERY_STAGES == tuple(WorkflowStage)
    assert DISCOVERY_STAGES[0] is WorkflowStage.BASIC_INFO
    assert DISCOVERY_STAGES[-1] is WorkflowStage.COMPLETE


# ---------------------------------------------------------------------------
# Blocked progression on missing critical fields
# ---------------------------------------------------------------------------


def test_basic_info_incomplete_without_role_level() -> None:
    role = empty_role().model_copy(
        update={
            "basic_info": BasicRoleInfo(
                title="Marketing Intern",
                role_family=RoleFamily.MARKETING,
                employment_type=EmploymentType.INTERNSHIP,
            )
        }
    )
    state = WorkflowState(role_specification=role)

    assert stage_is_complete(state, WorkflowStage.BASIC_INFO) is False
    assert next_incomplete_stage(state) is WorkflowStage.BASIC_INFO


def test_day_one_requirements_incomplete_without_a_must_have() -> None:
    role = role_with_basic_info().model_copy(
        update={
            "business_need": BusinessNeed(problem="Support summer campaign delivery."),
            "success_outcomes": [
                SuccessOutcome(outcome_id="outcome_001", description="Ship posts.")
            ],
            "responsibilities": [
                Responsibility(
                    responsibility_id="responsibility_001",
                    description="Support campaign planning.",
                )
            ],
        }
    )
    state = WorkflowState(role_specification=role)

    assert stage_is_complete(state, WorkflowStage.DAY_ONE_REQUIREMENTS) is False


def test_approval_blockers_lists_every_gap_for_an_empty_role() -> None:
    blockers = approval_blockers(empty_role())

    assert "Role title is missing." in blockers
    assert "Business need is missing." in blockers
    assert "At least one success outcome is required." in blockers
    assert "At least one responsibility is required." in blockers
    assert "At least one justified must-have requirement is required." in blockers
    assert "At least one assessment method is required." in blockers
    assert "A human decision owner is required." in blockers


def test_approval_blockers_empty_for_a_fully_populated_role() -> None:
    assert approval_blockers(fully_populated_role()) == []


def test_unresolved_high_severity_contradiction_blocks_approval() -> None:
    role = fully_populated_role().model_copy(
        update={
            "quality": RoleQuality(
                contradictions=[
                    Contradiction(
                        contradiction_id="contradiction_001",
                        description="Entry-level title but senior ownership expected.",
                        severity="high",
                        source_statements=[
                            "It's an intern role.",
                            "They own the roadmap end to end.",
                        ],
                        resolved=False,
                    )
                ]
            )
        }
    )

    blockers = approval_blockers(role)

    assert "High or critical contradictions must be resolved." in blockers


def test_resolved_contradiction_does_not_block_approval() -> None:
    role = fully_populated_role().model_copy(
        update={
            "quality": RoleQuality(
                contradictions=[
                    Contradiction(
                        contradiction_id="contradiction_001",
                        description="Entry-level title but senior ownership expected.",
                        severity="high",
                        source_statements=["a", "b"],
                        resolved=True,
                    )
                ]
            )
        }
    )

    assert approval_blockers(role) == []


def test_quality_review_incomplete_while_blockers_remain() -> None:
    state = WorkflowState(role_specification=empty_role())

    assert stage_is_complete(state, WorkflowStage.QUALITY_REVIEW) is False


# ---------------------------------------------------------------------------
# Blocked manager approval
# ---------------------------------------------------------------------------


def test_manager_approval_stage_requires_explicit_human_approval() -> None:
    state = WorkflowState(role_specification=fully_populated_role(human_approved=False))

    assert stage_is_complete(state, WorkflowStage.MANAGER_APPROVAL) is False


def test_manager_approval_stage_completes_once_approved() -> None:
    state = WorkflowState(role_specification=fully_populated_role(human_approved=True))

    assert stage_is_complete(state, WorkflowStage.MANAGER_APPROVAL) is True
    assert next_incomplete_stage(state) is WorkflowStage.COMPLETE


def test_complete_stage_blocked_without_approval_even_if_everything_else_is_ready() -> None:
    state = WorkflowState(
        role_specification=fully_populated_role(human_approved=False),
        current_stage=WorkflowStage.COMPLETE,
    )

    assert stage_is_complete(state, WorkflowStage.COMPLETE) is False


# ---------------------------------------------------------------------------
# progress_recommendation_stage: mechanical stay/advance mapping
# ---------------------------------------------------------------------------


def test_progress_recommendation_stay_returns_current_stage() -> None:
    result = progress_recommendation_stage(
        WorkflowStage.BASIC_INFO, DiscoveryProgressRecommendation.STAY
    )

    assert result is WorkflowStage.BASIC_INFO


def test_progress_recommendation_advance_returns_next_stage() -> None:
    result = progress_recommendation_stage(
        WorkflowStage.BASIC_INFO, DiscoveryProgressRecommendation.ADVANCE
    )

    assert result is WorkflowStage.BUSINESS_NEED


def test_progress_recommendation_advance_past_complete_raises() -> None:
    with pytest.raises(DiscoveryTransitionUnavailable):
        progress_recommendation_stage(
            WorkflowStage.COMPLETE, DiscoveryProgressRecommendation.ADVANCE
        )


# ---------------------------------------------------------------------------
# resolve_next_stage: the deterministic gate on the model's advice
# ---------------------------------------------------------------------------


def test_resolve_next_stage_blocks_advance_when_current_stage_is_incomplete() -> None:
    """The model may recommend "advance" before the stage's own fields exist;
    the state machine must refuse to move regardless of that recommendation."""
    state = WorkflowState(role_specification=empty_role())
    recommended = progress_recommendation_stage(
        state.current_stage, DiscoveryProgressRecommendation.ADVANCE
    )

    resolved = resolve_next_stage(state, recommended)

    assert resolved is WorkflowStage.BASIC_INFO


def test_resolve_next_stage_allows_advance_once_current_stage_is_complete() -> None:
    state = WorkflowState(role_specification=role_with_basic_info())
    recommended = progress_recommendation_stage(
        state.current_stage, DiscoveryProgressRecommendation.ADVANCE
    )

    resolved = resolve_next_stage(state, recommended)

    assert resolved is WorkflowStage.BUSINESS_NEED


def test_resolve_next_stage_honours_stay_even_when_stage_is_complete() -> None:
    state = WorkflowState(role_specification=role_with_basic_info())
    recommended = progress_recommendation_stage(
        state.current_stage, DiscoveryProgressRecommendation.STAY
    )

    resolved = resolve_next_stage(state, recommended)

    assert resolved is WorkflowStage.BASIC_INFO
