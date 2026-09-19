INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
.timer on
COPY (SELECT count(*)::BIGINT n_rows, count(DISTINCT cod_imovel)::BIGINT n_distinct_ids
  FROM read_parquet('https://data.source.coop/tristangruppwri/cadastral/brazil-car-area-imovel/brazil_car_area_imovel.parquet'))
  TO '/workspace/answers/q03.csv' (HEADER, DELIMITER ',');
