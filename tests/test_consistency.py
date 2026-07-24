"""Cross-run consistency metrics. Synthetic runs, no network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

from consistency import compare_runs  # noqa: E402

RUN_A = {
    "GO-1": {"post2020_loss_ha": 10.0, "top_contact_entity_id": "SILO-1"},
    "GO-2": {"post2020_loss_ha": 5.0, "top_contact_entity_id": "SILO-2"},
}
RUN_B = dict(RUN_A)
RUN_C = {
    "GO-3": {"post2020_loss_ha": 1.0, "top_contact_entity_id": "SILO-9"},
}


def test_identical_runs_agree_perfectly():
    out = compare_runs([RUN_A, RUN_B], oracle=None)
    assert out["flag_jaccard"] == 1.0
    assert out["contact_agreement"] == 1.0
    assert out["unstable_cadasters"] == []


def test_disjoint_runs_agree_not_at_all():
    out = compare_runs([RUN_A, RUN_C], oracle=None)
    assert out["flag_jaccard"] == 0.0
    assert sorted(out["unstable_cadasters"]) == ["GO-1", "GO-2", "GO-3"]


def test_contact_disagreement_is_reported_per_cadaster():
    # same properties flagged, different buyer chosen for GO-1 in one of 3 runs.
    variant = {
        "GO-1": {"post2020_loss_ha": 10.0, "top_contact_entity_id": "SILO-7"},
        "GO-2": {"post2020_loss_ha": 5.0, "top_contact_entity_id": "SILO-2"},
    }
    out = compare_runs([RUN_A, RUN_A, variant], oracle=None)
    assert out["flag_jaccard"] == 1.0
    # GO-1 modal share 2/3, GO-2 modal share 3/3 -> mean 5/6
    assert out["contact_agreement"] == pytest.approx(5 / 6)


def test_agreement_without_correctness_is_visible():
    # two runs agree with each other but not with the oracle: high consistency,
    # zero oracle agreement — the whole reason stage 7 exists.
    oracle = {"GO-9": {"post2020_loss_ha": 3.0, "top_contact_entity_id": "SILO-X"}}
    out = compare_runs([RUN_A, RUN_B], oracle=oracle)
    assert out["flag_jaccard"] == 1.0
    assert out["oracle"]["flag_jaccard"] == 0.0


def test_partial_flag_overlap_lists_only_the_unstable_ones():
    # GO-2 flagged by both; GO-1 only by the first, GO-3 only by the second.
    r1 = {
        "GO-1": {"post2020_loss_ha": 4.0, "top_contact_entity_id": "A"},
        "GO-2": {"post2020_loss_ha": 5.0, "top_contact_entity_id": "B"},
    }
    r2 = {
        "GO-2": {"post2020_loss_ha": 5.0, "top_contact_entity_id": "B"},
        "GO-3": {"post2020_loss_ha": 6.0, "top_contact_entity_id": "C"},
    }
    out = compare_runs([r1, r2], oracle=None)
    assert sorted(out["unstable_cadasters"]) == ["GO-1", "GO-3"]
    assert out["flag_jaccard"] == pytest.approx(1 / 3)  # {GO-2} / {GO-1,2,3}
