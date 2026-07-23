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
