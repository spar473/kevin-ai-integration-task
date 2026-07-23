"""Domain schema validation and round-trip tests."""

import json

import pytest
from pydantic import ValidationError

from src.models import (
    BasicRoleInfo,
    BusinessNeed,
    CandidateEvaluation,
    CandidateSourceType,
    EmploymentType,
    EvidenceItem,
    Learnability,
    Requirement,
    RequirementAssessment,
    RequirementCategory,
    RequirementPriority,
    RoleFamily,
    RoleLevel,
    RoleQuality,
    RoleSpecification,
)


def valid_requirement() -> Requirement:
    """Return a compact valid must-have used by multiple tests."""
    return Requirement(
        requirement_id="req_001",
        category=RequirementCategory.DOMAIN,
        name="Social content awareness",
        priority=RequirementPriority.MUST_HAVE,
        learnability=Learnability.DAY_ONE,
        business_rationale="The intern will support channel-specific campaign work.",
        source_statement="They should be good with social media.",
        confidence=0.7,
    )


def valid_role() -> RoleSpecification:
    """Return a minimal valid role specification."""
    return RoleSpecification(
        role_id="role_marketing_intern",
        basic_info=BasicRoleInfo(
            title="Marketing Intern",
            role_family=RoleFamily.MARKETING,
            role_level=RoleLevel.INTERN,
            employment_type=EmploymentType.INTERNSHIP,
        ),
        business_need=BusinessNeed(problem="Support summer campaign delivery."),
        requirements=[valid_requirement()],
    )


def test_role_parent_version_must_precede_current_version() -> None:
    with pytest.raises(ValueError, match="parent_version"):
        RoleSpecification(
            role_id="role_invalid_version",
            version=2,
            parent_version=2,
        )


def test_valid_role_specification_and_human_approval_default() -> None:
    role = valid_role()

    assert role.role_id == "role_marketing_intern"
    assert role.human_approved is False
    assert role.requirements[0].approved_by_human is False


def test_invalid_confidence_fails() -> None:
    with pytest.raises(ValidationError):
        Requirement.model_validate(
            {
                **valid_requirement().model_dump(),
                "confidence": 1.2,
            }
        )


def test_must_have_without_rationale_fails() -> None:
    with pytest.raises(ValidationError, match="business_rationale"):
        Requirement(
            requirement_id="req_002",
            category=RequirementCategory.TECHNICAL,
            name="Design capability",
            priority=RequirementPriority.MUST_HAVE,
            source_statement="Maybe some design skills?",
        )


def test_missing_source_statement_fails() -> None:
    with pytest.raises(ValidationError):
        Requirement(
            requirement_id="req_003",
            category=RequirementCategory.DOMAIN,
            name="Campaign support",
            priority=RequirementPriority.PREFERRED,
            source_statement="   ",
        )


def test_candidate_score_out_of_bounds_fails() -> None:
    with pytest.raises(ValidationError, match="scale_max"):
        RequirementAssessment(
            requirement_id="req_001",
            score=6,
            scale_max=5,
            confidence=0.5,
        )


def test_evidence_preserves_source_and_missing_is_separate() -> None:
    evidence = EvidenceItem(
        evidence_id="evidence_001",
        requirement_id="req_001",
        source_type=CandidateSourceType.SCREENING_RESPONSE,
        source_id="response_001",
        quote="I planned three social posts for a student campaign.",
    )
    assessment = RequirementAssessment(
        requirement_id="req_001",
        score=3,
        confidence=0.7,
        evidence=[evidence],
        missing_evidence=["No outcome metric was supplied."],
        contradictory_evidence=[],
    )

    assert assessment.evidence[0].source_id == "response_001"
    assert assessment.missing_evidence
    assert assessment.contradictory_evidence == []


def test_candidate_evaluation_rejects_final_decision_field() -> None:
    with pytest.raises(ValidationError, match="final_decision"):
        CandidateEvaluation.model_validate(
            {
                "candidate_id": "candidate_001",
                "role_id": "role_001",
                "final_decision": "hire",
            }
        )


def test_role_json_round_trip() -> None:
    role = valid_role()
    payload = role.model_dump_json()
    restored = RoleSpecification.model_validate(json.loads(payload))

    assert restored == role


def test_role_quality_drops_retired_fields_from_legacy_session_payloads() -> None:
    payload = valid_role().model_dump(mode="python")
    payload["quality"].update(
        readiness_score=40,
        critical_missing_fields=["decision_owner"],
        ambiguities=["Location is unclear."],
    )
    role = RoleSpecification.model_validate(payload)

    assert role.quality.model_dump() == {
        "readiness_score": 40,
        "contradictions": [],
        "warnings": [],
        "warning_acknowledgements": [],
    }


def test_role_quality_still_rejects_unknown_non_legacy_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RoleQuality.model_validate({"unexpected_field": []})
