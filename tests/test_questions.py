"""Structural checks on the question set.

These do not need the network or the golden fixtures (except the last, which is
skipped until the oracle has run). They guard the invariants the grader relies
on: 31 questions, six stages, a dependency graph that points strictly backward,
and a prompt that does not leak the EUDR scope answer.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "fixtures" / "questions.yaml"
sys.path.insert(0, str(REPO / "harness"))

import ablation
import specdoc


def load() -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    return spec


def test_thirty_one_questions_six_stages_in_order() -> None:
    qs = load()["questions"]
    assert len(qs) == 31
    assert [q["id"] for q in qs] == [f"{n:02d}" for n in range(1, 32)]
    assert {q["stage"] for q in qs} == {1, 2, 3, 4, 5, 6}


def test_dependencies_point_backwards_and_resolve() -> None:
    qs = load()["questions"]
    ids = {q["id"] for q in qs}
    for q in qs:
        for dep in q.get("depends_on", []):
            assert dep in ids, f"q{q['id']} depends on unknown {dep}"
            assert dep < q["id"], f"q{q['id']} depends forward on {dep}"


def test_every_column_is_fully_specified() -> None:
    for q in load()["questions"]:
        for col in q["output"]["columns"]:
            assert {"name", "type", "description"} <= set(col), (q["id"], col)
            assert col["type"] in {"integer", "float", "string", "boolean"}


def test_no_question_mentions_the_banned_column() -> None:
    """No question may ask about hansen_covered_area. The header comment that
    documents its exclusion is fine; the question text is not."""
    for q in load()["questions"]:
        blob = q["question"] + " ".join(
            c["description"] for c in q["output"]["columns"]
        )
        assert "hansen_covered_area" not in blob, q["id"]


def test_ablated_spec_does_not_leak_the_eudr_scope_answer(tmp_path: Path) -> None:
    """The scope mapping lives in SPEC.md section 7, which the no-crops arm
    withholds. Once that section is cut, nothing left in the agent contract —
    the task, the other rules, the question definitions — may hand the answer
    back by naming the in-scope commodity list inline."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "SPEC.md").write_text(specdoc.render(REPO), encoding="utf-8")
    cfg = ablation.load_arms(REPO / "fixtures" / "ablations.yaml")
    ablation.apply_arm(ws, cfg["arms"]["no-crops"]["ops"])
    text = (ws / "SPEC.md").read_text(encoding="utf-8").lower()
    for leak in (
        "cattle, cocoa, coffee",
        "soya and wood",
        "annex i lists",
        "annex i covers",
    ):
        assert leak not in text, f"scope answer leaked: {leak!r}"


def test_the_full_spec_does_contain_the_scope_table() -> None:
    """The inverse guard: if the scope table ever moves out of the section
    the no-crops arm cuts, the leak test above passes vacuously. Pin the
    table's presence in the full view so the pair of tests stays meaningful."""
    text = specdoc.render(REPO)
    assert "Forest Plantation" in text and "assumed_pasture" in text


def test_geometry_questions_are_marked() -> None:
    """Questions whose numbers move under projection/distance choice must carry
    grading: geometry so the grader loosens tolerance for them."""
    by_id = {q["id"]: q for q in load()["questions"]}
    for qid in ("08", "09", "26"):
        assert by_id[qid].get("grading") == "geometry", qid


def test_q23_uses_geometry_only_for_area_columns() -> None:
    q23 = next(q for q in load()["questions"] if q["id"] == "23")
    policies = {
        column["name"]: column.get("grading", q23.get("grading", "exact"))
        for column in q23["output"]["columns"]
    }

    assert policies == {
        "field_id": "exact",
        "cod_imovel": "exact",
        "annex1_commodity": "exact",
        "field_area_ha": "geometry",
        "post2020_loss_ha": "geometry",
    }


def test_prompt_names_the_files_the_oracle_reads() -> None:
    """The session and the grader must query the same bytes.

    Catalog metadata alone does not get an agent to those files: the cadastral
    collection advertises its data as a `kdtree_cell=*/*.parquet` glob, which
    DuckDB cannot expand over plain HTTP, and the single-file parquet the
    oracle reads is not listed as an asset. So the prompt names the files, and
    this pins the prompt to the oracle's pins.
    """
    pins = json.loads((REPO / "fixtures" / "pins.json").read_text(encoding="utf-8"))[
        "catalogs"
    ]
    prompt = specdoc.render(REPO)

    reads = [
        pins["trazo"]["goias_parquet"],
        pins["cadastral"]["car_parquet"],
        pins["facilities"]["facilities_parquet"],
    ]
    for url in reads:
        name = url.rsplit("/", 1)[-1]
        assert name in prompt, (
            f"SPEC.md must name {name}; the agent cannot reach it "
            f"from catalog metadata alone"
        )
