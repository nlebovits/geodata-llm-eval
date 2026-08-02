"""Shaping the spec, and recording what a session actually saw.

The expensive failure this file guards is silence. A session costs about four
dollars, so an arm whose heading no longer matches the document would ablate
nothing, score like the baseline, and read as evidence that the spec does not
matter. Nearly every test here pins an error that must be raised rather than
a behaviour that must be tolerated.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import ablation

DOC = """\
# Title

Intro line.

## Keep me

Body of keep.

## Cut me

Body of cut.

### Nested under cut

Nested body.

## Keep me too

Trailing body.
"""


def workspace(tmp_path: Path, **files: str) -> Path:
    ws = tmp_path / "workspace"
    (ws / "policies").mkdir(parents=True)
    for name, text in files.items():
        (ws / name.replace("__", "/")).write_text(text, encoding="utf-8")
    return ws


# --- cut ---------------------------------------------------------------------


def test_cut_removes_the_heading_and_its_body_up_to_the_next_peer(
    tmp_path: Path,
) -> None:
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    receipt = ablation.apply_arm(
        ws, [{"cut": {"file": "policies/p.md", "heading": "Keep me"}}]
    )
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "Keep me\n" not in out and "Body of keep." not in out
    assert "## Cut me" in out and "Intro line." in out
    assert receipt == {"policies/p.md": 4}


def test_cut_takes_nested_subsections_with_their_parent(tmp_path: Path) -> None:
    """A `###` orphaned under an unrelated `##` reads as a corrupted document,
    which tells the session something was removed."""
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Cut me"}}])
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "Cut me" not in out
    assert "Nested under cut" not in out
    assert "Keep me" in out and "Keep me too" in out


def test_cut_on_a_subsection_leaves_its_parent_and_siblings(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    ablation.apply_arm(
        ws, [{"cut": {"file": "policies/p.md", "heading": "Nested under cut"}}]
    )
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "Nested under cut" not in out
    assert "## Cut me" in out and "Body of cut." in out


def test_cut_on_the_last_section_runs_to_the_end_of_the_file(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    ablation.apply_arm(
        ws, [{"cut": {"file": "policies/p.md", "heading": "Keep me too"}}]
    )
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "Keep me too" not in out and "Trailing body" not in out
    assert out.endswith("Nested body.\n")


def test_cut_raises_when_the_heading_is_absent_rather_than_ablating_nothing(
    tmp_path: Path,
) -> None:
    """The whole point. A silent no-op produces an arm that scores like the
    baseline, which reads as "the spec does not matter" and is both false and
    invisible. The error names the headings the file does have."""
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    with pytest.raises(ablation.AblationError) as err:
        ablation.apply_arm(
            ws, [{"cut": {"file": "policies/p.md", "heading": "Reworded since"}}]
        )
    assert "Reworded since" in str(err.value)
    assert "Keep me" in str(err.value), "the error must list what is available"


def test_cut_raises_when_a_heading_appears_twice(tmp_path: Path) -> None:
    ws = workspace(
        tmp_path, **{"policies__p.md": "# A\n\n## Dup\n\nx\n\n## Dup\n\ny\n"}
    )
    with pytest.raises(ablation.AblationError, match="2 headings"):
        ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Dup"}}])


def test_cut_matches_heading_text_without_the_hashes(tmp_path: Path) -> None:
    """The level lives in the file, not in the config, so promoting a heading
    later does not silently break an arm."""
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Cut me"}}])
    assert "Cut me" not in (ws / "policies/p.md").read_text(encoding="utf-8")


def test_cut_leaves_no_marker_and_no_run_of_blank_lines(tmp_path: Path) -> None:
    """A visible seam tells the session that spec was withheld, and it will
    hedge or report the policy as incomplete. That measures the notice, not
    the missing text."""
    ws = workspace(tmp_path, **{"policies__p.md": DOC})
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Cut me"}}])
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "\n\n\n" not in out
    for marker in ("removed", "redacted", "...", "<!--"):
        assert marker not in out
    assert out.endswith("Trailing body.\n")


def test_cut_ignores_heading_shaped_lines_inside_a_code_fence(tmp_path: Path) -> None:
    doc = "# T\n\n## One\n\n```sql\n# not a heading\n```\n\nstill one\n\n## Two\n\nb\n"
    ws = workspace(tmp_path, **{"policies__p.md": doc})
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "One"}}])
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "not a heading" not in out and "still one" not in out
    assert "## Two" in out


def test_a_horizontal_rule_is_not_read_as_a_setext_heading(tmp_path: Path) -> None:
    """policies/MATCHING.md ends its body with a bare `---`. A setext parser
    reads that as underlining the line above it."""
    doc = "# T\n\n## One\n\nbody\n\n---\n\n## Two\n\nb\n"
    ws = workspace(tmp_path, **{"policies__p.md": doc})
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Two"}}])
    out = (ws / "policies/p.md").read_text(encoding="utf-8")
    assert "## One" in out and "---" in out


def test_cutting_a_file_empty_points_at_drop_instead(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__p.md": "# Only\n\nbody\n"})
    with pytest.raises(ablation.AblationError, match="drop"):
        ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Only"}}])


# --- drop --------------------------------------------------------------------


def test_drop_removes_only_the_named_file(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__a.md": "a\n", "policies__b.md": "b\n"})
    receipt = ablation.apply_arm(ws, [{"drop": "policies/a.md"}])
    assert not (ws / "policies/a.md").exists()
    assert (ws / "policies/b.md").exists()
    assert receipt == {"policies/a.md": 1}


def test_drop_raises_when_the_file_is_not_there(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__a.md": "a\n"})
    with pytest.raises(ablation.AblationError):
        ablation.apply_arm(ws, [{"drop": "policies/gone.md"}])


@pytest.mark.parametrize("escape", ["../outside.md", "/etc/passwd"])
def test_a_path_escaping_the_workspace_is_rejected(tmp_path: Path, escape: str) -> None:
    ws = workspace(tmp_path, **{"policies__a.md": "a\n"})
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    with pytest.raises(ablation.AblationError):
        ablation.apply_arm(ws, [{"drop": escape}])


def test_an_unknown_operation_is_rejected(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__a.md": "a\n"})
    with pytest.raises(ablation.AblationError, match="unknown operation"):
        ablation.apply_arm(ws, [{"shred": "policies/a.md"}])


# --- fingerprint -------------------------------------------------------------


def test_two_identical_workspaces_fingerprint_the_same(tmp_path: Path) -> None:
    a = workspace(tmp_path / "a", **{"policies__p.md": DOC, "task.md": "t\n"})
    b = workspace(tmp_path / "b", **{"policies__p.md": DOC, "task.md": "t\n"})
    assert ablation.spec_fingerprint(a)[0] == ablation.spec_fingerprint(b)[0]


def test_the_fingerprint_moves_when_a_section_is_cut(tmp_path: Path) -> None:
    ws = workspace(tmp_path, **{"policies__p.md": DOC, "task.md": "t\n"})
    before, _ = ablation.spec_fingerprint(ws)
    ablation.apply_arm(ws, [{"cut": {"file": "policies/p.md", "heading": "Cut me"}}])
    assert ablation.spec_fingerprint(ws)[0] != before


def test_the_fingerprint_moves_when_a_file_is_dropped(tmp_path: Path) -> None:
    """Dropping changes no surviving file, so the path has to be inside the
    digest or the deletion is invisible to it."""
    ws = workspace(tmp_path, **{"policies__a.md": "a\n", "policies__b.md": "b\n"})
    before, _ = ablation.spec_fingerprint(ws)
    ablation.apply_arm(ws, [{"drop": "policies/a.md"}])
    assert ablation.spec_fingerprint(ws)[0] != before


def test_the_manifest_names_every_spec_file(tmp_path: Path) -> None:
    """It turns "these two runs disagree" into "...because MATCHING.md
    differs" without re-running either of them."""
    ws = workspace(tmp_path, **{"policies__a.md": "a\n", "task.md": "t\n"})
    _digest, manifest = ablation.spec_fingerprint(ws)
    assert sorted(manifest) == ["policies/a.md", "task.md"]


def test_the_input_lists_are_outside_the_fingerprint() -> None:
    """input_mode is already its own recorded axis; folding the lists in here
    would make every csv-versus-geometry comparison read as two specs."""
    assert "lists" not in ablation.FINGERPRINTED


# --- config ------------------------------------------------------------------


def config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ablations.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_arm_without_a_why_is_rejected(tmp_path: Path) -> None:
    path = config(tmp_path, "baseline: full\narms:\n  full:\n    ops: []\n")
    with pytest.raises(ablation.AblationError, match="why"):
        ablation.load_arms(path)


def test_an_arm_without_ops_is_rejected(tmp_path: Path) -> None:
    path = config(tmp_path, "baseline: full\narms:\n  full:\n    why: x\n")
    with pytest.raises(ablation.AblationError, match="ops"):
        ablation.load_arms(path)


def test_a_baseline_naming_no_arm_is_rejected(tmp_path: Path) -> None:
    path = config(tmp_path, "baseline: nope\narms:\n  full:\n    why: x\n    ops: []\n")
    with pytest.raises(ablation.AblationError, match="baseline"):
        ablation.load_arms(path)


def test_the_shipped_config_validates_against_the_real_policy_files(
    tmp_path: Path,
) -> None:
    """The highest-value test here. Reword a heading in COOPS.md and this
    fails in CI, rather than four hours into a sweep whose arms all quietly
    scored like the baseline."""
    cfg = ablation.load_arms(REPO_ROOT / "fixtures" / "ablations.yaml")
    receipts = ablation.validate_arms(cfg, REPO_ROOT, tmp_path)

    assert cfg["baseline"] in cfg["arms"]
    for arm, receipt in receipts.items():
        if not cfg["arms"][arm]["ops"]:
            assert receipt == {}, f"{arm} declares no operations but removed something"
            continue
        assert receipt, f"arm {arm} removed nothing"
        assert all(n > 0 for n in receipt.values()), f"arm {arm} removed 0 lines"


def test_validation_reports_which_arm_failed(tmp_path: Path) -> None:
    path = config(
        tmp_path,
        """
baseline: full
arms:
  full:
    why: everything
    ops: []
  broken:
    why: names a heading that is not there
    ops:
      - cut: {file: policies/COOPS.md, heading: No Such Heading}
""",
    )
    cfg = ablation.load_arms(path)
    with pytest.raises(ablation.AblationError, match="broken"):
        ablation.validate_arms(cfg, REPO_ROOT, tmp_path / "work")
