# Benchmark specification for deforestation reviews

**Draft for review.**

This document is the benchmark's single source of truth. It replaces the
documents that lived in `prompts/` and `policies/`. It also incorporates the
output contracts from `fixtures/questions.yaml` and the comparison rules from
`harness/grade.py`.

`harness/specdoc.py` renders the version that an agent receives. The renderer
removes question references, provenance, and the open questions. The
`spec_fingerprint` hashes this rendered version.

Each rule has a stable ID followed by its requirements. The **Questions
affected** field names its dependent questions, while **Provenance** records
the source and rationale. The final [Open questions](#open-questions) section
tracks conflicts that still need a decision.

---

## 1. The task

The analysis starts with a list of Brazilian rural properties. Complete an
analysis under the European Union Deforestation Regulation (EUDR):

1. Resolve each property to cadastral parcels
2. Match agricultural field boundaries to those parcels
3. Measure forest loss after 2020 on crops covered by the EUDR
4. Find the cooperative or buyer contact for each non-compliant property

Deliver one CSV per question in section 9, named `q01.csv` through `q31.csv`.
You may choose the column names, but their meanings and types must match the
contract.

Also deliver `workflow.csv`, with one row per flagged property and these
columns:

- `cod_imovel`
- `annex1_commodity`
- `post2020_loss_ha`
- `top_contact_entity_id`
- `entity_kind`
- `tier`
- `basis`
- `distance_km`

Questions build across six stages. Later answers depend on earlier results.

Read all catalog metadata before you begin the analysis. Keep slow queries in
the foreground instead of sending them to the background, and wait for them to
finish. Write each answer to disk after you produce it because a timeout
discards unsaved results.

---

## 2. Answer comparison

These rules apply to every answer. They define what "the same answer" means.

### Rule: rows-are-a-multiset
Rows compare as an unordered multiset. Row order matters only when a question
defines a ranking. In that case, follow the question's rank rule rather than
the file order.

Provenance: initial design (PR #5).

### Rule: column-names-are-free
Choose your column names and order. The grader matches columns by meaning and type only.

Provenance: initial design (PR #5).

### Rule: quantize-before-compare
Round numeric answers to match the golden value's decimal places before comparison. Output precision is not tested.

Provenance: issue #6. A golden rounded to one decimal failed a correct answer at small magnitudes. The fix went into the comparator, not the prompt.

### Rule: numeric-tolerance
Numbers compare at 0.1% relative error, with an absolute floor of 0.000000001.
Questions marked `geometry` can shift with the calculation method. Their
decimal values allow 1% relative error. Their integer counts allow a difference
of either 2 units or 1% of the golden value, whichever is larger.

These tolerances account for defensible differences in feature counts. A value
outside its tolerance but within ten times that limit counts as a near miss.

Geometry-graded questions: q08–q12, q14, q15, q19, q26, q30.

Provenance: initial design (PR #5). The project added the near-miss split for
triage.

### Rule: q23-grades-strict
q23 allows no tolerance for `field_id`. Adjacent plots have adjacent IDs, so a
"close enough" comparison could credit the wrong farm.

Provenance: PR #5, via the questions.yaml header.

### Rule: strings-fold-case
Compare strings without regard to case. The answer key does not use case to
distinguish values.

Provenance: PR #24 (issue #20). An arm run without the scope section lost five points to capitalization alone.

### Rule: booleans-are-liberal
Accept `true`, `True`, `TRUE`, `yes`, `t`, and `1` as true values.
Accept their negative forms as false values. A bare `1` or `0` counts as a
Boolean only when compared with a word, which prevents a count column from
matching a flag.

Provenance: issue #6. `True` against `true` failed a correct answer.

### Rule: area-and-distance-method-is-free
You may choose the method for computing areas and distances. Geodesic area,
equal-area projections, and Brazil Polyconic agree within 1%. The geometry
tolerance absorbs this spread.

This rule applies to all hectare and kilometer columns except q23.

Provenance: the questions.yaml header. One stored run used planar EPSG:5880 area and lost only q23, as designed.

---

## 3. Data sources

### Rule: source-files
Query three GeoParquet datasets remotely with DuckDB (spatial and httpfs extensions):

- Field boundaries (Trazo3, Goiás 2024): `https://data.source.coop/wri-data-lab/trazofields/trazo3-fields/trazo3_brazil_goias_2024.parquet`
- Cadastral parcels (Brazil's Rural Environmental Registry, known as CAR): `https://data.source.coop/tristangruppwri/cadastral/brazil-car-area-imovel/brazil_car_area_imovel.parquet`
- Commodity infrastructure: `https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/facilities/BR_facilities.parquet`

Provenance: prompts/task.md.

### Rule: coordinates-are-lon-lat
All geometry is WGS84 longitude-first. Set `geometry_always_xy = true` when loading. Loading with latitude-first axis order shrinks Goiás areas by a factor of about 1.5 and moves every distance.

Questions affected: q08–q15, q19, q22, q23, q26, q30, workflow.

Provenance: the oracle shipped this bug until July 26, 2026 (commit e606096)
and penalized correct answers. No agent-facing document described the correct
behavior before this specification.

### Rule: loss-bands-are-square-metres
Trazo3 deforestation columns are in square metres. Divide by 10,000 to get hectares.

Questions affected: q15, q17–q23, q30, workflow.

Provenance: Trazo3 catalog documentation. Previously stated only in an oracle
comment.

---

## 4. Input list handling

Start from `lists/goias-sample.csv`. The list can use one of three encodings:

- a CSV file with IDs and well-known text (WKT) geometry
- a separate geometry file
- a CSV and geometry file joined by ID

Detect the available columns. Account for every row because an omitted
property leaves part of the source area unaudited.

### Rule: dedupe-on-id-and-geometry
A repeated `cod_imovel` with identical geometry represents one parcel. Report
the duplicate count before collapsing those rows.

Questions affected: q05, q31.

Provenance: PR #19 (issue #18). The project added defects to the list to make
this rule measurable.

### Rule: centroid-resolves-by-containment
A point resolves to the CAR parcel that contains it. Mark it `centroid_resolved`. If the point lands in no parcel or in several, report it as unresolvable.

Questions affected: q05, q31.

Provenance: PR #19.

### Rule: idless-polygon-resolves-by-containment
A polygon with no id resolves by geometric match against CAR using the single-parcel containment test. Mark it `geometry_resolved`.

Questions affected: q05, q31.

Provenance: PR #19.

### Rule: axis-flip-repair
A geometry has swapped axes when its original coordinates fall outside Brazil
but exchanging latitude and longitude moves it inside Brazil. Exchange the
coordinates, resolve the geometry, and mark it `axis_repaired`. If both
orderings fall outside Brazil, report the geometry as unresolvable.

Questions affected: q31.

Provenance: PR #19. Measured as its own bucket because the oracle once shipped the same class of bug.

### Rule: reconciliation-identity
Every input row lands in exactly one of six buckets. They sum to the arrival count:

`resolved_clean + centroid_resolved + geometry_resolved + axis_repaired + duplicates_removed + unresolvable = input_rows`

The count `input_rows` is rows as they arrived, before deduplication.

Questions affected: q31.

Provenance: policies/INPUTS.md. Graded since PR #19.

---

## 5. Field–parcel matching

### Rule: inclusion-tests
A field qualifies when at least two-thirds of its area lies inside the listed
parcels. Either of these tests can qualify the field:

1. Single-parcel: the largest containment ratio is ≥ 0.667
2. Aggregate: total area inside all listed parcels (buffered 25 m) divided by field area is ≥ 0.667

Measure both areas in the EPSG:4326 coordinate reference system (CRS). Their
scale cancels in the ratio.

Questions affected: q09–q15 and everything downstream.

Provenance: Tristan Grupp (WRI), 2026-07-17. Runs without this rule invent a 0.5 threshold instead, which moves 16 of 31 questions.

### Rule: matching-parameters
Set `contain_threshold` = 0.667 and `neighbor_gap_tolerance_m` = 25. The 25 m buffer closes CAR sliver gaps between neighbors. Dissolve the buffered union before intersecting so overlapping parcels are never double-counted.

Questions affected: q09–q12.

Provenance: policies/MATCHING.md.

### Rule: primary-cadaster
Each matched field has one primary cadaster: the listed parcel with the largest
containment fraction. When multiple parcels fall within 1e-9 of the maximum,
choose the lowest `cod_imovel` by string comparison. Report the true maximum
as `max_single_frac`.

Questions affected: q13, q20, q23, q24, q26–q30, workflow.

Provenance: PR #13 (issue #12). Exactly 54 of 793 matched fields tie. One run
scored full marks only because it matched the oracle's scan order. Measurements
put method noise at no more than 1.7e-10 and the closest genuine gap at 1.6e-8,
which supports the 1e-9 tolerance.

### Rule: excluded-fields-are-counted
Exclude and count fields that fail both tests. An intersection requires
positive area, so touching a parcel boundary does not count.

Questions affected: q12.

Provenance: policies/MATCHING.md. The positive-area reading was oracle-only before this document.

### Rule: envelope
The stage-3 envelope is the bounding box of the union of the listed parcels. It is one box, not a per-parcel box. A field belongs to it if the geometries intersect.

Questions affected: q08.

Provenance: oracle-only before this document (fields_extract.sql.tmpl).

### Rule: duplicate-car-ids
CAR contains 8,453,554 rows for 8,437,940 distinct IDs. Treat each row as a
separate parcel in the single-parcel test. The aggregate test dissolves these
rows like any other overlap. If a listed ID is absent from CAR, report it as
missing and add no geometry.

Questions affected: q03, q05, q07.

Provenance: policies/MATCHING.md.

---

## 6. Deforestation measurement

### Rule: post-2020-loss
Post-2020 loss is the 2021–2024 era band alone. A field carries post-2020 loss when that band is greater than zero. There is no minimum area threshold.

Questions affected: q15, q17–q20, q23, q24, q26–q30, workflow.

Provenance: the EUDR cutoff is December 31, 2020, and the prior band ends in
2020. Only the oracle defined the zero threshold before this document.

### Rule: loss-from-era-bands-only
Total loss is the sum of the five era bands. Do not use `hansen_covered_area` or `hansen_loss_area`.

Questions affected: q21, q22.

Provenance: the questions.yaml header. The hansen_loss_area exclusion was oracle-only before this document.

### Rule: era-bands
The five Hansen eras are 2001-2004, 2005-2009, 2010-2014, 2015-2020, 2021-2024. Label them as year ranges in exactly that form.

Questions affected: q21.

Provenance: Trazo3 column structure.

---

## 7. EUDR scope and routing

Regulation (EU) 2023/1115 Annex I covers seven commodities: cattle, cocoa,
coffee, oil palm, rubber, soya, and wood. The scope table identifies covered
crops. A separate routing table identifies their delivery destinations.

### Rule: scope-table
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

Use lowercase tokens for commodity and caveat values. An empty cell represents
an empty value. Treat classes outside the table as out of scope with empty
commodity and caveat values.

The table omits cocoa and rubber because MapBiomas has no dedicated class for
either commodity and neither is materially present in Brazil.

Strings compare case-insensitively.

Questions affected: q16–q20, q23–q25, q30, workflow.

Provenance: policies/EUDR_CROPS.md. Cocoa and rubber omitted per TG, 2026-07-24.

### Rule: two-reasons-to-exclude
The regulation does not cover classes 18, 20, 40, 41, 47, 48, and 62. Class 9
(Forest Plantation) is out of scope for a different reason: the sensor cannot
detect it reliably. Planted timber remains an Annex I commodity. Any output
that excludes forest plantation must cite detection limits as the reason.

Questions affected: q16, q18.

Provenance: policies/EUDR_CROPS.md.

### Rule: routing-table
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

### Rule: dominant-class
When a parcel contains several field classes, the class covering the most
hectares is dominant. Break ties with the lowest `mbmode24` code. Include all
matched fields on the parcel, regardless of scope.

Questions affected: q24, q26, q28–q30, workflow.

Provenance: policies/EUDR_CROPS.md. This rule never varied across 19 stored runs.

### Rule: flagged-set
Flag a parcel as non-compliant when at least one matched, in-scope field has
post-2020 loss. These parcels form the flagged set.

Questions affected: q24, q26–q30, workflow.

Provenance: prompts/task.md.

---

## 8. Cooperative and buyer candidates

Build a ranked candidate set for each flagged parcel. Relationship evidence
links candidates by município, while delivery evidence links them by distance
without regard to município.

### Rule: tiers
| Tier | Answers | Entity kind | Basis | Match rule |
|---|---|---|---|---|
| membership_muni | relationship | cooperative | observed | município code equals the parcel's |
| intake_point | delivery (grain) | cooperative | observed | distance ≤ radius |
| slaughter_point | delivery (cattle) | buyer | observed | distance ≤ radius |
| mill_point | delivery (sugar cane) | mill | observed | distance ≤ radius |
| gravity_catchment | delivery | cooperative | modelled | parcel centroid inside the catchment polygon |

Because each mill sits at its town's centroid, its distance measures
town-to-parcel. Mark every mill candidate as `town_centroid` and exclude mills
from the proximity override.

Questions affected: q02, q24–q30, workflow.

Provenance: policies/COOPS.md.

### Rule: ranking
Order candidates by these keys:

1. `nearest_by_far` promotion
2. tier: `membership_muni`, `intake_point` or `slaughter_point`,
   `mill_point`, then `gravity_catchment`
3. distance ascending, with no distance for `gravity_catchment`
4. `evidence_value` descending
5. `entity_id` ascending

Distances have no tie tolerance.

Questions affected: q26, q28, q30, workflow.

Provenance: policies/COOPS.md. Forty-five Goiás parcels have two facilities at
the same distance, so the `entity_id` tie-break often applies.

### Rule: proximity-override
Promote a delivery facility closer than 10 km to the top and mark it
`nearest_by_far`. Keep the relationship candidate below it. Mills do not
participate.

Questions affected: q28–q30.

Provenance: policies/COOPS.md.

### Rule: widening
If fewer than 2 delivery candidates fall within 100 km, expand the radius up to
300 km until you find 2. Mark the result as `widened`. If no candidates fall
within 300 km, mark the result as `no_match`, and report it.

Questions affected: q28, q29.

Provenance: policies/COOPS.md. The nearest cooperative silo to Jussara is 58 km, so widening is normal, not failure.

### Rule: candidate-parameters
After ranking, keep at most 5 candidates before reconciliation. Set
`max_candidates` to 5 and `min_capacity_t` to 1000. The capacity threshold
applies only to `intake_point`. Compute distance from the parcel centroid to
the facility point in EPSG:5880.

Questions affected: q26, q28–q30, workflow.

Provenance: policies/COOPS.md.

### Rule: gap-markers
When a flagged parcel's commodity has no tier, set `routed_tier` to
`no_tier`. Route an unknown class as `unknown`. Join multiple tiers with
`|` in tier-name order. For empty cells, accept an empty string, `NULL`,
`none`, or `n/a`.

Questions affected: q16, q24, q25, q30, workflow.

Provenance: oracle-only before this document (coop_routing.sql.tmpl, portfolio.sql.tmpl).

---

## 9. The questions

The benchmark has 31 questions across 6 stages. Later stages depend on earlier
answers. The grader reports both raw accuracy and conditional accuracy for
questions whose dependencies are correct.

### Stage 1 — catalog discovery

**q01** — Count the SpatioTemporal Asset Catalog (STAC) Collections among the
TrazoFields child entries, and identify the collection that the metadata
recommends.

Columns: `n_collections` (integer), `recommended_id` (string). Rows: 1.

**q02** — Row count per tier in the unified facilities layer.

Columns: `tier` (string), `n` (integer). Rows: 5.

**q03** — Total rows and distinct `cod_imovel` in CAR.

Columns: `n_rows`, `n_distinct_ids` (integers). Rows: 1.

**q04** — Row count and EPSG code of the Trazo3 Goiás file.

Columns: `n_rows`, `epsg` (integers). Rows: 1.

### Stage 2 — cadaster resolution

**q05** — Resolve the list according to section 4. Report the distinct parcels,
the number found in CAR, and the number missing.

Count parcels, not rows. An id-less row counts under the parcel it resolves to. A duplicate counts once.

Columns: `list_ids`, `found_in_car`, `missing` (integers). Rows: 1. Depends: q03.

**q06** — Resolved parcels per município.

Columns: `municipio` (string), `n_parcels` (integer). Rows: data. Depends: q05.

**q07** — Count listed IDs with more than one CAR row. Also count resolved
parcels under 1 hectare, where CAR `num_area` is less than 1.

Columns: `n_duplicate_ids`, `n_parcels_under_1ha` (integers). Rows: 1. Depends: q05.

**q31** — Account for every input row across the six buckets of the reconciliation identity (section 4).

Columns: `input_rows`, `resolved_clean`, `centroid_resolved`,
`geometry_resolved`, `axis_repaired`, `duplicates_removed`,
`unresolvable` (integers). Rows: 1. Depends: q05.

This question is numbered q31 because renumbering the sequential IDs would
invalidate stored answer keys.

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

**q15** — Report the list-level totals for parcels, fields, matched hectares,
post-2020 loss hectares, and fields with post-2020 loss.

Columns: `n_parcels`, `n_fields` (integers), `matched_field_ha`, `post2020_loss_ha` (floats), `fields_with_post2020_loss` (integer). Rows: 1. Depends: q09. Geometry-graded.

**q16** — Classify every MapBiomas class on matched fields according to the
scope table. Report its commodity, scope status, and caveat. Leave the
commodity or caveat empty when none applies.

Columns: `mbmode24` (integer), `mb_class` (string), `annex1_commodity` (string), `in_scope` (boolean), `caveat` (string). Rows: data. Depends: q09.

**q17** — Post-2020 loss on in-scope crops by commodity.

Columns: `annex1_commodity` (string), `n_fields` (integer), `post2020_loss_ha` (float). Rows: data. Depends: q16.

**q18** — Post-2020 loss on out-of-scope classes, by class, so the exclusion is auditable.

Columns: `mb_class` (string), `n_fields` (integer), `post2020_loss_ha` (float). Rows: data. Depends: q16.

**q19** — Total matched-field area by in-scope commodity, and the share carrying post-2020 loss (0–1).

Columns: `annex1_commodity` (string), `total_ha`, `loss_share` (floats). Rows: data. Depends: q16. Geometry-graded.

**q20** — Ten cadasters with the most post-2020 loss on in-scope crops. Loss descending, ties by `cod_imovel` ascending.

Columns: `cod_imovel`, `municipio` (strings), `post2020_loss_ha` (float), `n_fields_post2020_loss` (integer). Rows: 10. Depends: q16.

**q21** — Loss by era band: the five eras with cleared hectares and percentage
of total loss.

Columns: `era` (string, for example `2001-2004`), `cleared_ha`,
`pct_of_loss` (floats). Rows: 5. Depends: q09.

**q22** — Distribution of dominant loss year across fields with loss: field count and cleared hectares per year.

Columns: `loss_year`, `n_fields` (integers), `cleared_ha` (float). Rows: data. Depends: q09.

**q23** — Ten worst in-scope plots by post-2020 loss. Ties by field id ascending. Grades strict (see section 2).

Columns: `field_id` (integer), `cod_imovel`, `annex1_commodity` (strings), `field_area_ha`, `post2020_loss_ha` (floats). Rows: 10. Depends: q16.

### Stage 5 — commodity infrastructure

**q24** — For each flagged parcel: dominant class, commodity, and its routing
tiers or gap marker.

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

**q30** — Report the top-ranked contact for every flagged parcel. Include the
commodity and post-2020 loss, plus the candidate's ID, kind, tier, basis, and
distance. Leave distance empty for non-distance tiers.

Columns: `cod_imovel`, `annex1_commodity` (strings), `post2020_loss_ha` (float), `entity_id`, `entity_kind`, `tier`, `basis` (strings), `distance_km` (float). Rows: data. Depends: q24, q26, q27, q28. Geometry-graded.

`workflow.csv` repeats q30 in the fixed eight-column form from section 1. The
harness checks this file for consistency but does not grade it.

---

## Open questions

Code under `oracle/` generates the answer key. An audit on August 2, 2026,
found eight conflicts between that code and this specification. Each conflict
needs a human decision. Until then, the answer key retains the behavior
described below.

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

4. **The grader does not implement the empty-cell promise.** The gap-marker
   rule accepts `NULL`, `none`, and `n/a` when the answer key has an empty
   cell. The grader currently rejects those values. Either implement this
   equivalence or remove it from the rule.

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
