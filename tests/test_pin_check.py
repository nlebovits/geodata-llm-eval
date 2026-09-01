"""The pin check is what makes a vanished remote object a named failure.

Without it the first symptom of a republished catalog is a session dying eight
minutes into a run, or — worse — goldens quietly regenerated against a file the
committed results were never graded on. These tests pin the two behaviors that
distinction rests on: a missing object and a changed object report differently,
and a check that read no footer never claims the footer matches.
"""

import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any, ClassVar, Self

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "harness"))

import duckdb
import pytest
from pin_check import (
    CHANGED,
    FINGERPRINTED,
    MISSING,
    OK,
    UNPINNED,
    Asset,
    assets,
    check,
    differing,
    fingerprint,
    geo_identity,
    geo_metadata,
    live_footer,
    live_head,
    main,
    recorded,
    repin,
    with_fingerprint,
    write_pins,
)

CAR = "https://example.invalid/car.parquet"
FIELDS = "https://example.invalid/fields.parquet"


class NullConnection:
    """Enough of a connection to select the deep path, and no more.

    `check` only asks whether it was handed one; the footer read itself is
    patched out in the test that uses this.
    """

    def execute(self, sql: str) -> Any:
        raise AssertionError("the footer read should have been patched")


def sample_pins() -> dict[str, Any]:
    """Two catalogs, one of them pinned, shaped like fixtures/pins.json."""
    return {
        "catalogs": {
            "cadastral": {
                "catalog": "https://example.invalid/catalog.json",
                "car_parquet": CAR,
                "snapshot": "2026-07-16",
                "identity": {
                    "car_parquet": with_fingerprint(
                        {
                            "bytes": 100,
                            "etag": '"abc"',
                            "rows": 8,
                            "row_groups": 2,
                            "epsg": 4326,
                            "geoparquet_version": "1.1.0",
                            "bbox_covering": True,
                            "columns": ["cod_imovel:VARCHAR"],
                        }
                    )
                },
            },
            "trazo": {
                "catalog": "https://example.invalid/trazo.json",
                "goias_parquet": FIELDS,
                "n_collections": 3,
            },
        }
    }


def head(**overrides: Any) -> dict[str, Any]:
    reading = {"ok": True, "bytes": 100, "etag": '"abc"'}
    reading.update(overrides)
    return reading


# --- what counts as a pinned asset -------------------------------------------


def test_assets_are_the_parquet_keys_in_a_stable_order() -> None:
    found = assets(sample_pins())
    assert [(a.catalog, a.key) for a in found] == [
        ("cadastral", "car_parquet"),
        ("trazo", "goias_parquet"),
    ]


def test_a_key_that_names_no_parquet_is_not_an_asset() -> None:
    """catalog.json and n_collections sit in the same block and are not files
    the goldens were built from, so a check must not try to HEAD them."""
    urls = {a.url for a in assets(sample_pins())}
    assert urls == {CAR, FIELDS}


def test_an_asset_added_to_the_pins_is_checked_without_editing_the_module() -> None:
    pins = sample_pins()
    pins["catalogs"]["trazo"]["mato_grosso_parquet"] = "https://example.invalid/mt.pq"
    assert len(assets(pins)) == 3


def test_recorded_is_none_when_a_pin_carries_no_identity() -> None:
    pins = sample_pins()
    trazo = Asset("trazo", "goias_parquet", FIELDS)
    assert recorded(pins, trazo) is None
    assert recorded(pins, Asset("cadastral", "car_parquet", CAR)) is not None


# --- the fingerprint ---------------------------------------------------------


def test_the_fingerprint_ignores_dict_order() -> None:
    one = {"bytes": 1, "rows": 2}
    other = {"rows": 2, "bytes": 1}
    assert fingerprint(one) == fingerprint(other)


def test_the_fingerprint_moves_when_a_covered_field_moves() -> None:
    base = {name: 1 for name in FINGERPRINTED}
    for name in FINGERPRINTED:
        moved = dict(base, **{name: 2})
        assert fingerprint(moved) != fingerprint(base), name


def test_the_fingerprint_ignores_anything_outside_the_covered_set() -> None:
    """It is a digest of the file's identity, not of the pin's bookkeeping, so
    adding a note beside a pin must not read as the object changing."""
    base = {"bytes": 1}
    assert fingerprint(dict(base, note="repinned by hand")) == fingerprint(base)


def test_with_fingerprint_records_every_covered_field() -> None:
    stamped = with_fingerprint({"bytes": 1})
    assert set(stamped) == set(FINGERPRINTED) | {"fingerprint"}
    assert stamped["rows"] is None
    assert stamped["fingerprint"] == fingerprint(stamped)


def test_the_committed_pins_carry_fingerprints_that_match_their_fields() -> None:
    """An offline guard against a hand-edited pin: change a row count without
    recomputing the digest and this fails without touching the network."""
    pins = json.loads((REPO / "fixtures/pins.json").read_text(encoding="utf-8"))
    for asset in assets(pins):
        identity = recorded(pins, asset)
        assert identity is not None, f"{asset.catalog}.{asset.key} is unpinned"
        assert identity["fingerprint"] == fingerprint(identity), asset.key


# --- reading the geometry metadata -------------------------------------------


def test_a_geoparquet_column_without_a_crs_reads_as_4326() -> None:
    """The specification defines an absent crs as OGC:CRS84. BR_facilities.parquet
    omits it, and reading that as an unknown CRS would report a false change on
    every check."""
    geo = {"version": "1.1.0", "primary_column": "g", "columns": {"g": {}}}
    assert geo_identity(geo)["epsg"] == 4326


def test_geo_identity_reads_the_epsg_version_and_covering() -> None:
    geo = {
        "version": "1.1.0",
        "primary_column": "g",
        "columns": {
            "g": {
                "crs": {"id": {"authority": "EPSG", "code": 4674}},
                "covering": {"bbox": {"xmin": ["bbox", "xmin"]}},
            }
        },
    }
    assert geo_identity(geo) == {
        "epsg": 4674,
        "geoparquet_version": "1.1.0",
        "bbox_covering": True,
    }


def test_a_non_epsg_authority_reads_as_unknown_rather_than_as_4326() -> None:
    geo = {
        "version": "1.1.0",
        "primary_column": "g",
        "columns": {"g": {"crs": {"id": {"authority": "OGC", "code": "CRS84"}}}},
    }
    assert geo_identity(geo)["epsg"] is None


def test_a_plain_parquet_has_no_geometry_identity() -> None:
    assert geo_identity({}) == {
        "epsg": None,
        "geoparquet_version": None,
        "bbox_covering": False,
    }


# --- reading a real footer ---------------------------------------------------


@pytest.fixture
def local_geoparquet(tmp_path: Path) -> Path:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(
        "CREATE TABLE t AS SELECT 1 AS cod_imovel, "
        "ST_SetCRS(ST_Point(-49.0, -16.0), 'EPSG:4674') AS geometry"
    )
    path = tmp_path / "car.parquet"
    con.execute(f"COPY t TO '{path.as_posix()}' (FORMAT PARQUET)")
    return path


def test_live_footer_describes_a_parquet_without_reading_its_rows(
    local_geoparquet: Path,
) -> None:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    identity = live_footer(con, local_geoparquet.as_posix())
    assert identity["rows"] == 1
    assert identity["row_groups"] == 1
    assert identity["epsg"] == 4674
    assert identity["bbox_covering"] is False
    assert identity["columns"] == [
        "cod_imovel:INTEGER",
        "geometry:GEOMETRY('EPSG:4674')",
    ]


def test_geo_metadata_is_empty_for_a_parquet_that_carries_none(
    tmp_path: Path,
) -> None:
    con = duckdb.connect()
    path = tmp_path / "plain.parquet"
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    con.execute(f"COPY t TO '{path.as_posix()}' (FORMAT PARQUET)")
    assert geo_metadata(con, path.as_posix()) == {}


# --- what a check does and does not claim ------------------------------------


def test_a_shallow_check_does_not_claim_the_row_count_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of --deep is that a HEAD cannot see inside the file. A
    shallow pass that reported `ok` on row count would be a lie that stops
    anyone running the deep one."""
    pins = sample_pins()
    pins["catalogs"]["cadastral"]["identity"]["car_parquet"]["rows"] = 999999
    monkeypatch.setattr("pin_check.live_head", lambda url, timeout=30: head())
    car = check(pins, con=None)[0]
    assert car.status == OK


def test_a_deleted_object_reports_missing_and_quotes_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pin_check.live_head",
        lambda url, timeout=30: {"ok": False, "detail": "HTTP 404 (delete marker set)"},
    )
    finding = check(sample_pins(), con=None)[0]
    assert finding.status == MISSING
    assert "delete marker" in finding.line()
    assert finding.live is None


def test_a_rewritten_object_names_the_fields_that_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pin_check.live_head", lambda url, timeout=30: head(bytes=200, etag='"zzz"')
    )
    finding = check(sample_pins(), con=None)[0]
    assert finding.status == CHANGED
    assert finding.fields == ("bytes", "etag")
    assert "bytes, etag" in finding.line()


def test_an_unpinned_asset_asks_to_be_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pin_check.live_head", lambda url, timeout=30: head())
    trazo = check(sample_pins(), con=None)[1]
    assert trazo.status == UNPINNED
    assert "--update" in trazo.line()


def test_a_deep_check_compares_the_footer_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pin_check.live_head", lambda url, timeout=30: head())
    monkeypatch.setattr(
        "pin_check.live_footer",
        lambda con, url: {
            "rows": 7,
            "row_groups": 2,
            "epsg": 4326,
            "geoparquet_version": "1.1.0",
            "bbox_covering": True,
            "columns": ["cod_imovel:VARCHAR"],
        },
    )
    finding = check(sample_pins(), con=NullConnection())[0]
    assert finding.status == CHANGED
    assert finding.fields == ("rows",)


def test_differing_reports_nothing_for_an_unmeasured_field() -> None:
    assert differing({"rows": 1}, {}) == ()
    assert differing({"rows": 1}, {"rows": 2}) == ("rows",)


# --- repinning ---------------------------------------------------------------


def test_repin_writes_the_observed_identity_with_its_digest() -> None:
    pins = sample_pins()
    observed = {"bytes": 200, "etag": '"zzz"'}
    asset = Asset("trazo", "goias_parquet", FIELDS)
    from pin_check import Finding

    repin(pins, [Finding(asset=asset, status=UNPINNED, live=observed)])
    written = pins["catalogs"]["trazo"]["identity"]["goias_parquet"]
    assert written["bytes"] == 200
    assert written["fingerprint"] == fingerprint(written)


def test_repin_leaves_a_missing_object_pinned_to_what_it_was() -> None:
    """Blanking the pin would lose the record of what the goldens were built
    from and replace it with nothing."""
    pins = sample_pins()
    before = dict(pins["catalogs"]["cadastral"]["identity"]["car_parquet"])
    asset = Asset("cadastral", "car_parquet", CAR)
    from pin_check import Finding

    assert repin(pins, [Finding(asset=asset, status=MISSING, live=None)]) == 0
    assert pins["catalogs"]["cadastral"]["identity"]["car_parquet"] == before


def test_write_pins_round_trips_through_the_committed_formatting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pins.json"
    write_pins(sample_pins(), path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.loads(text) == sample_pins()


# --- reachability over HTTP --------------------------------------------------


def test_live_head_surfaces_the_delete_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source.coop answers a deleted object with 404 plus x-amz-delete-marker,
    which separates a file the publisher removed from a path never right."""

    headers = Message()
    headers["x-amz-delete-marker"] = "true"

    def raise_gone(request: Any, timeout: int = 0) -> None:
        raise urllib.error.HTTPError(CAR, 404, "Not Found", headers, None)

    monkeypatch.setattr("urllib.request.urlopen", raise_gone)
    reading = live_head(CAR)
    assert reading["ok"] is False
    assert reading["detail"] == "HTTP 404 (delete marker set)"


def test_live_head_reports_a_network_failure_as_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_dns(request: Any, timeout: int = 0) -> None:
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr("urllib.request.urlopen", raise_dns)
    assert live_head(CAR)["ok"] is False


def test_live_head_sends_an_agent_the_edge_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The edge in front of source.coop answers 403 to the default
    Python-urllib agent, which would report every pin as missing."""
    seen: dict[str, Any] = {}

    class Response:
        headers: ClassVar[dict[str, str]] = {"content-length": "5", "etag": '"e"'}

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def capture(request: Any, timeout: int = 0) -> Response:
        seen["agent"] = request.get_header("User-agent")
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", capture)
    assert live_head(CAR) == {"ok": True, "bytes": 5, "etag": '"e"'}
    assert seen["agent"] and "urllib" not in seen["agent"].lower()


# --- the command line --------------------------------------------------------


def test_update_without_deep_refuses_to_pin_a_partial_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pinning size and etag alone would stamp a fingerprint over null row
    counts, and the next deep check would read that as the file changing."""
    path = tmp_path / "pins.json"
    write_pins(sample_pins(), path)
    monkeypatch.setattr("pin_check.live_head", lambda url, timeout=30: head())
    monkeypatch.setattr(sys, "argv", ["pin_check.py", "--pins", str(path), "--update"])
    assert main() == 1
    assert "use both" in capsys.readouterr().out


def test_a_broken_pin_exits_nonzero_and_says_what_each_status_needs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "pins.json"
    write_pins(sample_pins(), path)
    monkeypatch.setattr(
        "pin_check.live_head",
        lambda url, timeout=30: {"ok": False, "detail": "HTTP 404"},
    )
    monkeypatch.setattr(sys, "argv", ["pin_check.py", "--pins", str(path)])
    assert main() == 1
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "regenerated goldens" in out


def test_json_output_is_parseable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "pins.json"
    write_pins(sample_pins(), path)
    monkeypatch.setattr("pin_check.live_head", lambda url, timeout=30: head())
    monkeypatch.setattr(sys, "argv", ["pin_check.py", "--pins", str(path), "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in payload] == [OK, UNPINNED]
