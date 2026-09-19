INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SET geometry_always_xy = true;
.timer on
CREATE OR REPLACE TABLE trazo AS
  SELECT Id, hansen_covered_area, hansen_loss_area, mode_year, firstyear, firstyearmajority,
         deforestarea0104, deforestarea0509, deforestarea1014, deforestarea1520, deforestarea2124,
         mbmode24, mbcov_area_2024, mbvalid_area_2024,
         bbox,
         ST_AsWKB(geometry) AS geom_wkb
  FROM read_parquet('https://data.source.coop/wri-data-lab/trazofields/trazo3-fields/trazo3_brazil_goias_2024.parquet');
SELECT count(*) n FROM trazo;
