"""Tier 3b: the Step 4 adaptive and controlled Steps 1/5 sampled costings, and the one pool they share.

Lane XL. These pin the three honesty rules on both new lanes, the estimate-vs-bound structure, and the
pool-partition arithmetic that decides which combinations may be priced and which are refused.

The contracts are byte-identical mirrors of the research lane's own
(`postprocess/synthesis/qualification/{step4,controlled}_sampled_contract.json`, branch
`local/prepush-XC-tier3b`), and the frozen-contract tests below are the publishing-side copies of
`postprocess/synthesis/tests/test_phase_fallback_sampling.py`'s, so the two cannot drift apart silently.
"""
from fractions import Fraction
import json

import pytest

from export_v2 import controlled_sampled_fallback as cc
from export_v2 import rotation_pool
from export_v2 import sampled_fallback
from export_v2 import step4_sampled_fallback as s4


# --------------------------------------------------------------------------- fixtures


def directions(count, *, denominator=1 << 20, multiplicity=1, signed=0):
    """`count` distinct non-eighth turns in each mirrored direction, as the two census mappings."""
    turns = {Fraction(2 * i + 1, denominator): multiplicity for i in range(count)}
    signs = {t: signed for t in turns}
    return turns, signs


def flat_worker(value=Fraction(100)):
    def worker(job):
        turn, eps = job[0], job[1]
        return turn, (f"word:{turn}", None), None, (value, value)
    return worker


def ramp_worker(step=Fraction(1), base=Fraction(100)):
    def worker(job):
        turn = job[0]
        v = base + step * (turn.numerator % 97)
        return turn, (f"word:{turn}", None), None, (v, v)
    return worker


def site_worker(value=Fraction(100)):
    """The controlled worker shape: (turn, eps, gradient) -> (turn, expected-T upper)."""
    def worker(job):
        return job[0], value
    return worker


def ramp_site_worker(step=Fraction(1), base=Fraction(100)):
    def worker(job):
        turn = job[0]
        return turn, base + step * (turn.numerator % 97)
    return worker


#: A pool that partitions exactly, in the shape every reference plan uses: QFT ceilings + gradient
#: preparation + per-site allowance * synthesized sites == pool. Built so the numbers are exact.
POOL = Fraction(1, 200)
CEILINGS = (Fraction(1, 4000), Fraction(1, 4000), Fraction(1, 4000))     # 15% of the pool
PREP = Fraction(1, 2000)                                                 # 10% of the pool
RUN_SITES = 1_000_000
EPS = (POOL - sum(CEILINGS) - PREP) / RUN_SITES                          # the remaining 75%


def budget(**over):
    return {"pool": POOL, "qft_ceilings": CEILINGS, "gradient_prepare": PREP, "run_sites": RUN_SITES,
            **over}


def step4_contract(**over):
    return {**s4.load_contract(), **over}


def controlled_contract(**over):
    return {**cc.load_contract(), **over}


def space(step, calls=40, weights=50, **binding):
    return cc.IndexSpace(step, calls, weights, {"reference_plan_sha256": "a" * 64, **binding})


def turn_fns(denominator=1 << 20):
    def make(step):
        def turn_of(call, weight):
            return Fraction(2 * (call * 97 + weight * 7 + step) + 1, denominator)
        return turn_of
    return {step: make(step) for step in cc.STEPS}


def price_controlled(spaces=None, **over):
    spaces = spaces or {step: space(step) for step in cc.STEPS}
    kwargs = {"worker": site_worker(), "gradient": 38, "workers": 1,
              "contract": controlled_contract(sample_total=100), **budget(), **over}
    return cc.cost_policy_controlled(spaces, turn_fns(), EPS, **kwargs)


def price_step4(count=400, **over):
    turns, signs = over.pop("census", None) or s4.census_of_directions(*directions(count)[:1],
                                                                      *directions(count)[:1])
    kwargs = {"worker": flat_worker(), "workers": 1,
              "contract": step4_contract(sample_total=100, round_size=50), **budget(), **over}
    return s4.cost_policy_step4(turns, signs, EPS, **kwargs)


# --------------------------------------------------------------------------- the contracts ship and are frozen

RESEARCH_STEP4 = {"schema": "forshor.step4-sampled-contract/v1", "sample_total": 120000, "range_T": "80",
                  "conf": 0.95, "round_size": 500, "budget_seconds": 3600,
                  "max_halfwidth_fraction": "1/10000",
                  "salt_hex": "09d29cc1369a33791d9c830b29b612dd2fd425f2d70ab01a671dff55a41c99d7"}
RESEARCH_CONTROLLED = {"schema": "forshor.controlled-sampled-contract/v1", "sample_total": 32000,
                       "range_T": "74", "conf": 0.95, "budget_seconds": 1800,
                       "max_halfwidth_fraction": "1/10000",
                       "salt_hex": "fb06288074e78cc4281762e4a561971475a38415d062e4d3087c037afdf542a2"}


@pytest.mark.parametrize("module,frozen", [(s4, RESEARCH_STEP4), (cc, RESEARCH_CONTROLLED)],
                         ids=["step4", "controlled"])
def test_the_contract_ships_with_the_module_and_is_the_research_lanes_own(module, frozen):
    """Frozen BEFORE any priced run, so the sample cannot be chosen after seeing the answer. The values are
    the research lane's; a mismatch here means the mirror drifted from the contract XC calibrated."""
    c = module.load_contract()
    assert module.CONTRACT.exists()
    for key, value in frozen.items():
        assert c[key] == value, f"{key} drifted from the research contract"
    assert len(bytes.fromhex(c["salt_hex"])) == 32
    assert c["calibration"]["gate_halfwidth"] > 0


def test_the_step4_calibration_shows_the_cap_meets_its_gate():
    """120,000 of 495,000 turns: the calibrated half-width must be inside the gate, or the cap is not the
    cap the contract claims."""
    cal = s4.load_contract()["calibration"]
    assert cal["turns"] == 495000 and cal["halfwidth_by_sample_total"]["120000"] < cal["gate_halfwidth"]
    assert cal["expected_cap_within_budget"] is True


@pytest.mark.parametrize("bad", [{"schema": "other/v1"}, {"salt_hex": "00"}, {"sample_total": 1},
                                 {"conf": 0.9}, {"range_T": "0"}, {"budget_seconds": 0}])
@pytest.mark.parametrize("module", [s4, cc], ids=["step4", "controlled"])
def test_an_invalid_contract_is_refused(tmp_path, module, bad):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({**module.load_contract(), **bad}))
    with pytest.raises(ValueError):
        module.load_contract(path)


@pytest.mark.parametrize("module,other", [(s4, cc), (cc, s4)], ids=["step4", "controlled"])
def test_a_lane_refuses_the_other_lanes_contract(module, other):
    """The contracts are not interchangeable: the caps, ranges and salts were calibrated on different
    populations, so a lane handed the wrong one must refuse rather than price with it."""
    with pytest.raises(ValueError):
        module.load_contract(other.CONTRACT)


# --------------------------------------------------------------------------- an estimate is not a bound


def test_a_sampled_price_carries_no_bare_T_field():
    """XA's structure, on the estimate/bound axis: `tKind` plus exactly one of the two T fields. A consumer
    cannot pick up a key named `T` and rank it as if a circuit had been bound."""
    fields = sampled_fallback.t_fields("sampledEstimate", "expectedAdaptive", "94983648143")
    assert fields["tKind"] == "sampledEstimate" and fields["tSiteKind"] == "expectedAdaptive"
    assert fields["tSampledEstimateUpper"] == "94983648143" and fields["tCircuitBound"] is None
    assert fields["estimate"] is True and fields["circuit_bound"] is False
    assert "T" not in fields and "rankedT" not in fields


def test_the_two_kinds_of_number_are_structurally_uncollapsible():
    bound = sampled_fallback.t_fields("circuitBound", "deterministic", "7")
    assert bound["tCircuitBound"] == "7" and bound["tSampledEstimateUpper"] is None
    assert bound["estimate"] is False and bound["circuit_bound"] is True
    with pytest.raises(ValueError, match="unknown T kind"):
        sampled_fallback.t_fields("bound", "deterministic", "7")
    with pytest.raises(ValueError, match="unknown per-site T kind"):
        sampled_fallback.t_fields("circuitBound", "worstCase", "7")


@pytest.mark.parametrize("payload,message", [
    ({"tKind": "sampledEstimate", "tSiteKind": "expectedAdaptive", "T": 7}, "bare 'T'"),
    ({"tKind": "sampledEstimate", "tSiteKind": "expectedAdaptive", "tSampledEstimateUpper": "7",
      "tCircuitBound": "7", "estimate": True, "circuit_bound": False}, "requires exactly"),
    ({"tKind": "sampledEstimate", "tSiteKind": "expectedAdaptive", "tSampledEstimateUpper": None,
      "tCircuitBound": None, "estimate": True, "circuit_bound": False}, "requires exactly"),
    ({"tSampledEstimateUpper": "7"}, "must declare tKind"),
    ({"tKind": "sampledEstimate", "tSiteKind": "expectedAdaptive", "tSampledEstimateUpper": "7",
      "tCircuitBound": None, "estimate": False, "circuit_bound": True}, "contradict"),
])
def test_a_collapsed_or_contradictory_T_claim_is_refused(payload, message):
    with pytest.raises(ValueError, match=message):
        sampled_fallback.check_t_fields(payload)


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_both_lanes_report_an_estimate_of_an_expectation_and_say_why_no_bound_exists(lane):
    receipt = price_step4() if lane == "step4" else price_controlled()
    interval = receipt["interval"]
    assert interval["tKind"] == "sampledEstimate" and interval["tSiteKind"] == "expectedAdaptive"
    assert interval["tCircuitBound"] is None and int(interval["tSampledEstimateUpper"]) > 0
    assert interval["estimate"] is True and interval["circuit_bound"] is False
    assert "no complete cold word bank fits" in interval["no_bound_basis"] \
        or "refuses a sample by construction" in interval["no_bound_basis"]
    assert "T" not in interval


def test_every_controlled_per_step_block_declares_its_own_kind_too():
    """A summed endpoint is not a licence to ship an untyped per-step number: each block is checked."""
    receipt = price_controlled()
    assert sorted(receipt["steps"]) == ["1", "5"]
    total = sum(int(b["tSampledEstimateUpper"]) for b in receipt["steps"].values())
    assert total == int(receipt["interval"]["tSampledEstimateUpper"])
    for block in receipt["steps"].values():
        assert block["tKind"] == "sampledEstimate" and "T" not in block


# --------------------------------------------------------------------------- rule 1: a range excess is out of contract


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_an_observed_range_excess_is_refused_and_never_silently_widened(lane):
    """The research controlled lane set `spread = max(range_T, observed span)` and only FLAGGED the excess;
    that silently rests the ranked endpoint on a range nobody calibrated. Both lanes refuse instead."""
    with pytest.raises(sampled_fallback.OutOfContractRefusal, match="out of contract"):
        if lane == "step4":
            price_step4(worker=ramp_worker(step=Fraction(5)), contract=step4_contract(sample_total=100,
                                                                                      round_size=50,
                                                                                      range_T="2"))
        else:
            price_controlled(worker=ramp_site_worker(step=Fraction(5)),
                             contract=controlled_contract(sample_total=100, range_T="2"))


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_the_diagnostic_prices_an_excess_but_marks_it_non_rankable(lane):
    kwargs = dict(allow_out_of_contract=True)
    if lane == "step4":
        receipt = price_step4(worker=ramp_worker(step=Fraction(5)),
                              contract=step4_contract(sample_total=100, round_size=50, range_T="2"), **kwargs)
    else:
        receipt = price_controlled(worker=ramp_site_worker(step=Fraction(5)),
                                   contract=controlled_contract(sample_total=100, range_T="2"), **kwargs)
    assert receipt["verdict"] == "DIAGNOSTIC-NON-RANKABLE" and receipt["rankable"] is False
    assert "NON-RANKABLE" in receipt["interval"]["basis"]
    assert receipt["provenance"]["out_of_contract"] is True


def test_the_research_admitted_runs_were_in_contract_so_their_numbers_stand():
    """The silent-widening path existed in the research controlled lane but was never taken: the admitted
    run observed spans of 30.06 and 23.89 T against a `range_T` of 74. Pinning the margin here means a future
    change that pushes a span over the contract cannot be waved through as 'it was fine before'."""
    contract_range = float(Fraction(cc.load_contract()["range_T"]))
    assert contract_range == 74.0
    for observed in (30.06104182560163, 23.889265741632144):
        assert observed < contract_range


# --------------------------------------------------------------------------- rule 2: a resource stop is INCOMPLETE


class FakeClock:
    def __init__(self, *ticks):
        self.ticks, self.i = list(ticks), 0

    def __call__(self):
        t = self.ticks[min(self.i, len(self.ticks) - 1)]
        self.i += 1
        return t


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_a_blown_budget_is_incomplete_not_a_refusal_and_not_a_pass(lane):
    clock = FakeClock(0.0, 0.0, 0.0, 1e9)
    with pytest.raises(sampled_fallback.ResourceStop) as stop:
        if lane == "step4":
            price_step4(clock=clock, budget_seconds=1.0)
        else:
            price_controlled(clock=FakeClock(0.0, 0.0, 1e9, 1e9, 1e9, 1e9), budget_seconds=1.0)
    assert stop.value.verdict == "INCOMPLETE"
    assert "incomplete" in str(stop.value) and "no success receipt is emitted" in str(stop.value)
    assert not isinstance(stop.value, ValueError), "a resource stop is not a refusal"


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_a_resource_stop_emits_no_success_receipt(lane):
    """The raise is the only exit, so there is no object a caller could publish."""
    try:
        if lane == "step4":
            price_step4(clock=FakeClock(0.0, 0.0, 0.0, 1e9), budget_seconds=1.0)
        else:
            price_controlled(clock=FakeClock(0.0, 0.0, 1e9, 1e9, 1e9, 1e9), budget_seconds=1.0)
    except sampled_fallback.ResourceStop as stop:
        assert "verdict" not in stop.run and stop.run["budget_exceeded"] is True
    else:
        pytest.fail("the overrun did not stop the run")


def test_a_resource_stop_is_distinguished_from_a_missing_certified_word():
    def uncertified(job):
        raise ValueError("no word within the allowance")
    with pytest.raises(sampled_fallback.CertificationRefusal, match="missing certified word"):
        price_step4(worker=uncertified)
    with pytest.raises(sampled_fallback.CertificationRefusal, match="missing certified word"):
        price_controlled(worker=lambda job: (Fraction(1, 3), Fraction(100)))


def test_an_invalid_worker_or_a_missing_one_is_a_configuration_error():
    for call in (lambda: price_step4(worker=None), lambda: price_controlled(worker=None),
                 lambda: price_step4(workers=0), lambda: price_controlled(workers=17)):
        with pytest.raises(sampled_fallback.SamplingConfigurationError):
            call()


# --------------------------------------------------------------------------- rule 3: provenance


@pytest.mark.parametrize("lane", ["step4", "controlled"])
def test_the_receipt_records_samples_population_interval_bound_and_the_pricing_code(lane):
    module, receipt = (s4, price_step4()) if lane == "step4" else (cc, price_controlled())
    prov, interval = receipt["provenance"], receipt["interval"]
    assert receipt["verdict"] == "ACCEPTED" and receipt["rankable"] is True
    assert prov["n_sampled"] == interval["n_sampled"] > 0
    assert prov["range_T"] == str(Fraction(module.load_contract()["range_T"]))
    assert "calibrated and not proved" in prov["range_basis"]
    assert prov["priced_by"]["sha256"] == module.pricer_fingerprint()
    assert prov["priced_by"]["module"].endswith(f"{module.__name__.split('.')[-1]}.py")
    assert prov["priced_by"]["estimator"] == "evaluation/export_v2/sampled_fallback.py"
    assert prov["pool"]["closes_exactly"] is True
    module.check_receipt(receipt)


@pytest.mark.parametrize("module", [s4, cc], ids=["step4", "controlled"])
def test_an_interval_without_its_provenance_cannot_be_built(module):
    receipt = price_step4() if module is s4 else price_controlled()
    for key in module.REQUIRED_PROVENANCE:
        broken = {**receipt, "provenance": {k: v for k, v in receipt["provenance"].items() if k != key}}
        with pytest.raises(ValueError, match=key):
            module.check_receipt(broken)
    for key in module.REQUIRED_INTERVAL:
        broken = {**receipt, "interval": {k: v for k, v in receipt["interval"].items() if k != key}}
        with pytest.raises(ValueError, match=key):
            module.check_receipt(broken)


@pytest.mark.parametrize("module", [s4, cc], ids=["step4", "controlled"])
def test_the_receipt_binding_is_what_a_retry_must_reproduce(module):
    receipt = price_step4() if module is s4 else price_controlled()
    again = price_step4() if module is s4 else price_controlled()
    assert module.binding(receipt) == module.binding(again)
    assert len(set(module.binding(receipt))) == len(module.RECEIPT_BINDING)


@pytest.mark.parametrize("module", [s4, cc], ids=["step4", "controlled"])
def test_a_receipt_whose_T_was_collapsed_is_refused_by_the_checker(module):
    receipt = price_step4() if module is s4 else price_controlled()
    broken = {**receipt, "interval": {**receipt["interval"], "T": 1}}
    with pytest.raises(ValueError, match="bare 'T'"):
        module.check_receipt(broken)


# --------------------------------------------------------------------------- the sample is the policy's own


def test_both_mirrored_step4_directions_are_one_population_not_two():
    """The same turn forward and inverse is the same synthesis problem: multiplicities and sign bits add.
    Sampling the directions separately would spend the budget twice to learn one thing."""
    fwd, fwd_signs = directions(3, multiplicity=2, signed=1)
    inv, inv_signs = directions(3, multiplicity=5, signed=4)
    turns, signs = s4.census_of_directions(fwd, inv, forward_signs=fwd_signs, inverse_signs=inv_signs)
    assert len(turns) == 3 and set(turns.values()) == {7} and set(signs.values()) == {5}


@pytest.mark.parametrize("bad", [
    ({Fraction(3, 2): 1}, {}), ({Fraction(1, 3): 0}, {}), ({0.5: 1}, {}),
    ({Fraction(1, 3): 1}, {Fraction(1, 3): 2}),
])
def test_a_malformed_step4_census_is_refused(bad):
    counts, signs = bad
    with pytest.raises(ValueError):
        s4.census_of_directions(counts, {Fraction(1, 5): 1}, forward_signs=signs)


def test_an_empty_step4_census_is_refused():
    with pytest.raises(ValueError, match="at least one native turn"):
        s4.census_of_directions({}, {})


def test_the_step4_sample_is_salted_with_the_whole_inventory():
    """Binding the rank to the whole declared inventory means any change reshuffles the entire sample, so a
    submitter cannot steer which turns are drawn. Inherited from `sampled_fallback`; pinned here because the
    Step 4 contract has its own salt and the composition is what matters."""
    turns, signs = s4.census_of_directions(*directions(60)[:1], *directions(60)[:1])
    salt = bytes.fromhex(s4.load_contract()["salt_hex"])
    first = sampled_fallback.select_sample(turns, signs, salt=salt, total=10)[0]
    moved = {**turns, Fraction(777, 1 << 20): 1}
    second = sampled_fallback.select_sample(moved, {**signs, Fraction(777, 1 << 20): 0},
                                            salt=salt, total=10)[0]
    assert first != second, "a changed inventory must reshuffle the sample"


def test_the_controlled_index_space_digest_fixes_everything_a_submitter_controls():
    base = space(1)
    assert base.population == 2000 and base.scope == "step1"
    assert base.digest != space(1, reference_plan_sha256="b" * 64).digest
    assert base.digest != space(1, calls=41).digest
    assert base.digest != space(5).digest
    salt = bytes.fromhex(cc.load_contract()["salt_hex"])
    assert cc.select_indices(base, salt=salt, total=50) \
        != cc.select_indices(space(1, reference_plan_sha256="b" * 64), salt=salt, total=50)


def test_the_controlled_sample_is_without_replacement_and_has_no_modulo_bias():
    salt = bytes.fromhex(cc.load_contract()["salt_hex"])
    drawn = cc.select_indices(space(1), salt=salt, total=500)
    assert len(drawn) == len(set(drawn)) == 500
    assert all(0 <= i < 2000 for i in drawn)
    assert drawn == tuple(sorted(drawn))
    assert cc.select_indices(space(1), salt=salt, total=500) == drawn, "the draw is deterministic"


@pytest.mark.parametrize("bad", [{"step": 2}, {"step": 4}, {"calls": 0}, {"weights": 0}, {"binding": {}}])
def test_a_malformed_index_space_is_refused(bad):
    args = {"step": 1, "calls": 2, "weights": 2, "binding": {"a": 1}, **bad}
    with pytest.raises(ValueError, match="invalid controlled index space"):
        cc.IndexSpace(**args)


def test_a_sample_larger_than_the_population_is_a_configuration_error():
    with pytest.raises(sampled_fallback.SamplingConfigurationError, match="between 2 and the population"):
        cc.select_indices(space(1, calls=2, weights=2), salt=b"\x00" * 32, total=99)


def test_a_full_controlled_census_carries_no_sampling_uncertainty():
    small = space(1, calls=4, weights=5)
    receipt = price_controlled(spaces={1: small, 5: space(5, calls=4, weights=5)},
                               contract=controlled_contract(sample_total=20))
    for block in receipt["steps"].values():
        assert block["n_sampled"] == block["population"] == 20
        assert block["clt_halfwidth"] == block["eb_halfwidth"] == 0.0


def test_the_controlled_lane_prices_both_steps_or_neither():
    """Steps 1 and 5 are one claim against one partition. Pricing one of them alone, then the other, and
    adding the totals is the double-spend this release refuses, so the entry point will not do it."""
    with pytest.raises(sampled_fallback.SamplingConfigurationError, match=r"steps \[1, 5\] together"):
        cc.cost_policy_controlled({1: space(1)}, {1: turn_fns()[1]}, EPS, worker=site_worker(), gradient=38,
                                  contract=controlled_contract(sample_total=100), **budget())


# --------------------------------------------------------------------------- the research lane's own numbers

#: The two Step 4 non-vacuity runs the research lane recorded
#: (`results/validation/phase-fallback-exports/step4-nonvacuity.json`, commit `7b0a8ed`): the frozen contract
#: salt and the same salt with one hex digit flipped, 4,000 turns of 495,000 each.
STEP4_NONVACUITY = [
    {"label": "frozen_contract_salt", "point": 94881983359.64166, "clt": 123204886.00589107,
     "eb": 1197917803.5462778, "ranked_upper": 96079901165, "span": 23.733104379072614,
     "salt_hex": "09d29cc1369a33791d9c830b29b612dd2fd425f2d70ab01a671dff55a41c99d7"},
    {"label": "one_hex_digit_flipped", "point": 94820748319.46039, "clt": 123365938.58045354,
     "eb": 1197160121.2430215, "ranked_upper": 96017908442, "span": 22.842705929186643,
     "salt_hex": "09d29cc1369a33791d9c830b29b612dd2fd425f2d70ab01a671dff55a41c99d0"},
]


@pytest.mark.parametrize("run", STEP4_NONVACUITY, ids=[r["label"] for r in STEP4_NONVACUITY])
def test_the_ported_endpoint_rule_reproduces_the_research_lanes_recorded_runs(run):
    """The ranked endpoint is `ceil(exact_T + point + max(clt, eb)) + 1`, with `exact_T == 0` for Step 4
    (no eighth-turn sites). Reproducing both of the research lane's recorded endpoints TO THE INTEGER is what
    makes this a port rather than a reimplementation that happens to look similar."""
    import math
    assert math.ceil(run["point"] + max(run["clt"], run["eb"])) + 1 == run["ranked_upper"]
    assert run["span"] < float(Fraction(s4.load_contract()["range_T"])), "the recorded run was in contract"


def test_the_live_endpoint_obeys_the_same_rule_as_the_recorded_runs():
    """The identity above, asserted against this module's own output rather than against a transcript."""
    import math
    interval = price_step4()["interval"]
    point, width = float(interval["point_T"]), max(interval["clt_halfwidth"], interval["eb_halfwidth"])
    assert int(interval["tSampledEstimateUpper"]) == math.ceil(point + width) + 1 + interval["exact_site_T"]


def test_flipping_one_hex_digit_of_the_salt_reshuffles_the_step4_sample():
    """The research lane's non-vacuity run, through the ported selector: the two salts of
    `step4-nonvacuity.json` draw different turns, so the contract salt is load-bearing and the sample is not
    a constant the code would have drawn whatever the contract said."""
    turns, signs = s4.census_of_directions(*directions(400)[:1], *directions(400)[:1])
    drawn = []
    for run in STEP4_NONVACUITY:
        sample = sampled_fallback.select_sample(turns, signs, salt=bytes.fromhex(run["salt_hex"]), total=50)[0]
        drawn.append({t for stratum in sample.values() for t in stratum})
    assert drawn[0] != drawn[1] and len(drawn[0]) == len(drawn[1]) == 50


def test_the_admitted_step4_endpoint_is_the_contract_cap_and_gate_it_claims():
    """The admitted B3 receipt: 94,983,648,143 T over 2,027,025,000 sites from a 120,000-of-495,000 sample,
    empirical-Bernstein 7.54e7 against the contract's 8.48e7 gate. Pinned so a later change to the cap or the
    gate has to confront the receipt that was admitted under them."""
    cal = s4.load_contract()["calibration"]
    ranked, sites, eb = 94983648143, 2027025000, 75378488.95299721
    assert eb < cal["gate_halfwidth"] and cal["gate_halfwidth"] == 85000000.0
    assert eb < cal["halfwidth_by_sample_total"]["120000"] < cal["gate_halfwidth"]
    assert abs(ranked / sites - 46.8586) < 1e-3, "46.86 T per site"
    assert ranked / sites < 123.19262356285252, "under the Ross-Selinger per-site mean"


# --------------------------------------------------------------------------- the one pool


def test_the_reference_allocation_partitions_the_pool_exactly():
    part = rotation_pool.check_partition(**budget(), per_site=EPS, certified_fixed=sum(CEILINGS) + PREP)
    assert part["closes_exactly"] is True and part["run_sites"] == RUN_SITES
    assert Fraction(part["per_site_total"]) == EPS * RUN_SITES
    assert sum(map(Fraction, part["qft_ceilings"])) + Fraction(part["gradient_prepare"]) \
        + Fraction(part["per_site_total"]) == POOL


@pytest.mark.parametrize("over,message", [
    ({"pool": Fraction(1, 100)}, "not exactly the reference allocation"),
    ({"gradient_prepare": Fraction(1, 1000)}, "not exactly the reference allocation"),
    ({"run_sites": RUN_SITES + 1}, "not exactly the reference allocation"),
    ({"qft_ceilings": (Fraction(1, 4000),)}, "not exactly the reference allocation"),
])
def test_a_pool_that_does_not_close_is_refused_naming_the_residual(over, message):
    with pytest.raises(rotation_pool.PoolPartitionError, match=message):
        rotation_pool.check_partition(**budget(**over), per_site=EPS)


def test_a_float_budget_term_is_refused_rather_than_rounded():
    """The identity is exact or it is not an identity. A float term would close 'nearly', which is the
    failure mode that hands a submission free error."""
    with pytest.raises(rotation_pool.PoolPartitionError, match="exact rational"):
        rotation_pool.check_partition(**budget(pool=0.005), per_site=EPS)


def test_an_over_budget_certified_fixed_error_is_refused():
    with pytest.raises(rotation_pool.PoolPartitionError, match="over-budget"):
        rotation_pool.check_partition(**budget(), per_site=EPS, certified_fixed=POOL)


def test_a_scope_is_free_of_charge_exactly_when_it_re_prices_allocated_sites():
    part = rotation_pool.check_partition(**budget(), per_site=EPS)
    scope = rotation_pool.check_scope(part, scope="step4", scope_sites=600_000, per_site=EPS)
    assert scope["takes_a_second_share"] is False
    assert scope["retained_outside_scope_sites"] == 400_000
    assert Fraction(scope["scope_allocated"]) == EPS * 600_000


@pytest.mark.parametrize("over,message", [
    ({"scope_sites": RUN_SITES + 1}, "it is adding them"),
    ({"scope_sites": 0}, "it is adding them"),
    ({"per_site": EPS * 2}, "a second share of one pool"),
    ({"scope": "step3"}, "unknown phase-product scope"),
])
def test_a_scope_at_its_own_allowance_or_outside_the_census_is_a_second_spender(over, message):
    part = rotation_pool.check_partition(**budget(), per_site=EPS)
    args = {"scope": "step4", "scope_sites": 600_000, "per_site": EPS, **over}
    with pytest.raises(rotation_pool.PoolDoubleSpend, match=message):
        rotation_pool.check_scope(part, **args)


def test_one_sampled_phase_scope_is_admissible():
    for scope in sorted(rotation_pool.SAMPLED_SCOPES):
        rotation_pool.refuse_combination([scope])
    rotation_pool.refuse_combination(["step1", "step5"])        # one lane, one partition, one claim
    rotation_pool.refuse_combination([rotation_pool.QFT_DESIGN])


@pytest.mark.parametrize("claimants", [
    ("step4", rotation_pool.QFT_DESIGN), ("step1", "step5", rotation_pool.QFT_DESIGN),
    ("step2", rotation_pool.QFT_DESIGN),
])
def test_a_qft_design_and_a_sampled_phase_scope_cannot_be_asked_for_together(claimants):
    """Lane XF refused a QFT design plus a Step 2 fallback on this arithmetic. Step 4 adaptive and the
    controlled lane draw on the SAME pool -- both research plans declare `error_pool` 1/200, the engine's own
    pool at delta 1e-2 -- so the same refusal applies to them, and is made by name rather than inherited."""
    with pytest.raises(rotation_pool.PoolDoubleSpend, match="cannot be asked for together"):
        rotation_pool.refuse_combination(claimants)


@pytest.mark.parametrize("claimants", [("step2", "step4"), ("step4", "step1"), ("step2", "step1", "step5"),
                                       ("step2", "step4", "step5")])
def test_two_sampled_phase_scopes_cannot_be_priced_together(claimants):
    """Each scope's ledger reserves every other site at the per-site allowance and carries the whole fixed
    QFT and gradient term, so two independently priced scopes double-count that term and price each other's
    sites. Their endpoints cannot be added."""
    with pytest.raises(rotation_pool.PoolDoubleSpend, match="cannot be priced together"):
        rotation_pool.refuse_combination(claimants)


def test_an_unknown_pool_claimant_is_refused_rather_than_ignored():
    with pytest.raises(rotation_pool.PoolDoubleSpend, match="unknown pool claimant"):
        rotation_pool.refuse_combination(["step4", "readout"])


@pytest.mark.parametrize("lane,also", [("step4", rotation_pool.QFT_DESIGN), ("step4", "step2"),
                                       ("controlled", rotation_pool.QFT_DESIGN), ("controlled", "step4")])
def test_the_entry_points_refuse_the_combination_before_pricing_anything(lane, also):
    """Refused before the worker runs, so a combination cannot be priced halfway and then reported."""
    priced = []

    def spy(job):
        priced.append(job)
        return (job[0], (None, None), None, (Fraction(100), Fraction(100))) if lane == "step4" \
            else (job[0], Fraction(100))

    with pytest.raises(rotation_pool.PoolDoubleSpend):
        if lane == "step4":
            price_step4(worker=spy, also_asked=(also,))
        else:
            price_controlled(worker=spy, also_asked=(also,))
    assert priced == [], "the combination was priced before it was refused"


def test_the_receipt_states_the_scopes_share_of_the_one_pool():
    receipt = price_step4()
    pool = receipt["provenance"]["pool"]
    assert pool["scope"] == "step4" and pool["takes_a_second_share"] is False
    assert pool["scope_sites"] + pool["retained_outside_scope_sites"] == pool["run_sites"] == RUN_SITES
    assert Fraction(pool["per_site"]) == EPS

    controlled = price_controlled()["provenance"]["pool"]
    assert controlled["scope"] == ["step1", "step5"] and sorted(controlled["per_step"]) == ["step1", "step5"]
    assert controlled["scope_sites"] == 4000


def test_no_sampled_pricer_derives_its_own_allowance_from_the_engine_currency():
    """What makes a sampled phase scope free of charge against the pool is that it RECEIVES `eps` and only
    checks the partition. A pricer that started computing `eps` itself would be a second spender, and the
    whole double-spend analysis would have to be redone. `rotation_pool` names the currency in prose only --
    it imports nothing from the engine and derives nothing."""
    from pathlib import Path
    import re
    here = Path(sampled_fallback.__file__).parent
    for name in ("sampled_fallback.py", "step4_sampled_fallback.py", "controlled_sampled_fallback.py",
                 "rotation_pool.py"):
        text = (here / name).read_text()
        assert not re.search(r"^\s*(from|import)\s+\S*(sto_currency|engine)", text, re.M), \
            f"{name} now imports the engine currency: redo the double-spend analysis"
        assert "ROTATION_FRACTION" not in text or name == "rotation_pool.py"
        if name != "rotation_pool.py":
            for token in ("sto_currency", "rotation_eps", "ts_per_rotation"):
                assert token not in text, f"{name} now reads {token}"
