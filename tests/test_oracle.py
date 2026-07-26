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


def test_column_counts_match_the_question_contracts():
    spec = yaml.safe_load((REPO / "fixtures/questions.yaml").read_text("utf-8"))
    by_id = {f"q{q['id']}": q for q in spec["questions"]}
    for qid, (_stem, cols, _lim) in render.QUESTION_MAP.items():
        n_contract = len(by_id[qid]["output"]["columns"])
        assert len(cols) == n_contract, (qid, len(cols), n_contract)


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
