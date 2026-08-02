"""Verdict logic for the range-read probe.

Every test here is offline. The point of the probe is to keep a diagnosis
tied to readings, so the mapping from readings to conclusions is the part
that has to be pinned.
"""

import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))

import probe

MB = probe.MB


def reading(size_mb, got_mb, seconds, complete=None):
    return {
        "size_mb": size_mb,
        "bytes": int(got_mb * MB),
        "asked": size_mb * MB,
        "ttfb": 1.0,
        "total": seconds,
        "transfer": seconds - 1.0,
        "complete": complete if complete is not None else got_mb == size_mb,
        "edge": {},
        "error": None,
        "range_header": "bytes=0-",
    }


def test_a_steady_cut_time_reads_as_a_deadline():
    """Bytes drifting while the clock holds is the signature of a response
    deadline. Calling that a byte cap sends a bug report the wrong way."""
    sweep = [reading(8, 1.8, 5.5), reading(8, 2.9, 5.5), reading(32, 2.4, 5.5)]

    pattern = probe.truncation_pattern(sweep)

    assert pattern["truncated"] == 3
    assert pattern["seconds_spread"] == 0
    assert pattern["bytes_spread"] > MB


def test_a_steady_cut_size_reads_as_a_byte_cap():
    sweep = [reading(8, 2.0, 4.0), reading(8, 2.0, 9.0), reading(32, 2.0, 20.0)]

    pattern = probe.truncation_pattern(sweep)

    assert pattern["bytes_spread"] == 0
    assert pattern["seconds_spread"] > 1


def test_full_responses_report_no_truncation():
    assert (
        probe.truncation_pattern([reading(1, 1, 2.0), reading(4, 4, 5.0)])["truncated"]
        == 0
    )


def parallel(streams, mb_per_second, complete=True):
    return {
        "streams": streams,
        "bytes": streams * MB,
        "seconds": 1.0,
        "bytes_per_second": int(mb_per_second * MB),
        "all_complete": complete,
    }


def test_scaling_with_streams_rules_out_a_global_limit():
    """A host capping every client shows one number however many connections
    it is given. One capping each connection does not."""
    verdict = probe.concurrency_verdict(
        [parallel(1, 0.4), parallel(8, 2.1), parallel(32, 7.0)]
    )

    assert verdict.startswith("RULED OUT a shared ceiling")


def test_a_plateau_reads_as_a_shared_ceiling():
    verdict = probe.concurrency_verdict(
        [parallel(1, 0.4), parallel(8, 0.5), parallel(32, 0.45)]
    )

    assert verdict.startswith("CONSISTENT WITH a shared ceiling")


def test_matching_cut_points_rule_out_range_syntax():
    forms = {"closed": reading(8, 2.3, 5.5), "open_ended": reading(8, 2.4, 5.5)}

    assert probe.range_form_verdict(forms).startswith("RULED OUT range syntax")


def test_diverging_cut_points_implicate_the_request_shape():
    forms = {"closed": reading(8, 2.3, 5.5), "open_ended": reading(8, 8.0, 9.0)}

    assert "request-shape" in probe.range_form_verdict(forms)


def test_truncation_on_two_buckets_rules_out_one_bad_object():
    both = {
        "truncated": 3,
        "bytes_median": 2 * MB,
        "bytes_spread": MB,
        "seconds_median": 5.5,
        "seconds_spread": 0.1,
    }

    verdict = probe.second_object_verdict(both, both)

    assert verdict.startswith("RULED OUT a single bad object")


def test_truncation_on_one_object_only_stays_object_specific():
    hit = {
        "truncated": 3,
        "bytes_median": 2 * MB,
        "bytes_spread": MB,
        "seconds_median": 5.5,
        "seconds_spread": 0.1,
    }

    verdict = probe.second_object_verdict(hit, {"truncated": 0})

    assert "object-specific" in verdict


def test_a_slow_control_host_invalidates_the_rest():
    """Readings taken over a saturated link describe the link. The probe has
    to say so before anything else is read as a property of the server."""
    lines = probe.verdicts(
        {"bytes_per_second": int(0.2 * MB), "error": None},
        [reading(8, 2.0, 5.5)],
        parallel(8, 0.3),
        {"first": {"ttfb": 1.0}, "repeat": {"ttfb": 1.0}},
    )

    assert any(v.startswith("LOCAL LINK is slow") for v in lines)


def test_an_unreachable_control_host_stops_the_report():
    lines = probe.verdicts(
        {"bytes_per_second": None, "error": "boom"},
        [reading(8, 2.0, 5.5)],
        parallel(8, 2.0),
        {"first": {"ttfb": 1.0}, "repeat": {"ttfb": 1.0}},
    )

    assert lines == ["INCONCLUSIVE local link: control host unreachable"]


def test_a_faster_repeat_reads_as_caching():
    lines = probe.verdicts(
        {"bytes_per_second": 60 * MB, "error": None},
        [reading(8, 8, 3.0)],
        parallel(8, 2.0),
        {"first": {"ttfb": 3.2}, "repeat": {"ttfb": 0.4}},
    )

    assert any("CONSISTENT WITH caching" in v for v in lines)


def test_an_equal_repeat_rules_cache_warming_out():
    lines = probe.verdicts(
        {"bytes_per_second": 60 * MB, "error": None},
        [reading(8, 8, 3.0)],
        parallel(8, 2.0),
        {"first": {"ttfb": 1.3}, "repeat": {"ttfb": 1.7}},
    )

    assert any("RULED OUT cache warming" in v for v in lines)


class FakeResponse:
    """Enough of an HTTP response for fetch_range to time and measure."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self, size: int | None = None) -> bytes:
        return self._body[:size] if size else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(body: bytes, headers: dict | None = None):
    """A urlopen stand-in that hands the same body to every caller."""

    def _open(request, timeout=None):
        return FakeResponse(body, headers)

    return _open


def test_a_ranged_read_reports_its_three_phases(monkeypatch):
    """ttfb, transfer, and total are separated on purpose: a host that
    answers late and one that answers slowly look identical in a single
    elapsed number."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * MB))

    result = probe.fetch_range("https://example.test/f.parquet", 0, MB)

    assert result["bytes"] == MB
    assert result["complete"] is True
    assert result["error"] is None
    assert result["total"] >= result["transfer"]
    assert result["range_header"] == f"bytes=0-{MB - 1}"


def test_an_open_ended_read_asks_for_an_open_range(monkeypatch):
    """The two range forms are the whole point of that reading, so the
    header has to differ."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * MB))

    result = probe.fetch_range("https://example.test/f.parquet", 0, MB, open_ended=True)

    assert result["range_header"] == "bytes=0-"


def test_a_short_response_is_recorded_as_incomplete(monkeypatch):
    """Truncation is the signal every verdict is built on."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))

    result = probe.fetch_range("https://example.test/f.parquet", 0, MB)

    assert result["complete"] is False
    assert result["bytes"] == 1024


def test_edge_headers_are_carried_through(monkeypatch):
    """The report names the edge that served it; without that a rerun
    cannot be compared to this one."""
    monkeypatch.setattr(
        probe.urllib.request,
        "urlopen",
        serve(b"x" * MB, {"cf-ray": "abc-IAD", "server": "cloudflare"}),
    )

    result = probe.fetch_range("https://example.test/f.parquet", 0, MB)

    assert result["edge"]["cf_ray"] == "abc-IAD"
    assert result["edge"]["server"] == "cloudflare"


def test_the_control_reading_reports_throughput(monkeypatch):
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * MB))

    control = probe.control_reading()

    assert control["bytes"] == MB
    assert control["error"] is None
    assert control["bytes_per_second"] > 0


def test_an_unreachable_control_host_reports_the_error(monkeypatch):
    """A failed control reading has to survive as a value: the verdicts
    read it to decide whether the rest of the report means anything."""

    def refuse(url, timeout=None):
        raise probe.urllib.error.URLError("no route to host")

    monkeypatch.setattr(probe.urllib.request, "urlopen", refuse)

    control = probe.control_reading()

    assert control["bytes"] == 0
    assert control["bytes_per_second"] is None
    assert "no route to host" in control["error"]


def test_parallel_streams_are_summed_not_averaged(monkeypatch):
    """Aggregate throughput against the single-stream rate is what
    separates a per-connection limit from a shared one."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * MB))

    result = probe.parallel_reading("https://example.test/f.parquet", streams=4)

    assert result["streams"] == 4
    assert result["bytes"] == 4 * MB
    assert result["all_complete"] is True


def test_a_full_run_is_offline_reproducible(monkeypatch):
    """Every reading in a run goes through fetch_range, so one stand-in
    exercises the whole assembly."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))
    monkeypatch.setattr(probe, "other_load_present", list)

    report = probe.run("https://example.test/f.parquet", quick=True)

    assert report["url"] == "https://example.test/f.parquet"
    assert report["verdicts"]
    assert report["concurrency"] == []


def test_a_full_run_sweeps_concurrency_and_a_second_object(monkeypatch):
    """One misbehaving file explains less than a pattern that follows the
    host, so the unhurried run reads a second bucket."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))
    monkeypatch.setattr(probe, "other_load_present", list)

    report = probe.run("https://example.test/f.parquet", quick=False)

    assert [step["streams"] for step in report["concurrency"]] == list(
        probe.CONCURRENCY_STEPS
    )
    assert report["second_object"]["url"] == probe.SECOND_URL


def test_a_contended_link_warns_before_anything_else(monkeypatch):
    """A reading taken under load measures the contention. That has
    already produced one wrong diagnosis, so it leads the report."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))
    monkeypatch.setattr(
        probe, "other_load_present", lambda: ["geodata-llm-eval session-1"]
    )

    report = probe.run("https://example.test/f.parquet", quick=True)

    assert report["verdicts"][0].startswith("WARNING 1 container(s) running")


def test_running_containers_are_listed(monkeypatch):
    def fake_run(cmd, **kwargs):
        class Done:
            stdout = "geodata-llm-eval session-1\n\n"

        return Done()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe.other_load_present() == ["geodata-llm-eval session-1"]


def test_a_missing_docker_is_not_fatal(monkeypatch):
    """The probe still has a reading to report when docker is absent."""

    def refuse(cmd, **kwargs):
        raise OSError("docker not found")

    monkeypatch.setattr(probe.subprocess, "run", refuse)

    assert probe.other_load_present() == []


def test_the_rendered_report_names_every_reading(monkeypatch):
    """The probe's output is pasted into a bug report, so each reading has
    to be legible without the JSON beside it."""
    monkeypatch.setattr(
        probe.urllib.request,
        "urlopen",
        serve(b"x" * 1024, {"cf-ray": "abc-IAD", "cf-cache-status": "MISS"}),
    )
    monkeypatch.setattr(probe, "other_load_present", list)

    text = probe.render(probe.run("https://example.test/f.parquet", quick=False))

    assert "target: https://example.test/f.parquet" in text
    assert "control host:" in text
    assert "edge: abc-IAD" in text
    assert "single stream, by range size:" in text
    assert "parallel streams of 1 MB:" in text
    assert "aggregate throughput by stream count:" in text
    assert "closed range against open-ended:" in text
    assert "same range twice, then an untouched one:" in text
    assert "what the readings rule out:" in text
    assert "not answerable from one client on one network:" in text


def test_an_unreachable_control_host_leaves_its_line_out(monkeypatch):
    """Rendering a null throughput would divide by nothing."""

    def refuse(*args, **kwargs):
        raise probe.urllib.error.URLError("down")

    monkeypatch.setattr(probe.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(probe, "other_load_present", list)

    text = probe.render(probe.run("https://example.test/f.parquet", quick=True))

    assert "control host:" not in text
    assert "edge:" not in text


def test_main_prints_the_report_and_writes_it_where_asked(
    monkeypatch, tmp_path, capsys
):
    out = tmp_path / "probe.txt"
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))
    monkeypatch.setattr(probe, "other_load_present", list)
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe.py", "--quick", "--out", str(out), "--url", "https://e.test/f"],
    )

    assert probe.main() == 0

    written = out.read_text(encoding="utf-8")
    assert "target: https://e.test/f" in written
    assert written.rstrip("\n") == capsys.readouterr().out.rstrip("\n")


def test_main_can_emit_json(monkeypatch, capsys):
    """--json is what makes a reading comparable to a later one."""
    monkeypatch.setattr(probe.urllib.request, "urlopen", serve(b"x" * 1024))
    monkeypatch.setattr(probe, "other_load_present", list)
    monkeypatch.setattr(sys, "argv", ["probe.py", "--quick", "--json"])

    assert probe.main() == 0

    report = probe.json.loads(capsys.readouterr().out)
    assert report["url"] == probe.DEFAULT_URL
    assert report["verdicts"]
