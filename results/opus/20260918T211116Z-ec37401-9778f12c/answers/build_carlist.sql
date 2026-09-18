INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SET geometry_always_xy = true;
.timer on
CREATE OR REPLACE TABLE car_list AS
  SELECT c.cod_imovel, c.municipio, c.cod_estado, c.num_area, c.mod_fiscal,
         c.ind_status, c.ind_tipo, c.des_condic, c.bbox,
         ST_AsWKB(c.geometry) AS geom_wkb
  FROM read_parquet('https://data.source.coop/tristangruppwri/cadastral/brazil-car-area-imovel/brazil_car_area_imovel.parquet') c
  SEMI JOIN (SELECT DISTINCT cod_imovel FROM list_raw WHERE cod_imovel IS NOT NULL) l
    ON c.cod_imovel = l.cod_imovel
  WHERE c.cod_estado = 'GO';
SELECT count(*) n_rows, count(DISTINCT cod_imovel) n_ids FROM car_list;
