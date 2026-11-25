"""The publication gate on qualified readout and compiled full-run selections: what it now accepts, and what it still refuses.

Owner decision 2026-11-24: the blanket refusal in `publish_verification` ("qualified readout receipts are local only")
was a conservative default while readout qualification was pending. Qualification exists, so a document THIS RUN's
trusted verifier produced is priced on the measured path. The refusals that make the lift safe are pinned below, each
with the behaviour that must go red if it is removed.

REAL PATH vs SEAM. Every readout test here drives the real `bind_compiled_readout` against the recorded compiled export
(`data/readout_submitted_931bbf1.json.gz`) and, where marked slow, the real measured pricer. The full-run tests inject a
double for `export_v2.full_run_design` (the converter): they exercise this
file's ROUTING -- accept and forward the design, or refuse by name and price nothing -- not the conversion table.
"""
from copy import deepcopy
import gzip
from hashlib import sha256
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
import intake_v2  # noqa: E402
import export_v2  # noqa: E402
from export_v2 import canon, engine, epoch, evidence, mapping, readout_overlay as overlay, request  # noqa: E402

DATA = Path(__file__).parent / "data"
ARCHIVE = EVAL / "export_v2" / "evidence-archive" / "494185cf666f9752"
COHERENT_DIGEST = "6f036362302e1ecd"
FROZEN_CLOCK = "2026-11-24T00:00:00Z"

# A test double. An authenticated runner obtains real STO evidence; `test_intake_v2.py` replays a real envelope.
MIRROR = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
          "claim": "policy-algebra-and-exact-widths"}


@pytest.fixture(scope="module")
def measured():
    compiled = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
    return intake_v2.policy_from_document(engine.load(), compiled["document"]), compiled["document"], compiled["source"]


@pytest.fixture
def publisher(monkeypatch):
    """The live publisher, the dashboard and `/v2/publish`, with every sent payload recorded."""
    model = epoch.model_of()
    sent = []
    state = {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": 1},
             "completedEvaluations": {}, "submissionIds": ["f" * 40],
             "publication": {"updatedAt": FROZEN_CLOCK, "snapshotId": "s" * 64}}

    def request_json(url, token, payload=None):
        if url.endswith("/v2/state"):
            return state
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        sent.append(payload)
        return {"snapshotId": "n" * 64, "published": True}

    monkeypatch.setattr(intake_v2, "request_json", request_json)
    monkeypatch.setattr(intake_v2, "_now", lambda: FROZEN_CLOCK)
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda self, *a: (MIRROR, None))
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (MIRROR, None))
    return SimpleNamespace(sent=sent, model=model)


def qualify(measured, publisher, monkeypatch, tmp_path, document=None):
    """The receipt this run's trusted verifier produces for the recorded compiled export (`--qualify-readout`)."""
    _, compiled, source = measured
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: document or compiled)
    return intake_v2.verify_submission(None, lean_policy=source, login="tester", tree=tmp_path,
                                       workdir=tmp_path / "verify", forshor_tree=tmp_path,
                                       run_mirror_fn=lambda *a, **kw: {"record": {"verdict": "accepted"}},
                                       qualify_readout=True, log=lambda s: None)


def publish(v, tmp_path, name="p", **kw):
    return intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t",
                                          workdir=tmp_path / name, head_sha="a" * 40, run_id="1",
                                          log=lambda s: None, **kw)


# ---------------------------------------------------------------- what is now ACCEPTED

@pytest.mark.slow
def test_a_qualified_semiclassical_document_is_accepted_and_priced_on_the_measured_path(measured, publisher, monkeypatch, tmp_path):
    """REAL PATH: the recorded compiled export, the real readout binding and the real measured pricer."""
    policy, compiled, source = measured
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    assert v["localReadoutQualification"]["mode"] == "semiclassical"
    result = publish(v, tmp_path, qualified_in_run=True)
    assert result["published"] and result["boards"] == ["full", "pp"]
    payload = publisher.sent[-1]
    readout = payload["submission"]["verification"]["readout"]
    assert readout == overlay.bind_compiled_readout(policy, compiled, verifier_pin=overlay.VERIFIER_PIN,
                                                    lean_source=source.encode())
    assert readout["verifierPin"] == overlay.VERIFIER_PIN and readout["sourceSha256"] == v["leanSha256"]
    assert payload["submission"]["verification"]["forshorPolicy"] is None, "a measured proof claims no coherent IR agreement"
    assert len(payload["priced"]) == 8 and {p["board"] for p in payload["priced"]} == {"pp", "full"}
    assert {p["delta"] for p in payload["priced"]} == set(mapping.DELTA_KEYS), "no delta the boards do not publish"
    for item in payload["priced"]:
        if item["board"] == "full":                       # the measured circuit is what is ranked, not the coherent one
            run = item["panel"]["fullRun"]
            assert item["row"]["T"] == run["readout"]["T"] and run["T"] != item["row"]["T"]


def test_the_measured_route_replaces_the_coherent_checker_and_the_coherent_pricer(measured, publisher, monkeypatch, tmp_path):
    """The routing itself: `require_coherent_export` is for coherent documents, `build_measured_request` prices this one."""
    seen = {}
    monkeypatch.setattr(request, "build_request", lambda *a, **kw: pytest.fail("a measured document is not priced coherently"))
    monkeypatch.setattr(request, "build_measured_request", lambda policy_json, **kw: seen.update(kw) or _payload())
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    publish(v, tmp_path, qualified_in_run=True)
    assert seen["compiled"] is v["printed"] and seen["verifier_pin"] == overlay.VERIFIER_PIN
    assert seen["lean_source"] == v["leanSource"].encode() and seen["model"] == publisher.model
    assert "design" not in seen, "a document that names no full-run selection is priced on the reference pipeline"
    assert seen.get("forshor") is None


def _payload():
    row = {"name": "tester_sub0", "T": 1, "qubits": 1}
    return {"schema": "forshor.publish/v2", "evaluationKey": "d" * 64, "baseGeneration": "1", "model": epoch.model_of(),
            "evaluation": {"headSha": "a" * 40}, "archive": {"policyId": "a" * 40},
            "submission": {"id": "a" * 40, "boards": ["pp", "full"], "verification": {}},
            "priced": [{"board": "pp", "delta": "1e-2", "row": row, "panel": {}}]}


# ---------------------------------------------------------------- a measured receipt's file declares the readout

def test_a_measured_receipt_whose_file_does_not_declare_the_semiclassical_readout_is_refused(measured, publisher, monkeypatch, tmp_path):
    """The coherent branch refuses a file that declares the semiclassical readout (`refuse_unqualified_design`); the
    measured branch asked nothing of the file's readout, so a measured export beside a file that declares none was
    priced and archived. Red if `require_semiclassical_declared` stops being called on the measured branch."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    declared = [n for n, line in enumerate(v["leanSource"].splitlines()) if line.startswith("def submittedDesign ")]
    assert declared, "the recorded source declares the readout"
    kept, dropping = [], False
    for line in v["leanSource"].splitlines():
        dropping = line.startswith("def submittedDesign ") or (dropping and line[:1] in (" ", "\t") and line.strip() != "")
        if not dropping:
            kept.append(line)
    edited = json.loads(json.dumps(v))
    edited["leanSource"] = "\n".join(kept) + "\n"
    edited["leanSha256"] = sha256(edited["leanSource"].encode()).hexdigest()
    # keep the receipt self-consistent, so the source-to-evidence binding (which already refuses an edited file) is not what answers
    edited["localReadoutQualification"]["evidence"]["sourceSha256"] = edited["leanSha256"]
    with pytest.raises(intake_v2.Refused, match="does not declare the semiclassical readout"):
        publish(edited, tmp_path, qualified_in_run=True)
    assert publisher.sent == []


def test_a_measured_receipt_whose_file_declares_the_readout_is_not_refused_for_it(measured):
    _, _, source = measured
    intake_v2.require_semiclassical_declared(source)


# ---------------------------------------------------------------- the retained refusals

def test_a_participant_supplied_qualification_is_still_refused(measured, publisher, monkeypatch, tmp_path):
    """RETAINED. Hosted intake takes its inputs from the trusted GitHub run and the publisher, never from a receipt the
    submitter supplies. Red if `qualified_in_run` stops guarding the refusal."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    receipt = json.loads(json.dumps(v))                   # through a file, as an artifact travels
    for n, record in enumerate((receipt, {k: value for k, value in receipt.items() if k != "localReadoutQualification"})):
        with pytest.raises(intake_v2.Refused, match="published only from the trusted verification run"):
            publish(record, tmp_path, name=f"receipt{n}")
    assert publisher.sent == []


def test_measured_evidence_bound_to_another_source_or_verifier_is_refused(measured, publisher, monkeypatch, tmp_path):
    """RETAINED. `bind_compiled_readout` stays on the path: the receipt's own claim is never the proof. Red if the
    re-binding or the comparison with the qualification's evidence is removed."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("unbound evidence must not be priced"))
    other_source = v["leanSource"] + "\n-- a different submission\n"
    changes = [({"leanSource": other_source, "leanSha256": sha256(other_source.encode()).hexdigest()}, "does not bind"),
               ({"localReadoutQualification": {"mode": "semiclassical",
                 "evidence": {**v["localReadoutQualification"]["evidence"], "verifierPin": "0" * 40}}}, "does not bind"),
               ({"localReadoutQualification": {"mode": "semiclassical",
                 "evidence": {**v["localReadoutQualification"]["evidence"], "sourceSha256": "0" * 64}}}, "does not bind"),
               ({"printed": {**v["printed"], "readout": {**v["printed"]["readout"], "control": "flag"}}}, "loop schedule"),
               ({"localReadoutQualification": {"mode": "coherent", "evidence": None}}, "missing or mismatched compiled readout")]
    for n, (change, reason) in enumerate(changes):
        with pytest.raises(intake_v2.Refused, match=reason):
            publish({**deepcopy(v), **change}, tmp_path, name=f"bound{n}", qualified_in_run=True)
    assert publisher.sent == []


def test_a_qualified_run_still_refuses_a_source_that_disagrees_with_its_own_digest(measured, publisher, monkeypatch, tmp_path):
    """RETAINED (unchanged by the lift): the receipt's two source fields must agree before any gate's verdict is trusted."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    with pytest.raises(intake_v2.Refused, match="does not match its own digest"):
        publish({**v, "leanSha256": "0" * 64}, tmp_path, qualified_in_run=True)
    assert publisher.sent == []


def test_the_measured_route_prices_no_delta_the_boards_do_not_publish(measured):
    """RETAINED. delta = 1e-2 is the design's only delta, and the overlay prices none the published mapping omits."""
    policy, compiled, _ = measured
    assert mapping.DELTA_VALUES["1e-2"] == 0.01
    coherent = engine.load().fullalg.price_full(policy.with_widths("exact"), 2048, 0.01, detail=True)
    overlay.project(policy.with_widths("exact"), coherent)                      # the published delta binds
    with pytest.raises(ValueError, match="exact reference price"):
        overlay.project(policy.with_widths("exact"), {**coherent, "delta": 2e-2})


@pytest.mark.parametrize("route", ["verify", "publish"])
def test_an_epoch_mismatch_is_an_operator_stop_not_a_refusal_of_the_policy(route, measured, publisher, monkeypatch, tmp_path):
    """RETAINED, and newly ATTRIBUTED. The readout rule is qualified for one epoch; a checkout at
    another cannot rule on the submission at all. That is `Incomplete` at stage `epoch` -- which
    `scripts/ci/notify_submitters.js` LIMIT_STAGES already reports as incomplete/limits -- and never `Refused`, which
    would tell the participant their policy is what is wrong. Red if the check moves back behind
    `bind_compiled_readout`, whose own epoch `ValueError` surfaces as `Refused("forshor", ...)`."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a stopped run prices nothing"))
    v = None
    if route == "publish":                      # qualify under the real epoch, then stop the publish half
        v = qualify(measured, publisher, monkeypatch, tmp_path)
        assert v["localReadoutQualification"]["mode"] == "semiclassical"
    monkeypatch.setattr(overlay, "EPOCH", "0000000000000000")
    with pytest.raises(intake_v2.Incomplete) as stop:
        if route == "verify":
            qualify(measured, publisher, monkeypatch, tmp_path)
        else:
            publish(v, tmp_path, qualified_in_run=True)
    assert stop.value.stage == "epoch" and "not examined" in str(stop.value)
    assert "not a verdict on the policy" in str(stop.value)
    assert publisher.sent == []


# ---------------------------------------------------------------- full-run selections (SEAM: the converter)

QFT_ONLY = {"quotient-exact": {"data": {"backend": "ross_selinger", "target": "exact"},
                               "work": {"backend": "ross_selinger", "target": "exact"}},
            "quotient-catalytic": {"data": {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"},
                                   "work": {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}},
            "quotient-banded-data-catalytic-work": {"data": {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"},
                                                    "work": {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}}}
BEYOND_QFT = {"quotient-fallback-data-catalytic-work": "a fallback rotation backend on the data QFT",
              "quotient-step2-fallback": "a Step 2 phase-product backend",
              "split-step4-kicks": "the splitCompare multiplier with a Step 4 phase-product backend",
              "split-step2-fallback-step4-kicks": "the splitCompare multiplier with a Step 2 phase-product backend"}
SELECTION_SCHEMA = "forshor.full-run-selection/v1"


@pytest.fixture
def converter(monkeypatch):
    """SEAM. A stand-in for `export_v2.full_run_design`, faithful to its contract:
    `selection_of` reads the one top-level key, `design_of` converts the three QFT-only profiles and refuses the four
    that select a backend the engine's design cannot express -- BY NAME."""
    module = ModuleType("export_v2.full_run_design")

    def selection_of(document):
        if intake_v2.FULL_RUN_KEY not in document:
            return None
        selection = document[intake_v2.FULL_RUN_KEY]
        if selection is None:
            raise ValueError(f"compiled {intake_v2.FULL_RUN_KEY} is present but names no selection: "
                             "an explicit null is not the default")
        return selection

    def design_of(selection):
        if selection is None:
            return None
        if not isinstance(selection, dict) or selection.get("schema") != SELECTION_SCHEMA:
            raise ValueError(f"full-run selection does not carry schema {SELECTION_SCHEMA}")
        profile = selection.get("profile")
        if profile == "reference":
            return None
        if profile in QFT_ONLY:
            return deepcopy(QFT_ONLY[profile])
        if profile in BEYOND_QFT:
            raise ValueError(f"full-run selection {profile!r} cannot be priced here: it selects {BEYOND_QFT[profile]}, "
                             "which the engine's QFT design (data, work, scratch, exponent) cannot express. "
                             "Unsupported scopes are reported, not priced as defaults.")
        raise ValueError(f"unrecognised full-run selection {profile!r}")

    module.selection_of, module.design_of = selection_of, design_of
    module.DESIGN_KEY, module.QFT_KEY = "design", "qft"          # the wire keys `readout_qft_overlay` reads
    monkeypatch.setitem(sys.modules, "export_v2.full_run_design", module)
    monkeypatch.setattr(export_v2, "full_run_design", module, raising=False)
    return module


def with_profile(v, profile):
    """The receipt as it is for a full-run submission: the profile is semiclassical by force (`PolicyMain.lean`),
    so it carries the measured schema and its readout evidence re-binds over the document that names the selection."""
    printed = {**deepcopy(v["printed"]), intake_v2.FULL_RUN_KEY: {"schema": SELECTION_SCHEMA, "profile": profile}}
    evidence_ = overlay.bind_compiled_readout(engine.load().tables.Policy.from_json(v["policy"]), printed,
                                              verifier_pin=overlay.VERIFIER_PIN, lean_source=v["leanSource"].encode())
    return {**deepcopy(v), "printed": printed,
            "localReadoutQualification": {"mode": "semiclassical", "evidence": evidence_}}


def bound(monkeypatch):
    """SEAM: the profile receipts below carry a selection the recorded source does not declare (no source can: the
    source gate refuses a named profile by name). The source binding is pinned on its own in
    `test_design_body_fixture.py`; standing it aside here isolates the ROUTING of the converter's verdict."""
    monkeypatch.setattr(intake_v2, "check_full_run_binding", lambda source, document: True)


@pytest.mark.parametrize("profile", sorted(QFT_ONLY))
def test_a_convertible_full_run_profile_is_accepted_and_carries_its_design(profile, measured, publisher, converter, monkeypatch, tmp_path):
    """SEAM (routing only): accepted, and the converted design reaches the archive and the measured pricer.

    The measured pricer re-converts the compiled selection itself (and prices it together
    with the readout), so `publish_verification` no longer hands it a `design` keyword. What reaches the pricer is the
    compiled document carrying the selection; what reaches the archive is the converted design. Production change that
    would fail this: dropping the selection on the way to the pricer, or archiving a design other than the converted one."""
    seen, archived = {}, {}
    bound(monkeypatch)
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    monkeypatch.setattr(request, "build_measured_request", lambda policy_json, **kw: seen.update(kw) or _payload())
    write = lambda v, **kind: archived.update(kind) or {"path": "p", "commit": "c" * 40, "added": True}
    assert publish(with_profile(v, profile), tmp_path, qualified_in_run=True, archive_write=write)["published"]
    assert seen["compiled"][intake_v2.FULL_RUN_KEY] == {"schema": SELECTION_SCHEMA, "profile": profile}
    assert "design" not in seen
    assert archived == {"readout": "semiclassical", "primitive": None, "design": QFT_ONLY[profile]}


@pytest.mark.parametrize("profile", sorted(BEYOND_QFT))
def test_an_unconvertible_full_run_profile_is_refused_by_name_and_prices_nothing(profile, measured, publisher, converter, monkeypatch, tmp_path):
    """SEAM (routing only). RETAINED: a selection this pipeline cannot convert is reported, never priced as the default.
    Red if the converter's refusal stops being raised at intake."""
    bound(monkeypatch)
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("an unconvertible profile prices nothing"))
    monkeypatch.setattr(request, "build_request", lambda *a, **kw: pytest.fail("an unconvertible profile is not priced as the default"))
    with pytest.raises(intake_v2.Refused, match=profile):
        publish(with_profile(v, profile), tmp_path, qualified_in_run=True)
    assert publisher.sent == []


@pytest.mark.parametrize("selection", [None, "malformed", {}, {"schema": "forshor.full-run-selection/v2", "profile": "quotient-exact"},
                                       {"schema": SELECTION_SCHEMA, "profile": "invented"}])
def test_a_malformed_full_run_selection_is_refused(selection, measured, publisher, converter, monkeypatch, tmp_path):
    """SEAM (routing only). RETAINED: an explicit null, a foreign schema and an unknown profile are all refused, never
    read as "no selection" and priced as the default."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a malformed selection prices nothing"))
    monkeypatch.setattr(request, "build_request", lambda *a, **kw: pytest.fail("a malformed selection prices nothing"))
    printed = {**deepcopy(v["printed"]), intake_v2.FULL_RUN_KEY: selection}
    with pytest.raises(intake_v2.Refused):
        publish({**deepcopy(v), "printed": printed}, tmp_path, qualified_in_run=True)
    assert publisher.sent == []


def test_without_the_converter_a_full_run_key_keeps_its_standing_refusal(measured, publisher, monkeypatch, tmp_path):
    """RETAINED, and the seam's own safety: a compiled full-run key is never priced as the reference pipeline --
    with the converter absent OR present. Both branches of `compiled_full_run` are pinned here.

    The refusal appends the CAUSE after a colon, and documents the
    leading sentence (`policy_options.FULL_RUN_ROUTE`) as the anchor the rest of the repository matches as a PREFIX.
    This assertion was byte-equality against a base that had no cause clause; it now matches the anchor the same way
    `starter/test_pack_validation.py` does.

    The two branches are pinned separately and both refuse, for different reasons:

      * CONVERTER ABSENT (`ImportError` branch, `intake_v2.py:247-249`) -- still reachable, still shipped, so still
        tested, by hiding the module. The selection falls through to `check_no_full_run_export` and is refused with
        the anchor-plus-cause message.
      * CONVERTER PRESENT (this tree) -- the converter rules on the selection FIRST, at `intake_v2.py:605`, and
        refuses `quotient-exact` BY NAME on the topology gate: every one of the seven non-reference profiles
        selects a non-reference multiplier topology (`quotient_stream` or `split_compare`) that `qft_overlay`
        cannot honour, because it substitutes QFT sites into the engine's frozen REFERENCE census. The refusal is
        now MORE specific, not less: it names the topology instead of only the profile.

    Either way nothing is priced -- `build_measured_request` fails the test if reached, and `publisher.sent` stays
    empty. What ships for a design is the EXPLICIT per-family design on the reference topology, never a profile
    label, and the source gate admits a full-run declaration only in its one form beside the readout."""
    v = qualify(measured, publisher, monkeypatch, tmp_path)
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("refused, so nothing is priced"))
    printed = {**deepcopy(v["printed"]), intake_v2.FULL_RUN_KEY: {"schema": SELECTION_SCHEMA, "profile": "quotient-exact"}}

    # --- the converter-absent branch: hiding the module makes `from export_v2 import full_run_design` raise.
    # Both the package ATTRIBUTE and the `sys.modules` entry must go: `from export_v2 import full_run_design`
    # resolves the attribute first and only falls back to importing the submodule, where `None` forces ImportError.
    with monkeypatch.context() as hidden:
        hidden.delattr(export_v2, "full_run_design", raising=False)
        hidden.setitem(sys.modules, "export_v2.full_run_design", None)
        assert intake_v2.compiled_full_run({intake_v2.FULL_RUN_KEY: {"profile": "quotient-exact"}}) == (None, None)
        with pytest.raises(intake_v2.Refused) as absent:
            publish({**deepcopy(v), "printed": printed}, tmp_path, qualified_in_run=True)
    from policy_options import FULL_RUN_ROUTE
    assert FULL_RUN_ROUTE == "compiled submitted_full_run is accepted only together with the semiclassical readout declaration"
    assert absent.value.reason.startswith(FULL_RUN_ROUTE)
    assert absent.value.reason == f"{FULL_RUN_ROUTE}: the export selects the full-run profile 'quotient-exact'"

    # --- the converter-present branch, which is this tree: refused by name, on the topology.
    with pytest.raises(intake_v2.Refused) as present:
        publish({**deepcopy(v), "printed": printed}, tmp_path, qualified_in_run=True)
    # Reworded 2026-11-24 in participant terms (no internal module names). Production change that would fail this:
    # reverting the wording, or naming anything but the topology that was asked for.
    assert present.value.reason == (
        "full-run selection 'quotient-exact' selects the multiplier topology 'quotient_stream', which is not the "
        "reference topology and is refused: a full-run design is priced on the reference topology only, and a "
        "'quotient_stream' run is never priced as the reference one")
    assert publisher.sent == []


@pytest.mark.parametrize("qualify_readout", [False, True])
def test_the_full_run_source_gate_admits_only_the_one_form_and_only_beside_the_readout(qualify_readout, measured, publisher, monkeypatch, tmp_path):
    """The source gate admits ONE
    form -- `def submittedFullRunDesign : Shor.FullRunDesign := ...` beside the semiclassical readout declaration -- and
    keeps refusing everything else before any compile. A declaration of another type is still refused on both routes
    (`intake_v2.policy_from_lean` applies `check_legacy_full_run_source` before `kind` is read). Production change that
    would fail this: admitting a declaration that is not the admitted form, or compiling before the gate rules.

    POSITIVE CONTROL: the admitted form, beside the readout, reaches the compile on the measured route only."""
    _, _, source = measured
    declared = source.replace("def submittedDesign", "def submittedFullRunDesign : Nat := 0\n\ndef submittedDesign")
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("the source gate refuses before any compile"))
    with pytest.raises(intake_v2.Refused, match="submittedFullRunDesign is not accepted"):
        intake_v2.policy_from_lean(engine.load(), declared, tmp_path, tmp_path, qualify_readout=qualify_readout)
    admitted = source.replace("end PolicySubmission", "def submittedFullRunDesign : Shor.FullRunDesign :=\n"
                              "  { data := some ⟨.rossSelinger, .exact⟩ }\n\nend PolicySubmission")
    admitted = "import ForShor.ShorVerification.Framework.FullRunDesign\n" + admitted
    reached = []
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: reached.append(kw) or {"stop": True})
    if qualify_readout:
        with pytest.raises(Exception):
            intake_v2.policy_from_lean(engine.load(), admitted, tmp_path, tmp_path, qualify_readout=True)
        assert reached == [{"kind": "pp", "qualify_readout": True}], "the admitted form reaches the measured build"
    else:
        with pytest.raises(intake_v2.Refused, match="priced only together with the semiclassical readout"):
            intake_v2.policy_from_lean(engine.load(), admitted, tmp_path, tmp_path)
        assert reached == []
    assert publisher.sent == []


# ---------------------------------------------------------------- the coherent route is untouched

#: sha256 of the canonical `/v2/publish` payload for the archived PhaseProduct baseline at `792464f`, before the lift.
COHERENT_PAYLOAD_SHA256 = "37d2690a8465281a2f9b6bb9fb60eb56466c0cb718b39191b75d499ec5eb8cb9"


@pytest.mark.slow
def test_a_coherent_submission_sends_the_byte_identical_request(publisher, monkeypatch, tmp_path):
    """REAL PATH. The pin: nothing in the lift may move one byte of a coherent policy's request."""
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", evidence.EvidenceStore.stomirror)   # the real mirror record
    monkeypatch.setattr(evidence, "stomirror_from_envelope", evidence.stomirror_from_envelope)
    policy_doc = json.loads((ARCHIVE / f"{COHERENT_DIGEST}.policy.json").read_text())
    envelope = json.loads((ARCHIVE / f"{COHERENT_DIGEST}.evidence.json").read_text())
    digest = canon.policy_digest(intake_v2.normalise(policy_doc))
    v = {"schema": intake_v2.VERIFICATION_SCHEMA, "digest": digest, "mode": policy_doc["mode"], "board": "pp",
         "policy": policy_doc, "printed": None, "envelope": envelope, "forshor": None, "model": publisher.model,
         "login": "tester", "createdAt": FROZEN_CLOCK}
    assert publish(v, tmp_path)["published"]
    payload = deepcopy(publisher.sent[-1])
    assert payload["evaluation"].pop("evaluatedAt").endswith("Z"), "the recovery loop stamps its own clock"
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    assert sha256(text.encode()).hexdigest() == COHERENT_PAYLOAD_SHA256


def test_a_coherent_document_still_meets_the_coherent_checker(measured, publisher, monkeypatch, tmp_path):
    """The conditional checker: a coherent document gets `require_coherent_export`, so a relabelled measured export is
    still refused there. Red if the checker is applied to no document at all."""
    _, compiled, source = measured
    printed = {k: value for k, value in compiled.items() if k != "readout"}
    printed.update(schema="forshor.policy-submission/v1", submitted_readout="semiclassical")
    with pytest.raises(intake_v2.Refused, match="not supported by the coherent pricing route"):
        publish({"printed": printed}, tmp_path, qualified_in_run=True)
    assert publisher.sent == []


# ---------------------------------------------------------------- no refusal or stop may look like a success

@pytest.mark.parametrize("failure,code", [("refused", 1), ("incomplete", 2)])
def test_no_refusal_or_incomplete_emits_a_success_receipt(failure, code, measured, monkeypatch, tmp_path):
    """The success branch of `_run_args` must not be reached: no receipt, no result that claims publication."""
    _, _, source = measured
    policy = tmp_path / "Policy.lean"
    policy.write_text(source)
    out, receipt = tmp_path / "result.json", tmp_path / "verification.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    error = intake_v2.Refused("forshor", "did not bind") if failure == "refused" else \
        intake_v2.Incomplete("timeout", "ForShor's Lean build reached its 7200s time limit")
    monkeypatch.setattr(intake_v2, "verify_submission", lambda *a, **kw: (_ for _ in ()).throw(error))
    monkeypatch.delenv("PUBLISHER_URL", raising=False)
    assert intake_v2.main(["--qualify-readout", "--no-publish", "--login", "tester", "--lean-policy", str(policy),
                           "--tree", str(tmp_path), "--workdir", str(tmp_path / "w"),
                           "--verification-out", str(receipt), "--out", str(out)]) == code
    result = json.loads(out.read_text())
    assert not receipt.exists() and "verification" not in result and "published" not in result
    assert result.get("refused" if failure == "refused" else "failed") is True
