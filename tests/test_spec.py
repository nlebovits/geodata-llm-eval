"""Pin the public contract to its machine-readable derivation and boundary."""

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
SPEC = (REPO / "SPEC.md").read_text(encoding="utf-8")
REVIEW = (REPO / "docs" / "SPEC_REVIEW.md").read_text(encoding="utf-8")
sys.path.insert(0, str(REPO / "harness"))

import specdoc

QUESTIONS: list[dict[str, Any]] = yaml.safe_load(
    (REPO / "fixtures" / "questions.yaml").read_text(encoding="utf-8")
)["questions"]

QUESTION_BLOCK = re.compile(
    r"\*\*q(?P<id>\d{2})\*\*(?P<body>.*?)(?=\n\*\*q\d{2}\*\*|\n#|\n---|\Z)",
    re.DOTALL,
)
STAGE_HEADING = re.compile(r"^### Stage (\d)\b", re.MULTILINE)


def spec_questions() -> dict[str, dict[str, Any]]:
    """Question metadata as written in SPEC.md section 9."""
    section = SPEC.split("## 9.", 1)[1].split("## Open questions", 1)[0]
    found: dict[str, dict[str, Any]] = {}
    stage = None
    pos = 0
    stages = [(m.start(), int(m.group(1))) for m in STAGE_HEADING.finditer(section)]
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
            if depends
            else [],
        }
    return found


def test_spec_and_questions_yaml_cover_the_same_questions() -> None:
    spec_ids = set(spec_questions())
    yaml_ids = {q["id"] for q in QUESTIONS}
    assert spec_ids == yaml_ids, (
        f"only in SPEC.md: {sorted(spec_ids - yaml_ids)}; "
        f"only in questions.yaml: {sorted(yaml_ids - spec_ids)}"
    )


def test_spec_row_counts_match_questions_yaml() -> None:
    spec = spec_questions()
    for q in QUESTIONS:
        declared = spec[q["id"]]["rows"]
        expected = "data" if q["output"]["rows"] is None else str(q["output"]["rows"])
        assert declared == expected, (
            f"q{q['id']}: SPEC.md says rows {declared!r}, "
            f"questions.yaml says {expected!r}"
        )


def test_spec_dependencies_match_questions_yaml() -> None:
    spec = spec_questions()
    for q in QUESTIONS:
        assert spec[q["id"]]["depends"] == sorted(q["depends_on"]), (
            f"q{q['id']}: SPEC.md declares depends {spec[q['id']]['depends']},"
            f" questions.yaml declares {sorted(q['depends_on'])}"
        )


def test_spec_stages_match_questions_yaml() -> None:
    spec = spec_questions()
    for q in QUESTIONS:
        assert spec[q["id"]]["stage"] == q["stage"], (
            f"q{q['id']}: SPEC.md places it in stage "
            f"{spec[q['id']]['stage']}, questions.yaml in {q['stage']}"
        )


def test_spec_names_the_geometry_graded_questions() -> None:
    """Section 2 lists the geometry-graded set in one line; it must be the
    set questions.yaml actually grades that way."""
    graded = {q["id"] for q in QUESTIONS if q.get("grading") == "geometry"}
    line = next(ln for ln in SPEC.splitlines() if "Geometry-graded questions:" in ln)
    named: set[str] = set()
    for start, end in re.findall(r"q(\d{2})–q(\d{2})", line):
        named.update(f"{n:02d}" for n in range(int(start), int(end) + 1))
    stripped = re.sub(r"q\d{2}–q\d{2}", "", line)
    named.update(re.findall(r"q(\d{2})", stripped))
    assert named == graded, (
        f"SPEC.md names {sorted(named)}, questions.yaml grades "
        f"{sorted(graded)} as geometry"
    )


def test_agent_view_is_the_exact_contract() -> None:
    assert specdoc.agent_view(SPEC) == SPEC
    assert specdoc.render(REPO).encode() == (REPO / "SPEC.md").read_bytes()
    assert specdoc.CONTRACT_VERSION == 2


def test_agent_contract_keeps_the_rules_and_tables() -> None:
    for kept in (
        "### Rule: primary-cadaster",
        "### Rule: widening",
        "## 4. Input list handling",
        "Forest Plantation",
        "contain_threshold` = 0.667",
        "**q31**",
    ):
        assert kept in SPEC, f"agent contract lost {kept!r}"


def test_reviewer_only_canaries_never_enter_the_agent_contract() -> None:
    canaries = ("REVIEW_ONLY_CANARY", "GRADER_ONLY_CANARY")
    assert all(canary in REVIEW for canary in canaries)
    assert all(canary not in SPEC for canary in canaries)


def test_agent_contract_excludes_reviewer_and_comparator_details() -> None:
    forbidden = (
        "Provenance:",
        "Questions affected:",
        "Open questions",
        "quantize-before-compare",
        "strings-fold-case",
        "booleans-are-liberal",
        "near miss",
        "case-insensitively",
        "scan order",
    )
    for text in forbidden:
        assert text.lower() not in SPEC.lower(), f"agent contract exposes {text!r}"


def test_agent_contract_has_no_rendering_seams() -> None:
    assert "\n\n\n" not in SPEC
    assert SPEC.endswith("\n") and not SPEC.endswith("\n\n")
