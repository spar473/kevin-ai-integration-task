"""Deterministic role quality, readiness, contradiction, and approval rules.

The model may suggest that wording is ambiguous or contradictory, but every
score, severity, blocker, warning acknowledgement, and approval transition in
this module is application-owned and repeatable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import re
from typing import Iterable

from src.models import (
    ApprovalSection,
    Contradiction,
    Learnability,
    ProficiencyLevel,
    Requirement,
    RequirementCategory,
    RequirementPriority,
    ReviewStatus,
    RoleLevel,
    RoleSpecification,
    WarningAcknowledgement,
)


# Exact weights from docs/03_TECHNICAL_DESIGN_AND_METHODS.md section 10.
READINESS_WEIGHTS: dict[str, int] = {
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

REQUIRED_APPROVAL_SECTIONS: tuple[ApprovalSection, ...] = (
    ApprovalSection.BUSINESS_PURPOSE,
    ApprovalSection.OUTCOMES,
    ApprovalSection.MUST_HAVES,
    ApprovalSection.BEHAVIOURAL_CRITERIA,
    ApprovalSection.KEY_CONSTRAINTS,
)

# The task brief requires explicit confirmation of the top five when a manager
# provides a long day-one list.
MAX_DAY_ONE_MUST_HAVES = 5


@dataclass(frozen=True)
class ReadinessDimensionResult:
    """One all-or-nothing documented dimension of the readiness heuristic."""

    key: str
    label: str
    weight: int
    earned_points: int
    explanation: str

    @property
    def complete(self) -> bool:
        return self.earned_points == self.weight


@dataclass(frozen=True)
class ReadinessResult:
    """Deterministic score plus dimension-level explanation."""

    score: int
    interpretation: str
    dimensions: tuple[ReadinessDimensionResult, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class VaguePhraseFlag:
    """Untestable wording found in a manager-sourced role field."""

    flag_id: str
    phrase: str
    category: str
    why_untestable: str
    clarification: str
    status: str
    source: str


@dataclass(frozen=True)
class ExcessiveRequirementIssue:
    """One deterministic overload or calibration warning."""

    issue_id: str
    rule: str
    message: str
    clarification: str
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class QualityWarning:
    """A non-critical issue that a human may explicitly acknowledge."""

    warning_id: str
    rule: str
    message: str


@dataclass(frozen=True)
class RoleQualityReport:
    """Complete deterministic Phase 5 result for a role specification."""

    readiness: ReadinessResult
    vague_phrases: tuple[VaguePhraseFlag, ...]
    excessive_requirements: tuple[ExcessiveRequirementIssue, ...]
    contradictions: tuple[Contradiction, ...]
    warnings: tuple[QualityWarning, ...]
    blockers: tuple[str, ...]


class ApprovalBlockedError(ValueError):
    """Raised when explicit, deterministic approval conditions are unmet."""

    def __init__(self, reasons: Iterable[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("Role approval blocked: " + "; ".join(self.reasons))


class ContradictionNotFoundError(ValueError):
    """Raised when a resolution targets a contradiction that is not currently active."""

    def __init__(self, contradiction_id: str) -> None:
        super().__init__(f"Contradiction was not found: {contradiction_id}")


_DIMENSION_LABELS = {
    "business_purpose": "Business purpose and why now",
    "measurable_outcomes": "Measurable outcomes",
    "prioritised_responsibilities": "Prioritised responsibilities",
    "must_have_vs_preferred": "Must-have versus preferred",
    "proficiency_and_equivalents": "Proficiency and equivalents",
    "evidence_and_assessment": "Evidence and assessment",
    "observable_behaviours": "Observable behaviours",
    "logistics_and_constraints": "Logistics and constraints",
    "contradictions_resolved": "Contradictions resolved",
}

_DIMENSION_GAPS = {
    "business_purpose": "Define both the business problem and why the role is needed now.",
    "measurable_outcomes": "Define three to five outcomes, each with a measurable result.",
    "prioritised_responsibilities": "Assign an explicit priority to every responsibility.",
    "must_have_vs_preferred": "Separate genuine must-haves from preferred or optional requirements.",
    "proficiency_and_equivalents": "Define proficiency and accepted equivalents for every requirement.",
    "evidence_and_assessment": "Give each must-have an evidence method and confirm the assessment plan.",
    "observable_behaviours": "Translate behaviour into a scenario and evidence method.",
    "logistics_and_constraints": "Confirm both role location and work arrangement.",
    "contradictions_resolved": "Resolve every recorded or deterministically detected contradiction.",
}


def readiness_interpretation(score: int) -> str:
    """Return the documented interpretation band for a score from 0 to 100."""
    if score < 0 or score > 100:
        raise ValueError("readiness score must be between 0 and 100")
    if score < 40:
        return "not ready"
    if score < 70:
        return "significant gaps"
    if score < 85:
        return "usable with minor review"
    return "strongly defined"


def _dimension_completion(
    role: RoleSpecification, contradictions: tuple[Contradiction, ...]
) -> dict[str, bool]:
    must_haves = [
        item
        for item in role.requirements
        if item.priority is RequirementPriority.MUST_HAVE
    ]
    non_must_haves = [
        item
        for item in role.requirements
        if item.priority is not RequirementPriority.MUST_HAVE
    ]
    location = role.constraints.location or role.basic_info.location
    work_arrangement = (
        role.constraints.work_arrangement or role.basic_info.work_arrangement
    )

    return {
        "business_purpose": bool(
            role.business_need.problem and role.business_need.why_now
        ),
        "measurable_outcomes": (
            3 <= len(role.success_outcomes) <= 5
            and all(item.measure for item in role.success_outcomes)
        ),
        "prioritised_responsibilities": bool(
            role.responsibilities
            and all(item.priority is not None for item in role.responsibilities)
        ),
        "must_have_vs_preferred": bool(must_haves and non_must_haves),
        "proficiency_and_equivalents": bool(
            role.requirements
            and all(
                item.proficiency is not None and item.accepted_equivalents
                for item in role.requirements
            )
        ),
        "evidence_and_assessment": bool(
            role.assessment_methods
            and must_haves
            and all(item.evidence_methods for item in must_haves)
        ),
        "observable_behaviours": bool(
            role.zuru_dna_behaviours
            and all(
                item.scenario and item.evidence_method
                for item in role.zuru_dna_behaviours
            )
        ),
        "logistics_and_constraints": bool(location and work_arrangement),
        "contradictions_resolved": all(item.resolved for item in contradictions),
    }


def calculate_readiness(role: RoleSpecification) -> ReadinessResult:
    """Calculate the fixed 100-point role-readiness heuristic."""
    contradictions = normalise_contradictions(role)
    completion = _dimension_completion(role, contradictions)
    dimensions = tuple(
        ReadinessDimensionResult(
            key=key,
            label=_DIMENSION_LABELS[key],
            weight=weight,
            earned_points=weight if completion[key] else 0,
            explanation=(
                "Complete."
                if completion[key]
                else _DIMENSION_GAPS[key]
            ),
        )
        for key, weight in READINESS_WEIGHTS.items()
    )
    score = sum(item.earned_points for item in dimensions)
    return ReadinessResult(
        score=score,
        interpretation=readiness_interpretation(score),
        dimensions=dimensions,
        gaps=tuple(item.explanation for item in dimensions if not item.complete),
    )


_VAGUE_PHRASES: dict[str, tuple[str, str, str]] = {
    "good with people": (
        "subjective_behaviour",
        "It does not name the stakeholder situation or observable action.",
        "Which people situation matters, and what would an effective response look like?",
    ),
    "superstar": (
        "inflated_label",
        "It is a label without a capability, outcome, or evidence standard.",
        "Which two or three outcomes would distinguish strong performance?",
    ),
    "culture fit": (
        "subjective_fit",
        "It can hide personal similarity or bias instead of observable behaviour.",
        "Which job-relevant behaviour should be demonstrated in a real scenario?",
    ),
    "strategic": (
        "scope_ambiguity",
        "It does not state the decisions, time horizon, or ownership involved.",
        "Which decisions or plans will this person own, and over what horizon?",
    ),
    "fast-paced": (
        "environment_label",
        "It does not define workload, deadlines, or the required response.",
        "What changes quickly, and what observable behaviour handles that well?",
    ),
    "creative": (
        "subjective_capability",
        "It does not identify the creative task, audience, or quality evidence.",
        "What must they create, for whom, and how will quality be assessed?",
    ),
    "commercial": (
        "scope_ambiguity",
        "It does not identify the commercial decision or business measure.",
        "Which commercial decision or metric should this capability improve?",
    ),
    "self-starter": (
        "subjective_behaviour",
        "It does not define autonomy boundaries or expected initiative.",
        "What should they initiate without prompting, and when should they escalate?",
    ),
    "fun to work with": (
        "subjective_fit",
        "It is personal preference rather than a job-relevant behaviour.",
        "Which collaboration behaviour matters in a difficult work situation?",
    ),
}


def _stable_id(prefix: str, *parts: str) -> str:
    content = "\x1f".join(part.strip().lower() for part in parts)
    return f"{prefix}_{sha256(content.encode('utf-8')).hexdigest()[:12]}"


def _role_text_sources(role: RoleSpecification) -> list[tuple[str, str]]:
    """Return manager-sourced and role-defining text with stable labels."""
    sources: list[tuple[str, str | None]] = [
        ("initial manager statement", role.basic_info.initial_manager_statement),
        ("business problem", role.business_need.problem),
        ("why now", role.business_need.why_now),
        ("cost of vacancy", role.business_need.cost_of_vacancy),
    ]
    sources.extend(
        (f"outcome {item.outcome_id}", text)
        for item in role.success_outcomes
        for text in (item.description, item.source_statement)
    )
    sources.extend(
        (f"responsibility {item.responsibility_id}", text)
        for item in role.responsibilities
        for text in (item.description, item.source_statement)
    )
    sources.extend(
        (f"requirement {item.requirement_id}", text)
        for item in role.requirements
        for text in (item.name, item.description, item.source_statement)
    )
    sources.extend(
        (f"behaviour {index}", text)
        for index, item in enumerate(role.zuru_dna_behaviours, start=1)
        for text in (item.role_behaviour, item.scenario, item.source_statement)
    )
    return [(label, text) for label, text in sources if text]


def detect_vague_phrases(role: RoleSpecification) -> tuple[VaguePhraseFlag, ...]:
    """Find the documented untestable phrases using boundary-safe matching."""
    flags: list[VaguePhraseFlag] = []
    seen: set[tuple[str, str]] = set()
    for source, text in _role_text_sources(role):
        for phrase, (category, why, clarification) in _VAGUE_PHRASES.items():
            pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
            if not re.search(pattern, text, flags=re.IGNORECASE):
                continue
            key = (phrase, source)
            if key in seen:
                continue
            seen.add(key)
            flags.append(
                VaguePhraseFlag(
                    flag_id=_stable_id("vague", phrase, source),
                    phrase=phrase,
                    category=category,
                    why_untestable=why,
                    clarification=clarification,
                    status="needs_clarification",
                    source=source,
                )
            )
    return tuple(flags)


_CAPABILITY_CLUSTERS: dict[str, tuple[str, ...]] = {
    "software_engineering": (
        r"\bpython\b",
        r"\bapi\b",
        r"\bbackend\b",
        r"\bfrontend\b",
        r"\bsoftware\b",
        r"\bcloud\b",
        r"\baws\b",
        r"\bazure\b",
        r"\bgcp\b",
    ),
    "data_and_ai": (
        r"\bmachine learning\b",
        r"\bartificial intelligence\b",
        r"\bdata science\b",
        r"\bsql\b",
        r"\bdata engineering\b",
    ),
    "creative_production": (
        r"\bphotoshop\b",
        r"\billustrator\b",
        r"\bgraphic design\b",
        r"\billustration\b",
        r"\bvideo production\b",
        r"\b3d\b",
    ),
    "marketing": (
        r"\bcampaign\b",
        r"\bseo\b",
        r"\bsocial media\b",
        r"\bgrowth marketing\b",
        r"\bbrand strategy\b",
    ),
    "finance": (
        r"\bfinancial model",
        r"\baccounting\b",
        r"\bforecasting\b",
        r"\btreasury\b",
    ),
    "sales": (
        r"\bsales\b",
        r"\bcrm\b",
        r"\baccount management\b",
        r"\bsales pipeline\b",
        r"\bdeal pipeline\b",
    ),
}

_TOOL_PATTERNS = (
    r"\bpython\b",
    r"\bsql\b",
    r"\bexcel\b",
    r"\bfigma\b",
    r"\bphotoshop\b",
    r"\billustrator\b",
    r"\bsalesforce\b",
    r"\bhubspot\b",
    r"\baws\b",
    r"\bazure\b",
    r"\bgcp\b",
    r"\btableau\b",
    r"\bpower bi\b",
)


def _requirement_text(requirement: Requirement) -> str:
    return " ".join(
        text
        for text in (
            requirement.name,
            requirement.description,
            requirement.source_statement,
        )
        if text
    )


def _day_one_must_haves(role: RoleSpecification) -> list[Requirement]:
    return [
        item
        for item in role.requirements
        if item.priority is RequirementPriority.MUST_HAVE
        and item.learnability in {None, Learnability.DAY_ONE}
    ]


def _capability_clusters(requirements: Iterable[Requirement]) -> set[str]:
    clusters: set[str] = set()
    for item in requirements:
        text = _requirement_text(item)
        for cluster, patterns in _CAPABILITY_CLUSTERS.items():
            if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
                clusters.add(cluster)
    return clusters


def _contains_tool_name(requirement: Requirement) -> bool:
    text = _requirement_text(requirement)
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _TOOL_PATTERNS)


def _normalise_capability_name(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", name.lower()))


def detect_excessive_requirements(
    role: RoleSpecification,
) -> tuple[ExcessiveRequirementIssue, ...]:
    """Apply every documented excessive-requirement rule."""
    issues: list[ExcessiveRequirementIssue] = []
    day_one = _day_one_must_haves(role)
    day_one_ids = tuple(item.requirement_id for item in day_one)

    if len(day_one) > MAX_DAY_ONE_MUST_HAVES:
        issues.append(
            ExcessiveRequirementIssue(
                issue_id="excessive_day_one_must_haves",
                rule="too_many_day_one_must_haves",
                message=(
                    f"{len(day_one)} day-one must-haves are listed; the manager "
                    "must explicitly identify the top five."
                ),
                clarification="Which five capabilities are genuinely required on day one?",
                requirement_ids=day_one_ids,
            )
        )

    clusters = sorted(_capability_clusters(day_one))
    if len(clusters) >= 2:
        issues.append(
            ExcessiveRequirementIssue(
                issue_id="excessive_unrelated_clusters",
                rule="unrelated_capability_clusters",
                message=(
                    "Day-one must-haves span distinct specialist clusters: "
                    + ", ".join(item.replace("_", " ") for item in clusters)
                    + "."
                ),
                clarification=(
                    "Which cluster is the role's primary objective, and should the "
                    "other be learnable, preferred, or a separate role?"
                ),
                requirement_ids=day_one_ids,
            )
        )

    junior_role = role.basic_info.role_level in {RoleLevel.INTERN, RoleLevel.ENTRY}
    senior_requirements = [
        item
        for item in day_one
        if item.proficiency in {ProficiencyLevel.ADVANCED, ProficiencyLevel.EXPERT}
        or re.search(
            r"\b(senior leadership|enterprise architecture|executive ownership|"
            r"organisation-wide strategy)\b",
            _requirement_text(item),
            flags=re.IGNORECASE,
        )
    ]
    if junior_role and senior_requirements:
        issues.append(
            ExcessiveRequirementIssue(
                issue_id="excessive_seniority_mismatch",
                rule="seniority_mismatch",
                message=(
                    "An intern or entry-level role includes advanced, expert, or "
                    "senior-scope day-one ownership."
                ),
                clarification=(
                    "Should the role level increase, or should the ownership and "
                    "proficiency requirement be reduced?"
                ),
                requirement_ids=tuple(
                    item.requirement_id for item in senior_requirements
                ),
            )
        )

    tools_without_rationale = [
        item
        for item in role.requirements
        if item.category is RequirementCategory.TECHNICAL
        and _contains_tool_name(item)
        and not item.business_rationale
    ]
    if tools_without_rationale:
        issues.append(
            ExcessiveRequirementIssue(
                issue_id="excessive_tool_without_rationale",
                rule="tool_without_rationale",
                message="Named tools are listed without the business task they support.",
                clarification=(
                    "What job task requires each tool, and which equivalents would "
                    "demonstrate the same capability?"
                ),
                requirement_ids=tuple(
                    item.requirement_id for item in tools_without_rationale
                ),
            )
        )

    by_name: dict[str, list[Requirement]] = {}
    for item in role.requirements:
        by_name.setdefault(_normalise_capability_name(item.name), []).append(item)
    conflicting = [
        items
        for items in by_name.values()
        if len({item.proficiency for item in items if item.proficiency is not None}) > 1
    ]
    if conflicting:
        ids = tuple(
            item.requirement_id for items in conflicting for item in items
        )
        issues.append(
            ExcessiveRequirementIssue(
                issue_id="excessive_conflicting_proficiency",
                rule="conflicting_proficiency",
                message="The same capability has conflicting proficiency expectations.",
                clarification="Which proficiency level is actually required, and by when?",
                requirement_ids=ids,
            )
        )

    return tuple(issues)


def _combined_contradiction_text(contradiction: Contradiction) -> str:
    return " ".join(
        [contradiction.description, *contradiction.source_statements]
    ).lower()


def classify_contradiction_severity(contradiction: Contradiction) -> str:
    """Classify operational impact using deterministic documented patterns."""
    text = _combined_contradiction_text(contradiction)

    remote_conflict = "remote" in text and bool(
        re.search(
            r"(five|5)[ -]day[s]?(?: a week)?[^.]{0,40}(office|in[ -]office)|"
            r"(office|in[ -]office)[^.]{0,40}(five|5)[ -]day",
            text,
        )
    )
    eligibility_conflict = bool(
        re.search(r"\b(no work rights|not eligible)\b", text)
        and re.search(r"\b(work rights required|must be eligible)\b", text)
    )
    if remote_conflict or eligibility_conflict:
        return "critical"

    if re.search(
        r"\b(entry[ -]level|intern)\b", text
    ) and re.search(
        r"\b(senior ownership|senior leadership|enterprise architecture|"
        r"executive ownership|organisation-wide strategy)\b",
        text,
    ):
        return "high"
    if re.search(r"\bno (?:prior )?experience (?:is )?required\b", text) and any(
        re.search(pattern, text) for pattern in _TOOL_PATTERNS
    ):
        return "high"
    if (
        ("mandatory" in text or "must-have" in text or "must have" in text)
        and ("optional" in text or "preferred" in text)
    ):
        return "high"
    if "conflicting proficiency" in text:
        return "high"
    if re.search(r"\b(high autonomy|independent ownership)\b", text) and re.search(
        r"\b(every (?:task|output).{0,20}approval|approval for every)\b", text
    ):
        return "high"

    if "strategic" in text and re.search(
        r"\b(90%|ninety percent|nearly all|almost all).{0,30}execut", text
    ):
        return "medium"
    return "low"


def _role_text(role: RoleSpecification) -> str:
    values = [text for _, text in _role_text_sources(role)]
    values.extend(
        text
        for text in (
            role.basic_info.work_arrangement,
            role.constraints.work_arrangement,
            role.constraints.location,
        )
        if text
    )
    return " ".join(values)


def detect_contradictions(role: RoleSpecification) -> tuple[Contradiction, ...]:
    """Detect structured versions of the documented contradiction examples."""
    detected: list[Contradiction] = []
    combined = _role_text(role)
    lowered = combined.lower()

    if "remote" in lowered and re.search(
        r"(five|5)[ -]day[s]?(?: a week)?[^.]{0,40}(office|in[ -]office)|"
        r"(office|in[ -]office)[^.]{0,40}(five|5)[ -]day",
        lowered,
    ):
        detected.append(
            Contradiction(
                contradiction_id="contradiction_remote_office",
                description="The role is remote but requires five-day office attendance.",
                severity="critical",
                source_statements=[
                    text
                    for text in (
                        role.basic_info.work_arrangement,
                        role.constraints.work_arrangement,
                    )
                    if text
                ]
                or [combined],
            )
        )

    excessive = detect_excessive_requirements(role)
    seniority_issue = next(
        (item for item in excessive if item.rule == "seniority_mismatch"), None
    )
    if seniority_issue:
        requirements_by_id = {
            item.requirement_id: item for item in role.requirements
        }
        sources = [
            requirements_by_id[item_id].source_statement
            for item_id in seniority_issue.requirement_ids
            if item_id in requirements_by_id
        ]
        detected.append(
            Contradiction(
                contradiction_id="contradiction_seniority_scope",
                description="Entry-level role but senior ownership is required.",
                severity="high",
                source_statements=sources or [seniority_issue.message],
            )
        )

    specialised_day_one = [
        item for item in _day_one_must_haves(role) if _contains_tool_name(item)
    ]
    if re.search(r"\bno (?:prior )?experience (?:is )?required\b", lowered) and len(
        specialised_day_one
    ) >= 2:
        detected.append(
            Contradiction(
                contradiction_id="contradiction_experience_tools",
                description=(
                    "No experience is required but several specialised tools are "
                    "mandatory on day one."
                ),
                severity="high",
                source_statements=[
                    item.source_statement for item in specialised_day_one
                ],
            )
        )

    if "strategic" in lowered and re.search(
        r"\b(90%|ninety percent|nearly all|almost all).{0,30}execut", lowered
    ):
        detected.append(
            Contradiction(
                contradiction_id="contradiction_strategy_execution",
                description="The role is strategic but nearly all work is execution.",
                severity="medium",
                source_statements=[combined],
            )
        )

    if re.search(r"\b(high autonomy|independent ownership)\b", lowered) and re.search(
        r"\b(every (?:task|output).{0,20}approval|approval for every)\b", lowered
    ):
        detected.append(
            Contradiction(
                contradiction_id="contradiction_autonomy_approval",
                description="High autonomy conflicts with approval for every task.",
                severity="high",
                source_statements=[combined],
            )
        )

    by_name: dict[str, list[Requirement]] = {}
    for item in role.requirements:
        by_name.setdefault(_normalise_capability_name(item.name), []).append(item)
    for normalised_name, items in by_name.items():
        priorities = {item.priority for item in items}
        if len(priorities) > 1:
            detected.append(
                Contradiction(
                    contradiction_id=_stable_id(
                        "contradiction_priority", normalised_name
                    ),
                    description=(
                        "The same capability is described as both mandatory and "
                        "preferred or optional."
                    ),
                    severity="high",
                    source_statements=[item.source_statement for item in items],
                )
            )
        proficiencies = {
            item.proficiency for item in items if item.proficiency is not None
        }
        if len(proficiencies) > 1:
            detected.append(
                Contradiction(
                    contradiction_id=_stable_id(
                        "contradiction_proficiency", normalised_name
                    ),
                    description=(
                        "The same capability has conflicting proficiency expectations."
                    ),
                    severity="high",
                    source_statements=[item.source_statement for item in items],
                )
            )
    return tuple(detected)


def normalise_contradictions(
    role: RoleSpecification,
) -> tuple[Contradiction, ...]:
    """Classify existing conflicts and add non-duplicate deterministic detections."""
    severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    contradictions = [
        item.model_copy(
            update={
                "severity": max(
                    (item.severity, classify_contradiction_severity(item)),
                    key=severity_rank.__getitem__,
                )
            }
        )
        for item in role.quality.contradictions
    ]
    known_ids = {item.contradiction_id for item in contradictions}
    known_descriptions = {
        _normalise_capability_name(item.description) for item in contradictions
    }
    for item in detect_contradictions(role):
        normalised_description = _normalise_capability_name(item.description)
        if (
            item.contradiction_id in known_ids
            or normalised_description in known_descriptions
        ):
            continue
        contradictions.append(item)
        known_ids.add(item.contradiction_id)
        known_descriptions.add(normalised_description)
    return tuple(contradictions)


def approval_blockers(role: RoleSpecification) -> list[str]:
    """Return critical role gaps that cannot be overridden by acknowledgement."""
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
        for item in normalise_contradictions(role)
    ):
        blockers.append("High or critical contradictions must be resolved.")
    return blockers


def evaluate_role_quality(role: RoleSpecification) -> RoleQualityReport:
    """Run the complete deterministic quality engine without mutating the role."""
    vague_phrases = detect_vague_phrases(role)
    excessive = detect_excessive_requirements(role)
    contradictions = normalise_contradictions(role)
    warnings: list[QualityWarning] = []
    warnings.extend(
        QualityWarning(
            warning_id=item.flag_id,
            rule="vague_phrase",
            message=f"Vague phrase '{item.phrase}' in {item.source}.",
        )
        for item in vague_phrases
    )
    warnings.extend(
        QualityWarning(
            warning_id=item.issue_id,
            rule=item.rule,
            message=item.message,
        )
        for item in excessive
    )
    warnings.extend(
        QualityWarning(
            warning_id=item.contradiction_id,
            rule="unresolved_contradiction",
            message=f"{item.severity.title()} contradiction: {item.description}",
        )
        for item in contradictions
        if not item.resolved and item.severity in {"low", "medium"}
    )
    return RoleQualityReport(
        readiness=calculate_readiness(role),
        vague_phrases=vague_phrases,
        excessive_requirements=excessive,
        contradictions=contradictions,
        warnings=tuple(warnings),
        blockers=tuple(approval_blockers(role)),
    )


def refresh_role_quality(role: RoleSpecification) -> RoleSpecification:
    """Return the role with derived quality fields refreshed for display/storage.

    Only previously *recorded* contradictions are persisted here, with their
    severity reclassified against the role's current wording. Contradictions
    that :func:`detect_contradictions` finds purely from current role state
    are deliberately left out of storage -- they are recomputed fresh on every
    call, so a manager edit that removes the underlying conflict makes the
    issue disappear on the next render instead of leaving a permanent,
    unresolvable approval blocker. A detected contradiction only becomes
    persistent once a human resolves it through :func:`resolve_contradiction`.
    """
    report = evaluate_role_quality(role)
    severity_by_id = {
        item.contradiction_id: item.severity for item in report.contradictions
    }
    stored_contradictions = [
        item.model_copy(
            update={"severity": severity_by_id.get(item.contradiction_id, item.severity)}
        )
        for item in role.quality.contradictions
    ]
    quality = role.quality.model_copy(
        update={
            "readiness_score": report.readiness.score,
            "contradictions": stored_contradictions,
            "warnings": [item.message for item in report.warnings],
        }
    )
    return role.model_copy(update={"quality": quality})


def resolve_contradiction(
    role: RoleSpecification,
    contradiction_id: str,
    *,
    resolved_by: str,
    resolution: str,
    resolved_at: datetime | None = None,
) -> RoleSpecification:
    """Record an explicit human resolution for one currently active contradiction.

    ``contradiction_id`` may belong to an already-stored contradiction (for
    example one sourced from a discovery turn) or to one currently produced
    only by :func:`detect_contradictions`. Either way, resolving it is what
    makes the entry persistent: it is written into ``role.quality.contradictions``
    with ``resolved=True`` so a later refresh recognises it as already handled
    instead of re-detecting it as a fresh, unresolved issue.
    """
    if not resolved_by.strip():
        raise ValueError("resolved_by is required")
    if not resolution.strip():
        raise ValueError("resolution is required")

    report = evaluate_role_quality(role)
    match = next(
        (
            item
            for item in report.contradictions
            if item.contradiction_id == contradiction_id
        ),
        None,
    )
    if match is None:
        raise ContradictionNotFoundError(contradiction_id)

    timestamp = resolved_at or datetime.now(UTC)
    resolved_item = match.model_copy(
        update={
            "resolved": True,
            "resolution": resolution.strip(),
            "resolved_by": resolved_by.strip(),
            "resolved_at": timestamp,
        }
    )
    remaining = [
        item
        for item in role.quality.contradictions
        if item.contradiction_id != contradiction_id
    ]
    quality = role.quality.model_copy(
        update={"contradictions": [*remaining, resolved_item]}
    )
    return role.model_copy(
        update={
            "quality": quality,
            "parent_version": role.version,
            "version": role.version + 1,
            "audit": role.audit.model_copy(update={"updated_at": timestamp}),
        }
    )


def approve_role(
    role: RoleSpecification,
    *,
    approver: str,
    confirmed_sections: Iterable[ApprovalSection],
    acknowledged_warning_ids: Iterable[str] = (),
    approved_at: datetime | None = None,
) -> RoleSpecification:
    """Capture an explicit human approval after blockers and warnings are handled."""
    refreshed = refresh_role_quality(role)
    report = evaluate_role_quality(refreshed)
    reasons = list(report.blockers)
    if not approver.strip():
        reasons.append("Approver name is required.")

    confirmed = {
        item if isinstance(item, ApprovalSection) else ApprovalSection(item)
        for item in confirmed_sections
    }
    missing_sections = [
        item for item in REQUIRED_APPROVAL_SECTIONS if item not in confirmed
    ]
    if missing_sections:
        reasons.append(
            "All five approval sections must be explicitly confirmed: "
            + ", ".join(item.value for item in missing_sections)
            + "."
        )

    existing_acknowledgements = {
        item.warning_id: item
        for item in refreshed.quality.warning_acknowledgements
    }
    acknowledged_ids = set(acknowledged_warning_ids) | set(
        existing_acknowledgements
    )
    unacknowledged = [
        item for item in report.warnings if item.warning_id not in acknowledged_ids
    ]
    if unacknowledged:
        reasons.append(
            "All quality warnings must be acknowledged before approval."
        )
    if reasons:
        raise ApprovalBlockedError(reasons)

    timestamp = approved_at or datetime.now(UTC)
    warning_by_id = {item.warning_id: item for item in report.warnings}
    for warning_id in acknowledged_warning_ids:
        warning = warning_by_id.get(warning_id)
        if warning is None or warning_id in existing_acknowledgements:
            continue
        existing_acknowledgements[warning_id] = WarningAcknowledgement(
            warning_id=warning_id,
            warning=warning.message,
            acknowledged_by=approver.strip(),
            acknowledged_at=timestamp,
        )

    quality = refreshed.quality.model_copy(
        update={
            "warning_acknowledgements": list(existing_acknowledgements.values())
        }
    )
    return refreshed.model_copy(
        update={
            "quality": quality,
            "human_approved": True,
            "approved_by": approver.strip(),
            "approved_at": timestamp,
            "approved_sections": list(REQUIRED_APPROVAL_SECTIONS),
            "review_status": ReviewStatus.APPROVED,
            "parent_version": refreshed.version,
            "version": refreshed.version + 1,
            "audit": refreshed.audit.model_copy(update={"updated_at": timestamp}),
        }
    )
