INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SET geometry_always_xy = true;
.timer on
-- the 117 resolved parcel ids
CREATE OR REPLACE TABLE parcel_ids AS
  SELECT DISTINCT cod_imovel, 'clean' AS res_kind FROM list_raw WHERE cod_imovel IS NOT NULL
  UNION
  SELECT * FROM (VALUES
    ('GO-5212204-15108D4D7C134B8D9E71E50A2ED2E605','centroid'),
    ('GO-5212204-6A28AC5AF7BF4BEC8931E8E154D99AEE','geometry'),
    ('GO-5212204-F17BEA20B17148C1ADB4BD53A04EC448','axis')) v(cod_imovel,res_kind);
-- pull ALL car rows for these ids (no state filter) to catch duplicate rows
CREATE OR REPLACE TABLE car_rows AS
  SELECT c.cod_imovel, c.municipio, c.cod_estado, c.num_area, c.mod_fiscal,
         c.ind_status, c.ind_tipo, c.des_condic, c.bbox, ST_AsWKB(c.geometry) geom_wkb
  FROM read_parquet('https://data.source.coop/tristangruppwri/cadastral/brazil-car-area-imovel/brazil_car_area_imovel.parquet') c
  SEMI JOIN parcel_ids p ON c.cod_imovel = p.cod_imovel;
SELECT count(*) car_rows, count(DISTINCT cod_imovel) distinct_ids FROM car_rows;
