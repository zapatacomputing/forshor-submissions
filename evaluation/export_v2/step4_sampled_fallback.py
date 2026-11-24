"""Sampled costing of a Step 4 ADAPTIVE (B3) phase, from the submitted policy's OWN native turn inventory.

Why this exists. The retained Step 4 SELECTIVE-KICK route is qualified and complete by construction: it
charges every Step 4 product bit a unitary word or a kickback, so its tally needs no sample. The ADAPTIVE
route needs a signed word for each distinct native turn in BOTH mirrored directions -- 495,000 of them on the
2048-bit benchmark, which is 2.4x the bounded cold recertification cap of the research lane's
`bind_certified_step4` (4,096) and 2.5x its `build_catalogue` envelope (200,000). No complete cold word bank
fits, so there is no bound circuit to price. This lane prices Step 4 the way the Step 2 lane prices Step 2:
the ranked upper endpoint of a contract-fixed stratified sample, with every site charged the per-site
half-diamond allowance by contract.

IT IS AN ESTIMATE, NOT A BOUND, and the receipt cannot say otherwise: `sampled_fallback.t_fields` emits
`tClaimKind` plus exactly one of `tSampledEstimateUpper` / `tCircuitBound` and no bare `T`, so a consumer
cannot pick the number up and rank it as a bound. The per-site T is itself an UNCONDITIONAL EXPECTATION (B3 is
repeat-until-success), which `tKind` carries separately -- lane XA's axis, under XA's own name, orthogonal to
this one (owner ruling 2026-11-24). A sampled Step 4 price is therefore an estimate OF AN EXPECTATION, twice
removed from a worst case.

The sampling machinery is `sampled_fallback`'s, unchanged. That is deliberate and it is also what the research
lane does: `staging_retained_step4_sampled.py` calls `price_step2_budgeted` directly with the Step 4 contract,
because the estimator does not know or care which step the turns came from -- only the CONTRACT differs
(cap 120,000 of 495,000, `range_T` 80, salt frozen before any priced run). There is one Maurer-Pontil
implementation in this repository, not two.

Policy-generality, exactly as in `sampled_fallback`: nothing here names a fixture. The census is an argument;
the sample is a function of the exactly enumerated inventory the policy declares and the public contract salt
only, salted with `inventory_digest(turns, signs)` so any change to the inventory reshuffles the whole sample;
and the per-turn certification worker is supplied by the caller, so the synthesis backend is a parameter of
the costing and not a frozen operator bank.

THE THREE RULES ARE `sampled_fallback`'S, and they are not relaxed here:

1. A RANGE EXCESS IS OUT OF CONTRACT -- `OutOfContractRefusal`, refused for public ranking, never silently
   widened; only the explicit local diagnostic prices it, flagged NON-RANKABLE.
2. A RESOURCE STOP IS INCOMPLETE -- `ResourceStop`, never a refusal and never a pass, and the raise is the
   only exit, so no success receipt can be emitted for a run that stopped that way.
3. AN INTERVAL WITHOUT ITS PROVENANCE IS NOT EVIDENCE -- the receipt records the sample count, the inventory
   digest, the interval, the contract and range bound, and which code priced it.

THE POOL. Step 4's rotations are part of the one run-wide synthesis census, so this lane re-prices sites the
reference allocation has ALREADY charged, at the SAME per-site allowance. `rotation_pool.check_partition` and
`check_scope` make that a checked fact rather than a claim, and `rotation_pool.refuse_combination` refuses a
Step 4 adaptive price asked for together with a QFT design or with another sampled phase scope -- both of
which would divide one pool twice. See `rotation_pool.py` for the arithmetic.

Ported from the research lane (`postprocess/synthesis/tools/staging_retained_step4_sampled.py` and
`qualification/step4_sampled_contract.json`, branch `local/prepush-XC-tier3b`, commits `a12e431`, `789d1cc`
and `c69e4d5`), trimmed to the policy-general costing: the research staging lane's fixture binding, its
`reduced_audit` witness and its board reconciliation stay in the research repository, because they name an
instance. Kept in `evaluation/export_v2`, outside `evaluation/engine`, so adding it moves neither
`engineRevision` nor `dataFingerprint` -- see `pricing.py:16-25`.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import time
from typing import Any, Callable, Dict, Mapping, Tuple

from . import rotation_pool
# The exception types are re-exported deliberately: a caller catches them from the lane it called, and the
# three honesty rules they implement are this lane's rules too, not only Step 2's.
from .sampled_fallback import (CertificationRefusal, OutOfContractRefusal, RANGE_BASIS, RANKED_BASIS,
                               ResourceStop, SamplingConfigurationError, SamplingInfrastructureError,
                               check_t_fields, contract_digest, load_contract as _load_contract,
                               price_step2_budgeted, t_fields)

SCHEMA = "forshor.step4-sampled-contract/v1"
PROFILE = "policy-general-step4-b3-sampled/v1"
CONTRACT = Path(__file__).parent / "step4_sampled_contract.json"
SCOPE = "step4"
#: B3 is repeat-until-success: a site's T is an unconditional expectation, never a worst case.
SITE_T_KIND = "expectedAdaptive"
FAILURE_BASIS = ("rule-of-three approximation (3/n, clamped at 1) over distinct sampled turns; "
                 "not a circuit-level failure probability")
#: Why no bound circuit exists for this route, in the numbers the research lane measured.
NO_BOUND_BASIS = ("the adaptive route needs a certified signed word for every distinct native turn in both "
                  "mirrored directions; on the 2048-bit benchmark that is 495,000 turns, 2.4x the bounded "
                  "cold recertification cap (4,096) and 2.5x the catalogue envelope (200,000), so no "
                  "complete cold word bank fits and there is no circuit to bound")

__all__ = ["CONTRACT", "PROFILE", "SCHEMA", "SCOPE", "CertificationRefusal", "OutOfContractRefusal",
           "ResourceStop", "SamplingConfigurationError", "SamplingInfrastructureError", "binding",
           "census_of_directions", "check_receipt", "cost_policy_step4", "load_contract",
           "pricer_fingerprint"]


def load_contract(path=CONTRACT) -> Dict[str, Any]:
    """The Step 4 contract, under `sampled_fallback`'s own field rules and this lane's schema."""
    return _load_contract(path, schema=SCHEMA)


def pricer_fingerprint() -> str:
    """Which code priced it: this module and the estimator it delegates to, in that order."""
    from . import sampled_fallback
    return hashlib.sha256(Path(__file__).read_bytes()
                          + Path(sampled_fallback.__file__).read_bytes()).hexdigest()


# ---------------------------------------------------------------- the policy's own Step 4 census


def census_of_directions(forward: Mapping[Fraction, int], inverse: Mapping[Fraction, int], *,
                         forward_signs: Mapping[Fraction, int] = (),
                         inverse_signs: Mapping[Fraction, int] = ()) -> Tuple[Dict, Dict]:
    """(turns, signs) for ONE sampled population covering both mirrored native directions.

    The native Step 4 hierarchy runs each turn forward and inverse, mirrored turn for turn. The two
    directions are one population, not two: the same turn in two directions is the same synthesis problem, so
    sampling them separately would spend the budget twice to learn one thing. Multiplicities add; sign-bit
    counts add. Raises on a turn that is not a canonical `Fraction` in [0, 1) or a nonpositive multiplicity,
    and on a sign-bit count that exceeds its own direction's multiplicity."""
    forward_signs, inverse_signs = dict(forward_signs or {}), dict(inverse_signs or {})
    turns: Dict[Fraction, int] = {}
    signs: Dict[Fraction, int] = {}
    for direction, counts, sign_counts in (("forward", forward, forward_signs),
                                           ("inverse", inverse, inverse_signs)):
        for turn, n in dict(counts).items():
            if not isinstance(turn, Fraction) or not 0 <= turn < 1:
                raise ValueError(f"{direction} turn must be a canonical Fraction in [0, 1): {turn!r}")
            if type(n) is not int or n < 1:
                raise ValueError(f"{direction} multiplicity of {turn} must be a positive whole count: {n!r}")
            s = sign_counts.get(turn, 0)
            if type(s) is not int or not 0 <= s <= n:
                raise ValueError(f"{direction} sign-bit count of {turn} must lie in 0..{n}: {s!r}")
            turns[turn] = turns.get(turn, 0) + n
            signs[turn] = signs.get(turn, 0) + s
    if not turns:
        raise ValueError("a Step 4 adaptive census must declare at least one native turn")
    return turns, signs


# ---------------------------------------------------------------- the receipt


RECEIPT_BINDING = ("n_sampled", "inventory_sha256", "contract_sha256", "checked_words_sha256", "sample_sha256")
REQUIRED_PROVENANCE = (*RECEIPT_BINDING, "range_T", "range_basis", "observed_span_T", "priced_by", "profile",
                       "pool")
REQUIRED_INTERVAL = ("n_sampled", "n_cap", "point_T", "clt_halfwidth", "eb_halfwidth", "basis")


def binding(receipt: Mapping[str, Any]):
    """What a retry of an accepted evaluation must reproduce: reuse the exact receipt, never re-estimate and
    keep the better of several time-varying estimates."""
    return tuple(receipt["provenance"][k] for k in RECEIPT_BINDING)


def check_receipt(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """An interval without its provenance is not evidence, and an estimate must not look like a bound."""
    for key in ("verdict", "profile", "scope", "interval", "provenance", "run", "rankable"):
        if key not in receipt:
            raise ValueError(f"receipt is missing {key!r}")
    for key in REQUIRED_INTERVAL:
        if key not in receipt["interval"]:
            raise ValueError(f"receipt is missing interval.{key}")
    for key in REQUIRED_PROVENANCE:
        if key not in receipt["provenance"]:
            raise ValueError(f"receipt is missing provenance.{key}")
    check_t_fields(receipt["interval"])
    return receipt


def cost_policy_step4(turns, signs, eps: Fraction, *, worker, pool, qft_ceilings, gradient_prepare,
                      run_sites: int, certified_fixed=None, contract=None, workers: int = 4,
                      budget_seconds=None, clock: Callable[[], float] = time.monotonic,
                      allow_out_of_contract: bool = False,
                      also_asked: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Cost an arbitrary policy's Step 4 adaptive (B3) phase from its own native turn inventory.

    `turns`/`signs` come from `census_of_directions`; `eps` is the per-site half-diamond the reference plan
    charges EVERY synthesized site, and `pool`, `qft_ceilings`, `gradient_prepare` and `run_sites` are that
    plan's own budget terms, which `rotation_pool.check_partition` verifies close exactly. `also_asked` names
    any OTHER claimant on the same pool (`rotation_pool.QFT_DESIGN`, or another phase scope); naming one
    refuses the request rather than pricing half of it.

    Returns a certified-interval receipt whose T is structurally an estimate. Raises `ResourceStop`
    (INCOMPLETE, no receipt) if the budget was overrun, `OutOfContractRefusal` if a stratum's observed span
    exceeds the contract range, `CertificationRefusal` if a sampled turn has no certified word,
    `rotation_pool.PoolPartitionError` / `PoolDoubleSpend` if the budget is divided twice,
    `SamplingInfrastructureError` on worker failure and `SamplingConfigurationError` on a bad request."""
    rotation_pool.refuse_combination((SCOPE, *also_asked))
    contract = load_contract() if contract is None else dict(contract)
    if contract.get("schema") != SCHEMA:
        raise SamplingConfigurationError(
            f"this lane prices the {SCHEMA} contract only; got {contract.get('schema')!r}")
    if min(int(contract["sample_total"]), int(contract["round_size"])) < 1 or Fraction(contract["range_T"]) <= 0:
        raise SamplingConfigurationError("invalid sampled-costing contract")

    scope_sites = sum(turns.values())
    partition = rotation_pool.check_partition(pool=pool, qft_ceilings=qft_ceilings,
                                              gradient_prepare=gradient_prepare, per_site=eps,
                                              run_sites=run_sites, certified_fixed=certified_fixed)
    allocation = rotation_pool.check_scope(partition, scope=SCOPE, scope_sites=scope_sites, per_site=eps)

    budget = float(contract["budget_seconds"]) if budget_seconds is None else float(budget_seconds)
    price, run = price_step2_budgeted(turns, signs, eps, contract, budget_seconds=budget, workers=workers,
                                      worker=worker, clock=clock,
                                      allow_out_of_contract=allow_out_of_contract)
    if run["budget_exceeded"]:
        raise ResourceStop(
            f"incomplete: the sampled Step 4 adaptive phase overran its {budget:g} s budget after "
            f"{run['rounds']} round(s) ({run['elapsed_s']:.6g} s); no endpoint is claimed and no success "
            f"receipt is emitted", {**run, "budget_seconds": budget, "scope": SCOPE})
    receipt = {
        "verdict": "ACCEPTED",
        "profile": PROFILE,
        "scope": SCOPE,
        "rankable": not price.out_of_contract,
        "interval": {
            **t_fields("sampledEstimate", SITE_T_KIND, str(price.ranked_upper)),
            "no_bound_basis": NO_BOUND_BASIS,
            "n_sampled": price.n_sampled, "n_strata": price.n_strata, "n_cap": run["n_cap"],
            "point_T": repr(price.point), "exact_site_T": price.exact_T,
            "clt_halfwidth": price.clt_halfwidth, "eb_halfwidth": price.eb_halfwidth,
            "conf": float(contract["conf"]), "basis": RANKED_BASIS,
            "gate_halfwidth_fraction": contract["max_halfwidth_fraction"],
            "failure_fraction_upper": price.failure_fraction_upper, "failure_fraction_basis": FAILURE_BASIS,
        },
        "provenance": {
            "n_sampled": price.n_sampled,
            "inventory_sha256": price.inventory_sha256,
            "sample_sha256": price.sample_sha256,
            "checked_words_sha256": price.checked_words_sha256,
            "contract_sha256": contract_digest(contract),
            "range_T": str(Fraction(contract["range_T"])),
            "range_basis": RANGE_BASIS,
            "observed_span_T": price.observed_span_T,
            "out_of_contract": price.out_of_contract,
            "half_diamond_per_site": str(eps),
            "distinct_turns": len(turns),
            "sites": scope_sites,
            "pool": allocation,
            "profile": PROFILE,
            "priced_by": {"entry": "export_v2.step4_sampled_fallback.cost_policy_step4",
                          "module": "evaluation/export_v2/step4_sampled_fallback.py",
                          "estimator": "evaluation/export_v2/sampled_fallback.py",
                          "sha256": pricer_fingerprint()},
        },
        "run": {**run, "budget_seconds": budget, "workers": workers},
    }
    if price.out_of_contract:   # only reachable in diagnostic mode; say so wherever the numbers go
        receipt["verdict"] = "DIAGNOSTIC-NON-RANKABLE"
        receipt["interval"]["basis"] = (f"NON-RANKABLE diagnostic: observed span {price.observed_span_T:.6g} T "
                                        f"exceeds range_T {price.range_T:.6g} T (out of contract); " + RANKED_BASIS)
    return check_receipt(receipt)
