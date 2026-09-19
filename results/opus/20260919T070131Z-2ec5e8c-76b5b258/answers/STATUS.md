# Completion status — all 31 answers + workflow.csv written

Every `q01.csv`–`q31.csv` and `workflow.csv` is computed from the live datasets
with DuckDB (spatial + httpfs, `geometry_always_xy=true`). Metric work uses
**EPSG:31982** (SIRGAS 2000 / UTM 22S), the zone covering Goiás.

## Important caveat on the spec

`SPEC.md` contains only sections **1, 3, 9**; sections **2, 4, 5, 6, 7, 8 are
not in the file**. Per section 1 ("Read all catalog metadata before you begin"),
I recovered the procedures those sections would hold from the three datasets'
`AGENTS.md` / `llms.txt`. Where a numeric threshold or mapping was still not
pinned down anywhere, I chose the standard/defensible value and recorded it
below. Answers resting on such a choice are marked ⚠.

## Key results

- **Stage 1** q01 `3, trazo3-fields` · q02 5 tiers · q03 `8453552 / 8437938` · q04 `772404 / 4326`
- **Stage 2** List = 119 rows → 115 id rows (1 duplicate) + 4 id-less geometries.
  All 114 distinct listed IDs are in CAR. 3 id-less rows resolve to new Jussara
  parcels → **117 distinct parcels, 0 missing** (q05). q31 buckets:
  `119 = 114 clean + 1 centroid + 1 geometry + 1 axis_repaired + 1 duplicate + 1 unresolvable`.
- **Stage 3** 3291 fields in envelope; **798 matched** (τ=0.5); 37 116.4 matched ha.
- **Stage 4** post-2020 loss (band `deforestarea2124`) on matched fields = 120.57 ha
  across 42 fields; in-scope split soya 52.59 ha / cattle 43.45 ha.
- **Stage 5–6** 18 flagged parcels (1 soya, 17 cattle). Top contact = municipality
  cooperative (relationship tier) by default; 2 parcels flip to the nearest
  slaughterhouse under proximity override.

## Assumptions where the missing sections left a gap

- **§4 six-bucket resolution** — id-less rows bucketed by geometry: point in Goiás
  → `centroid_resolved`; polygon in Goiás → `geometry_resolved`; axis-swapped
  polygon (lat/lon reversed, `POLYGON((-15.5 -51.3…))`) → `axis_repaired`;
  `POINT(-35 -10)` (Atlantic) → `unresolvable`; repeated id → `duplicates_removed`.
  Resolution is point-in-polygon (`ST_Contains`) against CAR.
- ⚠ **§5 matching threshold** — single-parcel and aggregate(union) containment tests
  use τ = **0.5** (majority containment). Field attributed to its max-containment parcel.
- ⚠ **§6 scope table** (only 4 MapBiomas classes occur on matched fields):
  `15 Pasture→cattle (in-scope, caveat: land-cover proxy)`, `39 Soybean→soya (in-scope)`,
  `21 Mosaic→out`, `41 Other Temporary Crops→out`.
- ⚠ **q19 `loss_share`** = share of commodity matched-area on fields carrying any post-2020 loss.
- **§7 routing** (from facilities `llms.txt`): soya→`intake_point`, cattle→`slaughter_point`;
  `membership_muni` = relationship tier, `entity_id` = IBGE código (embedded in `cod_imovel`),
  evidence = member count (`weight`). coffee/oil-palm would have no delivery tier (none occur here).
  Distances = min parcel-to-facility distance in EPSG:31982.
- ⚠ **q28/q29 reconciliation** — delivery candidates = routed-tier facilities within a
  **50 km** catchment; `widened` if none within 50 km; `proximity_override`/`nearest_by_far`
  if nearest < ½ the 2nd-nearest; catchment candidates = `gravity_catchment` polygons
  containing the parcel (0 here).
- **§8 top contact** — relationship (cooperative) outranks delivery; proximity override
  promotes the nearest delivery facility. Distance blank for the non-distance membership tier.

The ⚠ items would shift if sections 2 and 4–8 supply different thresholds/mappings;
send those and I will re-run the affected questions.
