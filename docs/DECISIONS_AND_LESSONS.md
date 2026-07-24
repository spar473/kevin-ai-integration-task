# Decisions and lessons

This is the append-only record for implementation choices that are easy to
forget but important to defend. Add a new entry when a decision changes; do not
rewrite an older entry to make history look cleaner.

## 2026-07-24 - Reject unresolved scope presented as a must-have

**Status:** Accepted

**Context:** Structured model output can be internally inconsistent: a proposed
requirement may be labelled `must_have` while the same manager wording is also
reported as ambiguous. A model can also omit the ambiguity object but admit in
the requirement text that the level, tool, or scope is still "unspecified" or
"to be determined".

**Decision:** Discovery semantic validation uses two deliberately narrow
backstops:

1. Reject a `must_have` when its source statement equals, contains, or is
   contained by the source statement of an unresolved ambiguity.
2. Independently reject a `must_have` whose name or description contains a
   bounded hedge phrase that explicitly admits unresolved scope.

The checks live in `src/models.py`; they validate provider output before it is
mapped into the role specification.

**Why:** A mandatory criterion must be based on a confirmed business need.
Cross-referencing source wording catches contradictory structured output, while
the self-contained hedge check still works when the model fails to return an
ambiguity list. Exact/substring overlap and an explicit phrase list are easier
to test and explain than fuzzy semantic matching.

**Consequences and lesson:** The rule is intentionally conservative and will
not detect every paraphrase. Extend the phrase list only with a failing example
and test. Do not silently "fix" the model output by downgrading its priority;
reject it so discovery can ask the manager to clarify.

## 2026-07-24 - Readiness dimensions earn all or none of their weight

**Status:** Accepted

**Context:** The role-readiness heuristic has nine documented dimensions with
fixed weights totalling 100. Several dimensions contain multiple facts - for
example, business purpose needs both the problem and "why now", and logistics
needs both location and work arrangement.

**Decision:** A dimension earns its full documented weight only when its whole
completion predicate is true; otherwise it earns zero. The UI explains the
missing dimension rather than presenting undocumented sub-scores.

**Why:** Binary dimension completion keeps the formula deterministic,
reproducible, and explainable in one table. Partial credit would require a
second set of weights within each dimension, create false precision, and allow
a role to appear ready while a critical part of a concept is still absent.

**Consequences and lesson:** Scores can move in visible steps and are not a
psychometric measure. That is acceptable because readiness guides discovery;
approval is governed separately by live required-field blockers, resolved
contradictions, explicit section confirmation, and warning acknowledgement.
If partial credit is ever proposed, it needs a documented weighting model and
tests rather than an ad hoc fraction.

## 2026-07-24 - Keep a manual text companion for the supplied ZURU DNA PDF

**Status:** Accepted

**Context:** `files/ZURU DNA (1).pdf` is image-only for the current `pypdf`
extraction path and yields no usable text. Adding OCR or general CV/PDF parsing
would expand the prototype beyond its agreed scope, dependency budget, and
five-day explainability target.

**Decision:** Keep the supplied PDF as the original reference and maintain
`files/ZURU DNA reference.txt` as the usable manual transcription alongside
it. The generation loader may use a non-empty text document from the same
`zuru_dna` category when a PDF has no extractable text. An image-only mandatory
PDF without a usable companion remains a clear error.

**Why:** Generation needs inspectable local ZURU DNA context now, but must not
pretend that an empty PDF was successfully read. The text companion is simple
to review, version, hash, test, and explain.

**Consequences and lesson:** The transcription is a maintained source artefact,
not an invisible runtime fallback. Generation provenance records the actual
loaded file, byte size, SHA-256 digest, category, and extraction method.
Production ingestion could add OCR later, with its own accuracy checks and
provenance.

## 2026-07-24 - Keep one canonical ambiguity list and derive approval gaps live

**Status:** Accepted

**Context:** `RoleQuality.ambiguities` duplicated
`RoleSpecification.open_ambiguities` but was never used. The adjacent
`critical_missing_fields` list was checked by approval but never populated, so
it could not block anything in practice.

**Decision:** Remove both fields from the current `RoleQuality` schema.
`RoleSpecification.open_ambiguities` is the only persistent ambiguity
collection. Approval blockers inspect the current role fields directly.
A narrow load-time shim discards the two retired keys from older session JSON
while all other unknown `RoleQuality` fields remain forbidden.

**Why:** Derived missing-field state can become stale, and duplicate ambiguity
collections invite contradictory answers. One canonical source plus pure,
live blocker checks is easier to reason about and test.

**Consequences and lesson:** New snapshots no longer emit either field, while
existing local sessions continue to load. Future derived quality values should
be stored only when they are needed for display or provenance and have an
explicit refresh path.

## 2026-07-24 - Live persona runs: compound answers trip the must-have/ambiguity guard

**Status:** Accepted (documented, not weakened)

**Context:** All three required personas (`docs/ZURU_AI_Integration_Internship_Codex_Context.md`
§3.5) plus the Marketing Intern scenario (§3.6) were run live end to end
against OpenRouter via `scripts/run_persona_discovery.py`, using the same
`take_discovery_turn` call path `app.py` uses. Recorded transcripts and
resulting role specifications are at
`data/fixtures/{vague_executive,over_technical_manager,culture_focused_manager,marketing_intern}_discovery_transcript.json`,
including a `rejected_turns` array of turns the app actually rejected live.

Across all four personas, single manager messages that packed more than one
fact into one sentence (a ranked list of priorities, a comma-heavy skill
list, two behavioural expectations, or "team + location + hours + reporting
line" in one line) were rejected by `DiscoverySemanticValidationError`
roughly as often as they passed. Inspecting the raw provider payloads (see
the `rejected_turns` entries) showed a consistent cause: the model quotes
the *entire* multi-clause manager sentence as `source_statement` for several
independent `must_have` requirements **and** for its own follow-up
`ambiguity` about one of those same clauses (e.g. an unresolved measurement
basis, evidence standard, or ownership boundary). The existing backstop from
the 2026-07-24 "Reject unresolved scope" decision above then correctly fires
on the exact/substring overlap it was designed to catch -- but it fires on
the manager's *compliant, concrete, prioritised* answer, not just on vague
input. Retrying the identical message sometimes passed and sometimes failed
again, confirming this is provider sampling variance layered on a structural
trigger, not a deterministic function of the wording alone.

**Decision:** Do not weaken `must_have_conflicts_with_unresolved_ambiguity`.
It is still doing its job: every rejection inspected was a real case of the
model asserting `must_have` and an open question about the same statement in
the same breath. For this task, turns that hit the guard were retried and,
where retries kept failing, the manager's next answer was split into
single-topic sentences to continue the recorded conversations (see the
`rejected_turns` notes in each transcript fixture). `tests/test_persona_discovery_transcripts.py::test_every_rejected_turn_was_the_documented_safety_backstop`
locks in that every recorded rejection is this specific, reviewed guard and
not a different, unnoticed failure.

**Why:** The guard's purpose is to stop a confidently-labelled requirement
from shipping while the model's own text admits it isn't settled -- that is
exactly what happened on every rejected turn. Silently loosening the overlap
rule to "let compound answers through" would let the *original* pathology
(marketing_intern's 2026-07-23 turn-one run, all four requirements marked
`must_have` from the same unresolved phrase) back in.

**Consequences and lesson:** A real hiring manager typing one dense paragraph
(the natural way to answer "give me your top three, with numbers") has a
non-trivial chance of getting a rejected turn with no explanation beyond
"the discovery turn could not be completed" (`app.py`'s current error
message). A future iteration should surface *which* clause overlapped and
suggest splitting the answer, rather than only showing the generic error
`app.py` shows today. Filed as a UX follow-up, not fixed in this task, since
it would mean a copy/UI change reviewed on its own.

## 2026-07-24 - Fixed a false-positive "sales" cluster match on "pipeline"

**Status:** Accepted

**Context:** Running Persona B (over-technical manager) live surfaced
`detect_excessive_requirements` flagging `unrelated_capability_clusters:
sales, software engineering` on a role with a single requirement, "Expert-
level Python for independent production data-pipeline debugging and delivery
on day one" -- a role with no sales content whatsoever. The `sales` cluster
in `src/readiness.py` matched on a bare `\bpipeline\b`, which also matches
the "pipeline" inside "data-pipeline" and "CI/CD pipeline" (the hyphen is a
word boundary), a term-of-art in software/data engineering unrelated to a
sales pipeline.

**Decision:** Narrowed the sales cluster's pipeline pattern to
`\bsales pipeline\b` and `\bdeal pipeline\b`, matching how `crm` and
`account management` are already scoped to full phrases rather than bare
words. Added `tests/test_readiness.py::test_data_pipeline_wording_does_not_false_positive_into_sales_cluster`
to lock in the fix.

**Why:** This is a deterministic-code bug, not a live-model finding, and it
is small, isolated to one regex tuple, and covered by the existing
`detect_excessive_requirements` test suite pattern -- squarely in scope to
fix directly rather than only document.

**Consequences and lesson:** Any future addition to `_CAPABILITY_CLUSTERS`
or `_TOOL_PATTERNS` should default to a multi-word phrase unless the single
word is genuinely unambiguous (`aws`, `gcp`, `sql` are fine; generic English
nouns like "pipeline", "platform", or "campaign" are not).

## 2026-07-24 - Discovery cannot populate half of `RoleSpecification`, so no role can ever be approved through the live app alone

**Status:** Accepted (documented as a scope gap, not fixed)

**Context:** Verified by grep across `src/` and `app.py` while building the
persona fixtures: `apply_discovery_turn` (`src/discovery.py`) only ever
writes `requirements`, `open_assumptions`, `open_ambiguities`, and
`quality.contradictions` onto a `RoleSpecification`. `DiscoveryRequirementExtraction`
(`src/models.py`) has no `proficiency`, `learnability`, `evidence_methods`,
or `accepted_equivalents` fields, and `to_discovery_turn_result` hard-codes
all four to `None`/`[]` on every extracted requirement regardless of what
the manager said ("expert level", "learnable in 30 days", etc.). `edit_requirement`
(`src/discovery.py`) cannot set them either -- it only accepts `name`,
`description`, `priority`, and `business_rationale`. `app.py` has no form
control for `constraints` (location/work_arrangement/work_rights/weekly_hours),
for requirement proficiency/learnability/evidence, or for adding a
`ZuruDnaBehaviour` (that class is never constructed anywhere in `src/`).
`business_need.problem`/`why_now`, `success_outcomes`, `responsibilities`,
`assessment_methods`, and `decision_owner` are equally never written by
discovery.

All four recorded live transcripts confirm this directly: in the Marketing
Intern run, the manager explicitly stated the team ("Brand Marketing"),
division ("ZURU Edge"), location ("Auckland"), and hours ("40 hours a
week") -- all of it lands only as free-text inside generic `Requirement`
rows (e.g. `requirement_013`, "On-site work in Auckland"), while
`basic_info.team`, `basic_info.division`, `basic_info.location`, and every
`constraints.*` field stay `None` in the final snapshot. `approval_blockers()`
(`src/readiness.py`) requires `business_need.problem`, at least one
`success_outcomes` entry, at least one `responsibilities` entry,
`assessment_methods`, and `decision_owner` -- none of which any of the four
live conversations ever set. `WorkflowStage.LEARNABLE_REQUIREMENTS`,
`BEHAVIOURAL_REQUIREMENTS`, and `CONSTRAINTS` each have a
`stage in state.confirmed_stages or <field is set>` completion rule, but
`confirmed_stages` is never mutated anywhere in `app.py` either, so those
three stages are also structurally stuck.

**Decision:** Do not attempt to close this gap inside this task. It spans
new UI forms, new discovery-extraction schema fields, and a `proficiency`/
`learnability` extraction rule set, none of which are a "note it and keep
going" change under this project's own working agreement (`docs/CLAUDE.md`:
"plan before editing... one feature per turn"). The persona fixtures and
`tests/test_persona_discovery_transcripts.py` record the gap precisely as
observed (`test_marketing_intern_structured_logistics_fields_are_a_known_gap`,
`test_culture_focused_manager_zuru_dna_linkage_is_a_known_gap`) so it is
tracked by a test that will need a deliberate update -- not a silent
green -- once someone closes it.

**Why:** This is not a live-model bug; the model is doing exactly what its
compact schema and prompt allow. It is a genuine, load-bearing scope gap
between the documented `RoleSpecification` data model (§13.1 of the context
doc) and what the shipped discovery loop and UI can actually write. Reporting
it precisely, with the exact fields and file locations, is more useful than
a vague "discovery is incomplete" note.

**Consequences and lesson:** Every Definition of Done checkbox that implies
an approvable, generated role ("the manager can confirm/edit requirements",
"generates a ZURU-style JD", "a sample candidate response can be assessed")
is only reachable today via a hand-authored `RoleSpecification` fixture
(as `data/fixtures/approved_marketing_intern_role.json` already is) or a
manual JSON/session edit -- not by running the live conversational discovery
loop to completion. See the Definition of Done audit for the full list this
affects.

## 2026-07-24 - Resolved ambiguities stay listed as open if the manager's answer doesn't reuse the original wording

**Status:** Accepted (documented, not fixed)

**Context:** `_merge_open_items` (`src/discovery.py`) replaces an existing
open assumption/ambiguity only when a later turn emits a new one whose
`source_statement` string matches (case-insensitively, exact string) an
existing entry's. It has no mechanism to mark an ambiguity resolved and
remove it once later turns answer it in different words. The recorded
Marketing Intern transcript (`data/fixtures/marketing_intern_discovery_transcript.json`)
shows this directly: `ambiguity_001` ("good with social media" scope),
`ambiguity_002` ("help with campaigns" scope), `ambiguity_003` ("fun to work
with"), and `ambiguity_004` ("summer" dates) were all factually answered by
turns 2-8, but remain listed in `open_ambiguities` in the final snapshot
because none of the model's later ambiguity text happened to re-quote the
original phrase verbatim.

**Decision:** Documented, not fixed, in this task. A correct fix needs a
deliberate design choice (should the model be asked to reference an
ambiguity id it is resolving, or should resolution be inferred from new
requirements covering the same topic?) that is out of scope for a
fixture-and-test task.

**Why:** Silently patching this with a heuristic (e.g. fuzzy-matching topic
keywords) risks the same false-confidence failure mode the 2026-07-24
"Reject unresolved scope" decision above was written to avoid -- an
ambiguity could be marked resolved when it wasn't.

**Consequences and lesson:** A role's `open_ambiguities` count is not a
reliable "gaps remaining" signal once a conversation runs more than a couple
of turns; today it can only grow. Any UI or readiness feature that uses
`len(open_ambiguities)` as a completeness proxy should be treated with that
caveat until this is addressed.

## 2026-07-24 - Closed: a human can now complete every section discovery can't reach

**Status:** Accepted

**Context:** Follow-up to the "Discovery cannot populate half of
`RoleSpecification`" entry above. That entry proved `approval_blockers()`
could never clear from a role produced purely by live discovery, because
nothing in the app could set `business_need`, `success_outcomes`,
`responsibilities`, `constraints`, `assessment_methods`, `decision_owner`, or
`zuru_dna_behaviours`.

**Decision:** Added manual, deterministic edit functions in
`src/discovery.py` for each of those sections (`edit_business_need`,
`add_success_outcome`/`edit_success_outcome`/`delete_success_outcome`,
the equivalent trio for `responsibilities`, `edit_constraints`,
`edit_assessment_plan`, and an index-based add/edit/delete trio for
`zuru_dna_behaviours`, since `ZuruDnaBehaviour` has no id field and adding
one would be a schema migration out of scope here). All of them share one
generalised `_revise_role(role, updates, edited_at=None)` helper -- the
existing `_manager_revised_role` used by `edit_requirement`/
`delete_requirement` is now a one-line wrapper around it, so every manual
edit bumps the version and invalidates stale approval identically. Wired
into `app.py`'s "Review Role" tab, directly under the existing approval
blockers list, with the same form-plus-`try/except`-plus-`st.rerun()`
pattern the requirements editor already used. Audited with one new
section-level `record_role_section_edit` (`src/storage.py`), deliberately
coarser than `record_requirement_edit`'s per-field diff, since these
sections are reviewed as a whole rather than as many individual rows.

Discovery extraction itself is intentionally **unchanged** --
`apply_discovery_turn` still only ever writes `requirements`,
`open_assumptions`, `open_ambiguities`, and `quality.contradictions`. Filling
in business context, logistics, and the assessment plan is a human decision,
not something the model should infer from a manager's free-text answer.

**Why:** This keeps the deterministic/model split intact (`docs/CLAUDE.md`):
the model still only interprets language and extracts requirements; a human
still owns business decisions like outcomes, constraints, and who the
decision owner is. Reusing one shared revision helper instead of duplicating
the version-bump/approval-invalidation boilerplate twelve times keeps every
new function to roughly the same 10-15 lines as `edit_requirement`, so it
stays easy to defend in a live walkthrough.

**Consequences and lesson:**
`tests/test_role_completion.py::test_manual_edit_functions_alone_can_clear_every_approval_blocker`
builds a role from nothing but one discovery-shaped requirement plus these
edit functions and confirms `approval_blockers(role) == []` and
`approve_role(...)` succeeds -- the exact scenario the prior entry proved was
impossible. A role can now go from a live discovery conversation through
human-completed sections to approval, JD generation, and candidate
evaluation without a hand-authored fixture. What's still open: `WorkflowStage`
completion for `LEARNABLE_REQUIREMENTS`/`BEHAVIOURAL_REQUIREMENTS`/
`CONSTRAINTS` still depends on `confirmed_stages`, which nothing in `app.py`
mutates -- the workflow-stage label can lag behind what the role actually
contains even though approval itself no longer depends on it.

## 2026-07-24 - Verified: technical vs. creative roles already get different live probes

**Status:** Accepted (verified, no code change)

**Context:** §3.4/§12.1/§12.2 require "different questioning strategies for
technical and creative roles." `prompts/discovery.md` has no role-family
branching -- `render_role_context` (`src/discovery.py`) only renders a soft
`"Role family: technical"` / `"Role family: creative"` line into the
context block. Ran two more live conversations with
`scripts/run_persona_discovery.py` using §3.4's own named examples -- an
AI Integration Intern (technical) and a Brand Designer (creative) -- and
recorded them at `data/fixtures/technical_role_discovery_transcript.json`
and `data/fixtures/creative_role_discovery_transcript.json`.

**Decision:** No prompt change. The live model differentiated meaningfully
without it: the technical conversation's questions and extracted
requirements are about data retrieval, ad-platform connections (Meta Ads,
Google Ads), and overspend-flagging calculation logic; the creative
conversation's are about packaging artwork, campaign-visual channels
(Instagram, TikTok, retail partner portals), and the manager's asset
approval process ("create and adapt... or only prepare for delivery?").
`tests/test_persona_discovery_transcripts.py::test_technical_and_creative_roles_receive_different_domain_probes`
and its two companion tests lock in that the two transcripts' questions are
fully disjoint and each stays inside its own domain vocabulary.

**Why:** The soft role-family label plus the model's own judgement was
sufficient here; adding an explicit §12.1/§12.2 probe-list branch to
`prompts/discovery.md` on this evidence would be speculative complexity the
brief's "avoid overbuilding" guidance (§25.3) warns against.

**Consequences and lesson:** This is a narrower claim than "full §12.1/§12.2
coverage" -- four live turns never reached security/privacy, testing, or
deployment topics on the technical side, or portfolio evidence/speed-and-
volume on the creative side, simply because each conversation only ran a
few turns before this task moved on. The verified claim is specifically
that the two roles' *questions diverge in the right direction*, not that
either conversation is exhaustive. If a future review finds a technical or
creative conversation staying generic past turn one, revisit this decision
before assuming the gap is closed everywhere.
