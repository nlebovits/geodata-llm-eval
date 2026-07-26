"""Oracle checks.

The offline ones run always: every question maps to a template that exists, the
templates have no unfilled placeholders after substitution, the QUESTION_MAP
covers exactly the 30 questions, and the local scope tables build. The
generation, determinism, and partition checks need the live catalogs on
source.coop; they skip when it is unreachable (it throttles and occasionally
goes down), and are the real acceptance gate when it is up.
"""

import json
import socket
import sys
import urllib.request
from pathlib import Path

import duckdb
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "oracle"))

import render  # noqa: E402

PINS = json.loads((REPO / "fixtures" / "pins.json").read_text(encoding="utf-8"))


def _source_coop_reachable() -> bool:
    """A quick, bounded reachability + throughput check. source.coop throttles
    hard at times; if a 64 KB range read does not return in 15 s, the full
    pipeline (a multi-GB scan) is hopeless, so skip."""
    url = PINS["catalogs"]["facilities"]["facilities_parquet"]
    req = urllib.request.Request(url, headers={"Range": "bytes=0-65535"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return len(resp.read()) > 0
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        return False


needs_network = pytest.mark.skipif(
    not _source_coop_reachable(),
    reason="source.coop unreachable or throttled; golden generation skipped",
)


# --- offline -----------------------------------------------------------------

def test_question_map_covers_exactly_the_thirty_questions():
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    yaml_ids = {f"q{q['id']}" for q in spec["questions"]}
    assert set(render.QUESTION_MAP) == yaml_ids


def test_every_mapped_template_exists():
    for qid, (stem, _cols, _lim) in render.QUESTION_MAP.items():
        path = render.SQL_DIR / f"{stem}.sql.tmpl"
        assert path.exists(), f"{qid} -> missing template {path}"


def test_pipeline_templates_exist():
    for stem in render.PIPELINE:
        assert (render.SQL_DIR / f"{stem}.sql.tmpl").exists(), stem


def test_every_template_loading_spatial_declares_its_axis_order():
    """GeoParquet is longitude-first; DuckDB's spheroid and reprojection
    functions read latitude-first unless told otherwise.

    Left undeclared, ST_Area_Spheroid shrinks a Goiás field by about a third
    and ST_Transform stretches a degree of longitude from 107 km to 121 km,
    both silently and both in the direction that still looks plausible. DuckDB
    warns that its default is due to flip, so a template that leaves this
    implicit is also a template whose goldens change under a version bump.
    """
    offenders = []
    for path in sorted(render.SQL_DIR.rglob("*.sql.tmpl")):
        sql = path.read_text("utf-8")
        if "LOAD spatial" not in sql:
            continue
        if "geometry_always_xy = true" not in sql:
            offenders.append(path.relative_to(render.SQL_DIR).as_posix())
    assert not offenders, f"spatial templates missing axis declaration: {offenders}"


def test_the_axis_declaration_precedes_every_geometry_call():
    """Setting the axis order after the first geometry call would leave that
    call reading the old convention while the file looks correct."""
    for path in sorted(render.SQL_DIR.rglob("*.sql.tmpl")):
        sql = path.read_text("utf-8")
        if "geometry_always_xy = true" not in sql:
            continue
        body = "\n".join(line for line in sql.splitlines()
                         if not line.lstrip().startswith("--"))
        declaration = body.index("geometry_always_xy = true")
        for call in ("ST_Area_Spheroid", "ST_Transform", "ST_GeometryType"):
            first = body.find(call)
            if first != -1:
                assert first > declaration, f"{path.name}: {call} precedes the setting"


def test_column_counts_match_the_question_contracts():
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    by_id = {f"q{q['id']}": q for q in spec["questions"]}
    for qid, (_stem, cols, _lim) in render.QUESTION_MAP.items():
        n_contract = len(by_id[qid]["output"]["columns"])
        assert len(cols) == n_contract, (qid, len(cols), n_contract)


def test_every_question_reporting_a_computed_area_or_distance_is_tolerant():
    """No area or distance convention is stated to the session, so the grader
    has to absorb the spread between reasonable methods.

    Geodesic hectares, an equal-area projection, and Brazil Polyconic land
    within about a percent of each other, which the geometry tolerance covers.
    The loss questions stay strict because their hectares are sums of a column
    the catalog publishes: no method choice exists there, and the tolerance
    would only cost discrimination. q23 stays strict for a different reason,
    documented alongside it.
    """
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    graded = {q["id"] for q in spec["questions"]
              if q.get("grading") == "geometry"}
    assert {"14", "15", "19", "26", "30"} <= graded
    assert "23" not in graded


def test_q23_keeps_a_numeric_field_id_out_of_integer_slack():
    """Integer slack under geometry grading is max(2, 1% of golden), and the
    comparator tries column permutations, so it cannot tell an identifier from
    a quantity. Neighbouring plots carry adjacent ids, so a tolerant q23 would
    credit an answer naming the wrong farm."""
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    q23 = next(q for q in spec["questions"] if q["id"] == "23")
    ids = [c for c in q23["output"]["columns"]
           if c["name"] == "field_id" and c["type"] == "integer"]
    assert ids, "q23 lost its integer field_id; revisit the grading choice"
    assert q23.get("grading") != "geometry"


class _FlakyConnection:
    """Fails the first `failures` executions with `error`, then succeeds."""

    def __init__(self, error, failures):
        self.error, self.failures, self.calls = error, failures, 0

    def execute(self, sql, params=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error


def test_a_truncated_remote_read_is_retried(monkeypatch, tmp_path):
    """A throttled range request comes back short, and DuckDB reports the
    truncated body as a corrupt Parquet page rather than as an I/O error. Its
    own http_retries never see it, so the stage is retried here."""
    monkeypatch.setattr(render.time, "sleep", lambda _s: None)
    con = _FlakyConnection(duckdb.Error("TProtocolException: Invalid data"), 2)
    render.run_stage(con, "eudr_crops", render.substitutions(PINS, tmp_path))
    assert con.calls == 3


def test_a_sql_error_fails_on_the_first_attempt(monkeypatch, tmp_path):
    """A broken template fails identically every time; retrying it only delays
    the traceback."""
    monkeypatch.setattr(render.time, "sleep", lambda _s: None)
    con = _FlakyConnection(duckdb.Error("Binder Error: no such column"), 2)
    with pytest.raises(duckdb.Error):
        render.run_stage(con, "eudr_crops",
                         render.substitutions(PINS, tmp_path))
    assert con.calls == 1


def test_remote_reads_are_configured_to_outlast_a_slow_route():
    """DuckDB defaults to a 30 s HTTP timeout, and the CAR scan runs longer
    than that on a slow route."""
    con = duckdb.connect()
    render.tune_for_remote_reads(con)
    settings = dict(con.execute(
        "SELECT name, value FROM duckdb_settings() WHERE name IN "
        "('http_timeout', 'http_retries')").fetchall())
    assert int(settings["http_timeout"]) >= 120
    assert int(settings["http_retries"]) >= 5


def test_substitution_leaves_no_placeholders(tmp_path):
    subs = render.substitutions(PINS, tmp_path)
    seen = set()
    for stem, _cols, _lim in render.QUESTION_MAP.values():
        if stem in seen:
            continue
        seen.add(stem)
        sql = render.render_sql(stem, subs)
        assert "${" not in sql, f"{stem} has an unfilled placeholder"
    for stem in render.PIPELINE:
        sql = render.render_sql(stem, subs)
        assert "${" not in sql, f"{stem} has an unfilled placeholder"


def test_scope_tables_build_locally(tmp_path):
    # eudr_crops needs no network; it is the one pipeline stage that runs offline.
    subs = render.substitutions(PINS, tmp_path)
    con = duckdb.connect()
    con.execute(render.render_sql("eudr_crops", subs))
    n_classes, n_in_scope, n_routed = con.execute(
        "SELECT (SELECT count(*) FROM eudr_crops),"
        " (SELECT count(*) FROM eudr_crops WHERE in_scope),"
        " (SELECT count(DISTINCT mbmode24) FROM crop_routing)").fetchone()
    assert (n_classes, n_in_scope, n_routed) == (13, 5, 6)


# --- needs the live catalogs -------------------------------------------------

@needs_network
def test_render_all_emits_thirty_goldens_and_manifest(tmp_path):
    written = render.render_all(PINS, tmp_path)
    assert sorted(written) == [f"q{n:02d}" for n in range(1, 31)]
    for path in written.values():
        assert path.exists()
        assert path.read_text().count("\n") >= 2  # header + >=1 row
    assert (tmp_path / "SHA256SUMS").exists()
    assert (tmp_path / "workflow.csv").exists()


@needs_network
def test_render_all_is_deterministic(tmp_path):
    a = render.render_all(PINS, tmp_path / "a")
    b = render.render_all(PINS, tmp_path / "b")
    for qid in a:
        assert a[qid].read_bytes() == b[qid].read_bytes(), qid


@needs_network
def test_loss_partitions_into_in_scope_and_excluded(tmp_path):
    """q17 (in-scope) and q18 (excluded) field counts must sum to the matched
    total: every matched field is either in scope or out, never dropped."""
    render.render_all(PINS, tmp_path)

    def total(name):
        rows = list(__import__("csv").reader(
            open(tmp_path / name, encoding="utf-8")))[1:]
        return sum(int(r[1]) for r in rows)

    matched = int(list(__import__("csv").reader(
        open(tmp_path / "q09.csv", encoding="utf-8")))[1][0])
    assert total("q17.csv") + total("q18.csv") == matched


# --- statements that had to be run against synthetic inputs ------------------

def _create_statement(sql: str, table: str) -> str:
    """The CREATE statement for `table`, lifted out of a rendered template.

    Both statements below sit downstream of remote catalogs, so running the
    whole template offline is impossible. Lifting the one statement out and
    running it against a synthetic input tests the shipped SQL itself rather
    than a paraphrase of it.
    """
    head = f"CREATE OR REPLACE TABLE {table} AS"
    body = "\n".join(line for line in sql.splitlines()
                     if not line.lstrip().startswith("--"))
    start = body.index(head)
    end = body.index(";", start)
    return body[start:end + 1]


def test_candidate_cap_applies(tmp_path):
    """The cap used to bind to a placeholder column and never fire.

    `candidates` carried its own `rank`, set to 0 by every INSERT, and QUALIFY
    resolved the bare name against that base column rather than the window
    alias -- so `0 <= 5` held for every row and q28 reported 39 candidates
    where the policy documents 5.
    """
    subs = render.substitutions(PINS, tmp_path)
    sql = render.render_sql("coop_match", subs)
    stmt = _create_statement(sql, "candidates_final")

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE candidates AS
        SELECT 'CAD-1' AS cod_imovel, '5201' AS cod_ibge,
               CAST(i AS VARCHAR) AS entity_id, 'silo' AS entity_kind,
               'intake_point' AS tier, 'capacity' AS basis,
               1.0 AS evidence_value, CAST(i AS DOUBLE) AS distance_km,
               '' AS flags
        FROM range(1, 41) t(i);
    """)
    con.execute(stmt)
    n_kept, worst = con.execute(
        "SELECT count(*), max(rank) FROM candidates_final").fetchone()
    cap = PINS["coops"]["max_candidates"]
    assert (n_kept, worst) == (cap, cap)


def test_dominant_class_weighs_hectares_not_field_count(tmp_path):
    """dominant_mb was mode() over field count, which breaks ties arbitrarily
    and lets many small plots outvote one large one.

    EUDR_CROPS.md now defines the dominant class as the one covering the most
    hectares, ties broken by the lower class code. The rule picks the delivery
    tier, so it decides whether a farmer hears from a silo or a slaughterhouse.
    """
    subs = render.substitutions(PINS, tmp_path)
    stmt = _create_statement(render.render_sql("match", subs),
                             "dominant_class")

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE matched_fields AS
        SELECT * FROM (VALUES
            -- one big soya plot against four small pasture plots
            ('CAD-MIXED', 39, 144.9),
            ('CAD-MIXED', 15,  12.0), ('CAD-MIXED', 15, 11.0),
            ('CAD-MIXED', 15,  10.0), ('CAD-MIXED', 15,  9.5),
            -- equal hectares either way
            ('CAD-TIE',   39,  60.0), ('CAD-TIE',   15, 60.0),
            ('CAD-NULL',  NULL, 20.0)
        ) v(cod_imovel, mbmode24, field_area_ha);
    """)
    con.execute(stmt)
    rows = dict(con.execute(
        "SELECT cod_imovel, dominant_mb FROM dominant_class").fetchall())
    assert rows["CAD-MIXED"] == 39    # 144.9 ha of soya beats 42.5 ha over 4 plots
    assert rows["CAD-TIE"] == 15      # equal hectares -> lower class code
    assert "CAD-NULL" not in rows     # no classified field, no dominant class


def test_excluded_count_ignores_the_buffer_only_near_misses(tmp_path):
    """q12 asks how many fields intersect a listed parcel and fail both tests.

    The count used to include fields whose only overlap was with the parcels
    buffered outward by neighbor_gap_tolerance_m and dissolved -- fields that
    touch no parcel at all. That is a defensible audit population and an
    indefensible reading of the question.
    """
    subs = render.substitutions(PINS, tmp_path)
    stmt = render.render_sql("match_excluded", subs)

    con = duckdb.connect()
    con.execute("""
        CREATE TABLE decision AS
        SELECT * FROM (VALUES
            -- overlaps a parcel, under the bar on both tests: counted
            (1, 0.30, 0.42, false, false),
            (2, 0.10, 0.55, false, false),
            -- inside the 25 m cushion only, touching no parcel: not counted
            (3, 0.00, 0.31, false, false),
            (4, NULL, 0.12, false, false),
            -- admitted, so not excluded at all
            (5, 0.80, 0.90, true,  true),
            -- nowhere near the list
            (6, 0.00, 0.00, false, false)
        ) v(field_id, max_single_frac, union_frac, by_primary, by_aggregate);
    """)
    con.execute(stmt)
    assert con.execute("SELECT n_excluded FROM _q").fetchone()[0] == 2


def test_no_qualify_binds_a_base_column_named_rank():
    """Guard against the shadowing coming back in any template."""
    for path in sorted(render.SQL_DIR.glob("*.sql.tmpl")):
        text = path.read_text(encoding="utf-8")
        assert "QUALIFY rank" not in text, path.name
        assert "AS rank," not in text.replace(") AS rank,", ""), path.name
