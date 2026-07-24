"""Phase 5 deterministic role-quality and approval tests.

No test in this module calls a model.  The cases mirror the readiness
dimensions and quality rules documented in the Phase 5 specification.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.models import (
    ApprovalSection,
    BasicRoleInfo,
    BusinessNeed,
    Contradiction,
    DiscoveryExtractionResponse,
    EmploymentType,
    Learnability,
    ProficiencyLevel,
    Requirement,
    RequirementCategory,
    RequirementPriority,
    Responsibility,
    RoleConstraints,
    RoleFamily,
    RoleLevel,
    RoleQuality,
    RoleSpecification,
    SuccessOutcome,
    WorkflowStage,
    ZuruDnaBehaviour,
)
from src.readiness import (
    READINESS_WEIGHTS,
    REQUIRED_APPROVAL_SECTIONS,
    ApprovalBlockedError,
    ContradictionNotFoundError,
    approve_role,
    approval_blockers,
    calculate_readiness,
    classify_contradiction_severity,
    detect_excessive_requirements,
    detect_vague_phrases,
    evaluate_role_quality,
    refresh_role_quality,
    resolve_contradiction,
)


def requirement(
    requirement_id: str,
    name: str,
    *,
    category: RequirementCategory = RequirementCategory.DOMAIN,
    priority: RequirementPriority = RequirementPriority.MUST_HAVE,
    proficiency: ProficiencyLevel = ProficiencyLevel.INDEPENDENT,
    learnability: Learnability = Learnability.DAY_ONE,
    business_rationale: str | None = "Needed for the role's core weekly work.",
    evidence_methods: list[str] | None = None,
    accepted_equivalents: list[str] | None = None,
    source_statement: str | None = None,
) -> Requirement:
    return Requirement(
        requirement_id=requirement_id,
        category=category,
        name=name,
        priority=priority,
        proficiency=proficiency,
        learnability=learnability,
        business_rationale=business_rationale,
        evidence_methods=evidence_methods or ["Structured screening question"],
        accepted_equivalents=accepted_equivalents or ["Equivalent demonstrated experience"],
        source_statement=source_statement or f"We need {name}.",
    )


def complete_role() -> RoleSpecification:
    """Return a role that earns every documented readiness dimension."""
    return RoleSpecification(
        role_id="role_ready",
        basic_info=BasicRoleInfo(
            title="Growth Marketing Specialist",
            role_family=RoleFamily.MARKETING,
            role_level=RoleLevel.INTERMEDIATE,
            employment_type=EmploymentType.PERMANENT,
            location="Auckland",
            work_arrangement="Hybrid",
            initial_manager_statement="Own measurable growth campaign delivery.",
        ),
        business_need=BusinessNeed(
            problem="Campaign experiments are not being delivered consistently.",
            why_now="The next product launch begins this quarter.",
        ),
        success_outcomes=[
            SuccessOutcome(
                outcome_id=f"outcome_{index:03d}",
                description=description,
                time_horizon="First 90 days",
                measure=measure,
                source_statement=description,
            )
            for index, (description, measure) in enumerate(
                [
                    ("Launch the agreed campaign tests.", "Four tests launched."),
                    ("Improve reporting cadence.", "Weekly dashboard published."),
                    ("Document experiment learning.", "Four retrospectives shared."),
                ],
                start=1,
            )
        ],
        responsibilities=[
            Responsibility(
                responsibility_id="responsibility_001",
                description="Plan weekly campaign experiments.",
                frequency="Weekly",
                ownership_level="Own",
                priority=RequirementPriority.MUST_HAVE,
            ),
            Responsibility(
                responsibility_id="responsibility_002",
                description="Share experiment learning with the team.",
                frequency="Fortnightly",
                ownership_level="Contribute",
                priority=RequirementPriority.PREFERRED,
            ),
        ],
        requirements=[
            requirement(
                "requirement_001",
                "Campaign experiment design",
                priority=RequirementPriority.MUST_HAVE,
            ),
            requirement(
                "requirement_002",
                "Growth reporting",
                priority=RequirementPriority.PREFERRED,
                learnability=Learnability.WITHIN_30_DAYS,
                business_rationale="Useful for improving the reporting cadence.",
            ),
        ],
        zuru_dna_behaviours=[
            ZuruDnaBehaviour(
                value="Own It",
                role_behaviour="Explains a missed experiment and agrees the next action.",
                scenario="A launch test misses its delivery date.",
                evidence_method="Behavioural interview example",
                source_statement="They need to own delivery issues.",
            )
        ],
        constraints=RoleConstraints(
            country="New Zealand",
            location="Auckland",
            work_arrangement="Hybrid",
        ),
        assessment_methods=["Structured screening response"],
        decision_owner="Hiring Manager",
    )


def earned_points(role: RoleSpecification, key: str) -> int:
    dimensions = {item.key: item for item in calculate_readiness(role).dimensions}
    return dimensions[key].earned_points


# ---------------------------------------------------------------------------
# The nine documented readiness dimensions and fixed weights
# ---------------------------------------------------------------------------


def test_readiness_weights_match_the_documented_100_point_table() -> None:
    assert READINESS_WEIGHTS == {
        "business_purpose": 10,
        "measurable_outcomes": 20,
        "prioritised_responsibilities": 10,
        "must_have_vs_preferred": 15,
        "proficiency_and_equivalents": 10,
        "evidence_and_assessment": 15,
        "observable_behaviours": 10,
        "logistics_and_constraints": 5,
        "contradictions_resolved": 5,
    }
    assert sum(READINESS_WEIGHTS.values()) == 100
    assert calculate_readiness(complete_role()).score == 100


def test_business_purpose_requires_problem_and_why_now() -> None:
    role = complete_role().model_copy(
        update={"business_need": BusinessNeed(problem="Campaign delivery is slow.")}
    )

    assert earned_points(role, "business_purpose") == 0


def test_outcomes_require_three_to_five_measurable_results() -> None:
    role = complete_role()
    incomplete_outcomes = [
        outcome.model_copy(update={"measure": None}) for outcome in role.success_outcomes
    ]
    role = role.model_copy(update={"success_outcomes": incomplete_outcomes})

    assert earned_points(role, "measurable_outcomes") == 0


def test_responsibilities_require_explicit_priorities() -> None:
    role = complete_role()
    responsibilities = [
        role.responsibilities[0].model_copy(update={"priority": None}),
        role.responsibilities[1],
    ]
    role = role.model_copy(update={"responsibilities": responsibilities})

    assert earned_points(role, "prioritised_responsibilities") == 0


def test_requirements_must_distinguish_must_have_from_preferred() -> None:
    role = complete_role()
    requirements = [
        item.model_copy(update={"priority": RequirementPriority.MUST_HAVE})
        for item in role.requirements
    ]
    role = role.model_copy(update={"requirements": requirements})

    assert earned_points(role, "must_have_vs_preferred") == 0


def test_requirements_need_proficiency_and_accepted_equivalents() -> None:
    role = complete_role()
    requirements = [
        role.requirements[0].model_copy(update={"accepted_equivalents": []}),
        role.requirements[1],
    ]
    role = role.model_copy(update={"requirements": requirements})

    assert earned_points(role, "proficiency_and_equivalents") == 0


def test_must_haves_need_evidence_methods_and_role_needs_assessment_plan() -> None:
    role = complete_role()
    requirements = [
        role.requirements[0].model_copy(update={"evidence_methods": []}),
        role.requirements[1],
    ]
    role = role.model_copy(update={"requirements": requirements})

    assert earned_points(role, "evidence_and_assessment") == 0


def test_behaviours_need_a_scenario_and_evidence_method() -> None:
    role = complete_role()
    behaviours = [
        role.zuru_dna_behaviours[0].model_copy(update={"scenario": None})
    ]
    role = role.model_copy(update={"zuru_dna_behaviours": behaviours})

    assert earned_points(role, "observable_behaviours") == 0


def test_logistics_require_location_and_work_arrangement() -> None:
    role = complete_role().model_copy(
        update={
            "basic_info": complete_role().basic_info.model_copy(
                update={"location": None, "work_arrangement": None}
            ),
            "constraints": RoleConstraints(country="New Zealand"),
        }
    )

    assert earned_points(role, "logistics_and_constraints") == 0


def test_unresolved_contradiction_loses_contradiction_points() -> None:
    role = complete_role().model_copy(
        update={
            "quality": RoleQuality(
                contradictions=[
                    Contradiction(
                        contradiction_id="contradiction_001",
                        description="The role is remote but requires five office days.",
                        severity="low",
                        source_statements=[
                            "This is a remote role.",
                            "They must be in the office five days a week.",
                        ],
                    )
                ]
            )
        }
    )

    assert earned_points(role, "contradictions_resolved") == 0


@pytest.mark.parametrize(
    ("score", "interpretation"),
    [
        (0, "not ready"),
        (40, "significant gaps"),
        (70, "usable with minor review"),
        (85, "strongly defined"),
    ],
)
def test_readiness_interpretation_uses_documented_bands(
    score: int, interpretation: str
) -> None:
    from src.readiness import readiness_interpretation

    assert readiness_interpretation(score) == interpretation


# ---------------------------------------------------------------------------
# Vague language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "good with people",
        "superstar",
        "culture fit",
        "strategic",
        "fast-paced",
        "creative",
        "commercial",
        "self-starter",
        "fun to work with",
    ],
)
def test_each_documented_vague_phrase_is_flagged(phrase: str) -> None:
    role = complete_role()
    role = role.model_copy(
        update={
            "basic_info": role.basic_info.model_copy(
                update={"initial_manager_statement": f"We need someone {phrase}."}
            )
        }
    )

    flags = detect_vague_phrases(role)

    match = next(item for item in flags if item.phrase == phrase)
    assert match.category
    assert match.why_untestable
    assert match.clarification
    assert match.status == "needs_clarification"


def test_vague_phrase_matching_uses_phrase_boundaries() -> None:
    role = complete_role()
    role = role.model_copy(
        update={
            "basic_info": role.basic_info.model_copy(
                update={"initial_manager_statement": "The plan is commercially sound."}
            )
        }
    )

    assert all(item.phrase != "commercial" for item in detect_vague_phrases(role))


# ---------------------------------------------------------------------------
# Excessive requirement rules
# ---------------------------------------------------------------------------


def test_more_than_five_day_one_must_haves_is_flagged() -> None:
    role = complete_role().model_copy(
        update={
            "requirements": [
                requirement(f"requirement_{index:03d}", f"Capability {index}")
                for index in range(1, 7)
            ]
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "too_many_day_one_must_haves" in rules


def test_unrelated_specialist_capability_clusters_are_flagged() -> None:
    role = complete_role().model_copy(
        update={
            "requirements": [
                requirement(
                    "requirement_001",
                    "Python API development",
                    category=RequirementCategory.TECHNICAL,
                ),
                requirement(
                    "requirement_002",
                    "Photoshop brand illustration",
                    category=RequirementCategory.TECHNICAL,
                ),
            ]
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "unrelated_capability_clusters" in rules


def test_data_pipeline_wording_does_not_false_positive_into_sales_cluster() -> None:
    """A bare `pipeline` pattern previously matched "data pipeline" and
    "CI/CD pipeline" wording as the sales cluster, flagging an unrelated
    software/sales role split on a role with no sales content at all.
    Recorded live 2026-07-24: see docs/DECISIONS_AND_LESSONS.md."""
    role = complete_role().model_copy(
        update={
            "requirements": [
                requirement(
                    "requirement_001",
                    "Expert-level Python for production data-pipeline delivery",
                    category=RequirementCategory.TECHNICAL,
                )
            ]
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "unrelated_capability_clusters" not in rules


def test_advanced_day_one_capability_for_intern_is_seniority_mismatch() -> None:
    role = complete_role().model_copy(
        update={
            "basic_info": complete_role().basic_info.model_copy(
                update={"role_level": RoleLevel.INTERN}
            ),
            "requirements": [
                requirement(
                    "requirement_001",
                    "Enterprise architecture ownership",
                    category=RequirementCategory.TECHNICAL,
                    proficiency=ProficiencyLevel.EXPERT,
                )
            ],
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "seniority_mismatch" in rules


def test_named_tool_without_business_rationale_is_flagged() -> None:
    role = complete_role().model_copy(
        update={
            "requirements": [
                requirement(
                    "requirement_001",
                    "Python",
                    category=RequirementCategory.TECHNICAL,
                    priority=RequirementPriority.PREFERRED,
                    business_rationale=None,
                )
            ]
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "tool_without_rationale" in rules


def test_conflicting_proficiency_for_same_capability_is_flagged() -> None:
    role = complete_role().model_copy(
        update={
            "requirements": [
                requirement(
                    "requirement_001",
                    "Campaign analytics",
                    proficiency=ProficiencyLevel.WORKING,
                ),
                requirement(
                    "requirement_002",
                    "Campaign analytics",
                    proficiency=ProficiencyLevel.EXPERT,
                    priority=RequirementPriority.PREFERRED,
                ),
            ]
        }
    )

    rules = {item.rule for item in detect_excessive_requirements(role)}

    assert "conflicting_proficiency" in rules


# ---------------------------------------------------------------------------
# Contradiction classification and conversion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "sources", "severity"),
    [
        (
            "The role is remote but requires five office days.",
            ["This is remote.", "Office attendance is required five days a week."],
            "critical",
        ),
        (
            "Entry-level title but senior ownership is required.",
            ["This is entry level.", "They own enterprise architecture."],
            "high",
        ),
        (
            "No experience required but Python is mandatory on day one.",
            ["No experience required.", "Python is mandatory."],
            "high",
        ),
        (
            "High autonomy conflicts with approval for every task.",
            ["They have high autonomy.", "Every task requires approval."],
            "high",
        ),
        (
            "The capability is both mandatory and optional.",
            ["Manager A says mandatory.", "Manager B says optional."],
            "high",
        ),
        (
            "The role is strategic but almost all work is execution.",
            ["Set strategy.", "Ninety percent hands-on execution."],
            "medium",
        ),
        (
            "Two descriptions may overlap.",
            ["Support campaigns.", "Contribute to launches."],
            "low",
        ),
    ],
)
def test_contradiction_severity_is_rule_based(
    description: str, sources: list[str], severity: str
) -> None:
    contradiction = Contradiction(
        contradiction_id="contradiction_001",
        description=description,
        severity="low",
        source_statements=sources,
    )

    assert classify_contradiction_severity(contradiction) == severity


def test_discovery_conversion_uses_real_contradiction_severity() -> None:
    extraction = DiscoveryExtractionResponse.model_validate(
        {
            "incremental_requirements": [],
            "assumptions": [],
            "ambiguities": [],
            "possible_contradictions": [
                {
                    "description": "Entry-level title but senior ownership is required.",
                    "source_statements": [
                        "This is entry level.",
                        "They own enterprise architecture.",
                    ],
                }
            ],
            "next_question": "Which scope should change?",
            "stage_recommendation": "stay",
        }
    )

    result = extraction.to_discovery_turn_result(
        current_stage=WorkflowStage.QUALITY_REVIEW
    )

    assert result.contradictions[0].severity == "high"


# ---------------------------------------------------------------------------
# Blockers, warning acknowledgement, and explicit approval
# ---------------------------------------------------------------------------


def test_refresh_quality_persists_score_and_classified_contradictions() -> None:
    role = complete_role().model_copy(
        update={
            "quality": RoleQuality(
                contradictions=[
                    Contradiction(
                        contradiction_id="contradiction_001",
                        description="Entry-level title but senior ownership is required.",
                        severity="low",
                        source_statements=["Entry level.", "Own enterprise architecture."],
                    )
                ]
            )
        }
    )

    refreshed = refresh_role_quality(role)

    assert refreshed.quality.readiness_score == 95
    assert refreshed.quality.contradictions[0].severity == "high"


def test_critical_contradiction_blocks_approval() -> None:
    role = complete_role().model_copy(
        update={
            "quality": RoleQuality(
                contradictions=[
                    Contradiction(
                        contradiction_id="contradiction_001",
                        description="The role is remote but requires five office days.",
                        severity="low",
                        source_statements=[
                            "This is remote.",
                            "Office attendance is required five days a week.",
                        ],
                    )
                ]
            )
        }
    )

    assert "High or critical contradictions must be resolved." in approval_blockers(
        role
    )
    with pytest.raises(ApprovalBlockedError):
        approve_role(
            role,
            approver="Hiring Manager",
            confirmed_sections=REQUIRED_APPROVAL_SECTIONS,
        )


def test_all_five_approval_sections_must_be_explicitly_confirmed() -> None:
    sections = set(REQUIRED_APPROVAL_SECTIONS)
    sections.remove(ApprovalSection.KEY_CONSTRAINTS)

    with pytest.raises(ApprovalBlockedError, match="approval sections"):
        approve_role(
            complete_role(),
            approver="Hiring Manager",
            confirmed_sections=sections,
        )


def test_warning_must_be_acknowledged_and_remains_logged_after_approval() -> None:
    role = complete_role()
    role = role.model_copy(
        update={
            "basic_info": role.basic_info.model_copy(
                update={
                    "initial_manager_statement": "We need a fast-paced campaign owner."
                }
            )
        }
    )
    report = evaluate_role_quality(role)
    warning_ids = {item.warning_id for item in report.warnings}
    assert warning_ids

    with pytest.raises(ApprovalBlockedError, match="warnings"):
        approve_role(
            role,
            approver="Hiring Manager",
            confirmed_sections=REQUIRED_APPROVAL_SECTIONS,
        )

    approved = approve_role(
        role,
        approver="Hiring Manager",
        confirmed_sections=REQUIRED_APPROVAL_SECTIONS,
        acknowledged_warning_ids=warning_ids,
    )

    assert approved.human_approved is True
    assert approved.quality.warnings
    assert {
        item.warning_id for item in approved.quality.warning_acknowledgements
    } == warning_ids


def seniority_mismatch_role() -> RoleSpecification:
    """A role whose current state deterministically triggers a high-severity
    seniority-mismatch contradiction (see
    ``test_advanced_day_one_capability_for_intern_is_seniority_mismatch``)."""
    return complete_role().model_copy(
        update={
            "basic_info": complete_role().basic_info.model_copy(
                update={"role_level": RoleLevel.INTERN}
            ),
            "requirements": [
                requirement(
                    "requirement_001",
                    "Enterprise architecture ownership",
                    category=RequirementCategory.TECHNICAL,
                    proficiency=ProficiencyLevel.EXPERT,
                )
            ],
        }
    )


def test_auto_detected_contradiction_is_not_persisted_and_clears_when_fixed() -> None:
    role = seniority_mismatch_role()

    refreshed = refresh_role_quality(role)

    # The transient detection still blocks approval right now...
    assert refreshed.quality.contradictions == []
    assert "High or critical contradictions must be resolved." in approval_blockers(
        refreshed
    )

    # ...and re-refreshing an untouched role never accumulates ghost entries.
    refreshed_again = refresh_role_quality(refreshed)
    assert refreshed_again.quality.contradictions == []

    # Once the manager fixes the underlying data, the blocker clears on its own.
    fixed = refreshed.model_copy(
        update={
            "requirements": [
                requirement(
                    "requirement_001",
                    "Content calendar coordination",
                    category=RequirementCategory.TECHNICAL,
                    proficiency=ProficiencyLevel.INDEPENDENT,
                )
            ]
        }
    )
    assert "High or critical contradictions must be resolved." not in approval_blockers(
        fixed
    )


def test_resolve_contradiction_persists_and_survives_refresh() -> None:
    role = seniority_mismatch_role()

    resolved = resolve_contradiction(
        role,
        "contradiction_seniority_scope",
        resolved_by="Hiring Manager",
        resolution="Accepted: this specific expert skill is intentional here.",
    )

    assert [item.contradiction_id for item in resolved.quality.contradictions] == [
        "contradiction_seniority_scope"
    ]
    stored = resolved.quality.contradictions[0]
    assert stored.resolved is True
    assert stored.resolved_by == "Hiring Manager"
    assert stored.resolution
    assert stored.resolved_at is not None
    assert resolved.parent_version == role.version
    assert resolved.version == role.version + 1
    assert "High or critical contradictions must be resolved." not in approval_blockers(
        resolved
    )

    # A later refresh keeps the resolution instead of re-detecting a fresh one.
    refreshed_again = refresh_role_quality(resolved)
    assert [item.contradiction_id for item in refreshed_again.quality.contradictions] == [
        "contradiction_seniority_scope"
    ]
    assert refreshed_again.quality.contradictions[0].resolved is True


def test_resolving_unknown_contradiction_raises() -> None:
    with pytest.raises(ContradictionNotFoundError):
        resolve_contradiction(
            complete_role(),
            "does_not_exist",
            resolved_by="Hiring Manager",
            resolution="No such issue.",
        )


def test_approval_captures_approver_time_and_all_sections() -> None:
    approved_at = datetime(2026, 7, 23, 9, 30, tzinfo=UTC)

    approved = approve_role(
        complete_role(),
        approver="Aroha Manager",
        confirmed_sections=REQUIRED_APPROVAL_SECTIONS,
        approved_at=approved_at,
    )

    assert approved.human_approved is True
    assert approved.approved_by == "Aroha Manager"
    assert approved.approved_at == approved_at
    assert approved.approved_sections == list(REQUIRED_APPROVAL_SECTIONS)
    assert approved.parent_version == 1
    assert approved.version == 2
    assert approved.review_status.value == "approved"
