"""Build the Mato Grosso adversarial input set (experiment 2).

Selects real MT soy properties carrying post-2020 deforestation from the live
catalogs, then injects a seeded set of input defects and emits the portfolio in
three encodings (csv, geometry, split). A ground-truth manifest records what
each row really is, so the experiment's oracle and grader know the correct
resolution — the agent never sees the manifest or the defect labels.

Deterministic: same config + seed => byte-identical outputs. The two remote
pulls (CAR parcels in the target municipios, Trazo3 soy-loss fields in their
envelope) are cached under _cache/ so re-runs are fast; pass --force to re-pull.

Extend by editing config.json (municipios, commodity, sample size, defect
counts) or by adding a defect kind to DEFECTS below and documenting it in the
experiment README.

Usage:
    python experiments/mt-adversarial/build_inputs.py [--force]
"""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
PINS = json.loads((REPO / "fixtures" / "pins.json").read_text(encoding="utf-8"))

CAR_URL = PINS["catalogs"]["cadastral"]["car_parquet"]
TRAZO_MT = PINS["catalogs"]["trazo"]["mato_grosso_parquet"]

CACHE = HERE / "_cache"
INPUTS = HERE / "inputs"

# Defect kinds. Each maps a disjoint slice of the clean sample to a transformation
# applied when the rows are emitted. Counts come from config["defects"].
#   duplicate       : row repeated verbatim (same id + geometry)
#   centroid        : id kept, polygon replaced by its centroid (a point)
#   no_id           : id blanked, polygon kept
#   centroid_no_id  : id blanked, polygon replaced by centroid
DEFECTS = ["duplicate", "centroid", "no_id", "centroid_no_id"]
DEFECT_CONFIG_KEY = {
    "duplicate": "duplicate_cadasters",
    "centroid": "centroid_rows",
    "no_id": "polygons_without_id",
    "centroid_no_id": "centroids_without_id",
}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    return con


def build_cache(con: duckdb.DuckDBPyConnection, force: bool) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    cad = CACHE / "cad_mt.parquet"
    soy = CACHE / "soy_mt.parquet"
    munis = ", ".join("'%s'" % m for m in CONFIG["region"]["municipalities"])

    if force or not cad.exists():
        print("[pull] CAR parcels in target municipios (cold, cached after)")
        con.execute(f"""
            COPY (
                SELECT cod_imovel, municipio, num_area,
                       ST_SetCRS(geometry, 'EPSG:4326') AS geometry
                FROM read_parquet('{CAR_URL}')
                WHERE cod_estado = 'MT' AND municipio IN ({munis})
            ) TO '{cad.as_posix()}' (FORMAT PARQUET)
        """)
    else:
        print("[cache] cad_mt.parquet")

    if force or not soy.exists():
        # Envelope of the target parcels, so the Trazo3 read is bbox-limited
        # (Trazo3 has bbox covering -> row-group skipping) instead of scanning MT.
        print("[pull] Trazo3 soy-loss fields in the municipio envelope")
        con.execute(f"CREATE OR REPLACE TABLE _cad AS SELECT * FROM read_parquet('{cad.as_posix()}')")
        con.execute("CREATE OR REPLACE TABLE _env AS SELECT ST_Envelope(ST_Union_Agg(geometry)) AS g FROM _cad")
        cls = CONFIG["selection"]["commodity_class"]
        loss = "AND coalesce(f.deforestarea2124, 0) > 0" if CONFIG["selection"]["require_post2020_loss"] else ""
        con.execute(f"""
            COPY (
                SELECT f.Id, coalesce(f.deforestarea2124, 0) AS loss_m2, f.geometry
                FROM read_parquet('{TRAZO_MT}') f, _env e
                WHERE CAST(f.mbmode24 AS INT) = {cls} {loss}
                  AND ST_Intersects(f.geometry, e.g)
            ) TO '{soy.as_posix()}' (FORMAT PARQUET)
        """)
    else:
        print("[cache] soy_mt.parquet")


def select_clean(con: duckdb.DuckDBPyConnection) -> None:
    """Clean base cadasters = target parcels containing >=1 soy-loss field,
    sampled deterministically. Result table `clean` has (cod_imovel, municipio,
    geometry), ordered by a seeded hash so the sample and the defect assignment
    are reproducible."""
    seed = CONFIG["selection"]["seed"]
    n = CONFIG["selection"]["sample_size"]
    con.execute(f"CREATE OR REPLACE TABLE cad AS SELECT * FROM read_parquet('{(CACHE / 'cad_mt.parquet').as_posix()}')")
    con.execute(f"CREATE OR REPLACE TABLE soy AS SELECT * FROM read_parquet('{(CACHE / 'soy_mt.parquet').as_posix()}')")
    con.execute(f"""
        CREATE OR REPLACE TABLE clean AS
        WITH hits AS (
            SELECT DISTINCT c.cod_imovel, c.municipio, c.geometry
            FROM cad c JOIN soy f ON ST_Intersects(c.geometry, f.geometry)
        )
        SELECT cod_imovel, municipio, geometry,
               row_number() OVER (ORDER BY md5(cod_imovel || '{seed}')) - 1 AS rn
        FROM hits
        QUALIFY rn < {n}
    """)
    got = con.execute("SELECT count(*) FROM clean").fetchone()[0]
    if got < n:
        raise SystemExit(f"only {got} soy-deforestation cadasters found; lower sample_size")


def assign_defects() -> dict:
    """Map each clean-sample row index (0..n-1) to a defect kind (or 'clean'),
    in disjoint contiguous slices ordered as DEFECTS then clean remainder."""
    assign = {}
    i = 0
    for kind in DEFECTS:
        for _ in range(CONFIG["defects"][DEFECT_CONFIG_KEY[kind]]):
            assign[i] = kind
            i += 1
    return assign  # remaining indices are implicitly clean


def emit(con: duckdb.DuckDBPyConnection) -> dict:
    INPUTS.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        "SELECT rn, cod_imovel, municipio, ST_AsText(geometry) AS wkt FROM clean ORDER BY rn"
    ).fetchall()
    assign = assign_defects()

    # Build the emitted row set with ground truth. Each emitted row:
    #   emit_id, cod_imovel (may be ''), wkt (polygon or point), defect, origin
    emitted = []
    for rn, cod, muni, wkt in rows:
        kind = assign.get(rn, "clean")
        if kind == "clean":
            emitted.append((cod, muni, wkt, False, "clean", cod))
        elif kind == "duplicate":
            emitted.append((cod, muni, wkt, False, "clean", cod))           # the original
            emitted.append((cod, muni, wkt, False, "duplicate", cod))       # verbatim repeat
        elif kind == "centroid":
            emitted.append((cod, muni, wkt, True, "centroid", cod))         # id kept, point
        elif kind == "no_id":
            emitted.append(("", muni, wkt, False, "no_id", cod))            # id blanked, polygon
        elif kind == "centroid_no_id":
            emitted.append(("", muni, wkt, True, "centroid_no_id", cod))    # id blanked, point

    # Materialize as a table, converting points where the defect calls for it.
    con.execute("CREATE OR REPLACE TABLE emit (emit_id INT, cod_imovel VARCHAR, municipio VARCHAR, wkt VARCHAR, as_point BOOLEAN, defect VARCHAR, origin VARCHAR)")
    for k, (cod, muni, wkt, as_point, defect, origin) in enumerate(emitted):
        con.execute("INSERT INTO emit VALUES (?,?,?,?,?,?,?)", [k, cod, muni, wkt, as_point, defect, origin])
    con.execute("""
        CREATE OR REPLACE TABLE emit_geom AS
        SELECT emit_id, cod_imovel, municipio, defect, origin,
               CASE WHEN as_point THEN ST_Centroid(ST_GeomFromText(wkt))
                    ELSE ST_GeomFromText(wkt) END AS geometry
        FROM emit
    """)

    # --- csv encoding: id-bearing rows only (clean + duplicate + centroid) ---
    con.execute(f"""
        COPY (SELECT cod_imovel, municipio FROM emit_geom
              WHERE cod_imovel <> '' ORDER BY emit_id)
        TO '{(INPUTS / 'mt-adversarial.csv').as_posix()}' (HEADER, FORMAT CSV)
    """)
    # --- geometry encoding: every row, id may be blank ---
    con.execute(f"""
        COPY (SELECT cod_imovel, geometry FROM emit_geom ORDER BY emit_id)
        TO '{(INPUTS / 'mt-adversarial.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    # --- split encoding: id csv + geometry parquet keyed by id (id-bearing) ---
    con.execute(f"""
        COPY (SELECT cod_imovel, municipio FROM emit_geom
              WHERE cod_imovel <> '' ORDER BY emit_id)
        TO '{(INPUTS / 'mt-adversarial-split.csv').as_posix()}' (HEADER, FORMAT CSV)
    """)
    con.execute(f"""
        COPY (SELECT cod_imovel, geometry FROM emit_geom
              WHERE cod_imovel <> '' ORDER BY emit_id)
        TO '{(INPUTS / 'mt-adversarial-split-geom.parquet').as_posix()}' (FORMAT PARQUET)
    """)

    # --- ground-truth manifest (NEVER mounted to the agent) ---
    # ORDER BY so the manifest is byte-stable across runs, not just its data.
    counts = dict(con.execute(
        "SELECT defect, count(*) FROM emit_geom GROUP BY defect ORDER BY defect"
    ).fetchall())
    manifest = {
        "experiment": CONFIG["experiment"],
        "seed": CONFIG["selection"]["seed"],
        "n_clean_sample": CONFIG["selection"]["sample_size"],
        "n_emitted_rows": len(emitted),
        "defect_counts": counts,
        "distinct_origin_cadasters": con.execute(
            "SELECT count(DISTINCT origin) FROM emit_geom").fetchone()[0],
        "expected_resolution": {
            "clean": "resolve by cod_imovel",
            "duplicate": "dedupe on (id, geometry); report the removed count",
            "centroid": "id present -> resolve by id; flag point-precision geometry",
            "no_id": "resolve by geometric match to the containing CAR parcel",
            "centroid_no_id": "resolve by point-in-parcel; ambiguous or outside -> unresolvable",
        },
        "ground_truth_rows": con.execute(
            "SELECT emit_id, cod_imovel, defect, origin FROM emit_geom ORDER BY emit_id"
        ).fetchall(),
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-pull the cached remote extracts")
    args = ap.parse_args()
    con = connect()
    build_cache(con, args.force)
    select_clean(con)
    manifest = emit(con)
    print(f"emitted {manifest['n_emitted_rows']} rows from "
          f"{manifest['distinct_origin_cadasters']} cadasters -> {INPUTS}")
    print("defect counts:", manifest["defect_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
