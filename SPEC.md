# EUDR benchmark specification

Status: draft for review.

This document defines the benchmark. It replaces scattered documents in `prompts/` and `policies/`, consolidates output contracts from `fixtures/questions.yaml`, and documents the comparison rules in `harness/grade.py`. The agent receives a generated view of this document with rule prose only — the "Questions affected" lines, the "Provenance" lines, and the open-questions list are stripped. The `spec_fingerprint` is the hash of that rendered view.

Each rule below has a stable id and one or two sentences of prose. "Questions affected" lists which questions depend on the rule. "Provenance" says where the rule came from and why it exists. Contested or undocumented behaviors appear under [Open questions](#open-questions) at the end.

---

## 1. The task

The analysis starts with a list of Brazilian rural properties. Your job:

1. Resolve each property to cadastral parcels
2. Match agricultural field boundaries to those parcels
3. Measure forest loss after 2020 on crops covered by the EUDR
4. Find the cooperative or buyer contact for each non-compliant property

Deliver one CSV per question in section 9 (q01.csv through q31.csv) and a workflow file. Each answer must have the correct columns by meaning and type; you choose the column names. The workflow file has one row per flagged property with these eight columns: `cod_imovel`, `annex1_commodity`, `post2020_loss_ha`, `top_contact_entity_id`, `entity_kind`, `tier`, `basis`, `distance_km`.

Questions build across six stages. Later answers depend on earlier results.

Read all catalog metadata first. Do all work in the foreground; do not background slow queries — wait for them. Write each answer to disk as soon as it exists. Do not hold results to the end of the session — they will be lost on a timeout.

---

## 2. Answer comparison

These rules apply to every answer. They define what "the same answer" means.

### rule: rows-are-a-multiset
Rows compare as an unordered set. Row order never matters, except where a question explicitly asks for a ranking—there the ranking is expressed through that question's rank rule, not through file order.

Provenance: initial design (PR #5).

### rule: column-names-are-free
Choose your column names and order. The grader matches columns by meaning and type only.

Provenance: initial design (PR #5).

### rule: quantize-before-compare
Round numeric answers to match the golden value's decimal places before comparison. Output precision is not tested.

Provenance: issue #6. A golden rounded to one decimal failed a correct answer at small magnitudes. The fix went into the comparator, not the prompt.

### rule: numeric-tolerance
Numbers compare at 0.1% relative error, with a floor of 0.000000001 absolute. Questions marked `geometry` have values that shift depending on your method. For these, allow 1% relative error on decimals. On counts (integers), allow the larger of: 2 units difference, or 1% of the golden value. This accounts for defensible differences in how you count features. A value that fails tolerance but falls within ten times the tolerance counts as a near miss rather than a complete miss.

Geometry-graded questions: q08–q12, q14, q15, q19, q26, q30.

Provenance: initial design (PR #5). The near-miss split was added for triage.

### rule: q23-grades-strict
q23 grades strictly. Do not allow any tolerance on field_id even though it's an integer. If the correct answer is field 100 and you answer field 101, that's wrong. Adjacent plot ids are adjacent by design—allowing "close enough" would credit naming the wrong farm entirely.

Provenance: PR #5, via the questions.yaml header.

### rule: strings-fold-case
Compare strings case-insensitively. No golden value is distinguished by case alone.

Provenance: PR #24 (issue #20). An arm run without the scope section lost five points to capitalization alone.

### rule: booleans-are-liberal
Accept `true`, `True`, `TRUE`, `yes`, `t`, `1` (and their negatives) as booleans. A bare `1` or `0` counts as boolean only when compared against a word. Never silently match a count column against a flag.

Provenance: issue #6. `True` against `true` failed a correct answer.

### rule: area-and-distance-method-is-free
No specific method for computing areas or distances is required. Geodesic area, equal-area projection, and Brazil Polyconic all agree within a percent. The geometry tolerance absorbs this spread.

This rule applies to all hectare and kilometer columns except q23.

Provenance: the questions.yaml header. One stored run used planar EPSG:5880 area and lost only q23, as designed.

---

## 3. Data sources

### rule: source-files
Query three GeoParquet datasets remotely with DuckDB (spatial and httpfs extensions):

- Field boundaries (Trazo3, Goiás 2024): `https://data.source.coop/wri-data-lab/trazofields/trazo3-fields/trazo3_brazil_goias_2024.parquet`
- Cadastral parcels (Brazil CAR): `https://data.source.coop/tristangruppwri/cadastral/Brazil_CAR_AREA_IMOVEL.parquet`
- Commodity infrastructure: `https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/facilities/BR_facilities.parquet`

Provenance: prompts/task.md.

### rule: coordinates-are-lon-lat
All geometry is WGS84 longitude-first. Set `geometry_always_xy = true` when loading. Loading with latitude-first axis order shrinks Goiás areas by a factor of about 1.5 and moves every distance.

Questions affected: q08–q15, q19, q22, q23, q26, q30, workflow.

Provenance: the oracle itself shipped this bug until 2026-07-26 (commit e606096) and penalized correct answers. Stated nowhere agent-visible before this document.

### rule: loss-bands-are-square-metres
Trazo3 deforestation columns are in square metres. Divide by 10,000 to get hectares.

Questions affected: q15, q17–q23, q30, workflow.

Provenance: trazofields catalog documentation. Previously stated only in an oracle comment.

---

## 4. Input list handling

Start from `lists/goias-sample.csv`. The list may arrive as CSV with ids and WKT geometry, as a separate geometry file, or as a CSV-plus-geometry pair joined on id. Detect the columns actually present. Never drop a row silently—an omitted property is an un-audited source area.

### rule: dedupe-on-id-and-geometry
A `cod_imovel` appearing more than once with identical geometry is one parcel. Count and report duplicates, never silently collapse them.

Questions affected: q05, q31.

Provenance: PR #19 (issue #18). Defects were injected into the list so this rule could be measured at all.

### rule: centroid-resolves-by-containment
A point resolves to the CAR parcel that contains it. Mark it `centroid_resolved`. If the point lands in no parcel or in several, report it as unresolvable.

Questions affected: q05, q31.

Provenance: PR #19.

### rule: idless-polygon-resolves-by-containment
A polygon with no id resolves by geometric match against CAR using the single-parcel containment test. Mark it `geometry_resolved`.

Questions affected: q05, q31.

Provenance: PR #19.

### rule: axis-flip-repair
A geometry outside Brazil as given, but inside Brazil when latitude and longitude are exchanged, has swapped axes. Repair by exchanging them back. Resolve as normal and mark `axis_repaired`. A geometry outside Brazil under both orderings is unresolvable, not an axis flip.

Questions affected: q31.

Provenance: PR #19. Measured as its own bucket because the oracle once shipped the same class of bug.

### rule: reconciliation-identity
Every input row lands in exactly one of six buckets. They sum to the arrival count:

`resolved_clean + centroid_resolved + geometry_resolved + axis_repaired + duplicates_removed + unresolvable = input_rows`

The count `input_rows` is rows as they arrived, before deduplication.

Questions affected: q31.

Provenance: policies/INPUTS.md. Graded since PR #19.

---

## 5. Field–parcel matching

### rule: inclusion-tests
A field is included if two-thirds or more of its area lies inside the listed parcels. A field passes if **either** test holds:

1. Single-parcel: the largest containment ratio is ≥ 0.667
2. Aggregate: total area inside all listed parcels (buffered 25 m) divided by field area is ≥ 0.667

Measure areas as ratios in EPSG:4326 so the CRS cancels.

Questions affected: q09–q15 and everything downstream.

Provenance: Tristan Grupp (WRI), 2026-07-17. Runs without this rule invent a 0.5 threshold instead, which moves 16 of 31 questions.

### rule: matching-parameters
Set `contain_threshold` = 0.667 and `neighbor_gap_tolerance_m` = 25. The 25 m buffer closes CAR sliver gaps between neighbors. Dissolve the buffered union before intersecting so overlapping parcels are never double-counted.

Questions affected: q09–q12.

Provenance: policies/MATCHING.md.

### rule: primary-cadaster
Each matched field has exactly one primary cadaster: the listed parcel with the largest containment fraction. If multiple parcels tie within 1e-9 of the maximum, break the tie by lowest `cod_imovel` (string comparison). Report the true maximum as `max_single_frac`.

Questions affected: q13, q20, q23, q24, q26–q30, workflow.

Provenance: PR #13 (issue #12). 54 of 793 matched fields tie exactly, and a run once scored full marks only by matching the oracle's scan order. The 1e-9 tolerance was measured: method noise is at most 1.7e-10 and the closest genuine gap is 1.6e-8.

### rule: excluded-fields-are-counted
Fields failing both tests are dropped and counted, never silently. A field "intersects" a listed parcel only when the intersection has positive area. A boundary touch does not count.

Questions affected: q12.

Provenance: policies/MATCHING.md. The positive-area reading was oracle-only before this document.

### rule: envelope
The stage-3 envelope is the bounding box of the union of the listed parcels. It is one box, not a per-parcel box. A field belongs to it if the geometries intersect.

Questions affected: q08.

Provenance: oracle-only before this document (fields_extract.sql.tmpl).

### rule: duplicate-car-ids
CAR carries 8,453,554 rows under 8,437,940 distinct ids. Each row is its own parcel in the single-parcel test. The aggregate union dissolves them like any overlap. A listed id absent from CAR contributes no geometry and is reported missing.

Questions affected: q03, q05, q07.

Provenance: policies/MATCHING.md.

---

## 6. Deforestation measurement

### rule: post-2020-loss
Post-2020 loss is the 2021–2024 era band alone. A field carries post-2020 loss when that band is greater than zero. There is no minimum area threshold.

Questions affected: q15, q17–q20, q23, q24, q26–q30, workflow.

Provenance: the EUDR cut-off is 31 December 2020 and the prior band ends at 2020. The zero threshold was oracle-only before this document.

### rule: loss-from-era-bands-only
Total loss is the sum of the five era bands. Do not use `hansen_covered_area` or `hansen_loss_area`.

Questions affected: q21, q22.

Provenance: the questions.yaml header. The hansen_loss_area exclusion was oracle-only before this document.

### rule: era-bands
The five Hansen eras are 2001-2004, 2005-2009, 2010-2014, 2015-2020, 2021-2024. Label them as year ranges in exactly that form.

Questions affected: q21.

Provenance: Trazo3 column structure.

---

## 7. EUDR scope and routing

Regulation (EU) 2023/1115 Annex I covers cattle, cocoa, coffee, oil palm, rubber, soya, and wood. Scope and routing are separate: one table says which crops are covered, another says where they deliver.

### rule: scope-table
Map MapBiomas classes to EUDR commodities:

| mbmode24 | Class | Commodity | Covered | Caveat |
|---|---|---|---|---|
| 15 | Pasture | cattle | yes | |
| 39 | Soybean | soya | yes | |
| 35 | Palm Oil | oil palm | yes | mixed_detection_quality |
| 46 | Coffee | coffee | yes | mixed_detection_quality |
| 21 | Mosaic of Uses | cattle | yes | assumed_pasture |
| 9 | Forest Plantation | wood | no | unreliable_detection |
| 18 | Agriculture | | no | |
| 20 | Sugarcane | | no | |
| 40 | Rice | | no | |
| 41 | Other Temporary Crops | | no | |
| 47 | Citrus | | no | |
| 48 | Other Perennial | | no | |
| 62 | Cotton | | no | |

Commodity and caveat values are lowercase tokens; an empty cell means an empty value. A class outside the table is out of scope with an empty commodity and caveat. Cocoa and rubber are omitted: no dedicated MapBiomas class, not materially present in Brazil.

Strings compare case-insensitively.

Questions affected: q16–q20, q23–q25, q30, workflow.

Provenance: policies/EUDR_CROPS.md. Cocoa and rubber omitted per TG, 2026-07-24.

### rule: two-reasons-to-exclude
Classes 18, 20, 40, 41, 47, 48, 62 are out of scope because the regulation does not cover them. Class 9 (Forest Plantation) is out because the sensor cannot detect it reliably. Planted timber is a genuine Annex I commodity, but Trazo does not detect it. Any output excluding forest plantation must say it was excluded on detection grounds.

Questions affected: q16, q18.

Provenance: policies/EUDR_CROPS.md.

### rule: routing-table
Map MapBiomas classes to delivery tiers:

| mbmode24 | Class | Delivery tiers |
|---|---|---|
| 15 | Pasture | slaughter_point |
| 21 | Mosaic of Uses | slaughter_point |
| 20 | Sugarcane | mill_point |
| 39 | Soybean | intake_point |
| 41 | Other Temporary Crops | intake_point |
| 18 | Agriculture | intake_point, mill_point |
| 35 | Palm Oil | none |
| 46 | Coffee | none |

Coffee and palm have no facilities in the product, so there are no delivery candidates. Mark these as `no_match` rather than substituting a grain silo. `gravity_catchment` follows `intake_point` wherever it exists. `membership_muni` is never routed away.

A class absent from the scope table keeps every tier (the unknown-crop case).

Questions affected: q24–q26, q28–q30, workflow.

Provenance: policies/EUDR_CROPS.md and policies/COOPS.md.

### rule: dominant-class
Where a parcel holds fields of several classes, its dominant class is the one covering the most hectares. Ties break by lowest `mbmode24` code. All matched fields on the parcel count, in scope or not.

Questions affected: q24, q26, q28–q30, workflow.

Provenance: policies/EUDR_CROPS.md. This rule never varied across 19 stored runs.

### rule: flagged-set
A parcel is flagged non-compliant when at least one of its matched, in-scope fields carries post-2020 loss. The flagged set is all such parcels.

Questions affected: q24, q26–q30, workflow.

Provenance: prompts/task.md.

---

## 8. Cooperative and buyer candidates

Each flagged parcel gets a ranked set of candidates, never a single forced assignment. Two families of evidence surface: who has the relationship (matched by município) and where they deliver (matched by distance, município-agnostic).

### rule: tiers
| Tier | Answers | Entity kind | Basis | Match rule |
|---|---|---|---|---|
| membership_muni | relationship | cooperative | observed | município code equals the parcel's |
| intake_point | delivery (grain) | cooperative | observed | distance ≤ radius |
| slaughter_point | delivery (cattle) | buyer | observed | distance ≤ radius |
| mill_point | delivery (sugar cane) | mill | observed | distance ≤ radius |
| gravity_catchment | delivery | cooperative | modelled | parcel centroid inside the catchment polygon |

Mills sit at their town's centroid, so the distance is town-to-parcel. Every mill candidate carries the flag `town_centroid`. Mills are excluded from the proximity override.

Questions affected: q02, q24–q30, workflow.

Provenance: policies/COOPS.md.

### rule: ranking
Order candidates by: `nearest_by_far` promotions first. Then membership_muni, intake_point/slaughter_point, mill_point, gravity_catchment. Then distance ascending (gravity_catchment has no distance). Then evidence_value descending. Then lowest entity_id. Distances carry no tie tolerance.

Questions affected: q26, q28, q30, workflow.

Provenance: policies/COOPS.md. 45 Goias parcels have two facilities at identical distances, so the entity_id tie-break fires regularly.

### rule: proximity-override
A delivery facility closer than 10 km is promoted to the top and flagged `nearest_by_far`. The relationship candidate stays visible below it. Mills do not participate.

Questions affected: q28–q30.

Provenance: policies/COOPS.md.

### rule: widening
If fewer than 2 delivery candidates fall within 100 km, widen the radius toward 300 km until the count is met. Mark `widened`. When even 300 km yields none, mark `no_match` and report it.

Questions affected: q28, q29.

Provenance: policies/COOPS.md. The nearest cooperative silo to Jussara is 58 km, so widening is normal, not failure.

### rule: candidate-parameters
Cap at 5 candidates after ranking and before reconciliation. `max_candidates` = 5, `min_capacity_t` = 1000 (gates intake_point only). Compute distances in EPSG:5880 from the parcel's centroid to the facility point.

Questions affected: q26, q28–q30, workflow.

Provenance: policies/COOPS.md.

### rule: gap-markers
When a flagged parcel's commodity routes to no tier, set `routed_tier` to `no_tier`. An unknown class routes as `unknown`. When a class routes to several tiers, join them with `|` in tier-name order. Empty cells are empty strings, or accept `NULL`, `none`, `n/a`.

Questions affected: q16, q24, q25, q30, workflow.

Provenance: oracle-only before this document (coop_routing.sql.tmpl, portfolio.sql.tmpl).

---

## 9. The questions

31 questions across 6 stages. Later stages depend on earlier answers. The grader reports raw accuracy and conditional accuracy when dependencies are correct.

### Stage 1 — catalog discovery

**q01** — How many trazofields child entries are STAC Collections? Which collection id does the metadata recommend?

Columns: `n_collections` (integer), `recommended_id` (string). Rows: 1.

**q02** — Row count per tier in the unified facilities layer.

Columns: `tier` (string), `n` (integer). Rows: 5.

**q03** — Total rows and distinct `cod_imovel` in CAR.

Columns: `n_rows`, `n_distinct_ids` (integers). Rows: 1.

**q04** — Row count and EPSG code of the Trazo3 Goiás file.

Columns: `n_rows`, `epsg` (integers). Rows: 1.

### Stage 2 — cadaster resolution

**q05** — Resolve the list per section 4. How many distinct parcels resolved, found in CAR, and missing?

Count parcels, not rows. An id-less row counts under the parcel it resolves to. A duplicate counts once.

Columns: `list_ids`, `found_in_car`, `missing` (integers). Rows: 1. Depends: q03.

**q06** — Resolved parcels per município.

Columns: `municipio` (string), `n_parcels` (integer). Rows: data. Depends: q05.

**q07** — Listed ids with more than one CAR row, and resolved parcels under 1 hectare (CAR `num_area` < 1).

Columns: `n_duplicate_ids`, `n_parcels_under_1ha` (integers). Rows: 1. Depends: q05.

**q31** — Account for every input row across the six buckets of the reconciliation identity (section 4).

Columns: `input_rows`, `resolved_clean`, `centroid_resolved`, `geometry_resolved`, `axis_repaired`, `duplicates_removed`, `unresolvable` (integers). Rows: 1. Depends: q05. (Numbered 31 because ids are sequential; renumbering would invalidate stored goldens.)

### Stage 3 — field–cadaster matching

Except q13, these are geometry-graded.

**q08** — Fields intersecting the bounding envelope of the listed parcels.

Columns: `n_fields_in_envelope` (integer). Rows: 1. Depends: q05.

**q09** — Fields included by the matching policy.

Columns: `matched_fields` (integer). Rows: 1. Depends: q05.

**q10** — Matched fields split by which rule admitted them: passing single-parcel alone, or admitted only by aggregate.

The first column counts every field passing the single test, including those that also pass aggregate.

Columns: `by_single_parcel_rule`, `by_aggregate_rule_only` (integers). Rows: 1. Depends: q09.

**q11** — Containment-fraction bounds: minimum and mean single-parcel fraction, mean union fraction.

Columns: `min_single_frac`, `avg_single_frac`, `avg_union_frac` (floats). Rows: 1. Depends: q09.

**q12** — Fields intersecting a listed parcel with positive area but failing both tests.

Columns: `n_excluded` (integer). Rows: 1. Depends: q09.

**q13** — Ten cadasters with the most matched fields. Count descending, ties by `cod_imovel` ascending.

Columns: `cod_imovel` (string), `n_fields` (integer). Rows: 10. Depends: q09.

**q14** — Total matched-field area in hectares.

Columns: `matched_field_ha` (float). Rows: 1. Depends: q09.

### Stage 4 — EUDR deforestation

**q15** — List-level headline: parcel count, field count, matched hectares, post-2020 loss hectares, fields with post-2020 loss.

Columns: `n_parcels`, `n_fields` (integers), `matched_field_ha`, `post2020_loss_ha` (floats), `fields_with_post2020_loss` (integer). Rows: 1. Depends: q09. Geometry-graded.

**q16** — Classify every MapBiomas class on matched fields per the scope table: commodity (empty if none), in scope (yes/no), caveat (empty when none).

Columns: `mbmode24` (integer), `mb_class` (string), `annex1_commodity` (string), `in_scope` (boolean), `caveat` (string). Rows: data. Depends: q09.

**q17** — Post-2020 loss on in-scope crops by commodity.

Columns: `annex1_commodity` (string), `n_fields` (integer), `post2020_loss_ha` (float). Rows: data. Depends: q16.

**q18** — Post-2020 loss on out-of-scope classes, by class, so the exclusion is auditable.

Columns: `mb_class` (string), `n_fields` (integer), `post2020_loss_ha` (float). Rows: data. Depends: q16.

**q19** — Total matched-field area by in-scope commodity, and the share carrying post-2020 loss (0–1).

Columns: `annex1_commodity` (string), `total_ha`, `loss_share` (floats). Rows: data. Depends: q16. Geometry-graded.

**q20** — Ten cadasters with the most post-2020 loss on in-scope crops. Loss descending, ties by `cod_imovel` ascending.

Columns: `cod_imovel`, `municipio` (strings), `post2020_loss_ha` (float), `n_fields_post2020_loss` (integer). Rows: 10. Depends: q16.

**q21** — Loss by era band: the five eras with cleared hectares and percent of total loss.

Columns: `era` (string, e.g. `2001-2004`), `cleared_ha`, `pct_of_loss` (floats). Rows: 5. Depends: q09.

**q22** — Distribution of dominant loss year across fields with loss: field count and cleared hectares per year.

Columns: `loss_year`, `n_fields` (integers), `cleared_ha` (float). Rows: data. Depends: q09.

**q23** — Ten worst in-scope plots by post-2020 loss. Ties by field id ascending. Grades strict (see section 2).

Columns: `field_id` (integer), `cod_imovel`, `annex1_commodity` (strings), `field_area_ha`, `post2020_loss_ha` (floats). Rows: 10. Depends: q16.

### Stage 5 — commodity infrastructure

**q24** — For each flagged parcel: dominant class, commodity, and the tier(s) it routes to, or a gap marker.

Columns: `cod_imovel` (string), `dominant_mb` (integer), `annex1_commodity`, `routed_tier` (strings). Rows: data. Depends: q16, q20.

**q25** — For each in-scope commodity on the list: delivery tier, or a no-tier marker, and whether the product covers it.

Columns: `annex1_commodity`, `tier`, `coverage` (strings). Rows: data. Depends: q16.

**q26** — Nearest delivery facility per flagged parcel under the routing rule. Distance in a metric CRS. Parcels whose commodity has no tier have no row.

Columns: `cod_imovel`, `entity_id`, `tier` (strings), `distance_km` (float). Rows: data. Depends: q24. Geometry-graded.

**q27** — Membership-tier candidate per flagged parcel: entity id (the município code) and evidence (cooperative-member count).

Columns: `cod_imovel`, `entity_id` (strings), `evidence` (float). Rows: data. Depends: q20.

**q28** — Per-parcel candidate reconciliation: total candidates, relationship candidates, delivery candidates, and flags for widened, no-match, proximity override.

The candidate count includes catchment candidates, so it can exceed relationship + delivery.

Columns: `cod_imovel` (string), six integers. Rows: data. Depends: q24.

**q29** — Across the flagged list: parcels that required widening, ended with no match, or triggered proximity override.

Columns: `n_widened`, `n_no_match`, `n_nearest_by_far` (integers). Rows: 1. Depends: q24.

### Stage 6 — portfolio decision

**q30** — For every flagged parcel, the top-ranked contact: commodity, post-2020 loss, and the candidate's id, kind, tier, basis, and distance (empty for non-distance tiers).

Columns: `cod_imovel`, `annex1_commodity` (strings), `post2020_loss_ha` (float), `entity_id`, `entity_kind`, `tier`, `basis` (strings), `distance_km` (float). Rows: data. Depends: q24, q26, q27, q28. Geometry-graded.

`workflow.csv` repeats q30 in the fixed eight-column form from section 1. It is checked for consistency, not graded.

---

## Open questions

The answer key is generated by code (the "oracle" under `oracle/`). In eight places that code disagrees with this document, or the document is silent and the code decided alone. An audit found these on 2026-08-02. Each needs a human decision. Until then, the answer key keeps its current behavior.

1. **Which table drives the routing fallback?** The routing-table rule says: a class missing from the scope table keeps every delivery tier. The old policy text said: a class missing from the *routing* table keeps every tier. The answer key follows the scope-table reading. Under it, Rice, Citrus, Cotton, Other Perennial, and Forest Plantation get no delivery tiers at all; under the other reading they would get every tier. Decide which reading is intended. No answer changes today, because none of those classes appears on a flagged parcel in the Goiás list.

2. **q24 and q27 point at the wrong dependency.** Both are marked as depending on q20. But q20 is only the ten worst parcels, and these questions cover every flagged parcel. The dependency marker should point at the questions that define the flag. This is bookkeeping; no answers change.

3. **Widening counts facilities the crop cannot use.** To decide whether to widen the search radius, the answer key counts all delivery facilities within 100 km, of every type, and only afterwards removes the types the crop cannot deliver to. Read plainly, the widening rule counts only usable types. Concrete effect: a cattle parcel's radius can be set by a grain silo it is not allowed to use. Whichever reading wins, some current answers change.

4. **The empty-cell promise is not implemented.** The gap-markers rule says the grader accepts `NULL`, `none`, or `n/a` where the key has an empty cell. The grader does not do that yet. Implement it, or delete the promise from the rule.

5. **q22 does not measure what it says.** The question asks for "cleared hectares for that year." The answer key sums a field's loss across all years and files the total under the field's dominant loss year. So loss that happened in 2021 can be counted under 2019. Fix the question wording, or fix the key.

6. **A field can match while touching nothing.** The aggregate test buffers parcels outward by 25 m, so a field can pass it while touching no actual parcel. The answer key silently drops such fields; the matching rules imply they are included. No such field exists in the Goiás list, so nothing changes today.

7. **A point with an id is classified two ways.** Take an input row that has both an id and a point geometry. The input-handling rules read as: mark it `centroid_resolved`. The answer key marks any row with an id `resolved_clean` and never looks at its geometry. This only shows up in the `geometry` and `split` input encodings, which currently share the CSV answer key.

8. **"Outside Brazil" is undefined.** The axis-flip rule needs to know whether a coordinate is outside Brazil. The answer key uses a rectangle (75°W to 28°W, 34°S to 6°N); this document never defines it. A point in the Atlantic inside that rectangle counts as "in Brazil." Write the definition down.
