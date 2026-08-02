# EUDR benchmark specification

**Status: draft for review.** This document is the single source of ground
truth for the benchmark. It consolidates `prompts/task.md`, the four documents
under `policies/`, the output contracts in `fixtures/questions.yaml`, and the
comparison rules currently implemented in `harness/grade.py`. Once adopted,
those files become derived artifacts or disappear, the agent receives a
generated view of this document (rule prose only, no provenance or grading
fields), and `spec_fingerprint` becomes the hash of this one file.

Each rule below has a stable id, one or two sentences of prose, and up to
three fields:

- **equivalence** — what the grader treats as the same answer.
- **questions** — the questions whose grading depends on the rule.
- **provenance** — where the rule came from and why it exists.

Rules whose current behavior is contested or silently implemented in the
oracle are listed under [Open questions](#open-questions) at the end rather
than resolved here.

---

## 1. The task

Produce an EU Deforestation Regulation (EUDR) risk analysis for a portfolio of
Brazilian rural properties: resolve the input list to cadastral parcels, match
agricultural field boundaries to those parcels, measure post-2020 forest loss
on EUDR-relevant crops, and identify the cooperative or buyer to contact about
each non-compliant property.

Deliverables: one CSV per question (`answers/q01.csv` … `answers/q31.csv`) and
`answers/workflow.csv`, one row per non-compliant property, with columns
`cod_imovel`, `annex1_commodity`, `post2020_loss_ha`, `top_contact_entity_id`,
`entity_kind`, `tier`, `basis`, `distance_km`.

Questions are staged 1–6 and build on each other; later answers rely on
earlier results.

---

## 2. How answers are compared

These rules apply to every answer at once. They define what "the same answer"
means.

### rule: rows-are-a-multiset
Rows compare as an unordered multiset. Row order never matters, except where a
question explicitly asks for a ranking — there the ranking is expressed
through the rank rule in the question, not through file order.
- provenance: initial design (PR #5). Observed run-to-run row-order variance
  is the most common divergence and moves no score.

### rule: column-names-are-free
Column names and column order are the agent's choice. The grader matches
columns by trying permutations against the golden; only column meaning and
type bind.
- provenance: initial design (PR #5).

### rule: quantize-before-compare
A numeric answer is rounded to the golden value's number of decimals before
comparison. Output precision is not part of the test.
- provenance: issue #6 — a golden rounded to one decimal made a correct
  answer fail at small magnitudes; fixed in the comparator, not the prompt.

### rule: numeric-tolerance
Numbers compare at relative 1e-3 (absolute 1e-9 floor). Questions marked
`geometry` — whose values move under defensible method choices — compare at
relative 1e-2, with integer slack of max(2, 1% of golden). A value failing
its tolerance but within ten times it is recorded as a near miss, separately
from a plain miss.
- questions: geometry-graded are q08–q12, q14, q15, q19, q26, q30.
- provenance: initial design (PR #5); near-miss split added for triage.

### rule: q23-grades-strict
q23 grades at strict tolerance despite reporting hectares, because integer
slack would also apply to its `field_id` column — adjacent plots carry
adjacent ids, and slack would credit an answer naming the wrong farm.
- questions: q23.
- provenance: `questions.yaml` header, PR #5 lineage.

### rule: strings-fold-case
Strings compare case-insensitively. No golden value anywhere is distinguished
from another by case alone, so folding can only turn a wrong answer right if
it was already right.
- provenance: PR #24 (issue #20) — an arm run without `EUDR_CROPS.md` lost
  five points to capitalization alone; the fix was placed in the comparator so
  it applies to all arms symmetrically and leaks nothing.

### rule: booleans-are-liberal
`true`, `True`, `TRUE`, `yes`, `t`, `1` (and their negatives) all read as
booleans. A bare `1`/`0` counts as boolean only when compared against a word,
so count columns never silently match a flag.
- provenance: issue #6 — `True` vs `true` failed a correct answer.

### rule: area-and-distance-method-is-free
No convention for computing absolute areas or distances is imposed. Geodesic
area, an equal-area projection, and Brazil Polyconic agree within a percent;
the `geometry` tolerance absorbs the spread and the agent picks its method.
The oracle happens to use spheroid areas only because this Trazo3 release
ships no area column.
- questions: every question reporting hectares or km except q23.
- provenance: `questions.yaml` header. One stored run chose planar EPSG:5880
  area and lost only q23, as designed.

---

## 3. Data sources

### rule: source-files
Three GeoParquet datasets, queried remotely with DuckDB (`spatial` and
`httpfs` extensions):
- Field boundaries (Trazo3, Goiás 2024):
  `https://data.source.coop/wri-data-lab/trazofields/trazo3-fields/trazo3_brazil_goias_2024.parquet`
- Cadastral parcels (Brazil CAR):
  `https://data.source.coop/tristangruppwri/cadastral/Brazil_CAR_AREA_IMOVEL.parquet`
- Commodity infrastructure:
  `https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/facilities/BR_facilities.parquet`
- provenance: `prompts/task.md`.

### rule: coordinates-are-lon-lat
All geometry is WGS84 longitude-first. Loading with axis order defaulted to
latitude-first shrinks Goiás areas by roughly 1.5× and moves every distance;
the oracle sets `geometry_always_xy = true` in every stage.
- questions: q08–q15, q19, q22, q23, q26, q30, workflow.
- provenance: the oracle itself shipped an axis-order bug until 2026-07-26 and
  penalized correct answers for it (commit e606096). Currently stated nowhere
  agent-visible; promoted to a rule here.

### rule: loss-bands-are-square-metres
The Trazo3 era-band deforestation columns are in square metres; hectares are
band ÷ 10,000.
- questions: q15, q17–q23, q30, workflow.
- provenance: trazofields catalog documentation; previously stated only in an
  oracle comment.

---

## 4. Input list handling

The analysis starts from `lists/goias-sample.csv`. The list may arrive as a
CSV (`cod_imovel` + WKT `geometry`), a geometry file with or without ids, or a
split CSV-plus-geometry pair joined on id. Detect the columns actually
present; never assume a fixed schema. Never drop a row silently — an omitted
property is an un-audited sourcing area, the exact failure an EUDR workflow
exists to prevent.

### rule: dedupe-on-id-and-geometry
A `cod_imovel` appearing more than once with identical geometry is
deduplicated on (id, geometry). Duplicates are counted and reported, never
silently collapsed.
- questions: q05, q31.
- provenance: PR #19 (issue #18) — defects were injected into the list so
  this rule could be measured at all.

### rule: centroid-resolves-by-containment
A row carrying a point where a polygon is expected resolves to the CAR parcel
that contains the point, and is marked `centroid_resolved`. If the point lands
in no parcel or in several, the row is unresolvable — reported with a count,
never guessed, never dropped.
- questions: q05, q31.
- provenance: PR #19.

### rule: idless-polygon-resolves-by-containment
A polygon with no id resolves by geometric match against CAR using the
single-parcel containment test from the matching policy, and is marked
`geometry_resolved`.
- questions: q05, q31.
- provenance: PR #19.

### rule: axis-flip-repair
A geometry outside Brazil as given, but inside Brazil once latitude and
longitude are exchanged, has swapped axes: repair by exchanging them back,
resolve as normal, and mark `axis_repaired`. Outside Brazil under both
orderings is not an axis flip; it is unresolvable. `axis_repaired` is its own
bucket because a coordinate-order fault is not a missing identifier.
- questions: q31.
- provenance: PR #19; measured separately because the oracle once shipped the
  same class of bug.

### rule: reconciliation-identity
Every input row lands in exactly one of six buckets, and they sum to the
arrival count:
`resolved_clean + centroid_resolved + geometry_resolved + axis_repaired +
duplicates_removed + unresolvable = input_rows`.
`input_rows` counts rows as they arrived, before deduplication.
- questions: q31.
- provenance: `policies/INPUTS.md`, graded since PR #19.

---

## 5. Field–parcel matching

### rule: inclusion-tests
A field is included if two-thirds or more of its area lies inside the listed
parcels, measured either way — a field passes if **either** test holds:
1. Single-parcel: max over listed parcels of
   `area(field ∩ parcel) / area(field)` ≥ `contain_threshold`.
2. Aggregate: `area(field ∩ union of all listed parcels, buffered)` /
   `area(field)` ≥ `contain_threshold`.
Areas are compared as ratios in EPSG:4326, so the CRS cancels.
- questions: q09–q15 and everything downstream.
- provenance: Tristan Grupp (WRI), 2026-07-17, verbatim: *"match them if they
  are 2/3 contained within the cadastral boundaries individually OR if
  cadastral boundaries touch or are direct neighbors … If the sum of
  crossovers for a field between cadasters in the list is greater than or
  equal to 2/3, then include."* Runs without this rule independently invent a
  0.5 majority threshold, which moves 16 of 31 questions.

### rule: matching-parameters
`contain_threshold` = 0.667. `neighbor_gap_tolerance_m` = 25 — the outward
buffer applied to parcels before the aggregate union, closing CAR sliver gaps
between neighbours; converted to degrees as metres / 111,320. The union is
dissolved before intersecting, so overlapping parcels are never
double-counted.
- questions: q09–q12.
- provenance: `policies/MATCHING.md`.

### rule: primary-cadaster
Every matched field carries exactly one primary cadaster: the listed parcel
with the largest containment fraction. Fractions within
`primary_tie_tolerance` = 1e-9 of the maximum are tied, and the tie goes to
the lowest `cod_imovel` by ordinary string comparison. The reported
`max_single_frac` stays the true maximum.
- questions: q13, q20, q23, q24, q26–q30, workflow.
- provenance: PR #13 (issue #12) — 54 of 793 matched fields tie bit-for-bit;
  before the rule existed, a run scored full marks only by writing the same
  scan-order idiom as the oracle. The 1e-9 tolerance is measured, not chosen:
  it sits between method noise (≤1.7e-10) and the closest genuine gap
  (1.6e-8).

### rule: excluded-fields-are-counted
Fields failing both tests are dropped and counted, never silently. A field
"intersects" a listed parcel when the intersection has positive area — a
boundary touch does not count.
- questions: q12.
- provenance: exclusion discipline from `MATCHING.md`; the positive-area
  reading was previously oracle-only (`match_excluded.sql.tmpl`), promoted to
  a rule here.

### rule: envelope
The stage-3 envelope is the bounding envelope of the dissolved union of the
listed parcels — one box, not a union of per-parcel boxes. A field belongs to
it if the geometries intersect.
- questions: q08.
- provenance: previously oracle-only (`fields_extract.sql.tmpl`), promoted to
  a rule here.

### rule: duplicate-car-ids
CAR carries 8,453,554 rows under 8,437,940 distinct ids. Each row counts as
its own parcel in the single-parcel test; the aggregate union dissolves them
like any other overlap. A listed id absent from CAR contributes no geometry
and is reported missing.
- questions: q03, q05, q07.
- provenance: `policies/MATCHING.md`.

---

## 6. Deforestation measurement

### rule: post-2020-loss
Post-2020 loss is the 2021–2024 era band (`deforestarea2124`), alone. A field
carries post-2020 loss when that band is greater than zero — no minimum area.
- questions: q15, q17–q20, q23, q24, q26–q30, workflow.
- provenance: the EUDR cut-off is 31 Dec 2020 and the prior band ends at
  2020. The zero threshold was previously oracle-only; promoted to a rule
  here. No stored run has ever chosen differently.

### rule: loss-from-era-bands-only
Total loss is the sum of the five era bands. Neither `hansen_covered_area`
nor `hansen_loss_area` is used.
- questions: q21, q22.
- provenance: `questions.yaml` header; the `hansen_loss_area` exclusion was
  previously oracle-only.

### rule: era-bands
The five Hansen eras are 2001-2004, 2005-2009, 2010-2014, 2015-2020,
2021-2024, labelled as year ranges in exactly that form.
- questions: q21.
- provenance: Trazo3 column structure.

---

## 7. EUDR scope and routing

Regulation (EU) 2023/1115 Annex I covers cattle, cocoa, coffee, oil palm,
rubber, soya, and wood. Scope (is the crop covered?) and routing (where would
it deliver?) are two different questions and two tables — sugarcane is
outside Annex I but still delivers to a mill.

### rule: scope-table
| `mbmode24` | Class | Annex I commodity | In scope | Caveat |
|---|---|---|---|---|
| 15 | Pasture | cattle | yes | |
| 39 | Soybean | soya | yes | |
| 35 | Palm Oil | oil palm | yes | `mixed_detection_quality` |
| 46 | Coffee | coffee | yes | `mixed_detection_quality` |
| 21 | Mosaic of Uses | cattle | yes | `assumed_pasture` |
| 9 | Forest Plantation | wood | **no** | `unreliable_detection` |
| 18 | Agriculture | | no | |
| 20 | Sugarcane | | no | |
| 40 | Rice | | no | |
| 41 | Other Temporary Crops | | no | |
| 47 | Citrus | | no | |
| 48 | Other Perennial | | no | |
| 62 | Cotton | | no | |

Commodity and caveat values are these exact lowercase tokens; a class outside
the table is out of scope with an empty commodity and caveat. Cocoa and rubber
are omitted: no dedicated MapBiomas class, not materially present in Brazil.
- equivalence: case-insensitive, like all strings.
- questions: q16–q20, q23–q25, q30, workflow.
- provenance: `policies/EUDR_CROPS.md`; TG 2026-07-24 for cocoa/rubber. Runs
  without this table reconstruct the class→commodity mapping correctly from
  class names but invent free-prose caveats, which is the arm working as
  designed.

### rule: two-reasons-to-exclude
Classes 18, 20, 40, 41, 47, 48, 62 are out of scope because the regulation
does not cover them. Class 9 (Forest Plantation) is out because the sensor
cannot see it — planted timber is a genuine Annex I commodity, but Trazo does
not detect it reliably. Any output excluding forest plantation must say it was
excluded on detection grounds.
- questions: q16, q18.
- provenance: `policies/EUDR_CROPS.md` — conflating the two "produces a
  defensible-looking wrong answer."

### rule: routing-table
| `mbmode24` | Class | Delivery tiers |
|---|---|---|
| 15 | Pasture | `slaughter_point` |
| 21 | Mosaic of Uses | `slaughter_point` |
| 20 | Sugarcane | `mill_point` |
| 39 | Soybean | `intake_point` |
| 41 | Other Temporary Crops | `intake_point` |
| 18 | Agriculture | `intake_point`, `mill_point` |
| 35 | Palm Oil | none — no infrastructure in the product |
| 46 | Coffee | none — no infrastructure in the product |

`gravity_catchment` follows `intake_point` wherever it survives.
`membership_muni` is never routed away. Coffee and palm keep no delivery
candidate — the facilities product has no tier for them, and substituting a
grain silo would name an organisation with no relationship to that farm; the
gap is reported as `no_match`. A class absent from the **scope** table keeps
every tier (the unknown-crop case) — see open question 1 for the wording
conflict this sentence resolves.
- questions: q24–q26, q28–q30, workflow.
- provenance: `policies/EUDR_CROPS.md`, `policies/COOPS.md`.

### rule: dominant-class
Where a parcel carries fields of several classes, its dominant class is the
one covering the most hectares — not the most fields — with ties broken by
the lower `mbmode24` code. All matched fields on the parcel count, in scope
or not.
- questions: q24, q26, q28–q30, workflow.
- provenance: `policies/EUDR_CROPS.md`. Never varied in 19 stored runs; the
  source column's own documentation pins it.

### rule: flagged-set
A cadaster is flagged non-compliant when at least one of its matched,
in-scope fields carries post-2020 loss. The flagged set is all such cadasters,
not the top ten from q20.
- questions: q24, q26–q30, workflow.
- provenance: `prompts/task.md` ("post-2020 loss on an EUDR-relevant crop");
  see open question 2 for the dependency-graph mismatch.

---

## 8. Cooperative and buyer candidates

Each flagged cadaster gets a ranked set of candidates, never a single forced
assignment. Two families of evidence, both surfaced: who has the relationship
(matched by município) and where they would deliver (matched by distance,
município-agnostic).

### rule: tiers
| Tier | Answers | Entity kind | Basis | Match rule |
|---|---|---|---|---|
| `membership_muni` | relationship | cooperative | observed | município code equals the cadaster's |
| `intake_point` | delivery (grain) | cooperative | observed | distance ≤ radius |
| `slaughter_point` | delivery (cattle) | buyer | observed | distance ≤ radius |
| `mill_point` | delivery (sugar cane) | mill | observed | distance ≤ radius |
| `gravity_catchment` | delivery | cooperative | modelled | cadaster centroid inside the catchment polygon |

`mill_point` ranks below the other delivery tiers: mills sit at their town's
centroid, so the distance is town-to-cadaster. Every mill candidate carries
the flag `town_centroid`, and mills are excluded from the proximity override.
- questions: q02, q24–q30, workflow.
- provenance: `policies/COOPS.md`.

### rule: ranking
Order: `nearest_by_far` promotions first, then `membership_muni` →
`intake_point`/`slaughter_point` → `mill_point` → `gravity_catchment`, then
`distance_km` ascending with catchment candidates (no distance) last, then
`evidence_value` descending, then the lower `entity_id`. Distances carry no
tie tolerance.
- questions: q26, q28, q30, workflow.
- provenance: `policies/COOPS.md` — 45 Goiás cadasters have two facilities at
  identical distances; without the `entity_id` rule those rankings fall to
  scan order.

### rule: proximity-override
A delivery facility closer than `proximity_override_km` = 10 is promoted to
the top and flagged `nearest_by_far`; the relationship candidate stays
visible below it. Mills do not participate.
- questions: q28–q30.
- provenance: `policies/COOPS.md`.

### rule: widening
If fewer than `min_candidates` = 2 delivery candidates fall within
`intake_km` = 100 km, widen the radius toward `intake_km_ceiling` = 300 km
until the count is met, and mark `widened`. When even the ceiling yields
none, mark `no_match` — reported, never hidden.
- questions: q28, q29.
- provenance: `policies/COOPS.md` — the nearest cooperative silo to Jussara
  is 58 km, so widening is expected, not failure. See open question 3 for
  whether the radius is computed before or after crop routing.

### rule: candidate-parameters
`max_candidates` = 5, applied after ranking and before reconciliation counts,
so `n_candidates` never exceeds 5. `min_capacity_t` = 1000 gates
`intake_point` only. Distances are computed in EPSG:5880, from the cadastral
parcel's centroid to the facility point — every threshold reads against the
centroid figure.
- questions: q26, q28–q30, workflow.
- provenance: `policies/COOPS.md`.

### rule: gap-markers
When a flagged cadaster's commodity routes to no tier, `routed_tier` is the
token `no_tier`; a class absent from the scope table routes as `unknown`.
When a class routes to several tiers, they are joined with `|` in tier-name
order. Empty cells (no commodity, no caveat, no distance) are empty strings.
- equivalence: an empty golden cell should also accept `NULL`, `none`, and
  `n/a` — adopted here, previously ungraded; see open question 4.
- questions: q16, q24, q25, q30, workflow.
- provenance: previously oracle-only (`coop_routing.sql.tmpl`,
  `portfolio.sql.tmpl`); promoted to rules here. Three stored runs produced
  three different caveat strings for one concept, and one run's comma-joined
  tier list failed two questions.

---

## 9. The questions

31 questions in 6 stages. Later stages depend on earlier answers; the grader
reports accuracy both raw and conditional on dependencies being correct.
`rows: n` means the answer has exactly n rows; `rows: data` means the count
is data-determined.

### Stage 1 — catalog discovery

**q01** — How many of the trazofields catalog's child entries are STAC
Collections, and which collection id does the metadata recommend?
`n_collections` (integer), `recommended_id` (string). rows: 1.

**q02** — Row count per tier in the unified facilities layer.
`tier` (string), `n` (integer). rows: 5.

**q03** — Total rows and distinct `cod_imovel` in the CAR file.
`n_rows`, `n_distinct_ids` (integers). rows: 1.

**q04** — Row count and geometry EPSG code of the Trazo3 Goiás file.
`n_rows`, `epsg` (integers). rows: 1.

### Stage 2 — cadaster resolution

**q05** — Resolve the list per section 4. Distinct parcels resolved, found in
CAR, and missing. Count parcels, not rows: an id-less row counts under the
parcel it resolves to; a duplicate counts once.
`list_ids`, `found_in_car`, `missing` (integers). rows: 1. Depends: q03.

**q06** — Resolved parcels per município.
`municipio` (string), `n_parcels` (integer). rows: data. Depends: q05.

**q07** — Listed ids with more than one CAR row, and resolved parcels under
the 1-hectare smallholding threshold (`num_area < 1`).
`n_duplicate_ids`, `n_parcels_under_1ha` (integers). rows: 1. Depends: q05.

**q31** — Account for every input row across the six buckets of the
reconciliation identity (section 4).
`input_rows`, `resolved_clean`, `centroid_resolved`, `geometry_resolved`,
`axis_repaired`, `duplicates_removed`, `unresolvable` (integers). rows: 1.
Depends: q05. (Numbered 31 because ids are sequential and renumbering would
invalidate stored goldens; it belongs to stage 2.)

### Stage 3 — field–cadaster matching (geometry-graded except q13)

**q08** — Fields intersecting the bounding envelope of the listed parcels.
`n_fields_in_envelope` (integer). rows: 1. Depends: q05.

**q09** — Fields included by the matching policy.
`matched_fields` (integer). rows: 1. Depends: q05.

**q10** — Matched fields split by admitting rule: passing the single-parcel
rule, and admitted only by the aggregate rule.
`by_single_parcel_rule`, `by_aggregate_rule_only` (integers). rows: 1.
Depends: q09. (The first column counts every field passing the single test,
including those that also pass the aggregate test.)

**q11** — Containment-fraction bounds: minimum and mean single-parcel
fraction, mean union fraction.
`min_single_frac`, `avg_single_frac`, `avg_union_frac` (floats). rows: 1.
Depends: q09.

**q12** — Fields intersecting a listed parcel (positive-area) but failing
both tests.
`n_excluded` (integer). rows: 1. Depends: q09.

**q13** — Ten cadasters with the most matched fields, count descending, ties
by `cod_imovel` ascending.
`cod_imovel` (string), `n_fields` (integer). rows: 10. Depends: q09.

**q14** — Total matched-field area in hectares.
`matched_field_ha` (float). rows: 1. Depends: q09.

### Stage 4 — EUDR deforestation

**q15** — List-level headline: parcels, matched fields, matched hectares,
post-2020 loss hectares, fields with post-2020 loss.
`n_parcels`, `n_fields` (integers), `matched_field_ha`, `post2020_loss_ha`
(floats), `fields_with_post2020_loss` (integer). rows: 1. Depends: q09.
Geometry-graded.

**q16** — Classify every MapBiomas class present on matched fields per the
scope table: commodity (empty if none), in scope, caveat (empty when none).
`mbmode24` (integer), `mb_class` (string), `annex1_commodity` (string),
`in_scope` (boolean), `caveat` (string). rows: data. Depends: q09.

**q17** — Post-2020 loss on in-scope crops by commodity.
`annex1_commodity` (string), `n_fields` (integer), `post2020_loss_ha`
(float). rows: data. Depends: q16.

**q18** — The same breakdown for classes judged not in scope, by class, so
the exclusion is auditable.
`mb_class` (string), `n_fields` (integer), `post2020_loss_ha` (float).
rows: data. Depends: q16.

**q19** — Total matched-field area by in-scope commodity, and the share of
that area carrying post-2020 loss (0–1).
`annex1_commodity` (string), `total_ha`, `loss_share` (floats). rows: data.
Depends: q16. Geometry-graded.

**q20** — Ten cadasters with the most post-2020 loss on in-scope crops, loss
descending, ties by `cod_imovel` ascending.
`cod_imovel`, `municipio` (strings), `post2020_loss_ha` (float),
`n_fields_post2020_loss` (integer). rows: 10. Depends: q16.

**q21** — Loss by era band: the five eras with cleared hectares and percent
of total loss.
`era` (string, e.g. `2001-2004`), `cleared_ha`, `pct_of_loss` (floats).
rows: 5. Depends: q09.

**q22** — Distribution of dominant loss year across matched fields with
loss: field count and cleared hectares per year.
`loss_year`, `n_fields` (integers), `cleared_ha` (float). rows: data.
Depends: q09. (See open question 5 on what `cleared_ha` sums.)

**q23** — Ten worst in-scope plots by post-2020 loss, ties by field id
ascending. Grades strict (see section 2).
`field_id` (integer), `cod_imovel`, `annex1_commodity` (strings),
`field_area_ha`, `post2020_loss_ha` (floats). rows: 10. Depends: q16.

### Stage 5 — commodity infrastructure

**q24** — For each flagged cadaster: dominant class, its commodity, and the
delivery tier(s) it routes to, or a gap marker.
`cod_imovel` (string), `dominant_mb` (integer), `annex1_commodity`,
`routed_tier` (strings). rows: data. Depends: q16, q20.

**q25** — For each in-scope commodity on the list: its delivery tier, or a
no-tier marker, and whether the product covers it (`covered` / `none`).
`annex1_commodity`, `tier`, `coverage` (strings). rows: data. Depends: q16.

**q26** — Nearest delivery facility for each flagged cadaster under the
routing rule, distance in a metric CRS. Cadasters whose commodity has no
delivery tier have no row.
`cod_imovel`, `entity_id`, `tier` (strings), `distance_km` (float).
rows: data. Depends: q24. Geometry-graded.

**q27** — Membership-tier candidate per flagged cadaster: entity id (the
município code) and evidence (cooperative-member count).
`cod_imovel`, `entity_id` (strings), `evidence` (float). rows: data.
Depends: q20.

**q28** — Per-cadaster candidate reconciliation: total candidates,
relationship candidates, delivery candidates, and flags for widened,
no-match, and proximity override. (`n_candidates` includes catchment
candidates, so it can exceed relationship + delivery.)
`cod_imovel` (string), six integers. rows: data. Depends: q24.

**q29** — Across the flagged list: cadasters that required widening, ended
with no delivery match, or triggered the proximity override.
`n_widened`, `n_no_match`, `n_nearest_by_far` (integers). rows: 1.
Depends: q24.

### Stage 6 — portfolio decision

**q30** — For every flagged cadaster, the top-ranked contact: commodity,
post-2020 loss, and the candidate's id, kind, tier, basis, and distance
(empty for non-distance tiers).
`cod_imovel`, `annex1_commodity` (strings), `post2020_loss_ha` (float),
`entity_id`, `entity_kind`, `tier`, `basis` (strings), `distance_km`
(float). rows: data. Depends: q24, q26, q27, q28. Geometry-graded.

`workflow.csv` repeats q30's content in the fixed eight-column form from
section 1. It is checked for cross-run consistency, not graded.

---

## Open questions

Points where the oracle, the policies, and the question set currently
disagree, found by a three-way audit (spec, oracle SQL, stored transcripts) on
2026-08-02. Each needs a ruling; none is resolved by this draft. Rulings land
as edits to the rules above plus conformance tests.

1. **Routing fallback table.** `EUDR_CROPS.md` says a class absent from *the
   routing table* keeps every tier; the oracle keys the fallback off the
   *scope* table, so classes 9, 40, 47, 48, 62 keep nothing. The rule above
   states the oracle's behavior; the policy sentence contradicts it. Latent on
   the Goiás list.
2. **Flagged-set provenance.** q24 and q27 declare `depends_on: q20` (the top
   ten), but the flagged set is all non-compliant cadasters. The dependency
   graph should point at the flag definition, not the top-ten view.
3. **Widening pool.** The oracle computes the widening radius over all three
   delivery tiers, then deletes disallowed tiers; a plain reading of COOPS.md
   widens over allowed tiers only. Either ruling changes current goldens.
4. **Empty-cell equivalence.** Adopted in the gap-markers rule above but not
   yet implemented: the comparator should accept `NULL`/`none`/`n/a` against
   an empty golden cell, the same move as case folding.
5. **q22 `cleared_ha`.** The question says "cleared hectares for that year";
   the oracle sums all-era loss for fields whose *dominant* year is that
   year. The question text or the oracle should change.
6. **Aggregate-only fields with no intersecting parcel.** The 25 m buffer
   makes it possible to pass the aggregate test while touching no parcel; the
   oracle drops such fields, MATCHING.md implies they are included with a
   primary. Not exercised on the Goiás list.
7. **Point-with-id rows.** INPUTS.md's defect table implies a point carrying
   an id is `centroid_resolved`; the oracle marks any id-bearing row
   `resolved_clean` before looking at its geometry. Latent for the `geometry`
   and `split` encodings.
8. **Brazil extent.** "Outside Brazil" for axis-flip detection is a bounding
   box (-75, -34, -28, 6) in the oracle and undefined in the policy.
