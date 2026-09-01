"""Check that the remote objects the goldens were built from are still there.

Every scored answer in this benchmark traces back to a file on source.coop that
`fixtures/pins.json` names by URL. Those URLs are not immutable. The CAR object
the pins named was deleted in August 2026 and republished under a new path,
reprojected from SIRGAS 2000 to WGS 84, and rewritten with a bbox covering
column and 212 row groups. Four places in the repository went on naming a URL
that answered 404, and the first symptom was a session dying minutes into a run.

A URL alone cannot catch the other half of that failure. An object republished
in place keeps its address and answers 200 while holding different numbers, and
goldens regenerated against it would silently stop describing the file the
committed results were graded on. So a pin records what the object is as well as
where it lives: byte size, entity tag, row count, row-group count, declared
EPSG, GeoParquet version, whether the geometry column carries a bbox covering,
and the column list. A short fingerprint over those fields turns any change into
one line of diff.

The two failures report differently because the fixes differ. A missing object
needs a new URL. A changed object needs regenerated goldens and a note saying
which results predate the change.

Usage:
    python harness/pin_check.py                  # reachability, size, etag
    python harness/pin_check.py --deep           # also read the parquet footers
    python harness/pin_check.py --deep --update  # repin to what is live now
    python harness/pin_check.py --json           # machine-readable findings

Exit status is 0 when every pin matches, 1 when any pin is missing or changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from probe import USER_AGENT

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PINS = REPO_ROOT / "fixtures" / "pins.json"

# A pinned remote object is a catalog entry whose key names a parquet. Naming
# the convention rather than listing the four keys means a fifth asset added to
# pins.json is checked without editing this module.
ASSET_SUFFIX = "_parquet"

# Where the recorded identity of each asset lives inside a catalog block, keyed
# by the same field name that holds the URL.
IDENTITY_KEY = "identity"

# The identity fields the fingerprint covers, in a fixed order so the digest
# does not move when the dict does. `fingerprint` itself is excluded: it is
# derived from the rest.
FINGERPRINTED = (
    "bytes",
    "etag",
    "rows",
    "row_groups",
    "epsg",
    "geoparquet_version",
    "bbox_covering",
    "columns",
)

# HEAD alone answers "is it still there, and is it still the same object". It
# costs one request and no bytes, which is why it is the default mode.
HEAD_FIELDS = ("bytes", "etag")

OK = "ok"
MISSING = "missing"
CHANGED = "changed"
UNPINNED = "unpinned"


@dataclass(frozen=True)
class Asset:
    """One pinned remote object, and where its pin lives."""

    catalog: str
    key: str
    url: str


@dataclass(frozen=True)
class Finding:
    """What the live object turned out to be, against what the pin claims."""

    asset: Asset
    status: str
    fields: tuple[str, ...] = ()
    detail: str = ""
    live: dict[str, Any] | None = None

    def line(self) -> str:
        where = f"{self.asset.catalog}.{self.asset.key}"
        if self.status == OK:
            return f"ok       {where}"
        if self.status == MISSING:
            return (
                f"MISSING  {where}\n           {self.asset.url}"
                f"\n           {self.detail}"
            )
        if self.status == UNPINNED:
            return f"unpinned {where} — no identity recorded; run --update to pin it"
        changed = ", ".join(self.fields)
        return f"CHANGED  {where}\n           {self.asset.url}\n           differs in: {changed}"


class Executes(Protocol):
    """The one thing footer reads need from a connection.

    Narrower than DuckDBPyConnection so the tests can drive this without
    standing up a whole database handle, matching how oracle/render.py states
    the same dependency.
    """

    def execute(self, sql: str) -> Any: ...


def assets(pins: dict[str, Any]) -> list[Asset]:
    """Every pinned parquet in the pins file, in a stable order."""
    found = []
    for catalog, block in sorted(pins.get("catalogs", {}).items()):
        for key, value in sorted(block.items()):
            if key.endswith(ASSET_SUFFIX) and isinstance(value, str):
                found.append(Asset(catalog=catalog, key=key, url=value))
    return found


def recorded(pins: dict[str, Any], asset: Asset) -> dict[str, Any] | None:
    """The identity a pin claims for an asset, or None if it claims none."""
    block = pins["catalogs"][asset.catalog].get(IDENTITY_KEY, {})
    entry = block.get(asset.key)
    return entry if isinstance(entry, dict) else None


def fingerprint(identity: dict[str, Any]) -> str:
    """A short digest over the identity fields, stable across dict order.

    Twelve hex characters, matching `golden_fingerprint` in grade.py: long
    enough that two different files will not collide in a repository this size,
    short enough to read in a diff.
    """
    payload = {name: identity.get(name) for name in FINGERPRINTED}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def with_fingerprint(identity: dict[str, Any]) -> dict[str, Any]:
    """The identity plus its own digest, in the order a pin records them."""
    ordered = {name: identity.get(name) for name in FINGERPRINTED}
    ordered["fingerprint"] = fingerprint(ordered)
    return ordered


def live_head(url: str, timeout: int = 30) -> dict[str, Any]:
    """Size and entity tag from one HEAD, or the reason there is neither.

    A deleted object on source.coop answers 404 with `x-amz-delete-marker`,
    which is worth surfacing verbatim: it distinguishes a file the publisher
    removed from a path that was never right.
    """
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        # url comes from fixtures/pins.json, which is committed.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            headers = response.headers
            length = headers.get("content-length")
            return {
                "ok": True,
                "bytes": int(length) if length is not None else None,
                "etag": headers.get("etag"),
            }
    except urllib.error.HTTPError as exc:
        marker = exc.headers.get("x-amz-delete-marker") if exc.headers else None
        deleted = " (delete marker set)" if marker else ""
        return {"ok": False, "detail": f"HTTP {exc.code}{deleted}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "detail": str(exc)}


def geo_metadata(con: Executes, url: str) -> dict[str, Any]:
    """The GeoParquet `geo` key, or an empty dict for a plain parquet.

    Not every pinned asset carries geometry metadata, and a missing key is a
    fact about the file rather than an error, so it reads as absent rather than
    raising.
    """
    # url is a committed pin read from fixtures/pins.json, not caller input.
    query = (
        f"SELECT decode(value) FROM parquet_kv_metadata('{url}') "  # nosec B608
        "WHERE decode(key) = 'geo'"
    )
    rows = con.execute(query).fetchall()
    if not rows:
        return {}
    try:
        parsed = json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def geo_identity(geo: dict[str, Any]) -> dict[str, Any]:
    """EPSG, GeoParquet version, and covering, read off the `geo` metadata.

    The EPSG comes from the PROJJSON `id` of the primary column's CRS. A
    GeoParquet column may omit `crs` entirely, which the specification defines
    as OGC:CRS84 — longitude-first WGS 84, EPSG:4326 for every purpose in this
    benchmark — so an absent CRS reads as 4326 rather than as unknown.
    """
    if not geo:
        return {"epsg": None, "geoparquet_version": None, "bbox_covering": False}
    primary = geo.get("primary_column")
    column = geo.get("columns", {}).get(primary, {})
    crs = column.get("crs")
    if crs is None:
        epsg: int | None = 4326
    else:
        code = crs.get("id", {}).get("code") if isinstance(crs, dict) else None
        epsg = int(code) if isinstance(code, int) else None
    return {
        "epsg": epsg,
        "geoparquet_version": geo.get("version"),
        "bbox_covering": "covering" in column,
    }


def live_footer(con: Executes, url: str) -> dict[str, Any]:
    """Row count, row groups, columns, and geometry metadata from the footer.

    Reads kilobytes, not gigabytes: every value here comes from the parquet
    footer and the file's key-value metadata, so a 3.3 GB object is described
    without transferring it.
    """
    # url is a committed pin read from fixtures/pins.json, not caller input.
    footer = f"parquet_file_metadata('{url}')"
    counts = f"SELECT num_rows, num_row_groups FROM {footer}"  # nosec B608
    rows, row_groups = con.execute(counts).fetchall()[0]
    describe = f"DESCRIBE SELECT * FROM read_parquet('{url}')"  # nosec B608
    described = con.execute(describe).fetchall()
    identity: dict[str, Any] = {
        "rows": int(rows),
        "row_groups": int(row_groups),
        "columns": [f"{name}:{dtype}" for name, dtype, *_ in described],
    }
    identity.update(geo_identity(geo_metadata(con, url)))
    return identity


def observe(
    asset: Asset, con: Executes | None, timeout: int = 30
) -> tuple[bool, dict[str, Any], str]:
    """What the live object is right now: (reachable, identity, detail)."""
    head = live_head(asset.url, timeout=timeout)
    if not head["ok"]:
        return False, {}, str(head["detail"])
    identity = {field: head[field] for field in HEAD_FIELDS}
    if con is not None:
        identity.update(live_footer(con, asset.url))
    return True, identity, ""


def differing(pinned: dict[str, Any], live: dict[str, Any]) -> tuple[str, ...]:
    """Fields the live object disagrees with the pin on.

    Only fields the observation actually measured are compared. A shallow check
    reads no footer, and a footer field it never looked at must not read as a
    change — silence about row counts is not a claim that they match.
    """
    return tuple(
        field
        for field in FINGERPRINTED
        if field in live and pinned.get(field) != live[field]
    )


def check(
    pins: dict[str, Any], con: Executes | None = None, timeout: int = 30
) -> list[Finding]:
    """One finding per pinned asset, in pins order."""
    findings = []
    for asset in assets(pins):
        reachable, identity, detail = observe(asset, con, timeout=timeout)
        if not reachable:
            findings.append(
                Finding(asset=asset, status=MISSING, detail=detail, live=None)
            )
            continue
        pinned = recorded(pins, asset)
        if pinned is None:
            findings.append(Finding(asset=asset, status=UNPINNED, live=identity))
            continue
        fields = differing(pinned, identity)
        status = CHANGED if fields else OK
        findings.append(
            Finding(asset=asset, status=status, fields=fields, live=identity)
        )
    return findings


def repin(pins: dict[str, Any], findings: list[Finding]) -> int:
    """Write observed identities into the pins structure. Returns how many.

    A missing object is left alone: its identity is unknown, and blanking the
    pin would lose the record of what the goldens were built from without
    replacing it with anything.
    """
    updated = 0
    for finding in findings:
        if finding.live is None:
            continue
        block = pins["catalogs"][finding.asset.catalog]
        block.setdefault(IDENTITY_KEY, {})[finding.asset.key] = with_fingerprint(
            finding.live
        )
        updated += 1
    return updated


def write_pins(pins: dict[str, Any], path: Path) -> None:
    """Rewrite the pins file in the formatting it is committed in."""
    path.write_text(
        json.dumps(pins, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def as_json(findings: list[Finding]) -> str:
    return json.dumps(
        [
            {
                "catalog": f.asset.catalog,
                "key": f.asset.key,
                "url": f.asset.url,
                "status": f.status,
                "fields": list(f.fields),
                "detail": f.detail,
                "live": f.live,
            }
            for f in findings
        ],
        indent=2,
    )


def connect_for_footers() -> Any:
    """A DuckDB connection set up to read remote parquet footers."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    return con


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pins", type=Path, default=DEFAULT_PINS)
    ap.add_argument(
        "--deep",
        action="store_true",
        help="read parquet footers too (row counts, schema, CRS, covering)",
    )
    ap.add_argument(
        "--update",
        action="store_true",
        help="rewrite the pins file to the identities observed now",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable findings")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    pins = json.loads(args.pins.read_text(encoding="utf-8"))
    con = connect_for_footers() if args.deep else None
    findings = check(pins, con=con, timeout=args.timeout)

    if args.json:
        print(as_json(findings))
    else:
        for finding in findings:
            print(finding.line())

    if args.update:
        if not args.deep:
            print("\n--update without --deep would pin size and etag only; use both.")
            return 1
        updated = repin(pins, findings)
        write_pins(pins, args.pins)
        print(f"\nrepinned {updated} asset(s) in {args.pins}")
        return 0

    broken = [f for f in findings if f.status in (MISSING, CHANGED, UNPINNED)]
    if broken and not args.json:
        print(
            f"\n{len(broken)} pin(s) need attention. A MISSING object needs a new "
            "URL in pins.json and everywhere else it is named; a CHANGED object "
            "needs regenerated goldens and a note on which results predate it."
        )
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
