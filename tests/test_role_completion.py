"""Proves the 2026-07-24 documented gap is closed: a role built from nothing
but a discovery-style requirement plus the Phase 1 manual edit functions can
reach human approval without any hand-authored fixture or session edit.

See docs/DECISIONS_AND_LESSONS.md, 2026-07-24 entries "Discovery cannot
populate half of RoleSpecification..." and its follow-up closing this gap.
"""

from __future__ import annotations

from src.discovery import (
    add_responsibility,
    add_success_outcome,
    add_zuru_dna_behaviour,
    edit_assessment_plan,
    edit_business_need,
    edit_constraints,
)
from src.models import (
    ApprovalSection,
    BasicRoleInfo,
    EmploymentType,
    Requirement,
    RequirementCategory,
    RequirementPriority,
    RoleFamily,
    RoleLevel,
    RoleSpecification,
)
from src.readiness import approval_blockers, approve_role, evaluate_role_quality


def _discovery_produced_role() -> RoleSpecification:
    """A role shaped like what live discovery alone can produce today: basic
    info plus one manager-sourced must-have requirement, nothing else."""
    return RoleSpecification(
        role_id="role_live_completion_test",
        basic_info=BasicRoleInfo(
            title="Marketing Intern",
            role_family=RoleFamily.MARKETING,
            role_level=RoleLevel.INTERN,
            employment_type=EmploymentType.INTERNSHIP,
            initial_manager_statement="We need a Marketing Intern for summer.",
        ),
        requirements=[
            Requirement(
                requirement_id="requirement_001",
                category=RequirementCategory.DOMAIN,
                name="Weekly TikTok content scripting",
                description="Script and film short TikTok videos with the team.",
                priority=RequirementPriority.MUST_HAVE,
                business_rationale="The manager explicitly assigned this task.",
                source_statement=(
                    "They'll script and film short TikTok videos with the team."
                ),
            )
        ],
    )


def test_manual_edit_functions_alone_can_clear_every_approval_blocker() -> None:
    role = _discovery_produced_role()
    assert approval_blockers(role), (
        "sanity check: a discovery-only role must still have blockers"
    )

    role = edit_business_need(
        role,
        problem="The team needs summer campaign delivery capacity.",
        why_now="The summer campaign calendar has increased.",
        cost_of_vacancy=None,
        is_replacement=False,
    )
    role = add_success_outcome(
        role,
        description="Deliver three approved TikTok posts each week.",
        time_horizon="First 30 days",
        measure="Three approved posts per week",
        priority=RequirementPriority.MUST_HAVE,
    )
    role = add_success_outcome(
        role,
        description="Support one full campaign cycle end to end.",
        time_horizon="First 90 days",
        measure="One completed campaign cycle",
        priority=RequirementPriority.PREFERRED,
    )
    role = add_success_outcome(
        role,
        description="Coordinate assets with the design team on request.",
        time_horizon="First 90 days",
        measure="Zero missed asset handoffs",
        priority=RequirementPriority.PREFERRED,
    )
    role = add_responsibility(
        role,
        description="Draft and schedule weekly campaign content with the team.",
        frequency="Weekly",
        ownership_level="Shared",
        priority=RequirementPriority.MUST_HAVE,
    )
    role = edit_constraints(
        role,
        country="New Zealand",
        location="Auckland",
        work_arrangement="On-site",
        work_rights="Must hold valid New Zealand work rights.",
        weekly_hours="40 hours",
        travel=None,
    )
    role = edit_assessment_plan(
        role,
        assessment_methods=["Structured screening questions"],
        decision_owner="Talent Acquisition Partner",
    )
    role = add_zuru_dna_behaviour(
        role,
        value="Collaboration",
        role_behaviour="Shares draft work early and incorporates feedback.",
        scenario="A campaign draft receives feedback.",
        evidence_method="Behavioural screening response",
    )

    assert approval_blockers(role) == []

    report = evaluate_role_quality(role)
    assert report.blockers == ()

    approved = approve_role(
        role,
        approver="Fixture Hiring Manager",
        confirmed_sections=[
            ApprovalSection.BUSINESS_PURPOSE,
            ApprovalSection.OUTCOMES,
            ApprovalSection.MUST_HAVES,
            ApprovalSection.BEHAVIOURAL_CRITERIA,
            ApprovalSection.KEY_CONSTRAINTS,
        ],
        acknowledged_warning_ids=[item.warning_id for item in report.warnings],
    )

    assert approved.human_approved is True
    assert approved.review_status.value == "approved"
