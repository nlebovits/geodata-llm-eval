"""Where runs live on disk, and which of them count.

A run used to be `results/{model}/pass-{n}`, a position in a sequence. Two
runs could want the same name, so the harness carried guards to stop the
second destroying the first, and the directory told a reader nothing about
when it ran or against what code. A run is now named for its start time and
the harness commit, which collides with nothing and reads on sight.

Nothing here knows that naming scheme. A run is a directory holding a
meta.json, which keeps the readers working whatever the name says.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

# A run's meta.json, read back as JSON. Heterogeneous by nature: strings,
# counts, nested blocks. Naming it once keeps every reader's signature honest
# without pretending the shape is narrower than it is.
Meta = dict[str, Any]

# The status a run carries on disk. The first four are what the harness
# itself observed; the last three are verdicts something outside the agent
# earned, and only they take a trial out of the reliability denominator.
#
# `done` and `incomplete` are execution outcomes, not trial outcomes. Both
# describe a session that ran and wrote answers, and only grading can say
# whether those answers were right, so trial_status turns them into PASSED
# or FAILED once the grader has recorded strict_success in the run's meta.
DONE = "done"
INCOMPLETE = "incomplete"
AGENT_TIMEOUT = "agent_timeout"
AGENT_PRODUCED_NOTHING = "agent_produced_nothing"
INFRASTRUCTURE_INVALID = "infrastructure_invalid"
AUTHENTICATION_INVALID = "authentication_invalid"
GRADER_ERROR = "grader_error"
# No strict verdict on record. Distinct from GRADER_ERROR, which is a grader
# that ran and crashed: this is one that never ran. Every run made before
# strict success existed reads as UNGRADED until grade.py is run again, which
# is a truer thing to print than a failure the run was never tested for.
UNGRADED = "ungraded"

PASSED = "passed"
FAILED = "failed"

# Every status a trial can end in, exactly one per attempted trial.
TRIAL_STATUSES = frozenset(
    {
        PASSED,
        FAILED,
        AGENT_TIMEOUT,
        AGENT_PRODUCED_NOTHING,
        INFRASTRUCTURE_INVALID,
        AUTHENTICATION_INVALID,
        GRADER_ERROR,
        UNGRADED,
    }
)

# The three a trial can be excused for. An agent that timed out, stopped
# early, or wrote nothing failed the task. Only a failure proven external to
# the agent -- a dead credential, unavailable infrastructure, a grader that
# crashed -- is invalidated, and everything else stays in the denominator.
INVALID_STATUSES = frozenset(
    {INFRASTRUCTURE_INVALID, AUTHENTICATION_INVALID, GRADER_ERROR, UNGRADED}
)

# A session that wrote no answer at all measured nothing about the model. It
# stays on disk for the audit trail and stays out of the per-question
# averages. Written as a denylist so that a status invented later scores by
# default, which costs an average one odd row rather than silently emptying
# it. The reliability figures ignore this and count every attempted trial.
UNSCORED_STATUSES = frozenset({"produced_nothing", AGENT_PRODUCED_NOTHING})


def run_dirs(results_dir: Path, model: str = "") -> list[Path]:
    """Every run directory, oldest first.

    Run ids start with a UTC timestamp, so sorting the names sorts by time.
    """
    pattern = f"{model}/*" if model else "*/*"
    return sorted(d for d in results_dir.glob(pattern) if (d / "meta.json").exists())


def read_meta(run_dir: Path) -> Meta:
    meta: Meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    return meta


def run_name(run_dir: Path) -> str:
    """How a run is named in output: model and run id."""
    return f"{run_dir.parent.name}/{run_dir.name}"


def is_scored(meta: Meta) -> bool:
    """Whether a run belongs in a per-question average.

    Runs predating the status field were all complete, so absence reads as
    scored rather than as a run to silently drop. This governs the diagnostic
    tables only. A run excluded here is still an attempted trial and still
    counts against strict success; see trial_status.
    """
    return meta.get("status", DONE) not in UNSCORED_STATUSES


def trial_status(meta: Meta) -> str:
    """The one status this trial ended in, drawn from TRIAL_STATUSES.

    Execution outcome first, grade second. A session that never reached the
    questions cannot have passed them, so a dead credential or an empty run
    is answered before strict_success is consulted. A run with no verdict on
    record reads as UNGRADED rather than as a silent failure, because a run
    the grader never reached cannot testify either way.

    Legacy `produced_nothing` reads as AGENT_PRODUCED_NOTHING and runs
    predating the status field read as DONE, so old run directories resolve
    to a status without being rewritten.
    """
    status = str(meta.get("status", DONE))
    if status in INVALID_STATUSES:
        return status
    if status in UNSCORED_STATUSES:
        return AGENT_PRODUCED_NOTHING
    if status == AGENT_TIMEOUT:
        return AGENT_TIMEOUT
    strict = meta.get("strict_success")
    if strict is None:
        return UNGRADED
    return PASSED if strict else FAILED


def is_valid(meta: Meta) -> bool:
    """Whether this trial testifies about the agent.

    False only for the three statuses proven external to it. An agent-caused
    timeout, an early stop, and an empty run are failures, and all three stay
    in the denominator.
    """
    return trial_status(meta) not in INVALID_STATUSES


def arm_of(meta: Meta) -> str:
    """Which ablation arm a run was given, defaulting to the whole spec.

    Runs predating the ablation harness carry no block, and they did see the
    whole spec, so reading absence as "full" is accurate rather than a guess.
    """
    return meta.get("ablation", {}).get("arm") or "full"


def spec_of(meta: Meta) -> str | None:
    """The digest of the spec a run actually saw, or None if it predates it."""
    return meta.get("spec_fingerprint")


def group_by_arm(dirs: list[Path]) -> dict[tuple[str, str | None], list[Path]]:
    """Runs bucketed by (arm, spec digest), oldest first within a bucket.

    Keyed on the pair, never the arm name alone. A name says what someone
    meant to run; the digest says what the session was handed. Edit a policy
    between Tuesday and Friday and both days answer to `no-coops` while
    measuring different specs, so pooling them would average two experiments.
    """
    groups: dict[tuple[str, str | None], list[Path]] = {}
    for run_dir in dirs:
        meta = read_meta(run_dir)
        groups.setdefault((arm_of(meta), spec_of(meta)), []).append(run_dir)
    return groups


def regraded(meta: Meta) -> bool:
    """Whether a run's score comes from different fixtures than it ran against.

    True only when both digests are known and disagree. Runs predating either
    field cannot be compared, and absence is not evidence of a mismatch.
    """
    ran, scored = meta.get("golden_fingerprint"), meta.get("graded_against")
    return bool(ran and scored and ran != scored)


class Fingerprint(NamedTuple):
    """Everything that has to match before two runs may be pooled.

    A reliability figure is a claim about one agent configuration measured
    against one task. Pool across a spec edit, a regenerated golden, a repinned
    dataset, or a harness change and the figure describes no experiment that
    was ever run. Naming the parts here rather than at each call site keeps the
    rule in one place and makes a new part one line to add.

    Optional fields are None for runs made before they existed. That is not
    evidence they match a later run, so an absent value groups apart from a
    present one rather than being waved through. `golden` is what existed when
    the agent ran; `graded_against` is the oracle that produced its stored
    verdict. Both matter: the former identifies the experiment, and the latter
    prevents scores from different grading passes being pooled.
    """

    model: str
    model_id: str | None
    agent: str | None
    agent_config: str | None
    arm: str
    spec: str | None
    golden: str | None
    graded_against: str | None
    pins: str | None
    harness_commit: str | None
    input_mode: str | None
    max_attempts: int | None
    max_wall_seconds: int | None

    def label(self) -> str:
        """One line naming the group, digests shortened to their first six."""
        identity = f"{self.agent or 'legacy'}/{self.model}"
        parts = [identity, self.arm]
        for name, digest in (
            ("agent-config", self.agent_config),
            ("spec", self.spec),
            ("golden-at-run", self.golden),
            ("graded", self.graded_against),
            ("pins", self.pins),
            ("harness", self.harness_commit),
        ):
            parts.append(f"{name} {digest[:6]}" if digest else f"{name} –")
        if self.input_mode:
            parts.append(self.input_mode)
        attempts = str(self.max_attempts) if self.max_attempts is not None else "–"
        if self.max_wall_seconds is None:
            wall = "–"
        elif self.max_wall_seconds == 0:
            wall = "unlimited"
        else:
            wall = f"{self.max_wall_seconds / 60:g}m"
        parts.append(f"attempt limit {attempts}")
        parts.append(f"wall limit {wall}")
        return " · ".join(parts)


def fingerprint_of(meta: Meta) -> Fingerprint:
    """The identity of the experiment this run belongs to."""
    return Fingerprint(
        model=meta.get("model", "unknown"),
        model_id=meta.get("model_id"),
        agent=meta.get("agent"),
        agent_config=meta.get("agent_config_fingerprint"),
        arm=arm_of(meta),
        spec=spec_of(meta),
        golden=meta.get("golden_fingerprint"),
        graded_against=meta.get("graded_against"),
        pins=meta.get("pins_fingerprint"),
        harness_commit=meta.get("harness_commit"),
        input_mode=meta.get("input_mode"),
        max_attempts=meta.get("max_attempts"),
        max_wall_seconds=meta.get("max_wall_seconds"),
    )


def group_by_fingerprint(dirs: list[Path]) -> dict[Fingerprint, list[Path]]:
    """Runs bucketed by full experiment identity, oldest first within bucket.

    Wider than group_by_arm, which keys on arm and spec alone because a sweep
    compares arms within one sitting. Reliability is reported across sittings,
    where the golden, the pinned data, and the harness all move.
    """
    groups: dict[Fingerprint, list[Path]] = {}
    for run_dir in dirs:
        groups.setdefault(fingerprint_of(read_meta(run_dir)), []).append(run_dir)
    return groups
