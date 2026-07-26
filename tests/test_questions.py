"""Structural checks on the question set.

These do not need the network or the golden fixtures (except the last, which is
skipped until the oracle has run). They guard the invariants the grader relies
on: 30 questions, six stages, a dependency graph that points strictly backward,
and a prompt that does not leak the EUDR scope answer.
"""
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
QUESTIONS = REPO / "fixtures" / "questions.yaml"


def load():
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))


def test_thirty_questions_six_stages_in_order():
    qs = load()["questions"]
    assert len(qs) == 30
    assert [q["id"] for q in qs] == [f"{n:02d}" for n in range(1, 31)]
    assert {q["stage"] for q in qs} == {1, 2, 3, 4, 5, 6}


def test_dependencies_point_backwards_and_resolve():
    qs = load()["questions"]
    ids = {q["id"] for q in qs}
    for q in qs:
        for dep in q.get("depends_on", []):
            assert dep in ids, f"q{q['id']} depends on unknown {dep}"
            assert dep < q["id"], f"q{q['id']} depends forward on {dep}"


def test_every_column_is_fully_specified():
    for q in load()["questions"]:
        for col in q["output"]["columns"]:
            assert {"name", "type", "description"} <= set(col), (q["id"], col)
            assert col["type"] in {"integer", "float", "string", "boolean"}


def test_no_question_mentions_the_banned_column():
    """No question may ask about hansen_covered_area. The header comment that
    documents its exclusion is fine; the question text is not."""
    for q in load()["questions"]:
        blob = q["question"] + " ".join(
            c["description"] for c in q["output"]["columns"])
        assert "hansen_covered_area" not in blob, q["id"]


def test_prompt_does_not_leak_the_eudr_scope_answer():
    """The scope mapping lives in policies/EUDR_CROPS.md, which the agent must
    read and apply. The prompt and question text must not hand over the answer
    by naming the in-scope commodity list inline."""
    text = (REPO / "prompts" / "task.md").read_text(encoding="utf-8").lower()
    text += QUESTIONS.read_text(encoding="utf-8").lower()
    for leak in ("cattle, cocoa, coffee", "soya and wood", "annex i lists"):
        assert leak not in text, f"scope answer leaked: {leak!r}"


def test_geometry_questions_are_marked():
    """Questions whose numbers move under projection/distance choice must carry
    grading: geometry so the grader loosens tolerance for them."""
    by_id = {q["id"]: q for q in load()["questions"]}
    for qid in ("08", "09", "26"):
        assert by_id[qid].get("grading") == "geometry", qid


def test_prompt_names_the_files_the_oracle_reads():
    """The session and the grader must query the same bytes.

    Catalog metadata alone does not get an agent to those files: the cadastral
    collection advertises its data as a `kdtree_cell=*/*.parquet` glob, which
    DuckDB cannot expand over plain HTTP, and the single-file parquet the
    oracle reads is not listed as an asset. So the prompt names the files, and
    this pins the prompt to the oracle's pins.
    """
    pins = json.loads(
        (REPO / "fixtures" / "pins.json").read_text(encoding="utf-8")
    )["catalogs"]
    prompt = (REPO / "prompts" / "task.md").read_text(encoding="utf-8")

    reads = [
        pins["trazo"]["goias_parquet"],
        pins["cadastral"]["car_parquet"],
        pins["facilities"]["facilities_parquet"],
    ]
    for url in reads:
        name = url.rsplit("/", 1)[-1]
        assert name in prompt, (
            f"prompts/task.md must name {name}; the agent cannot reach it "
            f"from catalog metadata alone"
        )
