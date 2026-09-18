INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SET geometry_always_xy = true;
.timer on
-- input list
CREATE OR REPLACE TABLE list_raw AS
  SELECT * FROM read_csv('/workspace/lists/goias-sample.csv', header=true, all_varchar=true);
-- facilities (small ~40k) - keep geometry as WKB blob for portability
CREATE OR REPLACE TABLE facilities AS
  SELECT entity_id, entity_kind, tier, weight, basis, geom_method, source,
         ST_AsWKB(geometry) AS geom_wkb
  FROM read_parquet('https://data.source.coop/tristangruppwri/soft-commodity-infrastructure/facilities/BR_facilities.parquet');
SELECT 'facilities' t, count(*) n FROM facilities;
