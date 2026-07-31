"""Checks on the input list and the reconciliation it is there to exercise.

The list used to be pristine, which made policies/INPUTS.md unmeasurable: every
rule in it described a case the fixture never produced, so withholding the
document cost a session nothing. These tests pin the defects into place. They
run offline against the committed fixtures; the generator's determinism check
needs the cached CAR extract and skips without it.
"""

import csv
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "harness"))
sys.path.insert(0, str(REPO / "fixtures" / "lists"))

import make_lists  # noqa: E402
import run  # noqa: E402

LISTS = REPO / "fixtures" / "lists"
GOLDEN = REPO / "fixtures" / "golden"

# What correct handling of the shipped list produces. The parcel count is the
# invariant the defect design turns on: every defect resolves to a parcel that
# was already on the clean list, so q06 through q30 keep their answers and the
# whole change costs one golden regeneration rather than two answer sets.
RESOLVED_PARCELS = 117
EXPECTED_BUCKETS = {
    "input_rows": 119,
    "resolved_clean": 114,
    "centroid_resolved": 1,
    "geometry_resolved": 1,
    "axis_repaired": 1,
    "duplicates_removed": 1,
    "unresolvable": 1,
}
BUCKETS = [k for k in EXPECTED_BUCKETS if k != "input_rows"]


def read_list() -> list[dict]:
    with open(LISTS / "goias-sample.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def golden_row(qid: str) -> dict:
    with open(GOLDEN / f"{qid}.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1, f"{qid} should be a single row"
    return {k: int(v) for k, v in rows[0].items()}


# --- the shipped fixtures ----------------------------------------------------

def test_every_promised_input_file_exists():
    """run.py copies these into the workspace before a container starts, so a
    name with no file behind it kills the session at shutil.copy. The geometry
    and split modes shipped in exactly that state and nothing had run them."""
    for mode in run.INPUT_FILES:
        for path in run.list_files(mode):
            assert path.exists(), f"--input-mode {mode} names a missing {path}"


def test_the_list_carries_one_of_every_defect():
    rows = read_list()
    assert len(rows) == EXPECTED_BUCKETS["input_rows"]

    seen, duplicated = set(), []
    for row in rows:
        key = (row["cod_imovel"], row["geometry"])
        if key in seen:
            duplicated.append(key)
        seen.add(key)
    assert len(duplicated) == EXPECTED_BUCKETS["duplicates_removed"]
    assert duplicated[0][0] == make_lists.DONORS["duplicate"]

    id_less = [r for r in rows if not r["cod_imovel"]]
    assert len(id_less) == 4, "three resolvable defects plus the unresolvable one"
    assert all(r["geometry"] for r in id_less), "an id-less row needs a geometry"


def test_the_axis_rule_fires_exactly_once():
    """The rule repairs a geometry that is outside Brazil until latitude and
    longitude are exchanged. Firing on a second row would mean a defect landing
    in the wrong bucket; firing on none would mean the bucket is empty and the
    rule untested. The unresolvable point deliberately does not qualify: it sits
    inside Brazil's bounding box and out in the Atlantic, so nothing repairs it
    and it fails on containment instead, which is the case INPUTS.md names."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET geometry_always_xy = true")
    qualifies = []
    for row in read_list():
        if not row["geometry"]:
            continue
        inside, inside_flipped = con.execute(
            """
            SELECT ST_Intersects(g, box), ST_Intersects(ST_FlipCoordinates(g), box)
            FROM (SELECT ST_GeomFromText(?) AS g,
                         ST_MakeEnvelope(-75, -34, -28, 6) AS box)
            """,
            [row["geometry"]],
        ).fetchone()
        if not inside and inside_flipped:
            qualifies.append(row["geometry"][:16])
    con.close()
    assert len(qualifies) == EXPECTED_BUCKETS["axis_repaired"], qualifies
    assert make_lists.UNRESOLVABLE_POINT[:16] not in qualifies


def test_the_damaged_list_still_names_the_whole_portfolio():
    """The ids left in the file, plus the parcels the defects were carved from,
    are the 117 the clean list carried. This catches a donor swapped for a
    parcel that was never on the list — the one edit that would move the
    portfolio while every count above still looked right. It does not catch a
    defect that resolves to the *wrong* parcel; that shows up as movement in
    the q06-q30 goldens, which is where it belongs."""
    named = {r["cod_imovel"] for r in read_list() if r["cod_imovel"]}
    assert len(named | set(make_lists.DONORS.values())) == RESOLVED_PARCELS
    carved = {donor for defect, donor in make_lists.DONORS.items() if defect != "duplicate"}
    assert not named & carved, (
        "a defect donor must not also appear as a clean id, or the defect "
        "would resolve to a parcel the list already carries and prove nothing"
    )


def test_the_three_encodings_describe_the_same_list():
    """A session is graded against one golden set whichever encoding it was
    handed, so the encodings have to carry the same rows and the same ids."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    geometry_rows, geometry_ids = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE cod_imovel <> '') "
        f"FROM read_parquet('{(LISTS / 'goias-sample.parquet').as_posix()}')"
    ).fetchone()
    split_rows, = con.execute(
        "SELECT count(*) FROM read_parquet("
        f"'{(LISTS / 'goias-sample-geom.parquet').as_posix()}')"
    ).fetchone()
    con.close()

    csv_rows = read_list()
    assert geometry_rows == len(csv_rows)
    assert geometry_ids == sum(1 for r in csv_rows if r["cod_imovel"])
    assert split_rows == geometry_ids


@pytest.mark.skipif(
    not (GOLDEN / "_work" / "cad.parquet").exists(),
    reason="the cached CAR extract is regenerable and gitignored",
)
def test_the_generator_is_deterministic(tmp_path):
    """No RNG and no network, so the same extract must give the same bytes.
    Otherwise a regeneration moves the golden fingerprint for no reason and
    every stored run silently stops being comparable."""
    make_lists.generate(GOLDEN / "_work", tmp_path)
    for name in ("goias-sample.csv", "goias-sample.parquet",
                 "goias-sample-geom.parquet"):
        assert (tmp_path / name).read_bytes() == (LISTS / name).read_bytes(), name


def test_the_generator_never_reaches_the_network():
    """It reads a cached parquet and writes three files. A URL in it would make
    the fixture depend on whatever source.coop served that day."""
    source = (LISTS / "make_lists.py").read_text(encoding="utf-8")
    for marker in ("http://", "https://", "s3://"):
        assert marker not in source, f"make_lists.py reaches for {marker}"


# --- the reconciliation golden -----------------------------------------------

def test_the_reconciliation_identity_holds():
    """policies/INPUTS.md:34. Every input row lands in exactly one bucket, so
    the buckets sum to the arrival count. A golden that breaks this is either a
    row counted twice or one dropped silently, which is the failure the whole
    document exists to prevent."""
    q31 = golden_row("q31")
    assert sum(q31[b] for b in BUCKETS) == q31["input_rows"]


def test_every_bucket_is_exercised():
    """A zero bucket is an unexercised rule, and an unexercised rule is what
    made the no-inputs ablation score 100% on a list with nothing to mishandle."""
    q31 = golden_row("q31")
    assert q31 == EXPECTED_BUCKETS
    for bucket in BUCKETS:
        assert q31[bucket] > 0, f"{bucket} has nothing to fire on"


def test_the_defects_leave_the_portfolio_where_it_was():
    """Handled correctly, the dirty list resolves to the same parcels the clean
    one did — which is what lets q06 through q30 keep their answers. Pinned
    here so a later fixture edit that quietly drops a parcel fails loudly
    instead of shifting every downstream golden."""
    q05 = golden_row("q05")
    assert q05["list_ids"] == RESOLVED_PARCELS
    assert q05["found_in_car"] == RESOLVED_PARCELS
    assert q05["missing"] == 0

    q31 = golden_row("q31")
    resolved_rows = q31["input_rows"] - q31["duplicates_removed"] - q31["unresolvable"]
    assert resolved_rows == RESOLVED_PARCELS


def test_no_template_reads_the_list_through_a_silent_distinct():
    """SELECT DISTINCT over the raw list collapses duplicates before anything
    can count them, which made the identity above unmeasurable: a dropped row
    and a deduplicated one produced the same extract. Resolution now happens in
    list_resolve, and every other template reads what it wrote."""
    offenders = []
    for path in sorted((REPO / "oracle" / "sql").rglob("*.sql.tmpl")):
        if path.name == "list_resolve.sql.tmpl":
            continue
        body = "\n".join(line for line in path.read_text("utf-8").splitlines()
                         if not line.lstrip().startswith("--"))
        if "read_csv_auto" in body:
            offenders.append(path.name)
    assert not offenders, f"templates still reading the raw list: {offenders}"


def test_the_generator_runs_from_the_command_line():
    """It is documented as `python fixtures/lists/make_lists.py`, and a
    generator that only works when imported is a generator nobody reruns."""
    proc = subprocess.run(
        [sys.executable, str(LISTS / "make_lists.py"), "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--work" in proc.stdout
