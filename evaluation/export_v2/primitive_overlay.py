"""Exact standalone-board pricing for a declared synthesis primitive, using the frozen coherent engine.

Local qualification only: this module does not grant publication authority. The caller of
bind_compiled_primitive must obtain the compiled document and the catalyst preparation directly from the
trusted verifier's own invocation, not from participant-supplied JSON.

`Shor.PrimitiveSynthesis` has three constructors. `rossSelinger` is the DEFAULT and stamps nothing into the
compiled document (`PrimitiveSynthesis.attest .rossSelinger scope doc = doc` is `rfl`), so the default price
is the engine's own and never reaches this module; an unsupported primitive is REFUSED here, never priced at
the default. The two standalone primitives are priced on the PhaseProduct and PhaseTripleProduct boards:

  catalyticB2  one prepared phase-gradient catalyst threaded through the native exact-width Toom tree. Under
               the uniform angle contract every phase site pays the worst-case full-width carry path, so its
               T is DETERMINISTIC.
  adaptiveB3   repeat-until-success certified-digit probes against a pinned word atlas. Its T is an
               UNCONDITIONAL EXPECTATION, not a worst case. The two are never collapsed: a result carries at
               most one of `tDeterministic` / `tExpectedAdaptive`, and `rankedTKind` tells whatever consumes
               `rankedT` which kind of number it got.

The arithmetic is the engine's own: the frozen table call graph at exact widths plus the Toom leaf product
cells, in frozen currency. Only the phase census, the catalyst and the peak are added from outside.

The hook this module needs in `pricing.price_views`, mirroring the `measured` hook, is one hunk in the
single-product branch (the standalone boards; the full order-finding board takes no primitive):

    def price_views(policy, title, delta_key, boards, *, measured=False, primitive=None, preparation=None):
    ...
             view = eng.policy_view.view(P, N, delta, title)
    +        if primitive:
    +            from .primitive_overlay import project
    +            view["primitive"] = project(P, view, primitive, preparation=preparation)
             view["tables"] = _all_tables(eng, P)

`primitive` defaults to None, so the default `rossSelinger` path never enters this module and its price is
bit-identical (`pytests/test_primitive_overlay.py::test_the_default_export_price_is_bit_identical_to_the_baseline`).
"""
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from . import engine, epoch
from .canon import epoch_of, policy_digest, sha256_hex
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "standalone-primitive-pricing-v1"
VERIFIER_PIN = "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"
SELECTION_SCHEMA = "forshor.primitive-selection/v1"
BOARD_SCHEMA = {"pp": "forshor.policy-submission/v1", "ptp": "forshor.ptp-policy-submission/v1"}
#: The compiled `submitted_primitive.backend` label of each `Shor.PrimitiveSynthesis` constructor that is
#: priced here (`PrimitiveSynthesis.attest`). `rossSelinger` has no label: it stamps nothing.
PRIMITIVE_OF = {"catalytic-b2": "catalyticB2", "adaptive-b3": "adaptiveB3"}
DEFAULT_PRIMITIVE = "rossSelinger"
#: Rational upper bound for pi (pi < 3.14159265358979323846264338328), so the rounding and preparation
#: allowances below are exact Fractions and never a float that rounds the wrong way.
PI_UP = Fraction("3.14159265358979323846264338328")
#: The uniform angle contract of the catalytic B2 standalone binding: one b-bit catalyst, b - 3 resident
#: slots, every site charged the full-width carry path.
B2_RULE = "standalone-angle-uniform-b2/v1"
#: The pinned adaptive B3 atlas. Board-independent, delta-independent: the expected T of one probe site is a
#: property of the atlas, so the whole B3 price moves only when the atlas does.
B3_ATLAS = {"rule": "standalone-b3-certified-digits/v1",
            "atlasSha256": "30fd0481b413753e564c48fc57ca430820447d312929cf388bdf70450d258a41",
            "siteAllowance": "1/225000000000", "literalWordLengthUpper": 2931,
            "phaseExpectedTUpper": "114001/100"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _refuse(primitive, board, message):
    """Fail closed, naming the primitive and the board; never fall back to the default price."""
    raise ValueError(f"{primitive} on the {board} board: {message}")


def rule_fingerprint():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def uniform_b(sites, delta):
    """The uniform catalyst width: the smallest b >= 4 whose per-site rounding over every site fits half the
    rotation pool. `delta` is exact; the pool fraction is the engine's own `currency.ROTATION_FRACTION`."""
    eng = engine.load()
    half_pool = delta * Fraction(str(eng.tcost.currency.ROTATION_FRACTION)) / 2
    for b in range(4, 65):
        if PI_UP * sites / (1 << b) <= half_pool:
            return b, half_pool
    raise ValueError(f"no uniform catalyst width within 64 bits for {sites} sites at delta {delta}")


def _schedule(eng, policy, n):
    """The standalone serial depth-first layout of one root at exact widths: frozen-currency arithmetic, the
    phase-site census and the live-slot peak excluding the catalyst. The table levels are the engine's own
    attested call graph; the Toom leaf charges 4 Ands per product cell (compute and inverse, 2 each)."""
    from qualtran.resource_counting import QECGatesCost, get_cost_value

    tb, currency = eng.tcost.bloqs, eng.tcost.currency
    sides, w, calls = tb.arity(policy.mode), n, 1
    table_t = table_meas = parent_extra = table_peak = 0
    while (step := policy.step(w)) is not None:
        table, child_w = step
        for bloq, mult in tb._level_call_graph(policy.mode, table, w, child_w, ()).items():
            g = get_cost_value(bloq, QECGatesCost())
            _require(not g.rotation, "standalone arithmetic must not contain an uncharged rotation")
            table_t += calls * mult * int(g.t + currency.TS_PER_TOFFOLI * (g.toffoli + g.and_bloq + g.cswap))
            table_meas += calls * mult * int(g.measurement)
        parent_extra += sides * (table.k * child_w - w)
        ops = {op[0] for op in table.ops}
        # Equal-width Add/Subtract use child_w - 1 carry slots; NegateW's pinned incrementer uses one clean ancilla.
        table_peak = max(table_peak, parent_extra + max(child_w - 1 if "addScaled" in ops else 0, int("negate" in ops)))
        calls *= table.q
        w = child_w
    # Each SignedProduct row holds its right operand's gated copy and one sign bit, plus the Add carries;
    # PhaseTripleProduct's second product also holds the first product live.
    products, cells = (2 * w, w * w) if sides == 2 else (5 * w, 3 * w * w)
    sites = calls * (2 * w if sides == 2 else 3 * w - 2)
    return {"sides": sides, "leafCalls": calls, "leafWidth": w, "sites": sites,
            "arithmeticT": table_t + calls * currency.TS_PER_TOFFOLI * 4 * cells,
            "arithmeticMeasurements": table_meas + calls * 4 * cells,
            "liveBase": sides * n, "leafProducts": products, "leafHold": products + 2 * (sides - 1) * w + 1,
            "tablePeak": table_peak, "parentExtra": parent_extra}


def _peak(sched, carry_slots):
    """The serial live-slot upper bound: the root registers, the resident catalyst (`carry_slots`, 0 for the
    adaptive probe, which measures and resets) and the deepest of the table levels and the leaf."""
    leaf_extra = max(sched["leafHold"], sched["leafProducts"] + carry_slots)
    return sched["liveBase"] + carry_slots + max(sched["tablePeak"], sched["parentExtra"] + leaf_extra)


def _prep_t(b, prep_budget, words):
    """The catalyst preparation's T, recounted from the attested Clifford+T words: `PhaseGradientState(b)` is
    one rotation per resolution 4..b. Angle correctness belongs to the trusted qualification; what is checked
    here is that the words are the right ones to charge for and that they fit the engine's own Ross-Selinger
    allowance for this b and budget."""
    from fullalg.l2q_full.catalysis import spec

    _require(isinstance(words, dict) and set(words) == {f"{(1 << j) - 1}/{1 << j}" for j in range(4, b + 1)},
             f"the catalyst preparation must be one word per resolution 4..{b}")
    _require(all(isinstance(word, str) and set(word) <= set("HSTX") for word in words.values()),
             "a catalyst preparation word must be a Clifford+T word over H, S, T, X")
    prep_t = sum(word.count("T") for word in words.values())
    allowance = spec.catalyst_prep_T(b, float(prep_budget))
    _require(prep_t <= allowance, f"the preparation costs {prep_t} T, above the {allowance:.1f} T allowance at b = {b}")
    return prep_t


def _catalytic_b2(sched, delta):
    """Deterministic T: frozen arithmetic, every site on the worst-case carry path, plus one preparation."""
    from fullalg.l2q_full.catalysis import spec

    b, prep_budget = uniform_b(sched["sites"], delta)
    phase_t = sched["sites"] * (spec.AND_T * (b - 3) + 1)
    rounding = PI_UP * sched["sites"] / (1 << b)
    return sched["arithmeticT"] + phase_t, _peak(sched, b - 3), {
        "angleRule": B2_RULE, "b": b, "residentSlots": b - 3, "phaseT": phase_t,
        "roundingUpper": str(rounding), "prepUpper": str(prep_budget),
        "halfDiamondUpper": str(rounding + prep_budget)}


def _adaptive_b3(sched):
    """Unconditional EXPECTED T: frozen arithmetic plus the atlas's expected probe cost at every site. The
    probe measures and resets, so no catalyst stays resident. The adaptive error certificate is the trusted
    qualification's, not this module's; only T and Q are priced here."""
    per_site = Fraction(B3_ATLAS["phaseExpectedTUpper"])
    phase_t = per_site * sched["sites"]
    _require(phase_t.denominator == 1, "the atlas's expected probe cost does not close over this site census")
    return sched["arithmeticT"] + int(phase_t), _peak(sched, 0), {
        **B3_ATLAS, "phaseExpectedT": int(phase_t), "probeMeasurementsResets": sched["sites"]}


def project(policy, view, selection, *, preparation=None):
    """Price one declared primitive against the frozen default view of the same policy; the default price in
    `view` is not touched. `selection` is exactly the compiled `submitted_primitive` object; `preparation` is
    the attested catalyst words, required by catalyticB2 and refused for adaptiveB3."""
    eng = engine.load()
    backend = selection.get("backend") if isinstance(selection, dict) else None
    primitive = PRIMITIVE_OF.get(backend, DEFAULT_PRIMITIVE if backend is None else f"unknown primitive {backend!r}")
    board = {eng.tables.PAIR: "pp", eng.tables.TRIPLE: "ptp"}.get(view.get("mode"), str(view.get("mode")))
    if primitive not in PRIMITIVE_OF.values():
        _refuse(primitive, board, "this overlay prices catalyticB2 and adaptiveB3 only; the default primitive "
                                  "is the engine's own price and an unsupported one is refused, not defaulted")
    if board not in BOARD_SCHEMA:
        _refuse(primitive, board, "a standalone primitive is priced on the PhaseProduct and PhaseTripleProduct boards only")
    if selection != {"schema": SELECTION_SCHEMA, "scope": board, "backend": backend}:
        _refuse(primitive, board, f"expected exactly the attested board-scoped selector for {board}")
    if epoch_of(epoch.model_of()) != EPOCH:
        _refuse(primitive, board, "the primitive overlay does not support this engine model")
    if view.get("n") != eng.fullalg.HERO_N or view.get("widths") != "exact":
        _refuse(primitive, board, "the recorded standalone science is the exact-width benchmark only")
    if view.get("delta") not in DELTA_VALUES.values():
        _refuse(primitive, board, f"delta {view.get('delta')!r} is not one of the epoch's deltas")
    sched = _schedule(eng, policy.with_widths("exact"), view["n"])
    if (sched["leafCalls"], sched["leafWidth"]) != (view.get("leaf_calls"), view.get("leaf_width")):
        _refuse(primitive, board, "the standalone layout disagrees with the priced ladder")
    if sched["sites"] != view.get("gate_counts", {}).get("rotation"):
        _refuse(primitive, board, "the arithmetic and phase occurrence ledgers disagree")
    try:
        if primitive == "catalyticB2":
            total, qubits, detail = _catalytic_b2(sched, Fraction(str(view["delta"])))
            detail["prepT"] = _prep_t(detail["b"], Fraction(detail["prepUpper"]), preparation)
            total += detail["prepT"]
            kind = "deterministic"
        else:
            _require(preparation is None, "the adaptive probe prepares no resident catalyst")
            total, qubits, detail = _adaptive_b3(sched)
            kind = "expectedAdaptive"
    except ValueError as error:   # every refusal of a priced primitive names the primitive and the board
        _refuse(primitive, board, str(error))
    return {"primitive": primitive, "backend": backend, "board": board, "pricingRule": RULE,
            "ruleSha256": rule_fingerprint(), "sites": sched["sites"], "arithmeticT": sched["arithmeticT"],
            "arithmeticMeasurements": sched["arithmeticMeasurements"], "qubits": qubits,
            "tKind": kind, "rankedT": total, "rankedTKind": kind,
            "tDeterministic": total if kind == "deterministic" else None,
            "tExpectedAdaptive": total if kind == "expectedAdaptive" else None, "detail": detail}


def bind_compiled_primitive(policy, document, *, verifier_pin, lean_source):
    """Bind one checked standalone primitive export; no sidecar or selectable pricing parameters."""
    from intake_v2 import MAX_FORSHOR_K, MAX_POLICY_BANDS, policy_from_document, policy_from_ptp_document

    eng = engine.load()
    _require(verifier_pin == VERIFIER_PIN, "the primitive overlay requires the pinned verifier build")
    _require(epoch_of(epoch.model_of()) == EPOCH, "the primitive overlay does not support this engine model")
    try:
        selection = document["submitted_primitive"]
        backend = selection["backend"]
        primitive = PRIMITIVE_OF.get(backend, DEFAULT_PRIMITIVE if backend is None else f"unknown primitive {backend!r}")
        board = selection["scope"]
        if primitive not in PRIMITIVE_OF.values() or board not in BOARD_SCHEMA:
            _refuse(primitive, board, "unsupported standalone primitive selector")
        if selection != {"schema": SELECTION_SCHEMA, "scope": board, "backend": backend}:
            _refuse(primitive, board, "expected exactly the attested board-scoped selector")
        if document.get("schema") != BOARD_SCHEMA[board] or document.get("negation") != "oneQubit" \
                or document.get("benchmark", {}).get("bits") != eng.fullalg.HERO_N:
            _refuse(primitive, board, "unsupported primitive benchmark, negation or document schema")
        if "readout" in document or document.get("submitted_readout", "coherent") != "coherent":
            _refuse(primitive, board, "a combined measured readout and primitive qualification is not admitted")
        if board == "pp" and document.get("ir", {}).get("width_rule", {}).get("rule") != "forshor.exact-width/v1":
            _refuse(primitive, board, "expected a checked exact-width PhaseProduct export")
        bands = document["bands"]
        _require(isinstance(bands, list) and 1 <= len(bands) <= MAX_POLICY_BANDS and all(
            type(b["k"]) is int and 2 <= b["k"] <= MAX_FORSHOR_K and type(b["threshold"]) is int and b["threshold"] >= 0
            for b in bands), "invalid primitive policy bands")
        convert = policy_from_document if board == "pp" else policy_from_ptp_document
        digest = policy_digest(policy.to_json())
        _require(policy_digest(convert(eng, document).to_json()) == digest, "primitive export policy mismatch")
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError(f"malformed primitive export: {error}") from error
    return {"primitive": primitive, "backend": backend, "board": board, "pricingRule": RULE,
            "ruleSha256": rule_fingerprint(), "verifierPin": verifier_pin, "documentSha256": sha256_hex(document),
            "policyDigest": digest, "sourceSha256": sha256(lean_source).hexdigest()}
