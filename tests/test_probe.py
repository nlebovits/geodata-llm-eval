"""Verdict logic for the range-read probe.

Every test here is offline. The point of the probe is to keep a diagnosis
tied to readings, so the mapping from readings to conclusions is the part
that has to be pinned.
"""

import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))

import probe  # noqa: E402

MB = probe.MB


def reading(size_mb, got_mb, seconds, complete=None):
    return {
        "size_mb": size_mb, "bytes": int(got_mb * MB), "asked": size_mb * MB,
        "ttfb": 1.0, "total": seconds, "transfer": seconds - 1.0,
        "complete": complete if complete is not None else got_mb == size_mb,
        "edge": {}, "error": None, "range_header": "bytes=0-",
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
    assert probe.truncation_pattern(
        [reading(1, 1, 2.0), reading(4, 4, 5.0)])["truncated"] == 0


def parallel(streams, mb_per_second, complete=True):
    return {"streams": streams, "bytes": streams * MB, "seconds": 1.0,
            "bytes_per_second": int(mb_per_second * MB),
            "all_complete": complete}


def test_scaling_with_streams_rules_out_a_global_limit():
    """A host capping every client shows one number however many connections
    it is given. One capping each connection does not."""
    verdict = probe.concurrency_verdict(
        [parallel(1, 0.4), parallel(8, 2.1), parallel(32, 7.0)])

    assert verdict.startswith("RULED OUT a shared ceiling")


def test_a_plateau_reads_as_a_shared_ceiling():
    verdict = probe.concurrency_verdict(
        [parallel(1, 0.4), parallel(8, 0.5), parallel(32, 0.45)])

    assert verdict.startswith("CONSISTENT WITH a shared ceiling")


def test_matching_cut_points_rule_out_range_syntax():
    forms = {"closed": reading(8, 2.3, 5.5), "open_ended": reading(8, 2.4, 5.5)}

    assert probe.range_form_verdict(forms).startswith("RULED OUT range syntax")


def test_diverging_cut_points_implicate_the_request_shape():
    forms = {"closed": reading(8, 2.3, 5.5), "open_ended": reading(8, 8.0, 9.0)}

    assert "request-shape" in probe.range_form_verdict(forms)


def test_truncation_on_two_buckets_rules_out_one_bad_object():
    both = {"truncated": 3, "bytes_median": 2 * MB, "bytes_spread": MB,
            "seconds_median": 5.5, "seconds_spread": 0.1}

    verdict = probe.second_object_verdict(both, both)

    assert verdict.startswith("RULED OUT a single bad object")


def test_truncation_on_one_object_only_stays_object_specific():
    hit = {"truncated": 3, "bytes_median": 2 * MB, "bytes_spread": MB,
           "seconds_median": 5.5, "seconds_spread": 0.1}

    verdict = probe.second_object_verdict(hit, {"truncated": 0})

    assert "object-specific" in verdict


def test_a_slow_control_host_invalidates_the_rest():
    """Readings taken over a saturated link describe the link. The probe has
    to say so before anything else is read as a property of the server."""
    lines = probe.verdicts(
        {"bytes_per_second": int(0.2 * MB), "error": None},
        [reading(8, 2.0, 5.5)], parallel(8, 0.3),
        {"first": {"ttfb": 1.0}, "repeat": {"ttfb": 1.0}},
    )

    assert any(v.startswith("LOCAL LINK is slow") for v in lines)


def test_an_unreachable_control_host_stops_the_report():
    lines = probe.verdicts(
        {"bytes_per_second": None, "error": "boom"},
        [reading(8, 2.0, 5.5)], parallel(8, 2.0),
        {"first": {"ttfb": 1.0}, "repeat": {"ttfb": 1.0}},
    )

    assert lines == ["INCONCLUSIVE local link: control host unreachable"]


def test_a_faster_repeat_reads_as_caching():
    lines = probe.verdicts(
        {"bytes_per_second": 60 * MB, "error": None},
        [reading(8, 8, 3.0)], parallel(8, 2.0),
        {"first": {"ttfb": 3.2}, "repeat": {"ttfb": 0.4}},
    )

    assert any("CONSISTENT WITH caching" in v for v in lines)


def test_an_equal_repeat_rules_cache_warming_out():
    lines = probe.verdicts(
        {"bytes_per_second": 60 * MB, "error": None},
        [reading(8, 8, 3.0)], parallel(8, 2.0),
        {"first": {"ttfb": 1.3}, "repeat": {"ttfb": 1.7}},
    )

    assert any("RULED OUT cache warming" in v for v in lines)
