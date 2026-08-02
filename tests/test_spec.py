"""SPEC.md is the single source of ground truth; fixtures/questions.yaml is
the machine-readable derivation the harness consumes. These tests pin the two
together so neither can drift without CI noticing, and pin the rendered agent
view to what a session is allowed to see."""

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SPEC = (REPO / "SPEC.md").read_text(encoding="utf-8")
sys.path.insert(0, str(REPO / "harness"))

import specdoc  # noqa: E402

QUESTIONS = yaml.safe_load(
    (REPO / "fixtures" / "questions.yaml").read_text(encoding="utf-8")
)["questions"]

QUESTION_BLOCK = re.compile(
    r"\*\*q(?P<id>\d{2})\*\*(?P<body>.*?)(?=\n\*\*q\d{2}\*\*|\n#|\n---|\Z)",
    re.DOTALL,
)
STAGE_HEADING = re.compile(r"^### Stage (\d)\b", re.MULTILINE)


def spec_questions() -> dict[str, dict]:
    """Question metadata as written in SPEC.md section 9."""
    section = SPEC.split("## 9.", 1)[1].split("## Open questions", 1)[0]
    found: dict[str, dict] = {}
    stage = None
    pos = 0
    stages = [(m.start(), int(m.group(1))) for m in
              STAGE_HEADING.finditer(section)]
    for match in QUESTION_BLOCK.finditer(section):
        while pos < len(stages) and stages[pos][0] < match.start():
            stage = stages[pos][1]
            pos += 1
        body = match.group("body")
        rows = re.search(r"Rows: (\d+|data)\.", body)
        depends = re.search(r"Depends: ([q\d, ]+)\.", body)
        found[match.group("id")] = {
            "stage": stage,
            "rows": rows.group(1) if rows else None,
            "depends": sorted(re.findall(r"q(\d{2})", depends.group(1)))
            if depends else [],
        }
    return found


def test_spec_and_questions_yaml_cover_the_same_questions():
    spec_ids = set(spec_questions())
    yaml_ids = {q["id"] for q in QUESTIONS}
    assert spec_ids == yaml_ids, (
        f"only in SPEC.md: {sorted(spec_ids - yaml_ids)}; "
        f"only in questions.yaml: {sorted(yaml_ids - spec_ids)}"
    )


def test_spec_row_counts_match_questions_yaml():
    spec = spec_questions()
    for q in QUESTIONS:
        declared = spec[q["id"]]["rows"]
        expected = "data" if q["output"]["rows"] is None \
            else str(q["output"]["rows"])
        assert declared == expected, (
            f"q{q['id']}: SPEC.md says rows {declared!r}, "
            f"questions.yaml says {expected!r}"
        )


def test_spec_dependencies_match_questions_yaml():
    spec = spec_questions()
    for q in QUESTIONS:
        assert spec[q["id"]]["depends"] == sorted(q["depends_on"]), (
            f"q{q['id']}: SPEC.md declares depends {spec[q['id']]['depends']},"
            f" questions.yaml declares {sorted(q['depends_on'])}"
        )


def test_spec_stages_match_questions_yaml():
    spec = spec_questions()
    for q in QUESTIONS:
        assert spec[q["id"]]["stage"] == q["stage"], (
            f"q{q['id']}: SPEC.md places it in stage "
            f"{spec[q['id']]['stage']}, questions.yaml in {q['stage']}"
        )


def test_spec_names_the_geometry_graded_questions():
    """Section 2 lists the geometry-graded set in one line; it must be the
    set questions.yaml actually grades that way."""
    graded = {q["id"] for q in QUESTIONS if q.get("grading") == "geometry"}
    line = next(ln for ln in SPEC.splitlines()
                if "Geometry-graded questions:" in ln)
    named: set[str] = set()
    for start, end in re.findall(r"q(\d{2})–q(\d{2})", line):
        named.update(f"{n:02d}" for n in range(int(start), int(end) + 1))
    stripped = re.sub(r"q\d{2}–q\d{2}", "", line)
    named.update(re.findall(r"q(\d{2})", stripped))
    assert named == graded, (
        f"SPEC.md names {sorted(named)}, questions.yaml grades "
        f"{sorted(graded)} as geometry"
    )


def test_agent_view_strips_what_a_session_must_not_see():
    view = specdoc.agent_view(SPEC)
    assert "provenance:" not in view, "provenance reaches the session"
    assert "equivalence:" not in view, "grader equivalences reach the session"
    assert "Open questions" not in view, "the contested list reaches the session"
    assert "PR #" not in view, "repo history reaches the session"
    assert "Questions affected" not in view, (
        "the per-rule impact map reaches the session")


def test_agent_view_keeps_the_rules_and_tables():
    view = specdoc.agent_view(SPEC)
    for kept in ("### rule: primary-cadaster", "### rule: widening",
                 "## 4. Input list handling", "Forest Plantation",
                 "contain_threshold` = 0.667", "**q31**"):
        assert kept in view, f"agent view lost {kept!r}"


def test_agent_view_leaves_no_seam():
    view = specdoc.agent_view(SPEC)
    assert "\n\n\n" not in view, "a blank-line run betrays the removals"
    assert view.endswith("\n") and not view.endswith("\n\n")
