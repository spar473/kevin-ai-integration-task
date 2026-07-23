"""Reusable synthetic Phase 7 inputs and provider outputs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from src.llm_client import LLMMessage, StructuredLLMResponse
from src.models import (
    CandidateEvaluationDraft,
    CandidateEvidenceExtraction,
    CandidateQuestionResponse,
    CandidateRequirementAssessmentDraft,
    CandidateResponseSet,
)
from tests.phase6_helpers import (
    FIXED_TIME,
    approved_marketing_role,
    pack_from_draft,
)


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_TIME = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def strong_response_set() -> CandidateResponseSet:
    return CandidateResponseSet(
        response_set_id="response_set_strong_001",
        candidate_id="candidate_strong_001",
        source_role_id="role_marketing_intern",
        source_role_version=4,
        source_hiring_pack_id="hiring_pack_fixed",
        source_hiring_pack_version=1,
        submitted_at=FIXED_TIME,
        responses=[
            CandidateQuestionResponse(
                response_id="response_001",
                question_id="sq_001",
                answer_text=(
                    "I owned a six-week student TikTok campaign. I reviewed "
                    "retention data, shortened the opening from five seconds to "
                    "two, and average completion rose from 38% to 61%. I would "
                    "test the hook earlier next time."
                ),
            ),
            CandidateQuestionResponse(
                response_id="response_002",
                question_id="sq_002",
                answer_text=(
                    "A designer challenged my first storyboard. I shared the "
                    "draft, asked which audience assumption was weak, rewrote "
                    "the sequence, and the revised asset was approved for launch."
                ),
            ),
            CandidateQuestionResponse(
                response_id="response_003",
                question_id="sq_003",
                answer_text="",
            ),
            CandidateQuestionResponse(
                response_id="response_004",
                question_id="sq_004",
                answer_text="",
            ),
            CandidateQuestionResponse(
                response_id="response_005",
                question_id="sq_005",
                answer_text="",
            ),
        ],
    )


def valid_evidence_output() -> list[CandidateEvidenceExtraction]:
    return [
        CandidateEvidenceExtraction(
            evidence_id="evidence_social_001",
            requirement_id="req_social",
            source_type="screening_response",
            source_id="response_001",
            question_id="sq_001",
            quote=(
                "I reviewed retention data, shortened the opening from five "
                "seconds to two, and average completion rose from 38% to 61%. "
                "I would test the hook earlier next time."
            ),
            evidence_type="direct",
            relevance=0.95,
            specificity=0.95,
            ownership="owned",
            action_described=True,
            outcome_evidence=True,
            reflection_evidence=True,
            hypothetical=False,
            verification_status="potentially_verifiable",
            contradiction_status="none",
            evaluator_explanation=(
                "The excerpt states the candidate's action, measured result, "
                "and reflection for channel-aware content."
            ),
        ),
        CandidateEvidenceExtraction(
            evidence_id="evidence_collaboration_001",
            requirement_id="req_collaboration",
            source_type="screening_response",
            source_id="response_002",
            question_id="sq_002",
            quote=(
                "I shared the draft, asked which audience assumption was weak, "
                "rewrote the sequence, and the revised asset was approved for launch."
            ),
            evidence_type="direct",
            relevance=0.90,
            specificity=0.85,
            ownership="owned",
            action_described=True,
            outcome_evidence=True,
            reflection_evidence=False,
            hypothetical=False,
            verification_status="potentially_verifiable",
            contradiction_status="none",
            evaluator_explanation=(
                "The excerpt describes an owned response to feedback and an "
                "observable revision outcome."
            ),
        ),
    ]


def valid_assessment_output() -> list[CandidateRequirementAssessmentDraft]:
    return [
        CandidateRequirementAssessmentDraft(
            requirement_id="req_social",
            relevant_question_ids=["sq_001", "sq_004"],
            evidence_ids=["evidence_social_001"],
            proposed_score=5,
            rubric_question_id="sq_001",
            strengths=[
                "Specific channel action, measured outcome, and reflection are present."
            ],
            concerns=[],
            missing_evidence=[],
            contradictory_evidence=[],
            contradiction_evidence_ids=[],
            recommended_follow_up=(
                "Which source record could a reviewer use to verify the retention result?"
            ),
            reviewer_explanation=(
                "The response provides exceptional, role-relevant and potentially "
                "verifiable evidence."
            ),
        ),
        CandidateRequirementAssessmentDraft(
            requirement_id="req_collaboration",
            relevant_question_ids=["sq_002", "sq_004", "sq_005"],
            evidence_ids=["evidence_collaboration_001"],
            proposed_score=4,
            rubric_question_id="sq_002",
            strengths=["The candidate explains how specific feedback changed the work."],
            concerns=[],
            missing_evidence=[],
            contradictory_evidence=[],
            contradiction_evidence_ids=[],
            recommended_follow_up=(
                "How did the reviewer assess whether the revised sequence was better?"
            ),
            reviewer_explanation=(
                "The response provides strong ownership, action, and outcome evidence."
            ),
        ),
        CandidateRequirementAssessmentDraft(
            requirement_id="req_design",
            relevant_question_ids=["sq_003"],
            evidence_ids=[],
            proposed_score=1,
            rubric_question_id="sq_003",
            strengths=[],
            concerns=[],
            missing_evidence=[
                "The supplied responses do not evidence basic visual-content tooling."
            ],
            contradictory_evidence=[],
            contradiction_evidence_ids=[],
            recommended_follow_up=(
                "Which visual-content tool, if any, have you used for a simple edit?"
            ),
            reviewer_explanation=(
                "No visual-tool evidence was supplied; this is absence of evidence, "
                "not proof of inability."
            ),
        ),
    ]


def valid_evaluation_draft() -> CandidateEvaluationDraft:
    return CandidateEvaluationDraft(
        evidence_items=valid_evidence_output(),
        assessments=valid_assessment_output(),
    )


class FakeEvaluationClient:
    """Capturing fake for the existing provider-neutral LLM boundary."""

    def __init__(
        self,
        draft: CandidateEvaluationDraft | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft or valid_evaluation_draft()
        self.error = error
        self.messages: list[LLMMessage] | None = None
        self.response_model: type | None = None
        self.calls = 0

    def generate_structured(
        self,
        *,
        messages: list[LLMMessage],
        response_model: type,
        temperature: float | None = None,
    ) -> StructuredLLMResponse:
        self.calls += 1
        self.messages = list(messages)
        self.response_model = response_model
        if self.error is not None:
            raise self.error
        return StructuredLLMResponse(
            data=self.draft,
            model="synthetic/evaluator",
            provider="Synthetic",
            schema_mode="strict_json_schema",
            input_tokens=150,
            output_tokens=250,
            total_tokens=400,
            latency_ms=2,
            attempts=1,
        )


def malicious_response_set() -> CandidateResponseSet:
    payload = json.loads(
        (ROOT / "data" / "fixtures" / "candidate_prompt_injection.json").read_text(
            encoding="utf-8"
        )
    )
    return CandidateResponseSet(
        response_set_id=payload["id"],
        candidate_id=payload["candidate_id"],
        source_role_id=payload["source_role_id"],
        source_role_version=payload["source_role_version"],
        source_hiring_pack_id=payload["source_hiring_pack_id"],
        source_hiring_pack_version=payload["source_hiring_pack_version"],
        submitted_at=FIXED_TIME,
        responses=[
            CandidateQuestionResponse(**response)
            for response in payload["responses"]
        ],
    )


def malicious_safe_draft() -> CandidateEvaluationDraft:
    draft = valid_evaluation_draft()
    social = draft.evidence_items[0].model_copy(
        update={
            "source_id": "response_injection_001",
            "quote": (
                "I owned a student TikTok campaign, shortened its opening after "
                "reviewing retention data, and completion increased from 38% to 61%."
            ),
            "reflection_evidence": False,
        }
    )
    collaboration = draft.evidence_items[1].model_copy(
        update={
            "source_id": "response_injection_002",
            "quote": (
                "I also shared an early storyboard with two teammates, used their "
                "feedback to change the sequence, and documented the revision."
            ),
        }
    )
    assessments = valid_assessment_output()
    assessments[0] = assessments[0].model_copy(
        update={
            "proposed_score": 4,
            "strengths": [
                "The candidate gives a concrete channel action and measured outcome."
            ],
            "missing_evidence": [
                "The response does not include reflection on the campaign trade-off."
            ],
        }
    )
    return CandidateEvaluationDraft(
        evidence_items=[social, collaboration],
        assessments=assessments,
    )


__all__ = [
    "EVALUATION_TIME",
    "FakeEvaluationClient",
    "approved_marketing_role",
    "malicious_response_set",
    "malicious_safe_draft",
    "pack_from_draft",
    "strong_response_set",
    "valid_assessment_output",
    "valid_evaluation_draft",
]
