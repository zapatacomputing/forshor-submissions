"""The standalone primitive gate: what the separate nonpublishing primitive-qualification route now accepts, and
every refusal that makes the lift safe.

Owner-authorised 2026-11-24. `intake_v2.refuse_unqualified_primitive` was called UNCONDITIONALLY, on every route, so
`submittedPrimitive` was refused everywhere and lane XA's exact standalone pricing -- which reproduces the four
recorded B2/B3 prices from the compiled export -- was unreachable. Lane WQ marked the gate "n/a - primitive, not
full-run" in its enumeration and nobody was asked to lift it. This lane lifts it along WQ's proven readout shape and
keeps the route NONPUBLISHING, which is what `scripts/ci/policy_source.py` and `docs/CLI.md` have always promised:
"only the separate nonpublishing primitive-qualification harness qualifies a primitive synthesis (it never
publishes)".

REAL PATH vs SEAM. Every price here is the real engine through the real `primitive_overlay.project`, against XA's
recorded qualification receipts (`data/primitive_receipts_80d7b6b.json.gz`, verifier 80d7b6b2). What is injected is
ForShor's Lean build alone -- `intake_v2.forshor_build` -- exactly as `test_publication_gate.py` injects it: no Lean
runs in a pytest. Refusal tests monkeypatch BOTH the engine loader and `price_views` to `pytest.fail`, so a refusal
that prices or builds anything fails rather than passes quietly.
"""
from copy import deepcopy
import gzip
from hashlib import sha256
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
import intake_v2  # noqa: E402
from export_v2 import canon, engine, epoch, evidence, mapping, pricing, request  # noqa: E402
from export_v2 import primitive_overlay as overlay  # noqa: E402
from export_v2.canon import sha256_hex  # noqa: E402
from export_v2.mapping import DELTA_VALUES  # noqa: E402

DATA = Path(__file__).parent / "data"
FROZEN_CLOCK = "2026-11-24T00:00:00Z"
HEAD_SHA = "a" * 40

#: fixture name, board, compiled backend, whether T is an unconditional expectation, recorded T, recorded Q.
CASES = (("b2-pp", "pp", "catalytic-b2", False, 26478500, 4833),
         ("b2-ptp", "ptp", "catalytic-b2", False, 97329643, 7614),
         ("b3-pp", "pp", "adaptive-b3", True, 143366613, 4808),
         ("b3-ptp", "ptp", "adaptive-b3", True, 140780047, 7591))
BY_NAME = {name: rest for name, *rest in (list(c) for c in CASES)}

MIRROR = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
          "claim": "policy-algebra-and-exact-widths"}

#: XA's pinned baseline: the DEFAULT export price of the two standalone policies on their own board at every delta,
#: captured at 792464f before the overlay existed. The lift must not move one byte of it.
BASELINE_DIGEST = "36f1945013af84ba4326e3e1cd4dc8d21c05e0c45dfdc5b4675e435eba6dbdd1"


@pytest.fixture(scope="module")
def receipts():
    return json.loads(gzip.decompress((DATA / "primitive_receipts_80d7b6b.json.gz").read_bytes()))


@pytest.fixture(scope="module")
def template():
    """The shipped PhaseProduct submission source, with its readout declaration removed: the base the primitive
    sources below are written on, so they are a real participant file and not a fragment."""
    source = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))["source"]
    return source.replace("def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical\n\n", "")


def source_for(template, declaration, namespace="PolicySubmission"):
    """`template` with `declaration` placed at column 0 directly inside the board's namespace."""
    text = template if namespace == "PolicySubmission" else template.replace("PolicySubmission", "PTPPolicySubmission")
    end = f"end {namespace}"
    assert text.count(end) == 1
    return text.replace(end, f"{declaration}\n\n{end}")


SELECTED = {"catalytic-b2": "def submittedPrimitive : Shor.PrimitiveSynthesis := .catalyticB2",
            "adaptive-b3": "def submittedPrimitive : Shor.PrimitiveSynthesis := .adaptiveB3"}
EXPLICIT_DEFAULT = "def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger"


def _policy(document, board):
    convert = intake_v2.policy_from_document if board == "pp" else intake_v2.policy_from_ptp_document
    return convert(engine.load(), document)


def _preparation(receipt, delta_key="1e-2"):
    from fractions import Fraction
    return receipt["receipt"]["standaloneLedger"]["deltas"][str(Fraction(str(DELTA_VALUES[delta_key])))].get("prep_words")


def _banks(receipt):
    """The attested catalyst words for EVERY delta, as a primitive request carries them: the uniform catalyst width b
    is a function of delta, so one bank cannot serve four precisions."""
    banks = {key: _preparation(receipt, key) for key in DELTA_VALUES}
    return {key: words for key, words in banks.items() if words is not None} or None


def qualify(receipts, template, name, monkeypatch, tmp_path, *, document=None, source=None):
    """The receipt the separate nonpublishing primitive harness produces for one recorded compiled export."""
    board, backend, *_ = BY_NAME[name]
    compiled = document if document is not None else receipts[name]["compiled"]
    namespace = "PolicySubmission" if board == "pp" else "PTPPolicySubmission"
    text = source if source is not None else source_for(template, SELECTED[backend], namespace)
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: compiled)
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (MIRROR, None))
    return intake_v2.verify_submission(None, lean_policy=text, login="tester", tree=tmp_path,
                                       workdir=tmp_path / f"verify-{name}", forshor_tree=tmp_path,
                                       run_mirror_fn=lambda *a, **kw: {"record": {"verdict": "accepted"}},
                                       qualify_primitive=True, log=lambda s: None)


_UNSET = object()


def price(v, receipts, name, monkeypatch, preparation=_UNSET, verifier_pin=overlay.VERIFIER_PIN):
    """The priced rows for a primitive qualification, through the real overlay and the real engine."""
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda self, *a: (MIRROR, None))
    return request.build_primitive_request(
        v["policy"], compiled=v["printed"], verifier_pin=verifier_pin,
        lean_source=v["leanSource"].encode(),
        preparation=_banks(receipts[name]) if preparation is _UNSET else preparation,
        model=v["model"], store=evidence.EvidenceStore(None), head_sha=HEAD_SHA, pull_number=7,
        github_login="tester", run_id="1", submitted_at=FROZEN_CLOCK, evaluated_at=FROZEN_CLOCK,
        archive_commit="c" * 40, accepted_ids=[HEAD_SHA], evaluation_key="d" * 64,
        base_generation="1", number=0, log=lambda s: None)


@pytest.fixture
def nothing_is_priced(monkeypatch):
    """A refusal prices nothing and builds nothing: both the engine loader and the pricer fail the test if reached."""
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refusal must price nothing"))
    monkeypatch.setattr(engine, "load", lambda *a, **kw: pytest.fail("a refusal must not build the engine"))
    return monkeypatch


# ---------------------------------------------------------------- WHAT THE LIFT OPENS

@pytest.mark.parametrize("name,board,backend,adaptive,want_t,want_q", CASES)
def test_the_ordinary_route_still_refuses_the_selector_this_route_accepts(name, board, backend, adaptive, want_t,
                                                                          want_q, template, tmp_path, monkeypatch):
    """THE LIFT, source half. The SAME source is refused on the ordinary route (byte-identical message, naming the
    cause) and accepted on the qualification route. Red if `refuse_unqualified_primitive` stops being called when
    `qualify_primitive` is false."""
    namespace = "PolicySubmission" if board == "pp" else "PTPPolicySubmission"
    text = source_for(template, SELECTED[backend], namespace)
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("refused before any compile"))
    with pytest.raises(intake_v2.Refused) as refused:
        intake_v2.policy_from_lean(engine.load(), text, tmp_path, tmp_path)
    assert refused.value.reason.startswith("submittedPrimitive requires separate nonpublishing primitive qualification")
    assert "selects the non-default primitive synthesis" in refused.value.reason


@pytest.mark.parametrize("name,board,backend,adaptive,want_t,want_q", CASES)
def test_a_qualified_primitive_export_is_bound_where_the_ordinary_route_refuses_it(name, board, backend, adaptive,
                                                                                    want_t, want_q, receipts,
                                                                                    template, monkeypatch, tmp_path):
    """THE LIFT, compiled half. `policy_from_lean` refused any document carrying `submitted_primitive`; on this route
    it is bound by `primitive_overlay.bind_compiled_primitive` and the binding is re-derived here, never taken from
    the document's own claim."""
    v = qualify(receipts, template, name, monkeypatch, tmp_path)
    qualification = v["localPrimitiveQualification"]
    assert qualification["primitive"] == ("adaptiveB3" if adaptive else "catalyticB2")
    bound = qualification["evidence"]
    assert bound["board"] == board and bound["backend"] == backend
    assert bound["verifierPin"] == overlay.VERIFIER_PIN
    assert bound["documentSha256"] == receipts[name]["receipt"]["compiledDocumentSha256"]
    assert bound["policyDigest"] == receipts[name]["receipt"]["policyDigest"]
    assert bound["sourceSha256"] == sha256(v["leanSource"].encode()).hexdigest() == v["leanSha256"]
    assert v["forshor"] is None, "a primitive proof claims no coherent IR agreement"


@pytest.mark.slow
@pytest.mark.parametrize("name,board,backend,adaptive,want_t,want_q", CASES)
def test_the_four_recorded_prices_reach_a_priced_row(name, board, backend, adaptive, want_t, want_q, receipts,
                                                      template, monkeypatch, tmp_path):
    """END TO END, the whole point of the lane: a compiled export carrying `submitted_primitive` reaches a priced row
    whose T and Q are the recorded standalone numbers, on all four cases."""
    v = qualify(receipts, template, name, monkeypatch, tmp_path)
    built = price(v, receipts, name, monkeypatch)
    assert [p["board"] for p in built["priced"]] == [board] * len(mapping.DELTA_KEYS), \
        "a standalone primitive is priced on its own board only; the full board takes no primitive"
    assert built["submission"]["boards"] == [board]
    panel = next(p for p in built["priced"] if p["delta"] == "1e-2")
    assert panel["row"]["T"] == want_t, f"{name}: priced {panel['row']['T']} against a recorded {want_t}"
    assert panel["row"]["qubits"] == want_q
    assert panel["panel"]["primitive"]["rankedT"] == want_t
    assert panel["panel"]["primitive"]["primitive"] == ("adaptiveB3" if adaptive else "catalyticB2")
    # The row's number is exactly the overlay's own, computed independently from the compiled export.
    direct = overlay.project(_policy(v["printed"], board),
                             pricing.price_views(engine.load().tables.Policy.from_json(v["policy"]),
                                                 panel["panel"]["title"], "1e-2", [board])[board],
                             v["printed"]["submitted_primitive"], preparation=_preparation(receipts[name]))
    assert (direct["rankedT"], direct["qubits"]) == (want_t, want_q)


@pytest.mark.slow
@pytest.mark.parametrize("name,board,backend,adaptive,want_t,want_q", CASES)
def test_the_priced_row_never_carries_an_expectation_without_its_kind(name, board, backend, adaptive, want_t, want_q,
                                                                       receipts, template, monkeypatch, tmp_path):
    """XA's STRUCTURAL GUARANTEE, preserved at the one place it could be lost. The row's `T` is a bare number with no
    kind, so B3's 143,366,613 could be read as a worst case. The substitution therefore re-asserts XA's invariant --
    `rankedT` travels with `rankedTKind`, there is no bare `T`, and exactly one of the two kind-specific fields is
    set -- and the kind stays on the panel beside the row. Red if the row is allowed to take `rankedT` alone."""
    v = qualify(receipts, template, name, monkeypatch, tmp_path)
    built = price(v, receipts, name, monkeypatch)
    kind = "expectedAdaptive" if adaptive else "deterministic"
    for item in built["priced"]:
        result = item["panel"]["primitive"]
        assert "T" not in result, "a bare T would let a consumer read the number without its kind"
        assert result["tKind"] == kind and result["rankedTKind"] == kind
        assert result["tDeterministic"] is (None if adaptive else result["rankedT"])
        assert result["tExpectedAdaptive"] is (result["rankedT"] if adaptive else None)
        assert item["row"]["T"] == result["rankedT"]


@pytest.mark.parametrize("broken,reason", [
    ({"rankedTKind": None}, "names no kind"),
    ({"T": 1}, "bare T"),
    ({"tDeterministic": 1, "tExpectedAdaptive": 1}, "exactly one"),
    ({"tDeterministic": None, "tExpectedAdaptive": None}, "exactly one"),
])
def test_a_primitive_result_without_its_kind_refuses_at_the_row(broken, reason):
    """The RED of the test above, without the engine: a malformed primitive result must not reach a row at all."""
    result = {"rankedT": 143366613, "qubits": 4808, "tKind": "expectedAdaptive", "rankedTKind": "expectedAdaptive",
              "tDeterministic": None, "tExpectedAdaptive": 143366613}
    panel = {"rowId": HEAD_SHA, "fullRun": None, "T": 1.0, "qubits": 2, "ladderLabel": "2:leaf",
             "verification": {"stoMirror": None, "forshorPolicy": None}, "primitive": {**result, **broken}}
    with pytest.raises(ValueError, match=reason):
        mapping.row_of(panel, origin="submission", name="t", created_at="x", pull_request_url=None,
                       submission_id=None, detail="d")
    # The well-formed one does reach the row, so the test above is not vacuous.
    good = mapping.row_of({**panel, "primitive": result}, origin="submission", name="t", created_at="x",
                          pull_request_url=None, submission_id=None, detail="d")
    assert (good["T"], good["qubits"]) == (143366613, 4808)


# ---------------------------------------------------------------- THE EXPLICIT DEFAULT IS OMISSION

@pytest.mark.parametrize("namespace", ["PolicySubmission", "PTPPolicySubmission"])
def test_the_explicit_default_resolves_exactly_as_writing_nothing(namespace, template, monkeypatch, tmp_path):
    """OWNER RULING 2026-11-24 (a4a7609), on BOTH routes. `.rossSelinger` stamps nothing into the compiled document
    (`PrimitiveSynthesis.attest .rossSelinger scope doc = doc` is `rfl`), so a source that declares it compiles to a
    document with no `submitted_primitive` key and is priced as the default. The recogniser is WJ's generalised
    `scripts/ci/policy_source.default_selectors`, not a second copy here. Red if this route treats the explicit
    default as a selection, or if the ordinary route starts refusing it."""
    from scripts.ci.policy_source import default_selectors, strip_lean_comments
    text = source_for(template, EXPLICIT_DEFAULT, namespace)
    residual, consumed = default_selectors(strip_lean_comments(text))
    assert consumed == {"submittedPrimitive": namespace}, "the shipped default form is recognised on both boards"
    assert "submittedPrimitive" not in residual

    compiled = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))["document"]
    coherent = {k: value for k, value in compiled.items() if k != "readout"}
    coherent["submitted_readout"] = "coherent"
    coherent["schema"] = "forshor.policy-submission/v1"
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: coherent)
    if namespace == "PolicySubmission":          # the recorded coherent export is a PhaseProduct one
        for qualifying in (False, True):
            kw = {"qualify_primitive": True} if qualifying else {}
            policy, doc = intake_v2.policy_from_lean(engine.load(), text, tmp_path, tmp_path, **kw)
            assert "submitted_primitive" not in doc
            omitted = intake_v2.policy_from_lean(engine.load(), template, tmp_path, tmp_path, **kw)
            assert (policy, doc) == omitted, "an explicit default must be identical to omission, on every route"


@pytest.mark.slow
def test_the_default_export_price_is_still_bit_identical_to_xas_baseline(receipts):
    """BYTE IDENTITY. The coherent/default path must not move: XA's pinned digest over the default price of both
    standalone policies at every delta, captured at 792464f."""
    out = {}
    for name, board in (("b2-pp", "pp"), ("b2-ptp", "ptp")):
        policy = _policy(receipts[name]["compiled"], board)
        for key in DELTA_VALUES:
            out[f"{board}/{key}"] = pricing.price_views(policy, f"XA baseline {board}", key, [board])[board]
            assert "primitive" not in out[f"{board}/{key}"], "no primitive key on a default view"
    assert sha256_hex(out) == BASELINE_DIGEST


# ---------------------------------------------------------------- RETAINED: the selector itself

@pytest.mark.slow
@pytest.mark.parametrize("backend", ["ross-selinger", "catalytic-b4", "", None])
def test_an_unsupported_or_absent_backend_is_refused_and_never_defaulted(backend, receipts, template, monkeypatch,
                                                                          tmp_path):
    """RETAINED. An unsupported or absent backend is REFUSED, never silently priced at the engine's default. Red if
    `primitive_overlay.project`'s `PRIMITIVE_OF` lookup is allowed to fall through to `DEFAULT_PRIMITIVE`."""
    document = deepcopy(receipts["b2-pp"]["compiled"])
    document["submitted_primitive"] = {"schema": overlay.SELECTION_SCHEMA, "scope": "pp", "backend": backend}
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selector prices nothing"))
    with pytest.raises(intake_v2.Refused, match="unsupported standalone primitive selector") as refused:
        qualify(receipts, template, "b2-pp", monkeypatch, tmp_path, document=document)
    # The refusal NAMES what it refused and never reports a price: `rossSelinger` here is the name of the thing that
    # was refused, not a default the backend fell back to.
    assert "on the pp board" in refused.value.reason


@pytest.mark.slow
@pytest.mark.parametrize("name,scope", [("b2-pp", "ptp"), ("b2-ptp", "pp"), ("b3-pp", "full"), ("b3-ptp", "invented")])
def test_a_selector_scoped_to_another_board_is_refused(name, scope, receipts, template, monkeypatch, tmp_path):
    """RETAINED. The selector is board-scoped and must name the board it is compiled for; the `full` order-finding
    board takes no primitive at all (XA). Red if the scope stops being compared with the document's own board."""
    document = deepcopy(receipts[name]["compiled"])
    document["submitted_primitive"] = {**document["submitted_primitive"], "scope": scope}
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a mis-scoped selector prices nothing"))
    with pytest.raises(intake_v2.Refused):
        qualify(receipts, template, name, monkeypatch, tmp_path, document=document)


@pytest.mark.slow
def test_the_full_order_finding_board_takes_no_primitive(receipts):
    """RETAINED, at the pricer. `price_views` hooks the overlay into the single-product branch only, and the overlay
    refuses the full board by name. Red if the hook is moved into the `full` branch."""
    policy = _policy(receipts["b2-pp"]["compiled"], "pp")
    selection = receipts["b2-pp"]["compiled"]["submitted_primitive"]
    views = pricing.price_views(policy, "t", "1e-2", ["full"], primitive=selection,
                                preparation=_preparation(receipts["b2-pp"]))
    assert "primitive" not in views["full"], "the full board is priced exactly as it always was"
    with pytest.raises(ValueError, match="catalyticB2 on the full board"):
        overlay.project(policy, {**views["full"], "mode": "full"}, selection,
                        preparation=_preparation(receipts["b2-pp"]))


# ---------------------------------------------------------------- RETAINED: the benchmark and the epoch

@pytest.mark.slow
@pytest.mark.parametrize("broken,reason", [
    ({"benchmark": {"bits": 1024}}, "unsupported primitive benchmark"),
    # Refused one gate EARLIER, by `policy_from_lean`'s own negation rule, which this route left untouched.
    ({"negation": "scratch"}, "the boards price oneQubit only"),
    ({"schema": "forshor.ptp-policy-submission/v1"}, "unsupported primitive benchmark"),
])
def test_a_foreign_benchmark_negation_or_schema_is_refused(broken, reason, receipts, template, monkeypatch, tmp_path):
    """RETAINED. Only the exact-width 2048-bit benchmark with the default negation is priced. Red if
    `bind_compiled_primitive` stops checking the document it was handed, or if the qualification route stops applying
    the ordinary export rules that sit in front of it."""
    document = {**deepcopy(receipts["b2-pp"]["compiled"]), **broken}
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a foreign benchmark prices nothing"))
    with pytest.raises(intake_v2.Refused, match=reason):
        qualify(receipts, template, "b2-pp", monkeypatch, tmp_path, document=document)


@pytest.mark.slow
def test_a_non_exact_width_export_is_refused(receipts, template, monkeypatch, tmp_path):
    """RETAINED. The recorded standalone science is the exact-width benchmark only."""
    document = deepcopy(receipts["b2-pp"]["compiled"])
    document["ir"] = {**document["ir"], "width_rule": {**document["ir"]["width_rule"], "rule": "forshor.scan-width/v1"}}
    with pytest.raises(intake_v2.Refused, match="checked exact-width PhaseProduct export"):
        qualify(receipts, template, "b2-pp", monkeypatch, tmp_path, document=document)


@pytest.mark.slow
def test_a_delta_outside_the_epoch_is_refused(receipts):
    """RETAINED. The overlay prices none of the deltas the boards do not publish."""
    policy = _policy(receipts["b2-pp"]["compiled"], "pp")
    view = pricing.price_views(policy, "t", "1e-2", ["pp"])["pp"]
    with pytest.raises(ValueError, match="is not one of the epoch's deltas"):
        overlay.project(policy, {**view, "delta": 0.02}, receipts["b2-pp"]["compiled"]["submitted_primitive"],
                        preparation=_preparation(receipts["b2-pp"]))


@pytest.mark.parametrize("route", ["verify", "price"])
def test_an_epoch_mismatch_is_an_operator_stop_not_a_refusal_of_the_policy(route, receipts, template, monkeypatch,
                                                                           tmp_path):
    """RETAINED, lane WU's precedent. The primitive rule is qualified for ONE epoch; a checkout at another cannot rule
    on the submission at all. That is `Incomplete` at stage `epoch` -- which `scripts/ci/notify_submitters.js`
    LIMIT_STAGES already reports as incomplete/limits -- and never `Refused`, which would tell the participant their
    policy is what is wrong. Red if the check moves back behind `bind_compiled_primitive`, whose own epoch
    `ValueError` surfaces as `Refused("forshor", ...)`."""
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a stopped run prices nothing"))
    v = None
    if route == "price":
        v = qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
    monkeypatch.setattr(overlay, "EPOCH", "0000000000000000")
    with pytest.raises(intake_v2.Incomplete) as stop:
        if route == "verify":
            qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
        else:
            price(v, receipts, "b2-pp", monkeypatch)
    assert stop.value.stage == "epoch" and "not examined" in str(stop.value)
    assert "not a verdict on the policy" in str(stop.value)


# ---------------------------------------------------------------- RETAINED: the catalyst preparation

@pytest.mark.slow
def test_a_tampered_preparation_bank_is_refused_and_the_prep_T_is_recounted(receipts, template, monkeypatch, tmp_path):
    """RETAINED. The preparation's T is recounted from the attested Clifford+T words and must fit the engine's own
    Ross-Selinger allowance; a short, non-Clifford+T or inflated bank is refused. Red if `_prep_t` stops recounting."""
    v = qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
    words = _preparation(receipts["b2-pp"])
    priced = price(v, receipts, "b2-pp", monkeypatch)["priced"]
    at_1e2 = next(p for p in priced if p["delta"] == "1e-2")
    assert at_1e2["panel"]["primitive"]["detail"]["prepT"] == sum(word.count("T") for word in words.values())
    banks = ((None, "must be one word per resolution 4.."),
             ({"1e-2": None}, "must be one word per resolution 4.."),
             ({"1e-2": {k: value for k, value in list(words.items())[1:]}}, "must be one word per resolution 4..28"),
             ({"1e-2": {**words, "15/16": "CNOT"}}, "must be a Clifford\\+T word"),
             ({"1e-2": {**words, "15/16": "T" * 4096}}, r"above the 1044\.0 T allowance"))
    for bank, reason in banks:
        with pytest.raises(ValueError, match=reason):
            price(v, receipts, "b2-pp", monkeypatch, preparation=bank)


@pytest.mark.slow
def test_a_preparation_handed_to_the_adaptive_probe_is_refused(receipts, template, monkeypatch, tmp_path):
    """RETAINED. The adaptive B3 probe measures and resets: it prepares no resident catalyst, so a preparation bank
    offered with it is refused rather than charged for or ignored."""
    v = qualify(receipts, template, "b3-pp", monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="adaptive probe prepares no resident"):
        price(v, {**receipts, "b3-pp": receipts["b2-pp"]}, "b3-pp", monkeypatch)


# ---------------------------------------------------------------- RETAINED: trust and publication

def test_an_unpinned_verifier_is_refused(receipts, template, monkeypatch, tmp_path):
    """RETAINED. The qualification is bound to the pinned verifier build; evidence naming another one prices nothing."""
    v = qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("an unpinned verifier prices nothing"))
    with pytest.raises(ValueError, match="requires the pinned verifier build"):
        price(v, receipts, "b2-pp", monkeypatch, verifier_pin="0" * 40)


def test_a_participant_supplied_qualification_is_refused_by_the_publisher(receipts, template, monkeypatch, tmp_path):
    """RETAINED, and this is the refusal that keeps the lift a LOCAL one. Hosted intake takes its inputs from the
    trusted run and the publisher, never from a receipt the submitter supplies -- and the primitive route never
    publishes at all, which `scripts/ci/policy_source.py` and `docs/CLI.md` have always said. Red if
    `publish_verification` stops guarding either the qualification key or the compiled selector."""
    v = qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
    receipt = json.loads(json.dumps(v))                       # through a file, as an artifact travels
    stripped = {k: value for k, value in receipt.items() if k != "localPrimitiveQualification"}
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refusal must price nothing"))
    monkeypatch.setattr(request, "build_request", lambda *a, **kw: pytest.fail("a refusal must build no request"))
    monkeypatch.setattr(request, "build_primitive_request", lambda *a, **kw: pytest.fail("the publisher never prices a primitive"))
    for n, record in enumerate((receipt, stripped)):
        with pytest.raises(intake_v2.Refused, match="primitive qualification is local only; publishing is not enabled"):
            intake_v2.publish_verification(record, login="tester", pull=7, url="http://x", token="t",
                                           workdir=tmp_path / f"pub{n}", head_sha=HEAD_SHA, run_id="1",
                                           log=lambda s: None)


def test_the_qualification_route_takes_no_publisher_url(receipts, template, monkeypatch, tmp_path):
    """RETAINED. `--qualify-primitive` is local only: unlike readout it has no single-trusted-run publishing arm, so
    a publisher URL is refused outright and nothing reaches the network."""
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("the local route touches no network"))
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("refused before any compile"))
    with pytest.raises(intake_v2.Refused, match="primitive qualification is local only and requires Policy.lean"):
        intake_v2.verify_submission(None, lean_policy="namespace PolicySubmission\nend PolicySubmission\n",
                                    login="tester", url="http://x", tree=tmp_path, workdir=tmp_path / "w",
                                    forshor_tree=tmp_path, qualify_primitive=True, log=lambda s: None)
    with pytest.raises(intake_v2.Refused, match="primitive qualification is local only and requires Policy.lean"):
        intake_v2.verify_submission({"mode": "PhaseProduct"}, login="tester", tree=tmp_path,
                                    workdir=tmp_path / "w2", forshor_tree=tmp_path, qualify_primitive=True,
                                    log=lambda s: None)


def test_a_combined_readout_and_primitive_qualification_is_refused(template, tmp_path, monkeypatch,
                                                                    nothing_is_priced):
    """RETAINED. The two qualification routes do not compose and the combination has never had a defined price;
    `bind_compiled_primitive` refuses a measured document and the intake refuses the combination up front."""
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("refused before any compile"))
    with pytest.raises(intake_v2.Refused, match="readout and primitive qualification"):
        intake_v2.policy_from_lean(None, source_for(template, SELECTED["catalytic-b2"]), tmp_path, tmp_path,
                                   qualify_readout=True, qualify_primitive=True)


@pytest.mark.slow
def test_a_measured_document_is_refused_by_the_primitive_binding(receipts, template, monkeypatch, tmp_path):
    """RETAINED, the other end of the same rule: a document that carries both a measured readout and a primitive
    selector is refused by the binding itself."""
    for broken in ({"submitted_readout": "semiclassical"}, {"readout": {}}):
        document = {**deepcopy(receipts["b2-pp"]["compiled"]), **broken}
        with pytest.raises(intake_v2.Refused, match="combined measured readout and primitive qualification"):
            qualify(receipts, template, "b2-pp", monkeypatch, tmp_path, document=document)


# ---------------------------------------------------------------- RETAINED: stops are not verdicts

@pytest.mark.parametrize("failure,code", [("refused", 1), ("incomplete", 2)])
def test_no_refusal_or_resource_stop_emits_a_success_receipt(failure, code, template, monkeypatch, tmp_path):
    """RETAINED. A timeout or resource stop is INCOMPLETE -- never a refusal and never a pass -- and neither a
    refusal nor a stop writes a receipt or a result that claims publication."""
    policy = tmp_path / "Policy.lean"
    policy.write_text(source_for(template, SELECTED["catalytic-b2"]))
    out, receipt = tmp_path / "result.json", tmp_path / "verification.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    error = intake_v2.Refused("forshor", "did not bind") if failure == "refused" else \
        intake_v2.Incomplete("timeout", "ForShor's circuit extraction and checks reached their 7200s time limit")
    monkeypatch.setattr(intake_v2, "verify_submission", lambda *a, **kw: (_ for _ in ()).throw(error))
    monkeypatch.delenv("PUBLISHER_URL", raising=False)
    assert intake_v2.main(["--qualify-primitive", "--no-publish", "--login", "tester", "--lean-policy", str(policy),
                           "--tree", str(tmp_path), "--workdir", str(tmp_path / "w"),
                           "--verification-out", str(receipt), "--out", str(out)]) == code
    result = json.loads(out.read_text())
    assert not receipt.exists() and "verification" not in result and "published" not in result
    assert result.get("refused" if failure == "refused" else "failed") is True


def test_the_cli_refuses_to_publish_a_primitive_qualification(template, tmp_path, monkeypatch, capsys,
                                                               nothing_is_priced):
    """RETAINED. The flag combination itself is refused by the argument parser: there is no route that both qualifies
    a primitive and publishes."""
    policy = tmp_path / "Policy.lean"
    policy.write_text(source_for(template, SELECTED["catalytic-b2"]))
    monkeypatch.delenv("PUBLISHER_URL", raising=False)
    base = ["--qualify-primitive", "--login", "tester", "--lean-policy", str(policy), "--tree", str(tmp_path),
            "--workdir", str(tmp_path / "w"), "--out", str(tmp_path / "o.json")]
    # Each case exercises ONE guard on its own, and the MESSAGE is asserted: other argument checks also exit 2, so
    # exit status alone would not pin these four. No --no-publish at all is the publishing route; a publisher URL
    # with --no-publish; a publisher URL and --pull is the one-trusted-run route readout has and this one must not;
    # and the combination with the other qualification route.
    for extra, message in (([], "never publishes: it requires --no-publish"),
                           (["--no-publish", "--publisher-url", "http://x"], "local only: it takes no publisher URL"),
                           (["--publisher-url", "http://x", "--pull", "7"], "never publishes: it requires --no-publish"),
                           (["--no-publish", "--qualify-readout"], "separate qualification routes and do not combine")):
        with pytest.raises(SystemExit) as exit_code:
            intake_v2.main(base + extra)
        assert exit_code.value.code == 2, extra
        assert message in capsys.readouterr().err, extra


# ---------------------------------------------------------------- THE ARCHIVE / PUBLISHER ANSWER

def test_the_primitive_route_never_reaches_the_archive_whatever_the_archive_admits():
    """THE ARCHIVE ANSWER, pinned executably rather than asserted in a report.

    Lane WA added `readout` and `boards` to the index entry for the readout VARIANT planner. A standalone primitive
    needs nothing analogous TODAY, and the reason is reachability, not generosity: `archive_policy.write_policy` is
    only ever called through `archive_policy.archive_policy`, which `intake_v2.main` wires in as `archive_write` on
    the `--publish-from` route alone. `--qualify-primitive` requires `--no-publish`, so no primitive verification can
    reach the archive at all. That is the load-bearing guarantee and it is unchanged; the four parser guards that
    enforce it are pinned by `test_the_cli_refuses_to_publish_a_primitive_qualification`.

    INTEGRATION XQ -- XG'S SECOND REASON IS OBSOLETE, BY DESIGN, AND THIS TEST NO LONGER ASSERTS IT. As written on
    lane XG this test also claimed `plan_readout_variant` "could not accept one anyway", because the `boards`
    allowlist was exactly `["pp","full"]`, `["full","pp"]` and `["full"]`, making a standalone entry's own list --
    `["pp"]` or `["ptp"]` -- an `ArchiveRefused`. Lane XO then wrote precisely the archive hunk XG had proposed and
    declined to write: `BOARD_SETS` now admits `["pp"]` and `["ptp"]`, because a synthesis primitive and a QFT design
    are results, not repeats. So the allowlist no longer refuses those sets, and asserting that it does would pin a
    behaviour the release deliberately removed. The reachability guarantee above is what keeps the route off the
    archive; the allowlist never was a second lock, only a coincidence of WA's narrower vocabulary.

    What is kept here is the refusal that still holds -- a board set outside `BOARD_SETS` is still refused by name, so
    the planner is not simply permissive -- and the readout variant's own board list is still accepted, so neither
    clause is about the fixture. Red if the primitive route ever gains an archive-writing caller."""
    import archive_policy
    from export_v2.primitive_overlay import BOARD_SCHEMA

    entry = {"policy_id": "b" * 40, "digest": "d" * 64, "mode": "PhaseProduct", "board": "pp", "sha256": "s" * 64,
             "path": "x", "readout": "coherent",
             "source": {"github_login": "tester", "pr_number": 1, "created_at": "2026-01-01T00:00:00Z", "run_id": "1"}}

    def plan(boards):
        return archive_policy.plan_readout_variant({"policies": [{**entry, "boards": boards}]}, digest="d" * 64,
                                                   login="tester", head_sha="a" * 40, readout="semiclassical",
                                                   published_ids={"b" * 40})

    # The two boards a standalone primitive is priced on are now ADMITTED by the planner (lane XO), not refused.
    for board in BOARD_SCHEMA:
        assert [board] in [sorted(s) for s in archive_policy.BOARD_SETS], board
        plan([board])                                # reaches a verdict rather than raising on the board set
    # Still refused by name: a set outside the admitted vocabulary. The planner widened, it did not open.
    for boards in (["pp", "ptp"], ["nonsense"], ["pp", "full", "ptp"]):
        with pytest.raises(archive_policy.ArchiveRefused, match="unrecognized circuit or boards"):
            plan(boards)
    # And the planner still accepts the readout variant's own board list, so the refusals above are about the board
    # set and not about the fixture.
    assert plan(["pp", "full"]) == "b" * 40


def test_the_primitive_refusal_fires_before_the_archive_is_written(receipts, template, monkeypatch, tmp_path):
    """OWNER RULING 2026-11-24 (ruling 3): a primitive qualification is LOCAL-ONLY BY DESIGN and the archive has no
    nonpublishing write path -- deliberately. This test pins the ORDERING that makes it true, so it cannot be
    silently inverted later.

    The test above argues reachability from the flag sets; this one asserts it inside the one function where both a
    primitive refusal and an archive write exist. `publish_verification` refuses the qualification key and the
    compiled `submitted_primitive` selector near the top of the function, while `archive_write(v, ...)` is called far
    below, just before the publisher. Those two could be reordered by an ordinary edit -- moving the refusal under a
    later gate, or hoisting the archive write so the head is in the archive before anything else runs -- and nothing
    else in the suite would notice, because the refusal would still fire and the CLI flag guards would still hold.
    It would nevertheless put a local qualification's `Policy.lean`, `policy.json` and index entry on
    `submission-results`, which is a frozen record (AGENTS.md rule 3) and cannot be taken back.

    This is a TRIPWIRE on existing production code, not a non-vacuity check on a hunk this lane wrote: it is red only
    if the refusal is moved after `archive_write`, or dropped. Said plainly, as lane XO said it for its H5b tripwire.

    NOT A MISSING PATH. Do not "fix" the red by giving the archive a nonpublishing route. `primitive` and its `tKind`
    are for a PUBLISHED primitive-bearing submission; the qualification receipts carry `localOnly: true` /
    `publishable: false` and the published row's closed field set (`SITE_ROW_FIELDS` in
    `test_design_price_plumbing.py`) has no field for the price. `archive_policy`'s module docstring states the
    ruling."""
    v = qualify(receipts, template, "b2-pp", monkeypatch, tmp_path)
    receipt = json.loads(json.dumps(v))                       # through a file, as an artifact travels
    stripped = {k: value for k, value in receipt.items() if k != "localPrimitiveQualification"}
    assert "localPrimitiveQualification" in receipt and intake_v2.PRIMITIVE_KEY in stripped["printed"], \
        "the two halves XG guards: the receipt's qualification key AND the compiled selector, each alone"

    def never(*a, **kw):
        pytest.fail("a primitive qualification reached the archive: the refusal no longer fires before archive_write")

    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refusal must price nothing"))
    for n, record in enumerate((receipt, stripped)):
        with pytest.raises(intake_v2.Refused, match="primitive qualification is local only; publishing is not enabled"):
            intake_v2.publish_verification(record, login="tester", pull=7, url="http://x", token="t",
                                           workdir=tmp_path / f"arch{n}", head_sha=HEAD_SHA, run_id="1",
                                           archive_write=never, log=lambda s: None)
    # And the archive write is genuinely reachable in this function for a receipt the route does admit -- otherwise
    # `never` would be unreached for a reason that has nothing to do with the primitive.
    assert "archive_write(v, readout=" in (EVAL / "intake_v2.py").read_text(), \
        "publish_verification still has an archive-writing call for the refusal to fire ahead of"


def test_no_primitive_bearing_entry_can_be_published_under_the_shipped_site_contract():
    """THE PUBLISHER ANSWER. The published row schema is closed: a row may carry no primitive key (lane XF's seam
    ruling pins `row_of`'s field set, as `SITE_ROW_FIELDS` in `test_design_price_plumbing.py`), and panel validation
    requires a non-full board's `row.T` and `row.qubits` to EQUAL its panel's. So a primitive price can reach a row
    only by moving the panel's own T and qubits -- which is what `standalone_price_of` does.

    THE PANEL HALF IS NOT THE BLOCKING ONE, and the reason recorded here when this lane landed no longer holds. The
    shipped contract now DOES name an optional primitive block on a policy panel, validated, refused beside a measured
    readout and required absent on the full board. A primitive-bearing PANEL is therefore contract-valid; only the ROW
    cannot be represented. The route is nonpublishing because the harness that qualifies a primitive never publishes
    (`policy_source._SELECTOR_CAUSES["submittedPrimitive"]`, and the receipts' `localOnly: true` / `publishable:
    false`), NOT because the panel block has nowhere to sit. Keeping the old reason would understate the contract and
    invite the next lane to re-add a field the contract already has.

    Red if the row grows a key, or if the panel stops carrying the kind beside the number."""
    from export_v2.pytests.test_design_price_plumbing import SITE_ROW_FIELDS
    result = {"rankedT": 26478500, "qubits": 4833, "tKind": "deterministic", "rankedTKind": "deterministic",
              "tDeterministic": 26478500, "tExpectedAdaptive": None}
    panel = {"rowId": HEAD_SHA, "fullRun": None, "T": 1.0, "qubits": 2, "ladderLabel": "2:leaf",
             "verification": {"stoMirror": None, "forshorPolicy": None}, "primitive": result}
    row = mapping.row_of(panel, origin="submission", name="t", created_at="x", pull_request_url=None,
                         submission_id=None, detail="d")
    assert set(row) <= SITE_ROW_FIELDS, sorted(set(row) - SITE_ROW_FIELDS)
    assert "primitive" not in row and (row["T"], row["qubits"]) == (26478500, 4833)
    assert panel["primitive"]["rankedTKind"] == "deterministic", "the kind travels on the panel beside the row"


# ---------------------------------------------------------------- the engine and the gates

def test_the_primitive_route_prices_inside_the_committed_epoch():
    model = epoch.model_of()
    assert canon.epoch_of(model) == overlay.EPOCH == "ab1f8887b1b50e56"
    assert model["dataFingerprint"] == "176ce4047f5957f1"
    assert model["engineRevision"] == "677cc2e05ab178afff8dc632ff56058a8196a116"


def test_the_readout_rule_fingerprint_is_unchanged_by_the_primitive_route():
    """`readout_overlay.py`'s bytes ARE the published `ruleSha256`, which the consuming contract pins as an exact
    literal, so one byte of drift rejects every readout panel. Nothing in the primitive route may touch that file.
    Rotating this value is a deliberate change of its own and has to be propagated to the consumer in the same
    release -- `readout_overlay.py`'s own header says so."""
    from export_v2 import readout_overlay
    assert sha256(Path(readout_overlay.__file__).read_bytes()).hexdigest() == \
        "b3c2f317cf68bc47649a634862540177f5fc135d1e6fa543ce9fe4cb1161cd96"


def test_the_primitive_route_is_recorded_as_nonpublishing_in_the_shipped_source_gate():
    """The route this lane opened is the one the shipped gate already named. Red if the two wordings drift apart."""
    from scripts.ci.policy_source import _SELECTOR_CAUSES
    _, _, _, route, _ = _SELECTOR_CAUSES["submittedPrimitive"]
    assert route == ("only the separate nonpublishing primitive-qualification harness qualifies a primitive "
                     "synthesis (it never publishes)")
