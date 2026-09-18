INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SET geometry_always_xy = true;
.timer on
CREATE OR REPLACE TABLE car_near AS
  SELECT c.cod_imovel, c.municipio, c.num_area, c.bbox, ST_AsWKB(c.geometry) geom_wkb
  FROM read_parquet('https://data.source.coop/tristangruppwri/cadastral/brazil-car-area-imovel/brazil_car_area_imovel.parquet') c
  WHERE c.cod_estado='GO'
    AND c.bbox.xmin <= -51.29 AND c.bbox.xmax >= -51.44
    AND c.bbox.ymin <= -15.48 AND c.bbox.ymax >= -15.88;
SELECT count(*) n_near FROM car_near;
