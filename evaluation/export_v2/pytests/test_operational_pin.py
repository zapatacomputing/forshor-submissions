"""The three ForShor pins and what each one decides. Run from the repository root: python -m pytest evaluation/export_v2/pytests -q.

The repository names three ForShor commits and they are NOT interchangeable:

  * the OPERATIONAL pin, `evaluation/policy-trees.json` -> `forshor.ref`: the checkout the policy verifier compiles a
    submission in. It is a verification input (`scripts/ci/verification_identity.py` INPUTS), so moving it re-verifies
    every already-verified head, but it is NOT an epoch input.
  * the FROZEN model/mirror pin, `lake-manifest.json` -> the ForShor package `rev`: the model fingerprint that
    `epoch.model_of()` reports as `forshor.pin` and that the archived STO-mirror evidence was produced at. Moving it
    moves the epoch and orphans the archive.
  * the epoch digest itself, which must stay `ab1f8887b1b50e56` across this release.

The engine and Lean are NOT needed here: only the pin files and `epoch.model_of()` are read.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from evaluation.export_v2 import canon, epoch  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

#: The epoch this release ships. Frozen by the owner; `delta=1e-2` only.
EPOCH = "ab1f8887b1b50e56"
#: The FROZEN model/mirror pin: the ForShor commit the archived STO-mirror evidence was produced at. DO NOT MOVE.
MODEL_PIN = "21e129aeefc0fd1bbb8fa63402f5799af2490788"
#: The OPERATIONAL pin: the native candidate this release moves the verifier checkout to. It is also the readout
#: verifier pin (`export_v2/readout_overlay.py` -> VERIFIER_PIN); the two are one checkout and must not diverge.
OPERATIONAL_PIN = "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"
#: `sha256` of `export_v2/readout_overlay.py`, which is the `ruleSha256` every measured row carries and which the
#: published contract pins as a literal on the publisher side. One byte of drift in that file rejects every readout panel.
READOUT_RULE_SHA256 = "b3c2f317cf68bc47649a634862540177f5fc135d1e6fa543ce9fe4cb1161cd96"


def operational_pin() -> str:
    return json.loads((REPO / "evaluation/policy-trees.json").read_text())["forshor"]["ref"]


def model_pin() -> str:
    for package in json.loads((REPO / "lake-manifest.json").read_text())["packages"]:
        if package["name"] in ("Fast_multiplication", "ForShor"):
            return package["rev"]
    raise AssertionError("lake-manifest.json has no ForShor package")


def test_operational_pin_is_the_native_candidate():
    # FAILS IF: the DIFF 1 edit to `evaluation/policy-trees.json` (`forshor.ref`: 82e2274... -> 80d7b6b...) is reverted
    # or moved on again. This is the pin the policy verifier compiles a submission in.
    assert operational_pin() == OPERATIONAL_PIN


def test_model_pin_is_still_frozen():
    # FAILS IF: anyone moves the ForShor `rev` in `lake-manifest.json`. That is the model fingerprint: moving it changes
    # `epoch.model_of()["forshor"]["pin"]`, so it changes the epoch digest and orphans every archived evidence row,
    # which all record `forshorPin: 21e129a...`.
    assert model_pin() == MODEL_PIN


def test_the_two_pins_are_distinct_roles():
    # FAILS IF: someone "tidies" the two pins into one value, e.g. by pointing `policy-trees.json` back at the frozen
    # model pin or by bumping `lake-manifest.json` to the native candidate. The verifier checkout and the pricing
    # baseline are deliberately different commits and the starter pack documents them separately.
    assert operational_pin() != model_pin()


def test_epoch_is_unchanged_by_the_pin_move():
    # FAILS IF: any epoch input moves (`lake-manifest.json`, `lean-toolchain`, `evaluation/engine`, STO_COMMIT, the
    # qualtran version or the engine data fingerprint). The DIFF 1 operational-pin move must leave this untouched; if
    # this goes red after editing `policy-trees.json`, the operational pin has been wired into the model and the move
    # must be reverted rather than re-baselined.
    model = epoch.model_of()
    assert model["forshor"]["pin"] == MODEL_PIN
    assert canon.epoch_of(model) == EPOCH


def test_the_readout_verifier_pin_is_the_operational_pin():
    # FAILS IF: `export_v2/readout_overlay.py`'s VERIFIER_PIN and `policy-trees.json`'s `forshor.ref` diverge. This is
    # the guard whose absence hid a release-breaker: `intake_v2._forshor_build` refuses readout qualification unless
    # `git rev-parse HEAD` of the verifier checkout EQUALS VERIFIER_PIN, and that checkout is built from the
    # operational pin. While VERIFIER_PIN sat at the readout seed 931bbf16 and the operational pin moved past it
    # (82e2274 -> 80d7b6b), no readout could be qualified on an operationally provisioned tree at all.
    #
    # Moving this literal is a COUPLED change: `rule_fingerprint()` hashes that whole file, so the published
    # `ruleSha256` moves with it, and the published contract pins both values as literals in several places on the
    # publisher side. They must be updated in the same release or every measured row is rejected by the contract.
    from evaluation.export_v2 import readout_overlay

    assert readout_overlay.VERIFIER_PIN == operational_pin()
    assert readout_overlay.VERIFIER_PIN == OPERATIONAL_PIN
    # The readout emitter must actually EXIST in the pinned checkout: 80d7b6b is a strict descendant of the readout
    # seed 931bbf16, so `--readout submitted` is supported there. The predecessor 82e2274 precedes every readout
    # commit, which is why the operational move was a precondition for this route and not a hazard to it.
    assert readout_overlay.VERIFIER_PIN != MODEL_PIN, "the readout verifier is not the frozen pricing baseline"


def test_the_readout_rule_fingerprint_is_the_value_the_site_contract_pins():
    # FAILS IF: `export_v2/readout_overlay.py` changes by one byte without this literal and the publisher's pinned
    # copy of it being updated together. Measured evidence carries `ruleSha256` and the published contract pins it as
    # an exact literal, so a drifted module invalidates every measured row at the contract boundary.
    import hashlib

    from evaluation.export_v2 import readout_overlay

    actual = hashlib.sha256((REPO / "evaluation/export_v2/readout_overlay.py").read_bytes()).hexdigest()
    assert readout_overlay.rule_fingerprint() == actual
    assert actual == READOUT_RULE_SHA256


def test_model_does_not_read_the_operational_pin():
    # FAILS IF: `epoch.model_of()` (or anything it calls) starts reading `evaluation/policy-trees.json`. The operational
    # pin must never reach the epoch digest, otherwise moving the verifier checkout silently restarts the epoch.
    model = epoch.model_of()
    assert OPERATIONAL_PIN not in canon.canonical_json(model)
    assert operational_pin() not in canon.canonical_json(model)
