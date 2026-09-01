"""Oracle checks.

The offline ones run always: every question maps to a template that exists, the
templates have no unfilled placeholders after substitution, the QUESTION_MAP
covers exactly the 31 questions, and the local scope tables build.

The generation, determinism, and partition checks need the live catalogs on
source.coop and are the real acceptance gate. Each renders the whole oracle,
which rescans a 3.3 GB CAR file, so they carry the `network` marker and sit out
of the default suite. Run them with `pytest -m network`; CI does so nightly.
They still skip when the host is unreachable, because a throttled route turns a
multi-GB scan into a hang rather than a result.
"""

import csv
import json
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "oracle"))
sys.path.insert(0, str(REPO / "harness"))

import render
from probe import USER_AGENT

PINS = json.loads((REPO / "fixtures" / "pins.json").read_text(encoding="utf-8"))


def one_row(result: duckdb.DuckDBPyConnection) -> tuple[Any, ...]:
    """The single row a query is expected to return.

    fetchone() is optional because a query can return nothing. These all ask
    for aggregates, so nothing means the query is wrong rather than the data
    being empty.
    """
    row = result.fetchone()
    assert row is not None, "the query returned no row"
    return row


def _source_coop_reachable() -> bool:
    """A quick, bounded reachability + throughput check. source.coop throttles
    hard at times; if a 64 KB range read does not return in 15 s, the full
    pipeline (a multi-GB scan) is hopeless, so skip."""
    url = PINS["catalogs"]["facilities"]["facilities_parquet"]
    req = urllib.request.Request(
        url, headers={"Range": "bytes=0-65535", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return len(resp.read()) > 0
    except (urllib.error.URLError, TimeoutError):
        return False


def needs_network(test: Callable[..., None]) -> Callable[..., None]:
    """Opt-in by marker, and skipped anyway when the host is not answering."""
    skip_if_down = pytest.mark.skipif(
        not _source_coop_reachable(),
        reason="source.coop unreachable or throttled; golden generation skipped",
    )
    marked: Callable[..., None] = pytest.mark.network(skip_if_down(test))
    return marked


# --- offline -----------------------------------------------------------------


def test_question_map_covers_exactly_the_question_set() -> None:
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    yaml_ids = {f"q{q['id']}" for q in spec["questions"]}
    assert set(render.QUESTION_MAP) == yaml_ids


def test_every_mapped_template_exists() -> None:
    for qid, (stem, _cols, _lim) in render.QUESTION_MAP.items():
        path = render.SQL_DIR / f"{stem}.sql.tmpl"
        assert path.exists(), f"{qid} -> missing template {path}"


def test_pipeline_templates_exist() -> None:
    for stem in render.PIPELINE:
        assert (render.SQL_DIR / f"{stem}.sql.tmpl").exists(), stem


def test_every_template_loading_spatial_declares_its_axis_order() -> None:
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


def test_the_axis_declaration_precedes_every_geometry_call() -> None:
    """Setting the axis order after the first geometry call would leave that
    call reading the old convention while the file looks correct."""
    for path in sorted(render.SQL_DIR.rglob("*.sql.tmpl")):
        sql = path.read_text("utf-8")
        if "geometry_always_xy = true" not in sql:
            continue
        body = "\n".join(
            line for line in sql.splitlines() if not line.lstrip().startswith("--")
        )
        declaration = body.index("geometry_always_xy = true")
        for call in ("ST_Area_Spheroid", "ST_Transform", "ST_GeometryType"):
            first = body.find(call)
            if first != -1:
                assert first > declaration, f"{path.name}: {call} precedes the setting"


def test_the_decision_table_carries_unrounded_fractions() -> None:
    """Rounding a containment fraction that later questions filter on turns a
    display choice into an undeclared threshold.

    At 4 decimals it excluded 16 fields that overlap a parcel by less than
    0.005% of their area from the excluded-field count, which is a
    minimum-overlap rule MATCHING.md never states. Views round on output.
    """
    sql = (render.SQL_DIR / "match.sql.tmpl").read_text("utf-8")
    body = sql[sql.index("CREATE OR REPLACE TABLE decision") :]
    body = body[: body.index(";")]
    for column in ("max_single_frac", "union_frac"):
        assert f"round(coalesce({column}" not in body.replace(" ", "")
    assert "round(" not in body, "decision must carry raw fractions"


def test_column_counts_match_the_question_contracts() -> None:
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    by_id = {f"q{q['id']}": q for q in spec["questions"]}
    for qid, (_stem, cols, _lim) in render.QUESTION_MAP.items():
        n_contract = len(by_id[qid]["output"]["columns"])
        assert len(cols) == n_contract, (qid, len(cols), n_contract)


def test_every_question_reporting_a_computed_area_or_distance_is_tolerant() -> None:
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
    graded = {q["id"] for q in spec["questions"] if q.get("grading") == "geometry"}
    assert {"14", "15", "19", "26", "30"} <= graded
    assert "23" not in graded


def test_q23_keeps_a_numeric_field_id_out_of_integer_slack() -> None:
    """Integer slack under geometry grading is max(2, 1% of golden), and the
    comparator tries column permutations, so it cannot tell an identifier from
    a quantity. Neighbouring plots carry adjacent ids, so a tolerant q23 would
    credit an answer naming the wrong farm."""
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    q23 = next(q for q in spec["questions"] if q["id"] == "23")
    ids = [
        c
        for c in q23["output"]["columns"]
        if c["name"] == "field_id" and c["type"] == "integer"
    ]
    assert ids, "q23 lost its integer field_id; revisit the grading choice"
    assert q23.get("grading") != "geometry"


class _FlakyConnection:
    """Fails the first `failures` executions with `error`, then succeeds."""

    def __init__(self, error: Exception, failures: int) -> None:
        self.error, self.failures, self.calls = error, failures, 0

    def execute(self, sql: str, params: object = None) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error


def test_a_truncated_remote_read_is_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A throttled range request comes back short, and DuckDB reports the
    truncated body as a corrupt Parquet page rather than as an I/O error. Its
    own http_retries never see it, so the stage is retried here."""
    monkeypatch.setattr(render.time, "sleep", lambda _s: None)
    con = _FlakyConnection(duckdb.Error("TProtocolException: Invalid data"), 2)
    render.run_stage(con, "eudr_crops", render.substitutions(PINS, tmp_path))
    assert con.calls == 3


def test_a_sql_error_fails_on_the_first_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken template fails identically every time; retrying it only delays
    the traceback."""
    monkeypatch.setattr(render.time, "sleep", lambda _s: None)
    con = _FlakyConnection(duckdb.Error("Binder Error: no such column"), 2)
    with pytest.raises(duckdb.Error):
        render.run_stage(con, "eudr_crops", render.substitutions(PINS, tmp_path))
    assert con.calls == 1


def test_remote_reads_are_configured_to_outlast_a_slow_route() -> None:
    """DuckDB defaults to a 30 s HTTP timeout, and the CAR scan runs longer
    than that on a slow route."""
    con = duckdb.connect()
    render.tune_for_remote_reads(con)
    settings = dict(
        con.execute(
            "SELECT name, value FROM duckdb_settings() WHERE name IN "
            "('http_timeout', 'http_retries')"
        ).fetchall()
    )
    assert int(settings["http_timeout"]) >= 120
    assert int(settings["http_retries"]) >= 5


def test_substitution_leaves_no_placeholders(tmp_path: Path) -> None:
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


def test_scope_tables_build_locally(tmp_path: Path) -> None:
    # eudr_crops needs no network; it is the one pipeline stage that runs offline.
    subs = render.substitutions(PINS, tmp_path)
    con = duckdb.connect()
    con.execute(render.render_sql("eudr_crops", subs))
    n_classes, n_in_scope, n_routed = one_row(
        con.execute(
            "SELECT (SELECT count(*) FROM eudr_crops),"
            " (SELECT count(*) FROM eudr_crops WHERE in_scope),"
            " (SELECT count(DISTINCT mbmode24) FROM crop_routing)"
        )
    )
    assert (n_classes, n_in_scope, n_routed) == (13, 5, 6)


# --- needs the live catalogs -------------------------------------------------


@needs_network
def test_render_all_emits_every_golden_and_manifest(tmp_path: Path) -> None:
    written = render.render_all(PINS, tmp_path)
    assert sorted(written) == [f"q{n:02d}" for n in range(1, 32)]
    for path in written.values():
        assert path.exists()
        assert path.read_text().count("\n") >= 2  # header + >=1 row
    assert (tmp_path / "SHA256SUMS").exists()
    assert (tmp_path / "workflow.csv").exists()


@needs_network
def test_render_all_is_deterministic(tmp_path: Path) -> None:
    a = render.render_all(PINS, tmp_path / "a")
    b = render.render_all(PINS, tmp_path / "b")
    for qid in a:
        assert a[qid].read_bytes() == b[qid].read_bytes(), qid


@needs_network
def test_loss_partitions_into_in_scope_and_excluded(tmp_path: Path) -> None:
    """q17 (in-scope) and q18 (excluded) field counts must sum to the matched
    total: every matched field is either in scope or out, never dropped."""
    render.render_all(PINS, tmp_path)

    def rows(name: str) -> list[list[str]]:
        with (tmp_path / name).open(encoding="utf-8") as handle:
            return list(csv.reader(handle))

    def total(name: str) -> int:
        return sum(int(row[1]) for row in rows(name)[1:])

    matched = int(rows("q09.csv")[1][0])
    assert total("q17.csv") + total("q18.csv") == matched


# --- statements that had to be run against synthetic inputs ------------------


def _create_statement(sql: str, table: str) -> str:
    """The CREATE statement for `table`, lifted out of a rendered template.

    The statements below sit downstream of remote catalogs, so running the
    whole template offline is impossible. Lifting the one statement out and
    running it against a synthetic input tests the shipped SQL itself rather
    than a paraphrase of it.
    """
    head = f"CREATE OR REPLACE TABLE {table} AS"
    body = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    start = body.index(head)
    end = body.index(";", start)
    return body[start : end + 1]


def test_candidate_cap_applies(tmp_path: Path) -> None:
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
    n_kept, worst = one_row(
        con.execute("SELECT count(*), max(rank) FROM candidates_final")
    )
    cap = PINS["coops"]["max_candidates"]
    assert (n_kept, worst) == (cap, cap)


def test_dominant_class_weighs_hectares_not_field_count(tmp_path: Path) -> None:
    """dominant_mb was mode() over field count, which breaks ties arbitrarily
    and lets many small plots outvote one large one.

    EUDR_CROPS.md now defines the dominant class as the one covering the most
    hectares, ties broken by the lower class code. The rule picks the delivery
    tier, so it decides whether a farmer hears from a silo or a slaughterhouse.
    """
    subs = render.substitutions(PINS, tmp_path)
    stmt = _create_statement(render.render_sql("match", subs), "dominant_class")

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
    rows = dict(
        con.execute("SELECT cod_imovel, dominant_mb FROM dominant_class").fetchall()
    )
    assert rows["CAD-MIXED"] == 39  # 144.9 ha of soya beats 42.5 ha over 4 plots
    assert rows["CAD-TIE"] == 15  # equal hectares -> lower class code
    assert "CAD-NULL" not in rows  # no classified field, no dominant class


def test_the_primary_cadaster_gives_a_tie_to_the_lowest_id(tmp_path: Path) -> None:
    """CAR parcels overlap, so a field inside the overlap of two of them
    intersects each in the same polygon and the two areas agree bit for bit.

    This was arg_max, which resolves such a tie by scan order. 54 of the 793
    matched fields tie, and one of them holds the only flagged loss of its
    parcel, so the arbitrary pick moved that parcel in and out of the flagged
    set and took eight questions with it. MATCHING.md gives the tie to the
    lowest cod_imovel; the row order below is reversed between the two tied
    fields so that scan order cannot pass this test.

    A tie is anything inside primary_tie_tolerance, not just bit-equality,
    because the fraction moves by about that much between area methods. Field 4
    sits inside the tolerance and ties despite the larger area; field 6 sits
    just outside it and keeps its winner.
    """
    subs = render.substitutions(PINS, tmp_path)
    stmt = _create_statement(render.render_sql("match", subs), "single")
    tol = PINS["matching"]["primary_tie_tolerance"]

    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE ov AS
        SELECT * FROM (VALUES
            -- a clear winner: the tie rule never reaches it
            (1, 'GO-B', 80.0, 100.0), (1, 'GO-A', 20.0, 100.0),
            -- exact ties, listed in both orders
            (2, 'GO-Z', 50.0, 100.0), (2, 'GO-C', 50.0, 100.0),
            (3, 'GO-C', 50.0, 100.0), (3, 'GO-Z', 50.0, 100.0),
            -- inside the tolerance: tied, so the lower id takes it
            (4, 'GO-Z', {50.0 + 100 * tol / 10:.12f}, 100.0),
            (4, 'GO-C', 50.0, 100.0),
            -- a field of zero area keeps a null fraction, as max() gave
            (5, 'GO-A', 0.0, 0.0),
            -- outside the tolerance: a real winner, and it keeps the field
            (6, 'GO-Z', {50.0 + 100 * tol * 10:.12f}, 100.0),
            (6, 'GO-C', 50.0, 100.0)
        ) v(field_id, cod_imovel, inter_area, field_area);
    """)
    con.execute(stmt)
    rows = dict(
        con.execute("SELECT field_id, primary_cod_imovel FROM single").fetchall()
    )
    assert rows == {1: "GO-B", 2: "GO-C", 3: "GO-C", 4: "GO-C", 5: "GO-A", 6: "GO-Z"}

    fracs = dict(con.execute("SELECT field_id, max_single_frac FROM single").fetchall())
    assert fracs[1] == pytest.approx(0.8)  # the winner's ratio, not the loser's
    assert fracs[2] == pytest.approx(0.5)
    assert fracs[5] is None  # nullif(0, 0), same as before
    # The reported fraction stays the true maximum even when a tie hands the
    # field to the other parcel, so contain_threshold is unaffected.
    assert fracs[4] == pytest.approx(0.5 + tol / 10, abs=1e-15)


def test_the_tie_tolerance_is_narrower_than_the_closest_real_separation() -> None:
    """The tolerance absorbs the spread between area methods and nothing more.

    Measured on the pinned extracts: the same fraction moves by a median 1e-12
    to 1e-11 between planar degrees, EPSG:5880 and the spheroid, the two fields
    that flip when the oracle switches to spheroid areas differ by 1.2e-11 and
    1.7e-10, and the nearest genuinely separated pair differs by 1.6e-8. A
    tolerance outside that window either stops fixing the flips or starts
    swallowing real winners.
    """
    tol = PINS["matching"]["primary_tie_tolerance"]
    assert 1.7e-10 < tol < 1.6e-8


def test_no_template_picks_a_label_with_an_unordered_aggregate() -> None:
    """arg_max and mode resolve ties by scan order, so a golden built on one
    can change under a replan with identical inputs, and a session that breaks
    the tie deterministically is marked wrong for being reproducible.

    Both have already done exactly that here: mode() in dominant_class and
    arg_max() in the primary-cadaster pick. Where a label has to be chosen,
    the ordering belongs in the query, spelled out in an ORDER BY that names
    a total tie-break.
    """
    offenders = []
    for path in sorted(render.SQL_DIR.rglob("*.sql.tmpl")):
        body = "\n".join(
            line
            for line in path.read_text("utf-8").splitlines()
            if not line.lstrip().startswith("--")
        )
        for call in ("arg_max(", "arg_min(", "mode("):
            if call in body:
                offenders.append(f"{path.name}: {call}")
    assert not offenders, f"unordered aggregate choosing a label: {offenders}"


def test_excluded_count_ignores_the_buffer_only_near_misses(tmp_path: Path) -> None:
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
    assert one_row(con.execute("SELECT n_excluded FROM _q"))[0] == 2


def test_no_qualify_binds_a_base_column_named_rank() -> None:
    """Guard against the shadowing coming back in any template."""
    for path in sorted(render.SQL_DIR.glob("*.sql.tmpl")):
        text = path.read_text(encoding="utf-8")
        assert "QUALIFY rank" not in text, path.name
        assert "AS rank," not in text.replace(") AS rank,", ""), path.name
