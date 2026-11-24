"""Run from evaluation/: python -m pytest export_v2/pytests -q

The four locally qualified standalone primitive receipts (catalyticB2 and adaptiveB3, each on the PhaseProduct
and PhaseTripleProduct boards, verifier 80d7b6b2, the operational pin) are the oracle: the overlay must reprice the compiled export
to the recorded T, qubits and error ledger without touching the default price. Nothing here publishes.
"""
from fractions import Fraction
import gzip
import json
import math
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import engine, pricing, primitive_overlay as overlay  # noqa: E402
from export_v2.canon import epoch_of, sha256_hex                     # noqa: E402
from export_v2.mapping import DELTA_VALUES                           # noqa: E402

DATA = Path(__file__).parent / "data"
#: The four cases: fixture name, board, and whether the backend's T is an expectation.
CASES = (("b2-pp", "pp", False), ("b2-ptp", "ptp", False), ("b3-pp", "pp", True), ("b3-ptp", "ptp", True))
#: The uniform catalyst width b of each catalyticB2 case at each epoch delta, in `DELTA_VALUES` order. `uniform_b`
#: makes b a function of delta, so the catalyst preparation is per-delta and one bank cannot serve four precisions.
B2_WIDTH_BY_DELTA = {"b2-pp": (28, 33, 34, 41), "b2-ptp": (26, 31, 33, 39)}

#: sha256 of the canonical DEFAULT export price of the two standalone policies on their own board at every
#: delta (no primitive, no measured readout), captured at 792464f BEFORE this overlay existed. The overlay
#: adds a file and nothing else, so this digest must not move; the per-case map names which price moved if it
#: ever does. `test_the_red_perturbation_moves_the_baseline` proves the digest is sensitive.
BASELINE_DIGEST = "36f1945013af84ba4326e3e1cd4dc8d21c05e0c45dfdc5b4675e435eba6dbdd1"
BASELINE_CASES = {
    "pp/1e-2": "21e8d9bbc84950d6ef86e8202391cda5cd0dd70134eaeeeb0126f2cc77223242",
    "pp/3e-4": "1a09aef5c2460cf9a7a1798bc2b12992a56c6c85395a95464a890094803b6b06",
    "pp/1e-4": "21ed9211454b70efc7bfe1d5f4a686b6143a9ddeecc3a10271e52893f942f5db",
    "pp/1e-6": "76e7f2d0656b734d22622d36325add97fd24066dfb1c2ea070174fb534d00390",
    "ptp/1e-2": "9ca3160f18a97f12bbcb7389b12c0a8cdfef0fa4a9ccf69865f04e70ebe3a0fc",
    "ptp/3e-4": "e975efc5981ea3a35f5166d7099d120bf7153b554616b1e097abcd7f39c3b794",
    "ptp/1e-4": "20f948a05475c2ab5be72779088a4050ba7fca955eb4004dbc9fb0a355904002",
    "ptp/1e-6": "c52ff555718c1395575b476e8fbd17a1d2c6bcd62cc9f430eab5909894575eb0",
}


@pytest.fixture(scope="module")
def receipts():
    return json.loads(gzip.decompress((DATA / "primitive_receipts_80d7b6b.json.gz").read_bytes()))


def _policy(document, board):
    from intake_v2 import policy_from_document, policy_from_ptp_document

    convert = policy_from_document if board == "pp" else policy_from_ptp_document
    return convert(engine.load(), document)


def _ledger(receipt, delta_key):
    return receipt["receipt"]["standaloneLedger"]["deltas"][str(Fraction(str(DELTA_VALUES[delta_key])))]


def _preparation(receipt, delta_key):
    """The attested catalyst words, for catalyticB2 only."""
    return _ledger(receipt, delta_key).get("prep_words")


@pytest.fixture(scope="module")
def priced(receipts):
    """The DEFAULT export price of each standalone policy on its own board, at every delta, priced once."""
    out = {}
    for name, board in (("b2-pp", "pp"), ("b2-ptp", "ptp")):
        policy = _policy(receipts[name]["compiled"], board)
        for key in DELTA_VALUES:
            out[f"{board}/{key}"] = pricing.price_views(policy, f"XA baseline {board}", key, [board])[board]
    return out


def _price(receipts, priced, name, board, delta_key="1e-2"):
    receipt = receipts[name]
    return overlay.project(_policy(receipt["compiled"], board), priced[f"{board}/{delta_key}"],
                           receipt["compiled"]["submitted_primitive"],
                           preparation=_preparation(receipt, delta_key))


# --------------------------------------------------------------------- the pinned contract (no engine)

def test_pi_up_is_a_rational_upper_bound_for_pi():
    assert overlay.PI_UP > Fraction(math.pi) and overlay.PI_UP - Fraction(math.pi) < Fraction(1, 10 ** 15)


def test_only_the_two_standalone_primitives_are_priced_and_the_default_is_refused():
    # `Shor.PrimitiveSynthesis.attest` labels catalyticB2 and adaptiveB3 only; rossSelinger stamps nothing.
    assert overlay.PRIMITIVE_OF == {"catalytic-b2": "catalyticB2", "adaptive-b3": "adaptiveB3"}
    assert overlay.DEFAULT_PRIMITIVE == "rossSelinger" and overlay.DEFAULT_PRIMITIVE not in overlay.PRIMITIVE_OF.values()


def test_rule_fingerprint_is_this_module():
    source = Path(overlay.__file__).read_bytes()
    assert overlay.rule_fingerprint() == sha256(source).hexdigest()
    assert overlay.EPOCH.encode() in source


def test_the_pinned_b3_atlas_is_the_recorded_one(receipts):
    for name in ("b3-pp", "b3-ptp"):
        assert receipts[name]["receipt"]["algorithm"] == overlay.B3_ATLAS
    assert Fraction(overlay.B3_ATLAS["phaseExpectedTUpper"]) == Fraction(114001, 100)


def test_the_receipt_fixture_is_the_recorded_local_qualification(receipts):
    for name, board, _ in CASES:
        receipt = receipts[name]["receipt"]
        assert receipt["epoch"] == overlay.EPOCH and receipt["verifier"]["base"] == overlay.VERIFIER_PIN
        # The committed tree of 80d7b6b (`verifier_snapshot` of a clean checkout), not a local dirty byte lock.
        assert receipt["verifier"]["tree_sha256"] == "35eeaf1d365cbe25191366ef72cea18854b1d35340b9af7d92d1230b3daa3c59"
        assert receipt["engineRevision"] == "677cc2e05ab178afff8dc632ff56058a8196a116"
        assert receipt["localOnly"] is True and receipt["publishable"] is False
        assert receipt["selection"]["scope"] == board
        assert sha256_hex(receipts[name]["compiled"]) == receipt["compiledDocumentSha256"]


def test_the_primitive_verifier_pin_is_the_operational_pin():
    """The qualification route compiles only in a clean checkout whose HEAD is VERIFIER_PIN
    (`intake_v2._pinned_verifier_or_refused`), and the verifier checkout is provisioned at the operational pin. While
    the two differed every primitive qualification was refused against the shipped checkout."""
    trees = json.loads((Path(__file__).resolve().parents[2] / "policy-trees.json").read_text())
    assert overlay.VERIFIER_PIN == trees["forshor"]["ref"] == "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"


# --------------------------------------------------------------------- the four recorded prices

@pytest.mark.slow
@pytest.mark.parametrize("name,board,adaptive", CASES)
def test_the_recorded_T_and_qubits_reproduce_from_the_compiled_export(receipts, priced, name, board, adaptive):
    recorded = _ledger(receipts[name], "1e-2")
    want_t = int(recorded["expected_T_upper"]) if adaptive else recorded["uniform_contract_candidate"]["T_upper"]
    want_q = recorded["serial_Q_upper"] if adaptive else recorded["uniform_contract_candidate"]["serial_Q_upper"]
    out = _price(receipts, priced, name, board)
    assert out["rankedT"] == want_t, f"{name}: priced {out['rankedT']} T against a recorded {want_t} T"
    assert out["qubits"] == want_q, f"{name}: priced {out['qubits']} qubits against a recorded {want_q}"
    assert out["sites"] == receipts[name]["receipt"]["standaloneLedger"].get(
        "sites_checked_in_tightest_emission", recorded.get("sites"))
    if not adaptive:
        assert out["arithmeticT"] == recorded["arithmetic_T_frozen_currency"]
        assert out["arithmeticMeasurements"] == recorded["arithmetic_measurements_model"]
        assert out["detail"]["prepT"] == recorded["prep_T"]
        for key, recorded_key in (("b", "b"), ("roundingUpper", "rounding_upper"),
                                  ("prepUpper", "prep_upper"), ("halfDiamondUpper", "half_diamond_upper")):
            assert str(out["detail"][key]) == str(recorded["uniform_contract_candidate"][recorded_key])
    else:
        assert out["detail"]["probeMeasurementsResets"] == recorded["probe_measurements_resets_upper"]
        assert out["detail"]["atlasSha256"] == receipts[name]["receipt"]["algorithm"]["atlasSha256"]


@pytest.mark.slow
@pytest.mark.parametrize("name,board", (("b2-pp", "pp"), ("b2-ptp", "ptp")))
def test_catalytic_b2_reproduces_the_recorded_price_at_every_delta(receipts, priced, name, board):
    for key in DELTA_VALUES:
        recorded = _ledger(receipts[name], key)["uniform_contract_candidate"]
        out = _price(receipts, priced, name, board, key)
        assert (out["detail"]["b"], out["rankedT"], out["qubits"]) == (
            recorded["b"], recorded["T_upper"], recorded["serial_Q_upper"]), f"{name} at {key}"
        assert out["detail"]["halfDiamondUpper"] == recorded["half_diamond_upper"]


@pytest.mark.slow
@pytest.mark.parametrize("name,board", (("b2-pp", "pp"), ("b2-ptp", "ptp")))
def test_the_catalyst_bank_is_per_delta_and_a_delta_without_its_own_bank_is_refused(receipts, priced, name, board):
    """b is a function of delta, so each delta needs its own attested bank. A bank attested for another delta, or no
    bank at all (what `request._build_request` passes for a delta the request carries no bank for), is REFUSED and
    never priced at the default."""
    keys = list(DELTA_VALUES)
    assert tuple(_price(receipts, priced, name, board, key)["detail"]["b"] for key in keys) == B2_WIDTH_BY_DELTA[name]
    document = receipts[name]["compiled"]
    policy, selection = _policy(document, board), document["submitted_primitive"]
    for key, b in zip(keys, B2_WIDTH_BY_DELTA[name]):
        borrowed = [_preparation(receipts[name], other) for other in keys if other != key]
        for bank in (None, *borrowed):
            with pytest.raises(ValueError, match=f"catalyticB2 on the {board} board: the catalyst preparation must be "
                                                 f"one word per resolution 4..{b}$"):
                overlay.project(policy, priced[f"{board}/{key}"], selection, preparation=bank)


@pytest.mark.slow
@pytest.mark.parametrize("name,board", (("b3-pp", "pp"), ("b3-ptp", "ptp")))
def test_the_adaptive_expectation_does_not_move_with_delta(receipts, priced, name, board):
    """The pinned atlas certifies one expected probe cost, so the B3 price is the same at every delta."""
    prices = {key: _price(receipts, priced, name, board, key) for key in DELTA_VALUES}
    recorded = {int(_ledger(receipts[name], key)["expected_T_upper"]) for key in DELTA_VALUES}
    assert len(recorded) == 1 and {p["rankedT"] for p in prices.values()} == recorded


# --------------------------------------------------------------------- deterministic vs expected

@pytest.mark.slow
@pytest.mark.parametrize("name,board,adaptive", CASES)
def test_a_deterministic_and_an_expected_T_never_share_a_field(receipts, priced, name, board, adaptive):
    out = _price(receipts, priced, name, board)
    kind = "expectedAdaptive" if adaptive else "deterministic"
    assert out["tKind"] == kind and out["rankedTKind"] == kind
    # The ranked price carries its own semantics, and the other kind's field is empty, not a second copy.
    assert out["tDeterministic"] is (None if adaptive else out["rankedT"])
    assert out["tExpectedAdaptive"] is (out["rankedT"] if adaptive else None)
    assert "T" not in out, "a bare T field would let a consumer read the number without its kind"
    assert len([v for v in (out["tDeterministic"], out["tExpectedAdaptive"]) if v is not None]) == 1


# --------------------------------------------------------------------- fail closed

@pytest.mark.slow
@pytest.mark.parametrize("backend", ["ross-selinger", "rossSelinger", "catalytic-b4", "", None])
def test_an_unsupported_primitive_is_refused_and_never_priced_at_the_default(receipts, priced, backend):
    selection = {"schema": overlay.SELECTION_SCHEMA, "scope": "pp", "backend": backend}
    with pytest.raises(ValueError, match="on the pp board: this overlay prices catalyticB2 and adaptiveB3 only"):
        overlay.project(_policy(receipts["b2-pp"]["compiled"], "pp"), priced["pp/1e-2"], selection)


@pytest.mark.slow
def test_a_selector_scoped_to_the_other_board_is_refused(receipts, priced):
    document = receipts["b2-pp"]["compiled"]
    selection = {**document["submitted_primitive"], "scope": "ptp"}
    with pytest.raises(ValueError, match="catalyticB2 on the pp board: expected exactly the attested"):
        overlay.project(_policy(document, "pp"), priced["pp/1e-2"], selection,
                        preparation=_preparation(receipts["b2-pp"], "1e-2"))


@pytest.mark.slow
def test_the_full_order_finding_board_and_a_foreign_benchmark_are_refused(receipts, priced):
    document = receipts["b2-pp"]["compiled"]
    policy, selection = _policy(document, "pp"), document["submitted_primitive"]
    prep = _preparation(receipts["b2-pp"], "1e-2")
    for view, message in (({**priced["pp/1e-2"], "mode": "full"}, "catalyticB2 on the full board"),
                          ({**priced["pp/1e-2"], "n": 1024}, "exact-width benchmark only"),
                          ({**priced["pp/1e-2"], "widths": "scan"}, "exact-width benchmark only"),
                          ({**priced["pp/1e-2"], "delta": 0.02}, "is not one of the epoch's deltas")):
        with pytest.raises(ValueError, match=message):
            overlay.project(policy, view, selection, preparation=prep)


@pytest.mark.slow
def test_a_price_for_another_policy_is_refused(receipts, priced):
    """The overlay re-walks the ladder itself and refuses to attach a price to a view of another policy."""
    with pytest.raises(ValueError, match="catalyticB2 on the pp board: the standalone layout disagrees"):
        overlay.project(_policy(receipts["b2-pp"]["compiled"], "pp"), {**priced["pp/1e-2"], "leaf_width": 11},
                        receipts["b2-pp"]["compiled"]["submitted_primitive"],
                        preparation=_preparation(receipts["b2-pp"], "1e-2"))


@pytest.mark.slow
def test_the_catalyst_preparation_is_recounted_and_cannot_be_inflated(receipts, priced):
    document = receipts["b2-pp"]["compiled"]
    policy, selection = _policy(document, "pp"), document["submitted_primitive"]
    words = _preparation(receipts["b2-pp"], "1e-2")
    priced_t = overlay.project(policy, priced["pp/1e-2"], selection, preparation=words)["detail"]["prepT"]
    assert priced_t == sum(word.count("T") for word in words.values())
    short = {k: v for k, v in list(words.items())[1:]}
    long_word = {**words, "15/16": "T" * 4096}
    named = "catalyticB2 on the pp board: "   # every refusal of a priced primitive names both
    for bank, message in ((None, named + "the catalyst preparation must be one word per resolution 4..28"),
                          (short, named + "the catalyst preparation must be one word per resolution 4..28"),
                          ({**words, "15/16": "CNOT"}, named + "a catalyst preparation word must be a Clifford"),
                          (long_word, named + r"the preparation costs \d+ T, above the 1044.0 T allowance at b = 28")):
        with pytest.raises(ValueError, match=message):
            overlay.project(policy, priced["pp/1e-2"], selection, preparation=bank)


@pytest.mark.slow
def test_the_adaptive_probe_refuses_a_resident_catalyst(receipts, priced):
    document = receipts["b3-pp"]["compiled"]
    with pytest.raises(ValueError, match="adaptiveB3 on the pp board: the adaptive probe prepares no resident"):
        overlay.project(_policy(document, "pp"), priced["pp/1e-2"], document["submitted_primitive"],
                        preparation=_preparation(receipts["b2-pp"], "1e-2"))


# --------------------------------------------------------------------- binding the compiled export

@pytest.mark.slow
@pytest.mark.parametrize("name,board,adaptive", CASES)
def test_bind_compiled_primitive_agrees_with_the_recorded_receipt(receipts, name, board, adaptive):
    receipt, document = receipts[name]["receipt"], receipts[name]["compiled"]
    bound = overlay.bind_compiled_primitive(_policy(document, board), document,
                                            verifier_pin=overlay.VERIFIER_PIN, lean_source=b"local source")
    assert bound["documentSha256"] == receipt["compiledDocumentSha256"]
    assert bound["policyDigest"] == receipt["policyDigest"]
    assert bound["sourceSha256"] == sha256(b"local source").hexdigest()
    assert bound["primitive"] == ("adaptiveB3" if adaptive else "catalyticB2") and bound["board"] == board
    assert bound["ruleSha256"] == overlay.rule_fingerprint()


@pytest.mark.slow
def test_bind_refuses_an_unpinned_verifier_a_measured_combination_and_a_scratch_negation(receipts):
    document = receipts["b2-ptp"]["compiled"]
    policy = _policy(document, "ptp")
    with pytest.raises(ValueError, match="requires the pinned verifier build"):
        overlay.bind_compiled_primitive(policy, document, verifier_pin="0" * 40, lean_source=b"")
    for broken, message in (({"submitted_readout": "semiclassical"}, "combined measured readout"),
                            ({"readout": {}}, "combined measured readout"),
                            ({"negation": "scratch"}, "unsupported primitive benchmark"),
                            ({"benchmark": {"bits": 1024}}, "unsupported primitive benchmark"),
                            ({"schema": "forshor.policy-submission/v1"}, "unsupported primitive benchmark")):
        with pytest.raises(ValueError, match=message):
            overlay.bind_compiled_primitive(policy, {**document, **broken},
                                            verifier_pin=overlay.VERIFIER_PIN, lean_source=b"")
    with pytest.raises(ValueError, match="malformed primitive export"):
        overlay.bind_compiled_primitive(policy, {k: v for k, v in document.items() if k != "submitted_primitive"},
                                        verifier_pin=overlay.VERIFIER_PIN, lean_source=b"")


# --------------------------------------------------------------------- the default path and the epoch

@pytest.mark.slow
def test_the_default_export_price_is_bit_identical_to_the_baseline(priced):
    assert {key: sha256_hex(view) for key, view in priced.items()} == BASELINE_CASES
    assert sha256_hex(priced) == BASELINE_DIGEST


@pytest.mark.slow
def test_the_red_perturbation_moves_the_baseline(priced):
    """The RED of the test above: one primitive price attached to one default view must change the digest."""
    perturbed = {**priced, "pp/1e-4": {**priced["pp/1e-4"], "primitive": {"rankedT": 26478500}}}
    assert sha256_hex(perturbed) != BASELINE_DIGEST
    assert {k for k in perturbed if sha256_hex(perturbed[k]) != BASELINE_CASES[k]} == {"pp/1e-4"}


@pytest.mark.slow
def test_the_overlay_prices_inside_the_committed_epoch():
    from export_v2 import epoch

    model = epoch.model_of()
    assert epoch_of(model) == overlay.EPOCH == "ab1f8887b1b50e56"
    assert model["dataFingerprint"] == "176ce4047f5957f1"
    assert model["engineRevision"] == "677cc2e05ab178afff8dc632ff56058a8196a116"
