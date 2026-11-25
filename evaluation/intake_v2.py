#!/usr/bin/env python3
"""One submission, end to end: a policy (the submitted Lean file `Policy.lean`, or its site policy JSON) through the Lean checks to the v2 publisher (and so onto the boards).

    python intake_v2.py (--lean-policy Policy.lean | --policy P.json) --login HANDLE --pull N --tree STO_TREE --publisher-url URL
                        [--forshor-tree FORSHOR_CHECKOUT] [--workdir DIR] [--head-sha SHA] [--run-id ID] [--stage-timeout S]

This is the whole of "everything after the participant's policy exists", in one command a workflow can call (GitHub CI supplies the PR's login, number
and head commit, the Lean trees and the publisher's URL and token; nothing else is needed). It never runs anything a participant wrote except Lean
on the policy's own data:

  1. reads the policy and normalises it to the policy the engine prices (a declared width ladder and any other extra key are dropped, never passed to Lean).
     With `--lean-policy` the submission is the Lean file itself: ForShor's lexical gate runs on it, ForShor builds it (needs `--forshor-tree`) and the document it
     prints is the policy (exact widths and the default negation only; 2 <= k <= 8 per band); the document's bands are what the mirror and the pricing use;
  2. refuses the reserved handle, and a publisher whose epoch was built for a different model than this checkout's, BEFORE any compute;
  3. runs the STO mirror (PhaseProduct) or the PhaseTripleProduct harness at exact widths in the Lean tree at the epoch's pins: an envelope that is not
     accepted ends the run, with the stage and reason;
  4. for a PhaseProduct policy whose k are all at most 8, and when a ForShor checkout is given: (JSON input) writes ForShor's PolicyTemplate for it into a disposable
     copy of the checkout and builds it, or (Lean input) uses the build of step 1; prints ForShor's document and requires its bands to be the submitted bands and its own
     IR to price to the same T as the policy. A check that was attempted and fails ends the run. Without a checkout, or for a PhaseTripleProduct policy, the row carries no
     ForShor record (a Lean submission always has one);
  5. prices the policy once, then publishes it through `publish_v2_with_recovery` (the CI client): a conflict or transport error re-reads the live state
     and refreshes only what depends on it.

Exit status: 0 published (or already published), 1 refused (the result says why), 2 the run could not complete (a tool failed).
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "starter" / "pack" / "harness"))

from publish import RetryPublication, request_json  # noqa: E402  (v1 helpers, unchanged)
from publish_v2 import ModelMismatch, ReservedHandle, policy_name, publish_v2_with_recovery  # noqa: E402
from export_v2.attempt import AttemptBusy, exclusive_attempt  # noqa: E402
import summary_tex  # noqa: E402  (the shared rules for the optional summary.tex)
from policy_options import check_coherent_export, check_measured_full_run_export, check_no_full_run_export  # noqa: E402

REPO = "zapatacomputing/forshor-submissions"
MAX_FORSHOR_K = 8           # a submission's bands take 2 <= k <= 8
MAX_LEAN_BYTES = 1_000_000  # the size limit of the submitted Lean file
MAX_POLICY_BANDS = 256     # the publisher's validators reject a policy with more bands, rather than truncating it, so this limit is shared and must not be raised alone

#: The mirror stages that are a RESOURCE STOP, not a verdict: they raise `Incomplete` (exit 2 with a stage and the
#: not-a-verdict sentence), never `Refused`, and never reach `write_receipt`. Keep in sync with the stages
#: `scripts/memcap.py:limit_rejection` can return (`memory`, `timeout`) plus the `timeout` the harnesses report for
#: their own `subprocess.TimeoutExpired` (`scripts/check_banded_policy.py:429-431`, `scripts/check_ptp_policy.py`).
#: A new resource stop the harness names differently would otherwise be reported as a refusal of the policy.
INCOMPLETE_STAGES = ("memory", "timeout")


class Refused(Exception):
    """The submission was examined and does not qualify. `stage` names the check, `reason` says why."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, reason


class Incomplete(RuntimeError):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}. This is not a verdict on the policy.")
        self.stage = stage


def check_band_limit(bands: Any) -> None:
    if isinstance(bands, (list, tuple)) and len(bands) > MAX_POLICY_BANDS:
        raise Refused("format", f"a policy may contain at most {MAX_POLICY_BANDS} bands; found {len(bands)}")


def normalise(policy_doc: Any) -> Dict[str, Any]:
    """Normalize a table policy. Ignore display/width metadata but never discard an explicit design selection."""
    from export_v2 import engine
    from policy_options import check_table_options
    try:
        check_table_options(policy_doc)
    except ValueError as error:
        raise Refused("format", str(error)) from error
    eng = engine.load()
    docs = eng.tables.policy_docs(policy_doc)
    if len(docs) != 1:
        raise Refused("format", f"expected one policy, found {len(docs)}")
    check_band_limit(docs[0].get("bands"))
    try:
        P = eng.tables.Policy.from_json(docs[0])
    except Exception as e:  # noqa: BLE001
        raise Refused("format", f"not an accepted policy: {e}")
    if not P.bands:
        raise Refused("format", "a policy needs at least one band (a bare leaf is not a submission; the Lean harness refuses it too)")
    return P.to_json()


def _point(p: Dict[str, Any]) -> str:
    """A ForShor point (`{"kind": "int", "z": z}` or a fraction with denominator m, 0 meaning infinity) in the site's spelling."""
    if p["kind"] == "int":
        return str(int(p["z"]))
    m = int(p["m"])
    return "inf" if m == 0 else f"{1 if m > 0 else -1}/{abs(m)}"


def policy_from_document(eng, doc: Dict[str, Any]):
    """The policy ForShor's printed document describes, as the engine's policy (its tables spelled as the site spells them)."""
    T = eng.tables
    return T.Policy(mode=T.PAIR, name="forshor-printed", bands=tuple(
        T.Band(int(b["threshold"]), T.Table.from_json({"mode": T.PAIR, "k": int(b["k"]), "point_order": [_point(p) for p in b["points"]],
                                                       "operations": b["ops"]})) for b in doc["bands"]))


def policy_from_ptp_document(eng, doc: Dict[str, Any]):
    """The PhaseTripleProduct policy ForShor's PTP executable printed, as the engine's policy (same spelling of points and operations as the PhaseProduct document)."""
    T = eng.tables
    return T.Policy(mode=T.TRIPLE, name="forshor-printed-ptp", bands=tuple(
        T.Band(int(b["threshold"]), T.Table.from_json({"mode": T.TRIPLE, "k": int(b["k"]), "point_order": [_point(p) for p in b["points"]],
                                                       "operations": b["ops"]})) for b in doc["bands"]))


def forshor_record(eng, P, doc: Dict[str, Any], pin: str) -> Dict[str, Any]:
    """`verification.forshorPolicy` for a PhaseProduct policy, from ForShor's printed document: its bands are the submitted bands and its own IR
    prices to the same single-run T as the policy does (the site's two agreement checks). Raises Refused when either fails."""
    from export_v2.evidence import FORSHOR_CLAIM
    require_legacy_export(check_no_full_run_export, doc)
    sys.path.insert(0, str(eng.engine_dir))
    from site_engine import DEFAULT_DELTA
    from site_engine import fullalg as fa
    printed = policy_from_document(eng, doc)
    bands_agree = [(b.threshold, b.table.k, b.table.digest) for b in printed.bands] == [(b.threshold, b.table.k, b.table.digest) for b in P.bands]
    rule = fa.doc_width_rule(doc)
    t_document = float(fa.price_document(doc, 2048, DEFAULT_DELTA)["T"])
    t_policy = float(fa.price_full(P, 2048, DEFAULT_DELTA, detail=False, rule=rule)["T"])
    ir_agrees = t_document == t_policy
    if not bands_agree:
        raise Refused("forshor", "the bands ForShor printed are not the submitted bands (the PhaseProduct document ForShor printed at the ForShor check)")
    if not ir_agrees:
        raise Refused("forshor", f"ForShor's own IR prices to {t_document!r} T, the policy to {t_policy!r} T")
    return {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": FORSHOR_CLAIM, "forshorCommit": pin}


#: What ForShor builds for each kind of Lean submission: the file's name in ForShor's tree, the targets, the executable and its arguments.
#: PhaseProduct: `PolicyTemplate.lean`, document printed under `--balanced`. PhaseTripleProduct: `PTPPolicyTemplate.lean`, no IR.
FORSHOR_KINDS = {
    "pp": ("PolicyTemplate.lean", ["PolicySubmission", "forshor_policy_submission"], ["forshor_policy_submission", "--balanced"]),
    "ptp": ("PTPPolicyTemplate.lean", ["PTPPolicySubmission", "forshor_ptp_policy_submission"], ["forshor_ptp_policy_submission"]),
}


def lean_kind(lean_text: str) -> str:
    """`"pp"` or `"ptp"`: the board a submitted `Policy.lean` is for, from the namespace it declares (exactly one of the two)."""
    pp = re.search(r"^\s*namespace\s+PolicySubmission\b", lean_text, re.M)
    ptp = re.search(r"^\s*namespace\s+PTPPolicySubmission\b", lean_text, re.M)
    if bool(pp) == bool(ptp):
        # Which of the two mistakes it is decides the edit: a file with neither is missing the declaration, a file with
        # both has to drop one. One message for the two sent half the participants to the wrong line.
        raise Refused("format", "the Lean file must declare exactly one of `namespace PolicySubmission` (PhaseProduct) and "
                                "`namespace PTPPolicySubmission` (PhaseTripleProduct); this file declares "
                                + ("both" if pp else "neither"))
    return "ptp" if ptp else "pp"


def _without_default_selectors(source: str) -> str:
    """The comment-stripped source with its explicit default selector declarations removed (an explicit default token, i.e.
    `.rossSelinger`, `.reference` and `{ readout := .coherent }`, is identical to omission). The recogniser is `scripts/ci/policy_source.py`'s, so the
    CI source gate, this intake and the publisher re-check cannot disagree; any other mention of a selector is left in the text for the refusals below."""
    sys.path.insert(0, str(HERE.parent))
    from scripts.ci.policy_source import strip_lean_comments, without_default_selectors
    return without_default_selectors(strip_lean_comments(source))


def _selector_cause(residual: str, selector: str) -> tuple:
    """(line, cause) for the first remaining mention of `selector`, in the source gate's three cause classes.

    The classifier is `scripts/ci/policy_source.py`'s, as the recogniser above is, so the CI source gate, this intake
    and the publisher re-check cannot disagree about WHICH cause refused: a non-default selection is a valid request on
    the wrong route, a mis-written default only has to be rewritten, and a bare mention has to be deleted. Those are
    three different participant actions. Classification happens after the refusal is decided and changes nothing about
    which sources are refused."""
    sys.path.insert(0, str(HERE.parent))
    from scripts.ci.policy_source import selector_cause
    return selector_cause(residual, selector)


def refuse_unqualified_design(source: str) -> None:
    """Refuse a `submittedDesign` declaration on a check that does not price the semiclassical readout.

    The message is the CI source gate's own (`policy_source.declared_readout`) for anything that is not one of the two
    accepted forms, so the pre-build and the intake refusal name the same line and the same rule. The semiclassical
    readout in its accepted form is refused here only because this check was run without the readout."""
    residual = _without_default_selectors(source)
    if re.search(r"(?<![\w'])submittedDesign(?![\w'])", residual):
        sys.path.insert(0, str(HERE.parent))
        from scripts.ci.policy_source import READOUT_REFUSAL, READOUT_UNPRICED_CAUSE, declared_readout
        try:
            declared_readout(source)
        except ValueError as error:
            raise Refused("format", str(error)) from error
        number = residual.count("\n", 0, re.search(r"(?<![\w'])submittedDesign(?![\w'])", residual).start()) + 1
        raise Refused("format", f"{READOUT_REFUSAL}: line {number}, {READOUT_UNPRICED_CAUSE}")


def require_semiclassical_declared(source: str) -> None:
    """Refuse a measured (semiclassical) receipt whose Lean file does not declare the semiclassical readout, in the one
    shipped form (`policy_source.declared_readout`, the source gate's own recogniser). Raises `Refused`."""
    sys.path.insert(0, str(HERE.parent))
    from scripts.ci.policy_source import declared_readout
    try:
        declared = declared_readout(source)
    except ValueError as error:
        raise Refused("format", str(error)) from error
    if declared != "semiclassical":
        raise Refused("format", "the verification carries a measured (semiclassical) export, but its Lean file does not declare the semiclassical readout "
                                "(`def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }`)")


#: The leading sentence of the refusal for a `submittedPrimitive` declaration on a scored submission.
PRIMITIVE_SOURCE_REFUSAL = "submittedPrimitive is not accepted for a scored submission"


def refuse_unqualified_primitive(source: str) -> None:
    """The standing refusal for every ORDINARY route: byte-identical, and still called wherever a primitive is not
    being qualified. `policy_from_lean(..., qualify_primitive=True)` is the separate local route, which does not
    publish, that the message points at (`scripts/ci/policy_source._SELECTOR_CAUSES`, `docs/CLI.md`).

    The explicit default is NOT a selection: `_without_default_selectors` consumes `def submittedPrimitive :
    Shor.PrimitiveSynthesis := .rossSelinger` first (recognised by
    `policy_source.default_selectors`), so an explicit default reaches this refusal on no route and is
    priced exactly as writing nothing is."""
    residual = _without_default_selectors(source)
    if re.search(r"(?<![\w'])submittedPrimitive(?![\w'])", residual):
        number, cause = _selector_cause(residual, "submittedPrimitive")
        raise Refused("format", f"{PRIMITIVE_SOURCE_REFUSAL}: line {number}, {cause}")


#: The one top-level key a compiled export names its standalone synthesis primitive in. `rossSelinger` stamps nothing,
#: so an export without this key IS the default -- identical to omission, on every route.
PRIMITIVE_KEY = "submitted_primitive"
#: The standing refusal of a compiled primitive export on the ordinary route, kept byte-identical.
PRIMITIVE_EXPORT_REFUSAL = "compiled primitive synthesis is not admitted by this intake"


def check_legacy_full_run_source(source: str) -> bool:
    """The CI source gate's full-run rule, applied to the submitted source: True when it declares an admitted
    reference-topology design (only ever beside the semiclassical readout declaration), False when it declares none
    or the explicit default `.reference`. Raises `ValueError` with the gate's own message otherwise."""
    sys.path.insert(0, str(HERE.parent))
    from scripts.ci.policy_source import check_full_run_selector, strip_lean_comments
    return check_full_run_selector(strip_lean_comments(source))


#: The refusal for a compiled export whose full-run selection is not the one its own source declares.
FULL_RUN_BINDING_REFUSAL = "the compiled full-run design does not match the submitted Lean file"
#: Why a declared design is refused when the run is not pricing the semiclassical readout.
FULL_RUN_PRICED_WITH_READOUT = ("submittedFullRunDesign is not accepted as written: a full-run design is priced only "
                                "together with the semiclassical readout")


def check_full_run_binding(source: str, document: Any) -> bool:
    """A compiled full-run selection must be the one the submitted source declares: the export carries
    `submitted_full_run` exactly when the source declares an admitted design. The verifier guarantees this for its
    own build; a receipt whose document names a selection its own source does not (or the reverse) is refused here,
    by name, before anything is priced or archived. Returns whether a design is declared. Raises `Refused`."""
    try:
        declared = check_legacy_full_run_source(source)
    except ValueError as error:
        raise Refused("format", str(error)) from error
    present = isinstance(document, dict) and FULL_RUN_KEY in document
    if present != declared:
        raise Refused("format", f"{FULL_RUN_BINDING_REFUSAL}: the export "
                                + ("names a full-run design the file does not declare" if present else
                                   "names no full-run design although the file declares one"))
    return declared


def require_legacy_export(check, document: Dict[str, Any]) -> None:
    try:
        check(document)
    except ValueError as error:
        raise Refused("format", str(error)) from error


def require_coherent_export(document: Dict[str, Any]) -> None:
    require_legacy_export(check_coherent_export, document)


#: The one top-level key a compiled export names its full-run selection in (`policy_options.DESIGN_KEYS`).
FULL_RUN_KEY = "submitted_full_run"


def compiled_full_run(printed: Any, *, measured: bool = False) -> tuple:
    """`(selection, design)` for a compiled export: the engine `design` its full-run selection converts to.

    `(None, None)` means "no selection to convert", and the caller then applies the ordinary export checks. That is the
    answer in two cases, and both are deliberate:

      * the document names no selection at all -- the reference pipeline, priced exactly as it always was;
      * this checkout has no `export_v2.full_run_design` converter.
        Then a document that DOES name a selection falls through to `check_no_full_run_export`, which refuses it with
        the standing message. A selection is never read as the default and never priced as one.

    A selection the converter cannot express is refused BY NAME, with the converter's own wording. On a `measured`
    export the conversion is `readout_qft_overlay.design_of_measured`, which also refuses the `exponent` family the
    readout replaces -- on the raw selection, before an all-exact design collapses to None."""
    if not isinstance(printed, dict) or FULL_RUN_KEY not in printed:
        return None, None
    try:
        from export_v2 import full_run_design
    except ImportError:
        return None, None
    try:
        selection = full_run_design.selection_of(printed)
        if measured:
            from export_v2.readout_qft_overlay import design_of_measured
            return selection, design_of_measured(selection)
        return selection, full_run_design.design_of(selection)
    except ValueError as error:
        raise Refused("format", str(error)) from error


def require_readout_epoch(model: Dict[str, Any]) -> None:
    """The measured readout rule is qualified for ONE epoch. A checkout whose engine is at another epoch cannot rule on
    a measured submission at all, so this is an operator-side stop and NOT a verdict on the policy: it is `Incomplete`
    at stage `epoch`, which `scripts/ci/notify_submitters.js` (LIMIT_STAGES) already reports to the submitter as
    incomplete/limits. Checked BEFORE `bind_compiled_readout`, whose own epoch `ValueError` would otherwise surface as
    `Refused("forshor", ...)` -- telling a participant their policy is wrong when the engine checkout is the thing that
    does not match. The cause belongs to the operator, so the message says so."""
    from export_v2.canon import epoch_of
    from export_v2.readout_overlay import EPOCH as READOUT_EPOCH
    if epoch_of(model) != READOUT_EPOCH:
        raise Incomplete("epoch", f"this checkout's engine is at epoch {epoch_of(model)} and the measured readout rule is "
                                  f"qualified for {READOUT_EPOCH}; the submission was not examined")


def require_primitive_epoch(model: Dict[str, Any]) -> None:
    """The standalone primitive rule is qualified for ONE epoch, exactly as the measured readout rule is. A checkout
    whose engine is at another epoch cannot rule on a primitive submission at all, so this is an operator-side stop and
    NOT a verdict on the policy: `Incomplete` at stage `epoch`, which `scripts/ci/notify_submitters.js` (LIMIT_STAGES)
    already reports to the submitter as incomplete/limits. Checked BEFORE `bind_compiled_primitive`, whose own epoch
    `ValueError` would otherwise surface as `Refused("forshor", ...)` -- telling a participant their policy is wrong
    when the engine checkout is the thing that does not match. The readout overlay's precedent, applied to the second one."""
    from export_v2.canon import epoch_of
    from export_v2.primitive_overlay import EPOCH as PRIMITIVE_EPOCH
    if epoch_of(model) != PRIMITIVE_EPOCH:
        raise Incomplete("epoch", f"this checkout's engine is at epoch {epoch_of(model)} and the standalone primitive "
                                  f"rule is qualified for {PRIMITIVE_EPOCH}; the submission was not examined "
                                  "(an operator-side stop, not a verdict on the policy)")


def bind_qualified_primitive(policy, printed: Dict[str, Any], lean_source: str) -> Dict[str, Any]:
    """The standalone primitive evidence for a qualified export, derived HERE from the compiled document and the
    submitted source by `primitive_overlay.bind_compiled_primitive` -- the document's own claim is never the proof.
    A selector bound to another policy, another board or another verifier is refused and prices nothing.
    Raises `Refused`."""
    from export_v2.primitive_overlay import VERIFIER_PIN, bind_compiled_primitive
    try:
        return bind_compiled_primitive(policy, printed, verifier_pin=VERIFIER_PIN,
                                       lean_source=lean_source.encode("utf-8"))
    except ValueError as error:
        raise Refused("forshor", str(error)) from error


def bind_measured_readout(verification: Dict[str, Any], policy, printed: Dict[str, Any]) -> Dict[str, Any]:
    """The readout evidence for a qualified measured export, re-derived HERE from the compiled document and the
    submitted source. The receipt's own `localReadoutQualification.evidence` is never the proof: it is compared with
    what `bind_compiled_readout` independently produces, so evidence bound to another source, or naming another
    verifier, is refused rather than published. Raises `Refused`."""
    from export_v2.readout_overlay import VERIFIER_PIN, bind_compiled_readout
    source = verification.get("leanSource")
    if not isinstance(source, str):
        raise Refused("format", "a qualified measured readout needs the submitted Lean source it was compiled from")
    qualification = verification.get("localReadoutQualification")
    if not isinstance(qualification, dict) or qualification.get("mode") != "semiclassical" \
            or printed.get("submitted_readout") != "semiclassical":
        raise Refused("forshor", "missing or mismatched compiled readout selection")
    try:
        readout = bind_compiled_readout(policy, printed, verifier_pin=VERIFIER_PIN, lean_source=source.encode("utf-8"))
    except ValueError as error:
        raise Refused("forshor", str(error)) from error
    if qualification.get("evidence") != readout:
        raise Refused("forshor", "the qualified readout evidence does not bind this source and this verifier")
    return readout


def forshor_build(forshor_tree: Path, template: str, workdir: Path, timeout: int = 7200, kind: str = "pp", *,
                  qualify_readout: bool = False, qualify_primitive: bool = False) -> Dict[str, Any]:
    """The Lean file as ForShor's template (`kind`: `pp` or `ptp`), in a disposable copy of the checkout: ForShor's lexical gate, its build, then the document its
    executable prints. Raises `Refused` at stage `lexical` or `forshor`; the file is only ever compiled, never run by us."""
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    with exclusive_attempt(workdir / ".forshor-build.lock") as lock_fd:
        return _forshor_build(forshor_tree, template, workdir, timeout, kind, qualify_readout, qualify_primitive, lock_fd)


def _pinned_verifier_or_refused(forshor_tree, template, pin: str, what: str) -> None:
    """A qualification route compiles in the checkout its rule is PINNED to, and nowhere else: the checkout's HEAD must
    equal `pin` and the tree must be clean, and the source must pass the trusted CI source gate before it is built.
    Shared by the readout and primitive routes so the two cannot drift apart."""
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=forshor_tree, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=forshor_tree, text=True)
    if actual != pin or dirty:
        raise Refused("forshor", f"{what} requires a clean checkout of the pinned verifier")
    source_gate = HERE.parent / "scripts" / "ci" / "policy_source.py"
    checked = subprocess.run([sys.executable, str(source_gate), "--stdin"], input=template, text=True, capture_output=True)
    if checked.returncode:
        raise Refused("policy-source", checked.stderr.strip())


def _forshor_build(forshor_tree, template, workdir, timeout, kind, qualify_readout, qualify_primitive, lock_fd):
    filename, targets, exe = FORSHOR_KINDS[kind]
    if qualify_readout and qualify_primitive:
        raise Refused("format", "a combined readout and primitive qualification is not admitted (asked for at the ForShor build)")
    if qualify_readout:
        from export_v2.readout_overlay import VERIFIER_PIN
        if kind != "pp":
            raise Refused("format", "readout qualification only supports PhaseProduct (asked for at the ForShor build)")
        _pinned_verifier_or_refused(forshor_tree, template, VERIFIER_PIN, "readout qualification")
        exe = [*exe, "--readout", "submitted"]
    if qualify_primitive:
        # The standalone primitive is qualified on BOTH single-product boards (`primitive_overlay.BOARD_SCHEMA`), so
        # unlike readout there is no `kind` restriction here. The pin is the primitive rule's own VERIFIER_PIN, the
        # build the recorded qualification receipts were taken at; it equals the operational pin in
        # `evaluation/policy-trees.json`, so the route compiles in the checkout the policy verifier is provisioned with.
        from export_v2.primitive_overlay import VERIFIER_PIN
        _pinned_verifier_or_refused(forshor_tree, template, VERIFIER_PIN, "primitive qualification")
        exe = [*exe, "--primitive", "submitted"]
    tree = workdir / "forshor-tree"
    if tree.exists():
        shutil.rmtree(tree)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from tree_copy import copy_lean_tree
    copy_lean_tree(forshor_tree, tree)
    if (qualify_readout or qualify_primitive) and (tree / ".git").is_file():
        (tree / ".git").unlink()
    target = tree / "ForShor" / "ShorVerification" / "Submission" / filename
    if not target.parent.is_dir() or (kind == "ptp" and not (target.parent / "PTPPolicyCheck.lean").exists()):
        raise RuntimeError(f"the ForShor checkout has no {'PhaseTripleProduct' if kind == 'ptp' else 'policy'} submission files (it needs ForShor's PTPPolicyCheck and PTPPolicyMain)")
    source = template.encode("utf-8")
    target.unlink(missing_ok=True)  # don't follow an old template symlink in the copy
    target.write_bytes(source)
    gate = subprocess.run(["bash", "scripts/check_template_lexical.sh", str(target)], cwd=tree, capture_output=True, text=True)
    if gate.returncode != 0:
        raise Refused("lexical", "ForShor's lexical gate refused the file: " + (gate.stdout + gate.stderr)[-500:].strip())
    log = workdir / "forshor.log"
    try:
        with open(log, "w") as out:
            built = subprocess.run(["lake", "build", *targets], cwd=tree, stdout=out, stderr=subprocess.STDOUT, timeout=timeout, pass_fds=(lock_fd,))
    except subprocess.TimeoutExpired:
        raise Incomplete("timeout", f"ForShor's Lean build reached its {timeout:g}s time limit") from None
    if built.returncode != 0:
        text = log.read_text()
        errors = [line for line in text.splitlines() if "error" in line.lower()]          # Lean's own error lines, not the tail of a long build log
        raise Refused("forshor", "ForShor's build of the policy template failed: " + ("\n".join(errors[:6]) or text[-500:])[:900])
    try:
        printed = subprocess.run(["lake", "exe", *exe], cwd=tree, capture_output=True, text=True, timeout=timeout, pass_fds=(lock_fd,))
    except subprocess.TimeoutExpired:
        raise Incomplete("timeout", f"ForShor's circuit extraction and checks reached their {timeout:g}s time limit") from None
    if printed.returncode != 0:
        raise Refused("forshor", "ForShor's check refused the policy: " + (printed.stderr or printed.stdout)[-500:])
    if target.read_bytes() != source:
        raise RuntimeError("Policy.lean changed during the ForShor compile attempt")
    document = json.loads(printed.stdout)
    if qualify_readout:
        # The measured route is the one route a full-run design publishes on: the verifier emits one only here.
        require_legacy_export(check_measured_full_run_export, document)
    elif qualify_primitive:
        require_legacy_export(check_no_full_run_export, document)
    return document


def forshor_check(eng, P, forshor_tree: Path, workdir: Path, pin: str, timeout: int = 7200) -> Dict[str, Any]:
    """ForShor's build of the policy's template in a disposable copy of the checkout, then its printed document, then `forshor_record`."""
    import policy_to_template as ptt
    return forshor_record(eng, P, forshor_build(forshor_tree, ptt.template_text(P, "ForShor"), workdir, timeout), pin)


def policy_from_lean(eng, lean_text: str, forshor_tree: Optional[Path], workdir: Path, timeout: int = 7200, *,
                     qualify_readout: bool = False, qualify_primitive: bool = False):
    """A submitted `Policy.lean` (ForShor's PolicyTemplate format for PhaseProduct, its PTPPolicyTemplate format for PhaseTripleProduct, told apart by the namespace it
    declares) to (the policy as site policy JSON, ForShor's printed document). ForShor builds the file, so the document is the policy it kernel-checked; the boards then
    check the same tables in the STO mirror and price them. Only exact widths and the default negation are priced, so a file that asks for anything else is refused
    rather than priced as something it is not.

    `qualify_primitive` is the separate standalone-primitive route, which does NOT publish. On it,
    and only on it, `submittedPrimitive` is not refused and a compiled `submitted_primitive` export is admitted, to be
    bound and priced by `export_v2.primitive_overlay`. Every other route keeps both refusals byte-identical, and the
    explicit default is identical to omission on all of them."""
    if len(lean_text.encode()) > MAX_LEAN_BYTES:
        raise Refused("format", f"the Lean file is larger than {MAX_LEAN_BYTES} bytes")
    if qualify_readout and qualify_primitive:
        # Each overlay re-prices the standalone circuit from the same frozen census and returns a FINAL price, and
        # `primitive_overlay.bind_compiled_primitive` refuses a measured document from the other end. The pair has
        # never had a defined price anywhere in this pipeline, so it is refused rather than priced on a guessed
        # precedence -- the same call as for readout + design.
        raise Refused("format", "a combined measured readout and primitive qualification is not admitted")
    try:
        declares_design = check_legacy_full_run_source(lean_text)
    except ValueError as error:
        raise Refused("format", str(error)) from error
    if declares_design and not qualify_readout:
        # The source gate admits a design only beside the semiclassical readout declaration, and that declaration is
        # refused by `refuse_unqualified_design` below on every route but the measured one. Said here, by name, so the
        # design is not reported as a readout problem.
        raise Refused("format", f"{FULL_RUN_PRICED_WITH_READOUT}, and this check was run without the readout")
    kind = lean_kind(lean_text)
    if not qualify_primitive:
        refuse_unqualified_primitive(lean_text)
    if qualify_readout and kind != "pp":
        raise Refused("format", "readout qualification only supports PhaseProduct (asked for at intake)")
    if not qualify_readout:
        refuse_unqualified_design(lean_text)
    if forshor_tree is None:
        raise RuntimeError("a Lean policy needs ForShor's build, and no ForShor checkout was given (--forshor-tree)")
    doc = forshor_build(forshor_tree, lean_text, workdir, timeout, kind=kind,
                        **({"qualify_readout": True} if qualify_readout else {}),
                        **({"qualify_primitive": True} if qualify_primitive else {}))
    if PRIMITIVE_KEY in doc and not qualify_primitive:
        raise Refused("format", PRIMITIVE_EXPORT_REFUSAL)
    # The route picks the checker, as it already does for readout. A primitive export carries the selector the
    # coherent checker has no opinion on, but it must still name no full-run selection and -- when it names NO
    # primitive at all, which is what the explicit default compiles to -- it is the plain coherent export and gets the
    # coherent checker, so the default path is identical to omission here too.
    if qualify_primitive and PRIMITIVE_KEY in doc:
        require_legacy_export(check_no_full_run_export, doc)
    elif qualify_readout:
        # A full-run design is admitted on the measured route, bound to this source, and converted now so that a
        # selection that is not priced is refused by name before the mirror runs.
        require_legacy_export(check_measured_full_run_export, doc)
        check_full_run_binding(lean_text, doc)
        compiled_full_run(doc, measured=True)
    else:
        require_legacy_export(check_coherent_export, doc)
    bands = doc["bands"]
    check_band_limit(bands)
    if not bands:
        raise Refused("format", "a policy needs at least one band; the bare leaf is not a submission")
    for band in bands:
        if not 2 <= band["k"] <= MAX_FORSHOR_K:
            raise Refused("format", f"band at threshold {band['threshold']} has k = {band['k']}; submissions take 2 <= k <= {MAX_FORSHOR_K}")
    if doc.get("negation", "oneQubit") != "oneQubit":
        raise Refused("format", f"the policy asks for negation {doc.get('negation')!r}; the boards price oneQubit only (from the compiled Lean export, not the submitted table JSON)")
    if kind == "pp":
        from site_engine import fullalg as fa
        if fa.doc_width_rule(doc) != "exact":
            raise Refused("format", "the policy does not use exact widths; the boards price at exact widths")
        P = policy_from_document(eng, doc)
    else:
        P = policy_from_ptp_document(eng, doc)
    return P.to_json(), doc


def _published_archive_commit(url: str, token: str) -> str:
    """The archive commit the live snapshot records, read from the published dashboard (`/results/v2/dashboard.json`, which a local publisher serves and
    staging serves from the bucket mount). `/v2/state` does not carry it. Without it, pass `archive_commit` (or `--archive`)."""
    try:
        return request_json(url.rstrip("/") + "/results/v2/dashboard.json", token)["publication"]["archiveCommit"]
    except (RetryPublication, RuntimeError, KeyError) as e:
        raise RuntimeError("the archive commit is not in /v2/state: pass --archive-commit (or --archive); "
                           f"reading it from the published dashboard failed ({type(e).__name__})") from None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


VERIFICATION_SCHEMA = "forshor.policy-verification/v1"
PUBLICATION_SCHEMA = "forshor.publication/v2"


def check_summary(summary: Optional[str], lean_policy: Optional[str]) -> None:
    """The optional `summary.tex` text: the shared rules (`summary_tex.problem`), and it goes with a Lean policy only. Raises `Refused("summary", ...)`. The text is never
    compiled, included or run by anything here: it is carried as data and archived verbatim."""
    if summary is None:
        return
    if lean_policy is None:
        raise Refused("summary", "a summary goes with a submitted Policy.lean, never alone")
    why = summary_tex.problem(summary.encode("utf-8", "surrogatepass"))
    if why:
        raise Refused("summary", why)


def verify_submission(policy_doc: Any, *, lean_policy: Optional[str] = None, summary: Optional[str] = None, login: str, url: Optional[str] = None, token: str = "local", tree: Path, workdir: Path,
                      forshor_tree: Optional[Path] = None, stage_timeout: Optional[int] = None, mirror_timeout: int = 7200,
                      run_mirror_fn: Optional[Callable[..., Dict[str, Any]]] = None, python: str = sys.executable,
                      qualify_readout: bool = False, qualify_primitive: bool = False, publishing: bool = False,
                      log: Callable[[str], None] = lambda s: print(s, flush=True)) -> Dict[str, Any]:
    """Everything up to the publish: the reserved handle and (when a publisher URL is given) the epoch, then ForShor's gate and build (a Lean submission), the STO mirror
    and ForShor's check. Returns the verification: the policy, ForShor's printed document, the mirror's envelope and the model it was verified under, as data a trusted
    job can publish from (`publish_verification`). Nothing is priced and nothing leaves the machine. Raises `Refused`, `ReservedHandle`, `ModelMismatch`."""
    workdir.mkdir(parents=True, exist_ok=True)
    with exclusive_attempt(workdir / ".verification.lock"):
        return _verify_submission(policy_doc, lean_policy, summary, login, url, token, tree, workdir, forshor_tree,
                                  stage_timeout, mirror_timeout, run_mirror_fn, python, qualify_readout,
                                  qualify_primitive, publishing, log)


def _run_mirror_or_incomplete(run, policy_json, board, tree, out_dir, **kw):
    """`run_mirror`, with its outer wall-clock stop reported as `Incomplete` rather than as a bare exception.

    `run_mirror` re-raises `subprocess.TimeoutExpired` (`export_v2/evidence.py:205-207`), which would otherwise escape
    `verify_submission` and be caught by the CLI's `subprocess.SubprocessError` arm: exit 2 with no stage and without
    the not-a-verdict sentence, i.e. indistinguishable in class from a `KeyError` bug. A resource stop is INCOMPLETE,
    never a refusal and never a pass, so the outer limit must agree with the harness's own stage timeout, which already
    maps to `Incomplete` through `INCOMPLETE_STAGES`. The exit code and the accept/refuse split are unchanged."""
    try:
        return run(policy_json, board, tree, out_dir, **kw)
    except subprocess.TimeoutExpired:
        raise Incomplete("timeout", f"the Lean mirror reached its {kw['timeout']:g}s wall-clock limit") from None


def _verify_submission(policy_doc, lean_policy, summary, login, url, token, tree, workdir, forshor_tree,
                       stage_timeout, mirror_timeout, run_mirror_fn, python, qualify_readout, qualify_primitive,
                       publishing, log):
    from export_v2 import engine, epoch
    from export_v2.canon import policy_digest
    from export_v2.evidence import run_mirror, stomirror_from_envelope
    from publish_v2 import HOUSE_HANDLE
    if qualify_readout and (lean_policy is None or (url and not publishing)):
        # `publishing`: `run_intake` verifies and publishes in ONE trusted run, so there the publisher URL is the
        # publisher it is about to publish to. A bare `--qualify-readout --no-publish` still touches no network.
        raise Refused("format", "the semiclassical readout is checked from Policy.lean, and only a run that publishes takes a publisher URL")
    if qualify_primitive and (lean_policy is None or url):
        # NO publishing arm, deliberately, and this is the difference from readout. The standalone primitive route
        # does not publish (`scripts/ci/policy_source._SELECTOR_CAUSES`, `docs/CLI.md`), the
        # recorded qualification receipts carry `localOnly: true` / `publishable: false`, and the site contract has no
        # field for a primitive price on either the row or the panel -- so there is no publisher to talk to and a URL
        # is refused outright rather than silently ignored.
        raise Refused("format", "primitive qualification is local only and requires Policy.lean")
    check_summary(summary, lean_policy)                         # a rule of the summary file first: cheap, and not a verdict on the policy
    eng = engine.load()
    model = epoch.model_of()
    if login.lower() == HOUSE_HANDLE:                           # the reserved handle, before any compute (also offline)
        raise ReservedHandle(f"the handle {HOUSE_HANDLE} is reserved for planner policies")
    if url:
        state = request_json(url.rstrip("/") + "/v2/state", token)
        policy_name(state, login)
        if state["model"] != model:
            raise ModelMismatch(f"live epoch {state['epoch']} was built for a different model than this checkout's; not publishing")

    printed = None
    if lean_policy is not None:                                 # a Lean submission: ForShor builds it, its printed document is the policy
        log("ForShor's lexical gate, build and printed document (the submitted Lean file) ...")
        policy_doc, printed = policy_from_lean(eng, lean_policy, forshor_tree, workdir,
                                               **({"qualify_readout": True} if qualify_readout else {}),
                                               **({"qualify_primitive": True} if qualify_primitive else {}))
    policy_json = normalise(policy_doc)
    digest = policy_digest(policy_json)
    board = "ptp" if policy_json["mode"] == eng.tables.TRIPLE else "pp"
    P = eng.tables.Policy.from_json(policy_json)
    primitive = None
    if qualify_primitive:
        if PRIMITIVE_KEY in printed:
            require_primitive_epoch(model)                      # an operator-side epoch stop is not this policy's fault
            primitive = bind_qualified_primitive(P, printed, lean_policy)
            pass
        else:
            # No selector in the compiled export IS the default: `.rossSelinger` stamps nothing, so this is the plain
            # coherent policy and is checked and priced exactly as an ordinary submission is.
            require_coherent_export(printed)
    readout = None
    if qualify_readout:
        from export_v2.readout_overlay import MEASURED_SCHEMA, VERIFIER_PIN, bind_compiled_readout
        selected = printed.get("submitted_readout")
        if selected == "semiclassical" and printed.get("schema") == MEASURED_SCHEMA:
            require_readout_epoch(model)                        # an operator-side epoch stop is not this policy's fault
            try:
                readout = bind_compiled_readout(P, printed, verifier_pin=VERIFIER_PIN, lean_source=lean_policy.encode("utf-8"))
            except ValueError as error:
                raise Refused("forshor", str(error)) from error
        elif selected != "coherent" or printed.get("schema") != "forshor.policy-submission/v1":
            raise Refused("forshor", "missing or mismatched compiled readout selection")
        else:
            require_coherent_export(printed)
    log(f"policy {digest[:12]} ({policy_json['mode']}, {len(policy_json['bands'])} bands): the Lean mirror at exact widths ...")
    envelope = _run_mirror_or_incomplete(run_mirror_fn or run_mirror, policy_json, board, tree, workdir / "evidence",
                                         timeout=mirror_timeout, python=python, stage_timeout=stage_timeout, fast=True)   # `fast`: skip the interpreted report stage, see run_mirror
    mirror, why = stomirror_from_envelope(envelope, policy_json, model, board)
    if mirror is None:
        rec = envelope.get("record", {})
        rej = rec.get("rejection") or {}
        stage = rej.get("stage") or "mirror"
        reason = f"{rej.get('reason') or why}: {str(rej.get('detail') or '')[:400]}".strip(": ")
        if stage in INCOMPLETE_STAGES:
            raise Incomplete(stage, reason)
        raise Refused(stage, reason)
    log("  accepted by the STO mirror")

    if readout is not None:
        forshor = None
        log("  verified the compiled readout and checked its reference arithmetic census")
    elif primitive is not None:
        # A standalone primitive proof claims no coherent IR agreement: `forshor_record` prices the document's own IR
        # against the coherent single-run cost, and a primitive export's phase sites are not that circuit.
        forshor = None
        log(f"  bound the compiled {primitive['primitive']} primitive on the {primitive['board']} board (local qualification only)")
    elif printed is not None and str(printed.get("schema", "")).startswith("forshor.ptp-"):
        log("  ForShor kernel-checked the PhaseTripleProduct policy (it has no IR, so the row carries no ForShor record)")
        forshor = None
    elif printed is not None:
        forshor = forshor_record(eng, P, printed, VERIFIER_PIN if qualify_readout else model["forshor"]["pin"])
        log("  ForShor policy-verified")
    elif forshor_tree and P.mode == eng.tables.PAIR and max(b.table.k for b in P.bands) <= MAX_FORSHOR_K:
        log("ForShor's build and printed document ...")
        import policy_to_template as ptt
        printed = forshor_build(Path(forshor_tree), ptt.template_text(P, "ForShor"), workdir)
        forshor = forshor_record(eng, P, printed, model["forshor"]["pin"])
        log("  ForShor policy-verified")
    else:
        forshor = None
        log("no ForShor check (" + ("PhaseTripleProduct" if P.mode != eng.tables.PAIR else "no ForShor checkout given" if not forshor_tree else f"k above {MAX_FORSHOR_K}") + ")")
    out = {"schema": VERIFICATION_SCHEMA, "digest": digest, "mode": policy_json["mode"], "board": board, "policy": policy_json, "printed": printed,
           "envelope": envelope, "forshor": forshor, "model": model, "login": login, "createdAt": _now()}
    if qualify_readout:
        out["localReadoutQualification"] = {"mode": selected, "evidence": readout}
    if qualify_primitive:
        # `primitive` is the DEFAULT name when the export named no selector, so the receipt states the default
        # explicitly rather than leaving a reader to infer it from an absent key.
        from export_v2.primitive_overlay import DEFAULT_PRIMITIVE
        out["localPrimitiveQualification"] = {"primitive": primitive["primitive"] if primitive else DEFAULT_PRIMITIVE,
                                              "evidence": primitive}
    if lean_policy is not None:                                 # what the archive stores; the publish job compares it with the file at the PR's head commit
        out.update(leanSource=lean_policy, leanSha256=hashlib.sha256(lean_policy.encode()).hexdigest())
    if summary is not None:                                     # data only: nothing above read it, so it cannot have changed the digest or the verdict
        raw = summary.encode("utf-8")
        out.update(summarySource=summary, summarySha256=summary_tex.sha256(raw), summaryBytes=len(raw))
    return out


def prepare_receipt_path(path: Path, *other_paths: Optional[Path]) -> None:
    if path.is_symlink() or any(other is not None and path.resolve() == other.resolve() for other in other_paths):
        raise ValueError("the verification receipt needs a separate, non-symlink output path")
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = None
        if not isinstance(old, dict) or old.get("schema") != VERIFICATION_SCHEMA:
            raise ValueError(f"not replacing an unrelated file at {path}; choose a fresh verification output path")
        path.unlink()


def write_receipt(path: Path, verification: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(json.dumps(verification) + "\n")
            f.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def archive_policy_rules() -> Dict[str, str]:
    import archive_policy
    return archive_policy.DUPLICATE_RULES


def publish_verification(v: Dict[str, Any], *, login: str, pull: int, url: str, token: str, workdir: Path, head_sha: Optional[str] = None, run_id: Optional[str] = None,
                         organizer_baseline: Optional[str] = None, archive_commit: Optional[str] = None, archive_view: Optional[Callable[[], Any]] = None,
                         archive_write: Optional[Callable[..., Dict[str, Any]]] = None, qualified_in_run: bool = False,
                         origin: Optional[Any] = None,
                         log: Callable[[str], None] = lambda s: print(s, flush=True)) -> Dict[str, Any]:
    """Price and publish a verification (`verify_submission`'s output, possibly produced by another job). No Lean runs here and nothing in `v` is trusted: the policy's digest, the
    mirror's envelope (against this checkout's model and the policy), the printed document and ForShor's record are all checked again, and the handle and epoch against the live
    publisher. Raises `Refused`, `ReservedHandle`, `ModelMismatch`.

    `origin` (an `export_v2.receipt_origin.ReceiptOrigin`) is how a receipt this process did NOT produce establishes a
    trusted origin: the publisher re-derives the verification identity from its own checkout and re-fetches the
    artifact the receipt's `ciVerification` stamp names. Without one, the receipt route refuses a qualified readout
    exactly as before.

    `archive_write` is called with the verification AND the result kind this run established -- `readout`, `primitive`
    and `design`, `archive_policy.KIND_DEFAULT`'s three axes. It is keyword-only for everything after the
    verification, so an axis the archive gains later arrives here as a new keyword and not as a positional shift."""
    from export_v2 import engine, epoch, receipt_origin, request
    from export_v2.canon import policy_digest
    from export_v2.evidence import EvidenceStore
    from export_v2.readout_overlay import MEASURED_SCHEMA
    printed = v.get("printed")
    # Owner decision 2026-11-24: the blanket refusal here was a conservative default while readout qualification was
    # pending. It exists now, so a qualified measured document THIS RUN's trusted verifier produced is priced on the
    # measured path below (`qualified_in_run`, set only by `run_intake`, which just ran the verifier itself).
    #
    # Production publishes through `--publish-from`, where `qualified_in_run` is false by construction, so the
    # refusal below also closed the ONLY route production uses. A receipt still vouches for nothing -- but it no longer
    # has to. `scripts/ci/policy_publication.js:96-98` already stamps the trusted `ciVerification` receipt into the
    # file this route is handed, and `receipt_origin.establish` turns that stamp into a proof by re-deriving the
    # verification identity HERE and re-fetching the artifact it names: the receipt is held to that comparison and
    # vouches for nothing itself. So the standing rule is kept in spirit and sharpened -- the inputs come from the
    # trusted GitHub run (the artifact) and this checkout (the fingerprint), and a receipt that is not that artifact is
    # refused by name. The comparison shows where the receipt came from, not that its content is right: the gates
    # below (the Lean file declares the readout the export carries, the export is the one the file declares) still rule.
    measured = isinstance(printed, dict) and printed.get("schema") == MEASURED_SCHEMA
    trusted_origin = None
    if "localReadoutQualification" in v or measured:
        if not qualified_in_run and origin is None:
            raise Refused("format", "a semiclassical readout receipt is published only from the trusted verification run that produced it, and this receipt does not come from one")
        if not qualified_in_run:
            try:
                trusted_origin = receipt_origin.establish(v, head_sha=head_sha, run_id=run_id, pull=pull, origin=origin)
            except receipt_origin.OriginMismatch as error:
                raise Refused("origin", str(error)) from error
            except receipt_origin.OriginUnavailable as error:
                # The trusted artifact did not answer. That is the publisher's fault, not the policy's: stage
                # `publisher` is in `scripts/ci/notify_submitters.js` LIMIT_STAGES, reported as incomplete/limits.
                raise Incomplete("publisher", str(error)) from error
            log(f"  trusted origin: artifact {trusted_origin['artifact']} of run {trusted_origin['runId']} "
                f"attempt {trusted_origin['runAttempt']}, at this checkout's verification fingerprint")
    # RETAINED, and NOT lifted with the source gate. The standalone primitive route does
    # not publish: the recorded qualification receipts carry
    # `localOnly: true` / `publishable: false`, and the published ROW's field set is closed and names no primitive
    # price or its kind -- transcribed into this repository as `SITE_ROW_FIELDS` in
    # `export_v2/pytests/test_design_price_plumbing.py`, where the publisher's validators are shown to REJECT an
    # unrecognised row key rather than ignore it. So a primitive-bearing row cannot be represented, let alone ranked.
    # (The PANEL half is not the blocking one: an optional primitive block on a policy panel
    # is contract-valid and validated. The route does not publish because the qualifying harness never publishes.)
    # Both halves are guarded, because either alone would be enough to publish a standalone price as if it were the
    # plain one: the receipt's qualification key AND the compiled selector.
    #
    # This was written beside the blanket readout refusal it branched on, which the trusted-origin establishment
    # above later replaced. The two are independent -- readout origin vs primitive
    # publishability -- so both survive, in their original relative order. The primitive route gains no origin arm
    # because it never publishes at all.
    if "localPrimitiveQualification" in v or (isinstance(printed, dict) and PRIMITIVE_KEY in printed):
        raise Refused("format", "primitive qualification is local only; publishing is not enabled")
    if measured:
        # The measured route's own document gate, then the fail-closed conversion (by name).
        require_legacy_export(check_measured_full_run_export, printed)
    selection, design = compiled_full_run(printed, measured=True) if measured else (None, None)
    if isinstance(printed, dict):
        # The route picks the checker, as `policy_from_lean` already does at the compile step: a measured export
        # carries the readout loop the coherent checker exists to refuse, and its full-run selection is ruled on by
        # the measured gate and `compiled_full_run` above.
        if not measured:
            require_coherent_export(printed)
        elif selection is None:
            # No selection converted. With no key this passes; with a key and no converter in this checkout, the
            # standing refusal names it -- a selection is never priced as the readout-only default.
            require_legacy_export(check_no_full_run_export, printed)
    if isinstance(v.get("leanSource"), str):
        # The receipt's two source fields must agree before the gates below are trusted: they rule on `leanSource`, while
        # `archive_policy.archive_policy` compares BOTH fields against the file at the pull request's head commit. If they
        # disagree, the gate's verdict is about text that is not the submission. The archive refuses that too, but only
        # when the publish job runs with `--archive`; nothing checked it on the `archive_write=None` route. Lifted from
        # `request.rehearse_qualified_request`, whose docstring forbids giving it a production caller: the CHECK moves
        # here, the trusted-source comparison stays in `archive_policy`.
        if isinstance(v.get("leanSha256"), str) and v["leanSha256"] != hashlib.sha256(v["leanSource"].encode()).hexdigest():
            raise Refused("format", "the verification's Lean source does not match its own digest")
        if measured:
            # The Lean file must declare the readout the export carries (the coherent branch below has
            # `refuse_unqualified_design` for the reverse): the verifier job builds a measured export only for a file that
            # declares it, so a receipt pairing one with a file that does not is not what that job produced.
            require_semiclassical_declared(v["leanSource"])
            # The selection must be the one this source declares (an inconsistent receipt is refused here,
            # by name, before anything is priced or archived).
            check_full_run_binding(v["leanSource"], printed)
        else:
            try:
                if check_legacy_full_run_source(v["leanSource"]):
                    raise ValueError(f"{FULL_RUN_PRICED_WITH_READOUT}, and this verification does not carry it")
            except ValueError as error:
                raise Refused("format", str(error)) from error
        if not measured:
            # The same precedent as `policy_from_lean`: a qualified measured submission declares the readout it
            # compiled (`submittedDesign ... readout := .semiclassical`), which is what qualification is for. The
            # refusal stays byte-identical for every ordinary submission.
            refuse_unqualified_design(v["leanSource"])
    eng = engine.load()
    model = epoch.model_of()
    if v.get("schema") != VERIFICATION_SCHEMA:
        raise Refused("format", "not a policy verification")
    if v.get("login") != login:
        raise Refused("format", "the verification was made for another handle")
    carried = [k for k in ("summarySource", "summarySha256", "summaryBytes") if k in v]
    if carried:                                                 # all three or none, and the text is checked again here: the artifact is data, not a verdict
        text = v.get("summarySource")
        if len(carried) != 3 or not isinstance(text, str) or check_summary(text, v.get("leanSource")) is not None \
                or v["summarySha256"] != summary_tex.sha256(text.encode("utf-8")) or v["summaryBytes"] != len(text.encode("utf-8")):
            raise Refused("summary", "the verification's summary is inconsistent")
    if v.get("model") != model:
        raise ModelMismatch("the verification was made under another model than this checkout's")
    state_of = lambda: request_json(url.rstrip("/") + "/v2/state", token)
    state = state_of()
    policy_name(state, login)                                   # the reserved handle
    if state["model"] != model:
        raise ModelMismatch(f"live epoch {state['epoch']} was built for a different model than this checkout's; not publishing")

    policy_json = normalise(v["policy"])
    digest = policy_digest(policy_json)
    if digest != v.get("digest"):
        raise Refused("format", "the verification's policy does not match its digest")
    board = "ptp" if policy_json["mode"] == eng.tables.TRIPLE else "pp"
    # Never let a prior pass in the workdir vouch for a different verification.
    store = EvidenceStore(envelope=v.get("envelope"))
    mirror, why = store.stomirror(policy_json, model, board)
    if mirror is None:
        raise Refused("mirror", f"the mirror evidence does not hold for this policy: {why}")
    ev = workdir / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    with exclusive_attempt(ev / ".mirror.lock"):
        (ev / f"{digest[:16]}.policy.json").write_text(json.dumps(policy_json, indent=1, ensure_ascii=False))
        (ev / f"{digest[:16]}.evidence.json").write_text(json.dumps(v["envelope"], indent=1, ensure_ascii=False))
    P = eng.tables.Policy.from_json(policy_json)
    printed = v.get("printed")
    if measured:
        require_readout_epoch(model)                            # before the binding, for the same reason as in verify
    readout = bind_measured_readout(v, P, printed) if measured else None
    forshor = None
    if readout is not None:
        # A measured proof claims no coherent IR agreement: `forshor_record` prices the document's own IR against the
        # coherent single-run cost, and a measured export's IR is the readout round. `_build_request` refuses a request
        # that carries both.
        log("  verified the compiled readout and checked its reference arithmetic census")
    elif printed is not None and not str(printed.get("schema", "")).startswith("forshor.ptp-"):
        forshor = forshor_record(eng, P, printed, model["forshor"]["pin"])
    elif printed is not None:
        # threshold, k and the table itself, band by band (as `forshor_record` does for PhaseProduct): two tables of one k are told apart
        if [(b.threshold, b.table.k, b.table.digest) for b in policy_from_ptp_document(eng, printed).bands] != [(b.threshold, b.table.k, b.table.digest) for b in P.bands]:
            raise Refused("forshor", "the bands ForShor printed are not the submitted bands (the PhaseTripleProduct document in the verification, re-checked at publication)")
    head_sha = head_sha or hashlib.sha1(digest.encode()).hexdigest()
    run_id = run_id or str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    submitted_at = _now()
    pr_url = f"https://github.com/{REPO}/pull/{pull}"
    evaluation_key = hashlib.sha256(f"{head_sha}:{run_id}:{pull}".encode()).hexdigest()
    metadata = {"headSha": head_sha, "pullRequestUrl": pr_url, "githubLogin": login, "runId": run_id, "submittedAt": submitted_at, "evaluationKey": evaluation_key}

    # Production: the accepted ids and the archive commit are the `submission-results` branch's (`archive_view`, as publish_v2 reads it). A local
    # rehearsal has no such branch: the accepted ids are the publisher's own ledger plus this submission.
    archive = archive_commit or (archive_view()[1] if archive_view else _published_archive_commit(url, token))
    accepted_now = archive_view or (lambda: (sorted(set(state_of().get("submissionIds", [])) | {head_sha}), archive))

    def price(primitive_from: Optional[str] = None) -> Dict[str, Any]:
        # `primitive_from` is the earlier archived copy of this policy that this one is a variant of, as the archive
        # step reported it: the request must then carry only the boards the archive entry claims (`archive_policy`).
        # None is every ordinary submission, and `_build_request` then builds exactly what it always built.
        accepted = [head_sha, primitive_from] if primitive_from else [head_sha]
        if readout is not None:
            from export_v2.readout_overlay import VERIFIER_PIN
            # The measured pricer re-binds the evidence and re-converts the compiled selection itself, so the design
            # it prices is the one `compiled_full_run` gave the archive; a document that names none prices on the
            # readout-only pipeline, bit for bit.
            return request.build_measured_request(policy_json, compiled=printed, verifier_pin=VERIFIER_PIN,
                                                  lean_source=v["leanSource"].encode("utf-8"), model=model, store=store,
                                                  head_sha=head_sha, pull_number=pull, github_login=login, run_id=run_id,
                                                  submitted_at=submitted_at, evaluated_at=submitted_at, archive_commit=archive,
                                                  accepted_ids=accepted, number=0, evaluation_key=evaluation_key,
                                                  base_generation="", primitive_from=primitive_from, log=log)
        return request.build_request(policy_json, model=model, store=store, head_sha=head_sha, pull_number=pull, github_login=login, run_id=run_id,
                                     submitted_at=submitted_at, evaluated_at=submitted_at, archive_commit=archive, accepted_ids=accepted, number=0,
                                     evaluation_key=evaluation_key, base_generation="", forshor=forshor, primitive_from=primitive_from, log=log)

    sent: Dict[str, Any] = {}

    def send(payload: Dict[str, Any]) -> Dict[str, Any]:
        if organizer_baseline:
            for priced in payload["priced"]:
                priced["row"]["organizerBaseline"] = organizer_baseline
        sent["payload"] = payload                              # the last attempt is the one that was accepted
        return request_json(url.rstrip("/") + "/v2/publish", token, payload)

    archived = None
    if archive_write is not None:                               # the policy joins the archive first: the publisher wants its head among the accepted ids
        # The archive records WHICH RESULT was published, on every axis
        # `archive_policy.KIND_DEFAULT` admits, and this call is where those values are. Until now it passed none of
        # them, so a published MEASURED submission was archived as a LEGACY entry -- which `entry_kind` reads as the
        # coherent readout. Three consequences, in the one record that is kept forever: the wrong readout was
        # recorded; the design was not recorded at all (nothing else can record it -- the ledger row has no field for
        # one -- transcribed here as `SITE_ROW_FIELDS` in
        # `export_v2/pytests/test_design_price_plumbing.py`); and `plan_readout_variant` compared the variant against
        # the default it is not, so your own measured and coherent copies of one policy were a repeat rather than the
        # variant pair the planner was built for.
        #
        # The two values come from THIS function, already checked, rather than being re-derived from `v` at the CLI:
        # `measured` is the compiled document's own schema and `bind_measured_readout` has re-derived its evidence,
        # and `design` is what `compiled_full_run` converted the full-run selection to -- the same object `price()`
        # hands the publisher, so the archive and the board cannot disagree about which result was priced.
        #
        # `primitive` is None, deliberately rather than by omission: the standalone primitive route does not
        # publish and its refusal above fires before this line, so no primitive value exists HERE to pass. It is
        # named explicitly so the axis is visibly considered; `archive_policy`'s module docstring states the rule.
        #
        # A REFUSAL WRITES NOTHING. The request is priced HERE, before the entry is written, so every
        # refusal pricing can raise -- e.g. `build_measured_request`'s unconditional `check_no_full_run_export` for a
        # receipt whose document names a selection its own source does not -- is raised with the archive untouched.
        # The recovery loop below then reuses this request instead of pricing again. Only the order moves: the request
        # carries `archive` (the commit read above) and `[head_sha]`, not the archive write, and every attempt
        # still replaces both from `accepted_now` before sending.
        price_with = price
        template = price_with()
        price = lambda: template                                # noqa: E731
        archived = archive_write(v, readout="semiclassical" if measured else "coherent", primitive=None, design=design)
        # The archive entry says which boards this submission occupies: your own earlier copy priced the other way keeps
        # the boards both results price alike, so this one is a variant of it and enters the full board alone. The
        # request is priced again with that earlier entry named (`primitiveFrom`) so it carries the same boards; only a
        # variant pays for the second pricing, and an ordinary submission keeps the request built above, byte for byte.
        if archived.get("primitiveFrom"):
            template = price_with(archived["primitiveFrom"])
        if archived.get("boards") is not None and sorted(archived["boards"]) != sorted(template["submission"]["boards"]):
            raise Incomplete("publisher", f"the archive entry occupies the boards {sorted(archived['boards'])} but the request prices {sorted(template['submission']['boards'])}")
        log(f"archived {archived['path']} on submission-results ({archived['commit'][:12]})" if archived["added"] else f"already archived: {archived['path']}")
        for repeat in archived.get("duplicates") or []:
            kind = "same_participant" if repeat["sameParticipant"] else "other_participant"   # a repeat the rules allow (archive_policy.DUPLICATE_RULES): identified here, published like any other
            log(f"duplicate-policy: same policy as {repeat['policyId'][:12]} by {repeat['login']} (PR #{repeat['pull']}, {str(repeat['createdAt'])[:10]}); "
                f"published anyway (rule: {kind}={archive_policy_rules()[kind]})")
    result = publish_v2_with_recovery(metadata, model, state_of, price, accepted_now, send)
    if archived is not None:
        result["archive"] = archived
    result["policy"] = {"digest": digest, "headSha": head_sha, "login": login, "pull": pull, "mode": policy_json["mode"],
                        "stoMirror": True, "forshorPolicy": bool(forshor)}
    # The publication record the trusted publish job uploads as `publication-result.json` (`./forshor status` and `watch` read it): which snapshot, which row, which boards.
    priced = (sent.get("payload") or {}).get("priced") or []
    result.update({"schema": PUBLICATION_SCHEMA, "kind": "policy", "name": (priced[0]["row"]["name"] if priced else None),
                   "boards": sorted({p["board"] for p in priced})})
    return result


def run_intake(policy_doc: Any, *, lean_policy: Optional[str] = None, login: str, pull: int, url: str, token: str, tree: Path, workdir: Path,
               head_sha: Optional[str] = None, run_id: Optional[str] = None, forshor_tree: Optional[Path] = None,
               stage_timeout: Optional[int] = None, mirror_timeout: int = 7200, archive_commit: Optional[str] = None,
               archive_view: Optional[Callable[[], Any]] = None, summary: Optional[str] = None,
               run_mirror_fn: Optional[Callable[..., Dict[str, Any]]] = None, python: str = sys.executable,
               qualify_readout: bool = False,
               log: Callable[[str], None] = lambda s: print(s, flush=True)) -> Dict[str, Any]:
    """The pipeline above, verify then publish in one call. Returns the publisher's result. Raises `Refused`, `ReservedHandle`, `ModelMismatch`.

    This is the ONE route that may publish a qualified measured readout: the verification `publish_verification` rules
    on is the one this call's own trusted verifier just produced, not a receipt handed in from elsewhere."""
    v = verify_submission(policy_doc, lean_policy=lean_policy, summary=summary, login=login, url=url, token=token, tree=tree, workdir=workdir, forshor_tree=forshor_tree,
                          stage_timeout=stage_timeout, mirror_timeout=mirror_timeout, run_mirror_fn=run_mirror_fn, python=python, log=log,
                          qualify_readout=qualify_readout, publishing=True)
    return publish_verification(v, login=login, pull=pull, url=url, token=token, workdir=workdir, head_sha=head_sha, run_id=run_id,
                                archive_commit=archive_commit, archive_view=archive_view, qualified_in_run=qualify_readout, log=log)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--policy", type=Path, help="a policy as site policy JSON (the pack's format)")
    ap.add_argument("--lean-policy", type=Path, help="a submitted Policy.lean (ForShor's PolicyTemplate format); needs --forshor-tree. Exactly one of --policy, --lean-policy and --publish-from")
    ap.add_argument("--summary", type=Path, help="the optional summary.tex that goes with --lean-policy: checked against the shared rules (evaluation/summary_tex.py), carried "
                    "in the verification and archived verbatim. It is never compiled or run")
    ap.add_argument("--no-publish", action="store_true",
                    help="verify only (the reserved handle, ForShor's gate and build, the STO mirror, ForShor's check) and write a verification file; nothing is priced, nothing is "
                         "sent, no publisher or token is needed. This is `./forshor check`, and the untrusted half of CI")
    ap.add_argument("--verification-out", type=Path, help="with --no-publish: where to write the verification (default: WORKDIR/verification.json)")
    ap.add_argument("--qualify-readout", action="store_true", help="compile PhaseProduct readout at the operational verifier pin. With --no-publish this is local only and takes no "
                    "publisher URL; with a publisher URL and --pull it verifies and publishes in one trusted run (the only route that may publish a measured readout)")
    ap.add_argument("--qualify-primitive", action="store_true", help="LOCAL ONLY, and it never publishes: compile and price a standalone synthesis primitive (catalyticB2, adaptiveB3) on the PhaseProduct or PhaseTripleProduct board, at the primitive rule's own pinned verifier. Requires --no-publish and --lean-policy, and takes no publisher URL; the result gets no score and the publisher refuses it")
    ap.add_argument("--organizer-baseline", choices=["baseline-gpt", "baseline-claude", "baseline-gpt-6.2-sol", "baseline-claude-opus-5-5", "baseline-gpt-6.2-sol-medium", "baseline-claude-opus-5-5-medium"], help="Display identity assigned by trusted publication CI")
    ap.add_argument("--publish-from", type=Path, help="price and publish a verification written by --no-publish (the trusted half of CI): no Lean runs, and everything in it is checked again")
    ap.add_argument("--trusted-origin", action="store_true", help="with --publish-from, in the trusted publish job only: establish where the receipt came from by re-deriving this "
                    "checkout's verification identity and re-fetching the artifact the receipt's ciVerification stamp names (needs GH_TOKEN, and only when a receipt actually "
                    "carries a qualified readout). Without it a qualified readout receipt is refused, as it always was")
    ap.add_argument("--login", required=True, help="the submitter's GitHub handle (the PR author)")
    ap.add_argument("--pull", type=int, help="the pull request number (not needed with --no-publish)")
    ap.add_argument("--head-sha", help="the PR head commit (40 hex); default: derived from the policy")
    ap.add_argument("--run-id")
    ap.add_argument("--tree", type=Path, help="the built Lean tree for the STO mirror, at the epoch's pins (not needed with --publish-from)")
    ap.add_argument("--forshor-tree", type=Path, help="a built ForShor checkout (copied per run); omit to skip the ForShor check of a JSON policy; required for a Lean policy")
    ap.add_argument("--publisher-url", default=os.environ.get("PUBLISHER_URL"), help="with --no-publish: optional, checks the handle and the epoch against the live publisher first")
    ap.add_argument("--workdir", type=Path, default=Path("intake-work"))
    ap.add_argument("--archive", type=Path, help="a checkout of the `submission-results` branch (as publish_v2.py reads it; needs GH_TOKEN); omit for a local rehearsal. With --publish-from "
                    "the policy is first archived there (Policy.lean, policy.json, verification.json, index entry; pushed to the branch) and then published")
    ap.add_argument("--archive-commit", help="the submission-results commit to record (default: the live snapshot's, read from the published dashboard)")
    ap.add_argument("--stage-timeout", type=int)
    ap.add_argument("--mirror-timeout", type=int, default=7200)
    ap.add_argument("--out", type=Path, help="write the result JSON here")
    a = ap.parse_args(argv)
    modes = [a.policy is not None, a.lean_policy is not None, a.publish_from is not None]
    if sum(modes) != 1:
        ap.error("give exactly one of --policy, --lean-policy and --publish-from")
    if a.summary is not None and a.lean_policy is None:
        ap.error("--summary goes with --lean-policy (the publish job takes the summary from the verification and the pull request's head commit)")
    if a.qualify_readout:
        if a.lean_policy is None:
            ap.error("--qualify-readout requires --lean-policy")
        if a.no_publish and a.publisher_url:
            ap.error("--qualify-readout with --no-publish is local only: it takes no publisher URL")
    if a.qualify_primitive:
        # Unlike --qualify-readout there is no single-trusted-run publishing arm: the route does not publish, so the
        # flag combination that would publish is refused by the parser rather than later by the publisher.
        if a.qualify_readout:
            ap.error("--qualify-primitive and --qualify-readout are separate qualification routes and do not combine")
        if a.lean_policy is None:
            ap.error("--qualify-primitive requires --lean-policy")
        if not a.no_publish:
            ap.error("--qualify-primitive never publishes: it requires --no-publish")
        if a.publisher_url:
            ap.error("--qualify-primitive is local only: it takes no publisher URL")
    if a.trusted_origin and a.publish_from is None:
        ap.error("--trusted-origin goes with --publish-from: it establishes where a receipt this process did not produce came from")
    if a.publish_from is not None:
        if a.no_publish:
            ap.error("--publish-from publishes: it cannot be combined with --no-publish")
        if not a.publisher_url or a.pull is None:
            ap.error("--publish-from needs --publisher-url (or PUBLISHER_URL) and --pull")
    else:
        if a.tree is None:
            ap.error("--tree is required to verify")
        if not a.no_publish and (not a.publisher_url or a.pull is None):
            ap.error("publishing needs --publisher-url (or PUBLISHER_URL) and --pull; use --no-publish to verify only")
    verification_path = a.verification_out or (a.workdir / "verification.json")
    outputs = {p.resolve() for p in (verification_path if a.no_publish else None, a.out) if p is not None}
    try:
        with ExitStack() as locks:
            for path in sorted(outputs):
                path.parent.mkdir(parents=True, exist_ok=True)
                locks.enter_context(exclusive_attempt(path.with_name(f".{path.name}.lock")))
            return _run_args(a, ap, verification_path)
    except (AttemptBusy, OSError) as e:
        # The other attempt owns the receipt/result. Do not overwrite either one.
        print(json.dumps({"failed": True, "error": str(e)}))
        return 2


def _run_args(a, ap, verification_path):
    if a.no_publish:
        try:
            prepare_receipt_path(verification_path, a.lean_policy, a.policy, a.summary, a.out)
        except (OSError, ValueError) as error:
            ap.error(str(error))
    summary_text = None
    if a.summary is not None:
        data, why = summary_tex.inspect_file(a.summary)
        if why or data is None:
            result = {"refused": True, "stage": "summary", "reason": why or "summary.tex is missing"}
            text = json.dumps(result, indent=1)
            print(text)
            if a.out:
                a.out.write_text(text + "\n")
            return 1
        summary_text = data.decode("utf-8")               # bytes in, bytes out: no newline translation, so the hash is the file's
    token = os.environ.get("PUBLISHER_TOKEN", "local")
    archive_view = (lambda: __import__("publish_v2")._archive_view(a.archive)) if a.archive else None
    archive_write = None
    if a.archive and a.publish_from is not None:
        def archive_write(v, *, readout="coherent", primitive=None, design=None):
            # The result kind is the CALLER's: `publish_verification` is the only thing that has checked the compiled
            # document, so it says which result is being archived and this closure only carries it through. The
            # defaults are `archive_policy`'s own (`KIND_DEFAULT`), so a caller that names no axis still writes the
            # legacy coherent entry byte for byte.
            import archive_policy as ap_
            if a.head_sha is None or a.run_id is None:
                raise ap_.ArchiveRefused("archiving needs --head-sha and --run-id")
            token_gh = os.environ["GH_TOKEN"]
            source = ap_.github_source(a.head_sha, token_gh, __import__("publish").request_json)
            summary = ap_.github_summary(a.head_sha, token_gh, __import__("publish").request_json)   # None when the pull request has none
            state = request_json(a.publisher_url.rstrip("/") + "/v2/state", token)
            __import__("publish_v2").reject_retired(state, a.head_sha)
            published = set(state.get("submissionIds", []))   # the heads the publisher has actually published
            return ap_.archive_policy(a.archive, v, login=a.login, pull=a.pull, head_sha=a.head_sha, run_id=a.run_id, source=source, summary=summary, token=token_gh,
                                      published_ids=published, readout=readout, primitive=primitive, design=design)
    try:
        if a.publish_from is not None:
            # The trusted workflow supplies the origin; the receipt never does. `trusted_origin` reads GH_TOKEN only
            # when a fetch actually happens, so an ordinary coherent publication -- which never reaches the origin
            # check -- is unaffected whether the flag is given or not.
            origin = None
            if a.trusted_origin:
                from export_v2 import receipt_origin
                origin = receipt_origin.trusted_origin()
            result = publish_verification(json.loads(a.publish_from.read_text()), login=a.login, pull=a.pull, url=a.publisher_url, token=token, workdir=a.workdir,
                                          head_sha=a.head_sha, run_id=a.run_id, archive_commit=a.archive_commit, archive_view=archive_view,
                                          archive_write=archive_write, organizer_baseline=a.organizer_baseline, origin=origin)
        elif a.no_publish:
            source = a.lean_policy.read_bytes().decode("utf-8") if a.lean_policy else None
            v = verify_submission(json.loads(a.policy.read_text()) if a.policy else None, lean_policy=source, summary=summary_text, login=a.login,
                                  url=a.publisher_url, token=token, tree=a.tree, workdir=a.workdir, forshor_tree=a.forshor_tree, stage_timeout=a.stage_timeout,
                                  mirror_timeout=a.mirror_timeout, **({"qualify_readout": True} if a.qualify_readout else {}),
                                  **({"qualify_primitive": True} if a.qualify_primitive else {}))
            write_receipt(verification_path, v)
            result = {"verified": True, "verification": str(verification_path), "policy": {"digest": v["digest"], "mode": v["mode"], "bands": len(v["policy"]["bands"]),
                      "stoMirror": True, "forshorPolicy": bool(v["forshor"]), "forshorChecked": v["printed"] is not None}}
        else:
            result = run_intake(json.loads(a.policy.read_text()) if a.policy else None, lean_policy=a.lean_policy.read_bytes().decode("utf-8") if a.lean_policy else None, summary=summary_text, login=a.login,
                                pull=a.pull, url=a.publisher_url, token=token, tree=a.tree, workdir=a.workdir, head_sha=a.head_sha, run_id=a.run_id, forshor_tree=a.forshor_tree,
                                stage_timeout=a.stage_timeout, mirror_timeout=a.mirror_timeout, archive_commit=a.archive_commit, archive_view=archive_view,
                                **({"qualify_readout": True} if a.qualify_readout else {}))
        code = 0
    except Incomplete as e:
        result, code = {"failed": True, "stage": e.stage, "error": str(e)[:600]}, 2
    except (Refused, ReservedHandle, ModelMismatch) as e:
        result, code = {"refused": True, "stage": getattr(e, "stage", type(e).__name__), "reason": getattr(e, "reason", str(e))}, 1
    except __import__("archive_policy").DuplicatePolicy as e:
        result, code = {"refused": True, "stage": "duplicate", "reason": str(e), "duplicateOf": e.duplicate_of}, 1
    except __import__("archive_policy").ArchiveRefused as e:
        result, code = {"refused": True, "stage": "archive", "reason": str(e)}, 1
    except (RetryPublication, RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
        result, code = {"failed": True, "error": f"{type(e).__name__}: {e}"[:600]}, 2
    text = json.dumps(result, indent=1)
    print(text)
    if a.out:
        a.out.write_text(text + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
