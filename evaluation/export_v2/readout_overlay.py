"""Exact reference-multiplier readout, using the frozen coherent engine.

Local qualification only: this module does not grant publication authority.
The caller of bind_compiled_readout must obtain the document directly from the
trusted verifier for the exact source, not from participant-supplied JSON.
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path

from . import engine, epoch
from .canon import epoch_of, policy_digest, sha256_hex
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "exact-reference-readout-v1"
# The OPERATIONAL pin (`evaluation/policy-trees.json` -> `forshor.ref`): the checkout a submission is compiled in, and
# the one `intake_v2._forshor_build` requires `git rev-parse HEAD` to equal before it will qualify a readout. These two
# must never diverge -- `pytests/test_operational_pin.py::test_the_readout_verifier_pin_is_the_operational_pin` is the
# guard. The pin is a literal here on purpose: `rule_fingerprint()` hashes this file, so the published `ruleSha256`
# covers which verifier the evidence names.
#
# EDITING THIS FILE ROTATES THE PUBLISHED PIN -- a comment counts, including this one. `rule_fingerprint()` is the
# sha256 of these bytes, every readout panel carries that digest as `ruleSha256`, and the consuming contract pins the
# exact value as a literal constant rather than recomputing it. So a one-byte edit here rejects every readout-bearing
# panel until the consumer is updated in the SAME change: this is a two-repository change, never a drive-by.
# `pytests/test_operational_pin.py` transcribes the current digest independently and is the guard that fires the
# moment the two drift -- if it is red, the published pin is stale, not wrong.
VERIFIER_PIN = "80d7b6b2d0e696df46f1fa6ed928cbd1d9d7d6c5"
MEASURED_SCHEMA = "forshor.policy-submission-semiclassical/v1"
SCHEDULE = {
    "schema": "forshor.readout-loop/v1", "mode": "semiclassical",
    "initialization": "initY1", "control": "referenceControl", "rounds": "referenceXWidth",
    "steps": ["H", "multiply", "ffPhase", "H", "measureReset"], "bit_order": "lsb_first", "angle_unit": "pi",
    "record": "rec[i] = outcome (0 or 1) of the i-th measureReset node executed (absolute slot, "
              "oldest first, 0-based); each measureReset appends one slot",
    "ff_angle_rule": "ffPhase(q, j) applies Phase(q, theta) with theta = -(sum_{i=0}^{j-1} rec[i] / 2^(j-i)) "
                     "in units of pi (a dyadic rational; a missing slot counts as 0)",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def rule_fingerprint():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _terms(term):
    if isinstance(term, tuple):
        yield term
        for child in term:
            yield from _terms(child)


def _reference_round(fa, native, core):
    """Check retained non-policy templates and the symbolic multiply census against the frozen reference."""
    import sympy as sp

    sy, pr = fa.sy, fa.pr
    ref = pr._BALANCED_DOCS[core.balanced_doc]
    for name in (*sy.BALANCED_TEMPLATES, "qft", "naive_leaf", "naive_cleaf"):
        actual, expected = native.template(name), ref.template(name)
        key = lambda t: (t.wParams, t.aParams, t.rParams, t.body)
        _require(key(actual) == key(expected), f"compiled {name} differs from the frozen reference")
    rnd = native.template("readout_round")
    _require(rnd.wParams == ("a", "N", "rounds", "j", "yW", "yCap", "workW", "workCap", "scratchW", "scratchCap")
             and not rnd.aParams and rnd.rParams == ("ctrl", "y", "work", "scratch", "flag"),
             "invalid measured round signature")
    terms = list(_terms(rnd.body))
    power = pr.ir_v1.parse_w("modpow(a, ((rounds - 1) - j), N)")
    _require({t for t in terms if t[:2] == ("opaque", "modpow")} == {power}, "invalid measured multiplier order")
    _require({t[4][0] for t in terms if t[:2] in (("call", "balanced_cphase_product"), ("call", "cphase_product"))}
             == {("var", "ctrl")}, "the round must use the recycled control")
    n = core.nbits
    conv = sy.Convention.for_modulus(1 << (n - 1), fa.precision_m(n))
    widths = sy.layout_widths(conv, n)
    rounds, j = widths["xW"], sp.Symbol("_j")
    caps = {k + "Cap": sp.Symbol(f"cap_{k}Cap", positive=True, integer=True) for k in ("y", "work", "scratch")}
    regs = {k: sy.SReg(widths[k + "W"], caps[k + "Cap"]) for k in ("y", "work", "scratch")}
    regs.update(ctrl=sy.SReg(sp.Integer(1), sp.Integer(0)), flag=sy.SReg(sp.Integer(1), sp.Integer(0)))
    variables = {"a": sp.Symbol("a"), "N": sp.Symbol("N"), "rounds": rounds, "j": j, **widths, **caps}
    walker = sy._Walker(native, conv)
    counts = walker.node({"w": variables, "r": regs}, rnd.body).simplified()
    walker._require_var_free(counts, j, "j")
    counts = counts.at(n)
    sites = Counter(dict(core.raw_sites))
    _require(sites.pop(("qft", rounds, 0), None) == 1, "reference lacks its unique exponent IQFT")
    for name, residual in (("sites", sites), *((k, dict(getattr(core, k))) for k in
                            ("add_scaled", "add_scaled_neg", "negate"))):
        _require({k: rounds * count for k, count in counts[name].items() if count} == dict(residual),
                 f"compiled round has different {name} from the reference")
    for name, frame in (("h", rounds), ("x", 1), ("cnot", 0), ("toffoli", 0)):
        _require(rounds * counts[name] + frame == getattr(core, name), f"compiled round has different {name} count")


def bind_compiled_readout(policy, document, *, verifier_pin, lean_source):
    """Bind one checked measured export; no sidecar or selectable pricing parameters."""
    from intake_v2 import MAX_FORSHOR_K, MAX_POLICY_BANDS, policy_from_document

    eng = engine.load()
    fa = eng.fullalg
    _require(verifier_pin == VERIFIER_PIN, "readout requires the pinned verifier build")
    _require(epoch_of(epoch.model_of()) == EPOCH, "readout overlay does not support this engine model")
    try:
        _require(document["schema"] == MEASURED_SCHEMA and document["submitted_readout"] == "semiclassical"
                 and document["readout"] == SCHEDULE, "missing compiled measured readout or incorrect loop schedule")
        _require(document["negation"] == "oneQubit" and document["benchmark"]["bits"] == fa.HERO_N
                 and document["benchmark"]["trial_count"] == str(fa.trial_count_2048())
                 and document["precision"]["m"] == str(fa.precision_m(fa.HERO_N)), "unsupported measured benchmark")
        bands = document["bands"]
        _require(isinstance(bands, list) and 1 <= len(bands) <= MAX_POLICY_BANDS and all(
            type(b["k"]) is int and 2 <= b["k"] <= MAX_FORSHOR_K and type(b["threshold"]) is int and b["threshold"] >= 0
            for b in bands), "invalid measured policy bands")
        digest = policy_digest(policy.to_json())
        _require(policy_digest(policy_from_document(eng, document).to_json()) == digest, "measured export policy mismatch")
        ir = document["ir"]
        _require(ir["schema"] == fa.pr.ir_v1.SCHEMA and ir["entry"] == "readout_round"
                 and ir["width_rule"]["rule"] == "forshor.exact-width/v1", "expected exact measured IR")
        raw_names = [t["name"] for t in ir["templates"]]
        names = {"phase_product", "cphase_product", "qft", "naive_leaf", "naive_cleaf", "readout_round",
                 *fa.sy.BALANCED_TEMPLATES, *(f"{family}_{i}" for family in ("phase_product", "cphase_product") for i in range(len(bands)))}
        _require(len(raw_names) == len(names) and set(raw_names) == names, "unexpected measured templates")
        _reference_round(fa, fa.pr.ir_v1.parse_doc(ir), fa.core(fa.HERO_N))
    except (KeyError, TypeError, AttributeError, RecursionError, IndexError) as error:
        raise ValueError(f"malformed measured export: {error}") from error
    return {"mode": "semiclassical", "pricingRule": RULE, "ruleSha256": rule_fingerprint(),
            "verifierPin": verifier_pin, "irSha256": sha256_hex(document),
            "policyDigest": digest, "sourceSha256": sha256(lean_source).hexdigest()}


def project(policy, coherent):
    """Adjust the frozen detailed coherent result; the coherent comparison stays untouched."""
    eng = engine.load()
    fa = eng.fullalg
    _require(coherent["n"] == fa.HERO_N and coherent["width_rule"] == "exact"
             and coherent["negate"] == fa.HEADLINE.negate and coherent["delta"] in DELTA_VALUES.values(),
             "readout overlay requires the current exact reference price")
    lp = fa.to_l2q_full(policy, "exact")
    w = coherent["widths"]
    rounds = w["xW"]
    iqfts = [p for p in coherent["parts"] if p["site"] == "qft" and p["xw"] == rounds and p["count"] == 1]
    _require(len(iqfts) == 1, "coherent reference needs one exponent IQFT")
    iqft = fa.cc.split(fa.pr.PolicyQFT(lp, rounds, fa.HEADLINE))
    _require(iqfts[0]["rotations"] == iqft.rot and iqfts[0]["T"] == iqft.base + iqft.rot * coherent["T_per_rotation"],
             "coherent exponent IQFT disagrees with the exact cost split")
    base = coherent["T_base"] - iqft.base
    rotation_count = coherent["rotations"] - iqft.rot + rounds - 1
    _require(base >= 0 and rotation_count >= rounds - 1 and rounds > 1, "invalid measured gate census")
    delta = coherent["delta"]
    rate = fa.sc.ts_per_rotation(delta, rotation_count)
    peak = max(fa.ppk.modmul_phase_peaks(lp, w["yW"], w["workW"], w["scratchW"], coherent["negate"]))
    return {"T": base + rotation_count * rate, "TBase": int(base), "rotations": int(rotation_count),
            "tPerRotation": rate, "eps": fa.sc.rotation_eps(delta, rotation_count), "rounds": rounds,
            "iqftTBase": int(iqft.base), "iqftRotations": int(iqft.rot), "peakExtra": peak,
            "qubits": 1 + coherent["peak_base_registers"] - rounds + peak}
