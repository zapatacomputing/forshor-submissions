"""intake_v2: a resource stop is an UNFINISHED RUN, never a verdict on the policy.

Owner priority 2026-11-24: "being able to handle and correctly differentiate incorrect policies
from correct policies is the priority". A timeout or a memory stop says nothing about the policy,
so it must not be reported in the same class as a refusal (the policy is bad) or as an
infrastructure fault (the harness is bad). `Incomplete` is that third class: exit 2 with a `stage`
and the sentence "This is not a verdict on the policy.", no success receipt, no mutated results.

Two gaps this pins, both found by the refusal-cause census for this release:

  * F2 - the OUTER wall-clock stop escaped `verify_submission` as `subprocess.TimeoutExpired` and
    was reported with no stage and no not-a-verdict sentence, indistinguishable from a KeyError bug;
  * C18 - the incomplete-stage allow-list was two inline string literals, so a resource stop the
    harness named differently would have been reported as a refusal of the policy.

Every test names the production change that would make it fail.
"""
import subprocess
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent / "scripts"))

import intake_v2  # noqa: E402


def test_the_incomplete_stages_are_exactly_the_resource_stops_the_harness_can_report():
    # Production change that would fail this: adding a stage to intake_v2.INCOMPLETE_STAGES that no
    # harness reports (an ordinary refusal would then be reported as an unfinished run), or adding a
    # resource stop to scripts/memcap.py:limit_rejection without listing it here (a resource stop
    # would then be reported as a verdict on the policy). Census C18: the allow-list used to be two
    # inline literals with nothing tying them to memcap.
    import memcap
    from_memcap = {memcap.limit_rejection({"outcome": f"killed: {word} cap 1 GiB"})[0] for word in ("memory", "timeout")}
    assert from_memcap == {"memory", "timeout"}, "memcap.limit_rejection's stages moved; INCOMPLETE_STAGES must follow"
    assert set(intake_v2.INCOMPLETE_STAGES) == from_memcap | {"timeout"}   # plus the harnesses' own TimeoutExpired stage


def test_an_ordinary_verdict_stage_is_not_in_the_incomplete_allow_list():
    # Production change that would fail this: widening INCOMPLETE_STAGES to cover a stage that IS a
    # verdict on the policy. `threshold` and the unnamed `mirror` fallback must stay refusals
    # (exit 1), so the accept/refuse boundary is exactly where it was.
    for stage in ("threshold", "mirror", "parse", "data", "report", "check", "widths", "cert", "replay", "audit"):
        assert stage not in intake_v2.INCOMPLETE_STAGES, stage


def test_the_outer_wall_clock_stop_is_incomplete_with_a_stage_and_the_not_a_verdict_sentence():
    # Production change that would fail this: removing intake_v2._run_mirror_or_incomplete, or
    # letting it raise anything other than Incomplete. run_mirror re-raises subprocess.TimeoutExpired
    # (export_v2/evidence.py:205-207); without the wrapper it escapes verify_submission and is caught
    # by the CLI's unlabelled `subprocess.SubprocessError` arm. Census finding F2.
    def timed_out(policy_json, board, tree, out_dir, **kw):
        raise subprocess.TimeoutExpired(cmd=["check_banded_policy.py"], timeout=kw["timeout"])

    with pytest.raises(intake_v2.Incomplete) as stop:
        intake_v2._run_mirror_or_incomplete(timed_out, {}, "pp", Path("."), Path("."), timeout=1800)
    assert stop.value.stage == "timeout"
    assert not isinstance(stop.value, intake_v2.Refused), "a resource stop is never a refusal"
    assert "1800" in str(stop.value), str(stop.value)                       # the limit it reached
    assert "not a verdict on the policy" in str(stop.value), str(stop.value)
    assert "TimeoutExpired" not in str(stop.value), str(stop.value)         # not a bare exception name


def test_the_wrapper_passes_an_accepted_envelope_through_untouched():
    # Production change that would fail this: having _run_mirror_or_incomplete swallow or rewrite the
    # envelope. A timeout must be the ONLY thing it changes: it may not move the accept/refuse
    # boundary and may not mutate the mirror's own result.
    envelope = {"record": {"verdict": "accepted"}}
    seen = {}

    def run(policy_json, board, tree, out_dir, **kw):
        seen.update(board=board, kw=kw)
        return envelope

    assert intake_v2._run_mirror_or_incomplete(run, {"mode": "pair"}, "pp", Path("."), Path("."), timeout=7200,
                                               fast=True) is envelope
    assert seen == {"board": "pp", "kw": {"timeout": 7200, "fast": True}}


def test_a_non_timeout_failure_out_of_the_mirror_is_not_reclassified():
    # Production change that would fail this: catching a broader exception than
    # subprocess.TimeoutExpired in _run_mirror_or_incomplete. A harness bug is an INFRASTRUCTURE
    # fault, not an unfinished run: calling it Incomplete would hide a real defect behind a stage
    # that reads as "try again".
    def broken(*args, **kwargs):
        raise RuntimeError("the harness wrote no record")

    with pytest.raises(RuntimeError) as error:
        intake_v2._run_mirror_or_incomplete(broken, {}, "pp", Path("."), Path("."), timeout=60)
    assert not isinstance(error.value, intake_v2.Incomplete), "a bug is not a resource stop"


def test_no_incomplete_path_can_reach_the_success_receipt():
    # Production change that would fail this: writing a receipt before the Incomplete arm of main(),
    # or catching Incomplete after the broad `subprocess.SubprocessError` arm (ordering matters:
    # Incomplete is a RuntimeError, so its `except` must come first). write_receipt is only reached
    # on the success branch; an unfinished run must leave no receipt and no result claiming a pass.
    source = Path(intake_v2.__file__).read_text()
    arms = [line.strip() for line in source.splitlines() if line.strip().startswith("except ")]
    incomplete = next(i for i, line in enumerate(arms) if "Incomplete" in line)
    assert incomplete < next(i for i, line in enumerate(arms) if "SubprocessError" in line)
    assert incomplete < next(i for i, line in enumerate(arms) if "Refused" in line)
    assert source.count("write_receipt(verification_path, v)") == 1   # one call site, on the success branch only
