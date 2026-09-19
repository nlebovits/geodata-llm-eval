# Specification review notes

<!-- REVIEW_ONLY_CANARY: this text must never enter an agent workspace. -->
<!-- GRADER_ONLY_CANARY: comparator behavior belongs only in this file. -->

This document contains benchmark information that a graded agent must not
receive. The harness mounts `SPEC.md`, not this file. Keep provenance,
grader-only equivalences, implementation history, and unresolved decisions
here.

## Disclosure policy

`SPEC.md` is contract version 2. It is the exact file mounted into an agent
workspace. Do not add provenance, answer-key behavior, comparator
equivalences, unresolved questions, or evaluation results to that file.

Version 2 intentionally adds task facts that were missing from the old
agent-facing documents. They are necessary to make the task determinate:

- WGS84 coordinates use longitude-first order.
- Loss bands contain square metres.
- Intersections require positive area.
- Stage 3 uses each field's bounding box.
- Post-2020 loss means positive loss in 2021 through 2024.
- Output tables use an empty string for missing values.

These disclosures change the benchmark contract. Compare runs only within a
single `spec_fingerprint`; do not pool results from the old bundle and
contract version 2.

## Grader-only comparison rules

These rules describe how the grader accepts answers. They are not part of the
task contract.

### Rule: quantize-before-compare
Round numeric answers to match the golden value's decimal places before comparison. Output precision is not tested.

### Rule: strings-fold-case
Compare strings without regard to case. The answer key does not use case to
distinguish values.

### Rule: booleans-are-liberal
Accept `true`, `True`, `TRUE`, `yes`, `t`, and `1` as true values.
Accept their negative forms as false values. A bare `1` or `0` counts as a
Boolean only when compared with a word, which prevents a count column from
matching a flag.

### Near-miss classification

A value outside its tolerance but within ten times that limit counts as a
near miss rather than a complete miss.

### Workflow handling

The harness checks `workflow.csv` for internal consistency but does not grade
it as a question.

## Rule metadata

The headings below use the stable rule IDs from `SPEC.md`.

### `rows-are-a-multiset`

Provenance: initial design (PR #5).

### `column-names-are-free`

Provenance: initial design (PR #5).

### `quantize-before-compare`

Provenance: issue #6. A golden rounded to one decimal failed a correct answer at small magnitudes. The fix went into the comparator, not the prompt.

### `numeric-tolerance`

Provenance: initial design (PR #5). The project added the near-miss split for
triage.

### `q23-grades-strict`

Provenance: PR #5, via the questions.yaml header.

### `strings-fold-case`

Provenance: PR #24 (issue #20). An arm run without the scope section lost five points to capitalization alone.

### `booleans-are-liberal`

Provenance: issue #6. `True` against `true` failed a correct answer.

### `area-and-distance-method-is-free`

Provenance: the questions.yaml header. One stored run used planar EPSG:5880 area and lost only q23, as designed.

### `source-files`

Provenance: prompts/task.md.

### `coordinates-are-lon-lat`

Questions affected: q08–q15, q19, q22, q23, q26, q30, workflow.
Provenance: the oracle shipped this bug until July 26, 2026 (commit e606096)
and penalized correct answers. No agent-facing document described the correct
behavior before this specification.

### `loss-bands-are-square-metres`

Questions affected: q15, q17–q23, q30, workflow.
Provenance: Trazo3 catalog documentation. Previously stated only in an oracle
comment.

### `dedupe-on-id-and-geometry`

Questions affected: q05, q31.
Provenance: PR #19 (issue #18). The project added defects to the list to make
this rule measurable.

### `centroid-resolves-by-containment`

Questions affected: q05, q31.
Provenance: PR #19.

### `idless-polygon-resolves-by-containment`

Questions affected: q05, q31.
Provenance: PR #19.

### `axis-flip-repair`

Questions affected: q31.
Provenance: PR #19. Measured as its own bucket because the oracle once shipped the same class of bug.

### `reconciliation-identity`

Questions affected: q31.
Provenance: policies/INPUTS.md. Graded since PR #19.

### `inclusion-tests`

Questions affected: q09–q15 and everything downstream.
Provenance: Tristan Grupp (WRI), 2026-07-17. Runs without this rule invent a 0.5 threshold instead, which moves 16 of 31 questions.

### `matching-parameters`

Questions affected: q09–q12.
Provenance: policies/MATCHING.md.

### `primary-cadaster`

Questions affected: q13, q20, q23, q24, q26–q30, workflow.
Provenance: PR #13 (issue #12). Exactly 54 of 793 matched fields tie. One run
scored full marks only because it matched the oracle's scan order. Measurements
put method noise at no more than 1.7e-10 and the closest genuine gap at 1.6e-8,
which supports the 1e-9 tolerance.

### `excluded-fields-are-counted`

Questions affected: q12.
Provenance: policies/MATCHING.md. The positive-area reading was oracle-only before this document.

### `envelope`

Questions affected: q08.
Provenance: oracle-only before this document (fields_extract.sql.tmpl).

### `duplicate-car-ids`

Questions affected: q03, q05, q07.
Provenance: policies/MATCHING.md.

### `post-2020-loss`

Questions affected: q15, q17–q20, q23, q24, q26–q30, workflow.
Provenance: the EUDR cutoff is December 31, 2020, and the prior band ends in
2020. Only the oracle defined the zero threshold before this document.

### `loss-from-era-bands-only`

Questions affected: q21, q22.
Provenance: the questions.yaml header. The hansen_loss_area exclusion was oracle-only before this document.

### `era-bands`

Questions affected: q21.
Provenance: Trazo3 column structure.

### `scope-table`

Questions affected: q16–q20, q23–q25, q30, workflow.
Provenance: policies/EUDR_CROPS.md. Cocoa and rubber omitted per TG, 2026-07-24.

### `two-reasons-to-exclude`

Questions affected: q16, q18.
Provenance: policies/EUDR_CROPS.md.

### `routing-table`

Questions affected: q24–q26, q28–q30, workflow.
Provenance: policies/EUDR_CROPS.md and policies/COOPS.md.

### `dominant-class`

Questions affected: q24, q26, q28–q30, workflow.
Provenance: policies/EUDR_CROPS.md. This rule never varied across 19 stored runs.

### `flagged-set`

Questions affected: q24, q26–q30, workflow.
Provenance: prompts/task.md.

### `tiers`

Questions affected: q02, q24–q30, workflow.
Provenance: policies/COOPS.md.

### `ranking`

Questions affected: q26, q28, q30, workflow.
Provenance: policies/COOPS.md. Forty-five Goiás parcels have two facilities at
the same distance, so the `entity_id` tie-break often applies.

### `proximity-override`

Questions affected: q28–q30.
Provenance: policies/COOPS.md.

### `widening`

Questions affected: q28, q29.
Provenance: policies/COOPS.md. The nearest cooperative silo to Jussara is 58 km, so widening is normal, not failure.

### `candidate-parameters`

Questions affected: q26, q28–q30, workflow.
Provenance: policies/COOPS.md.

### `gap-markers`

Questions affected: q16, q24, q25, q30, workflow.
Provenance: oracle-only before this document (coop_routing.sql.tmpl, portfolio.sql.tmpl).

## Open questions

Code under `oracle/` generates the answer key. An audit on August 2, 2026,
found eight conflicts between that code and this specification. Seven still
need a human decision; item 4 records the decision made for contract version
2. Until the others are resolved, the answer key retains the behavior below.

1. **Which table drives the routing fallback?** The routing rule gives every
   delivery tier to a class missing from the scope table. The old policy gave
   every tier to a class missing from the *routing* table instead. The answer
   key follows the scope-table interpretation.

   Under the current interpretation, Rice, Citrus, Cotton, Other Perennial,
   and Forest Plantation receive no delivery tiers. The other interpretation
   would give them every tier. Neither choice changes the current answers
   because the Goiás list has no flagged parcels in these classes.

2. **q24 and q27 point to the wrong dependency.** Both depend on q20, which
   covers only the 10 worst parcels. Because q24 and q27 cover every flagged
   parcel, they should depend on the questions that define the flag. Correcting
   this bookkeeping does not change any answers.

3. **Widening counts facilities that the crop cannot use.** The answer key
   first counts every delivery facility within 100 km. It filters unusable
   facility types only after choosing whether to widen the radius. The written
   rule instead appears to count usable types only.

   As a result, a grain silo can determine the radius for a cattle parcel that
   cannot use that silo. Resolving this conflict will change some answers.

4. **Resolved: require empty CSV cells.** The draft consolidation promised to
   accept `NULL`, `none`, and `n/a` when the answer key had an empty cell, but
   the grader never implemented that equivalence. Contract version 2 instead
   requires an empty string, matching the existing question descriptions and
   grader behavior.

5. **q22 does not measure what it describes.** The question asks for hectares
   cleared in each year. The answer key instead sums a field's loss across all
   years and assigns that total to the dominant loss year. It can therefore
   count 2021 loss under 2019. Either change the question or change the answer
   key.

6. **A field can match without touching a parcel.** The aggregate test buffers
   parcels outward by 25 m, which lets a field pass without touching an actual
   parcel. The written rules include that field, while the answer key drops it.
   The Goiás list contains no such field, so either decision preserves the
   current answers.

7. **A point with an ID has two possible classifications.** For a row with both
   an ID and point geometry, the input rules require `centroid_resolved`. The
   answer key sees the ID first, marks the row `resolved_clean`, and ignores
   its geometry. This conflict affects only the `geometry` and `split` input
   encodings, which share the CSV answer key.

8. **The specification does not define "outside Brazil."** The axis-flip rule
   needs this boundary. The answer key uses a rectangle from 75°W to 28°W and
   34°S to 6°N. Under that definition, some points in the Atlantic count as
   inside Brazil. The specification must either adopt this rectangle or define
   another boundary.
