"""Index-space sampled costing of the CONTROLLED Steps 1/5 adaptive (B3) phase.

Why the sample is drawn from an INDEX SPACE and not from a turn set. Steps 2 and 4 can enumerate their
distinct turns (123,750 and 495,000 on the 2048-bit benchmark) and rank each one by a contract salt, because
their phase is the same in every round. Steps 1 and 5 cannot. Their signed turn is
`multiplier * (2 r_e / N) * w / 2` with `r_e` the round's own modular coefficient, so the population is one
site per (checked native binding, signed-product weight group) -- 4,095 * 123,750 = 506,756,250 sites per
step, every weight group of multiplicity one and essentially every site a distinct turn, each `k/N` for a
2048-bit N. Enumerating that turn set would need about a terabyte and a complete certified word bank for both
steps would need roughly 1.0e9 words. So the sample is a simple random sample without replacement over
`[0, calls * weights)`, seeded by the contract salt and a digest that fixes the ENTIRE population: policy,
instance, engine pin, reference plan, allowance, the checked binding list and the weight list. Changing
anything a submitter controls reshuffles the whole sample, exactly as binding the Step 2 rank to its inventory
digest does. Rejection sampling over 64-bit blocks, so there is no modulo bias and no dependence on a PRNG
version.

IT IS AN ESTIMATE, NOT A BOUND, and the receipt cannot say otherwise: `sampled_fallback.t_fields` emits
`tClaimKind` plus exactly one of `tSampledEstimateUpper` / `tCircuitBound` and no bare `T`. Binding the
controlled plan needs every turn's certified word, which refuses a sample by construction, so no circuit is
bound here. The per-site T is additionally an UNCONDITIONAL EXPECTATION (B3 is repeat-until-success), carried
separately as `tKind` -- lane XA's axis, under XA's own name, orthogonal to this one (owner ruling 2026-11-24).

TWO STEPS, ONE CLAIM. Steps 1 and 5 are priced in a single run against a single pool partition, with their
own index space each and one summed endpoint. That is why they are ONE claimant on the budget and not two
(`rotation_pool.refuse_combination`): the alternative -- pricing them in two runs and adding the totals --
would double-count the fixed QFT and gradient term, which is precisely the double-spend this release refuses.

THE THREE RULES, APPLIED HERE RATHER THAN INHERITED. The research lane
(`synthback/backends/controlled_sampled_costing.py`) implements only the first in part: its `combine` sets
`spread = max(range_T, observed span)`, which SILENTLY WIDENS the empirical-Bernstein range to whatever the
sample showed and only flags it as `range_exceeded`, and its `price_controlled_step` has no wall-clock stop at
all. Both are out of line with SAMPLED-RULINGS.md and with the Step 2 and Step 4 lanes, so the port fixes
them rather than carrying them across:

1. A RANGE EXCESS IS OUT OF CONTRACT. An observed span above the contract's `range_T` raises
   `OutOfContractRefusal`: refused for public ranking, never silently widened. Only the explicit local
   diagnostic (`allow_out_of_contract=True`) prices it, and that price is flagged NON-RANKABLE.
   (The research lane's admitted run is unaffected: it observed spans of 30.06 and 23.89 T against a
   `range_T` of 74, so it was in contract and the number it reported stands.)
2. A RESOURCE STOP IS INCOMPLETE. The whole sample is always completed -- refusing a partial run keeps the
   reported population coverage honest -- so overrunning the contract's wall budget raises `ResourceStop`
   with `verdict == "INCOMPLETE"`: never a refusal, never a pass, and the raise is the only exit, so no
   success receipt can be emitted for a run that stopped that way.
3. AN INTERVAL WITHOUT ITS PROVENANCE IS NOT EVIDENCE. The receipt records the sample count, both index-space
   digests, the interval, the contract and range bound, and which code priced it; `check_receipt` refuses a
   receipt missing any of them.

Policy-generality. Nothing here names a fixture or an instance. The population is described by the caller as
an `IndexSpace` per step, the turn of a drawn coordinate is computed by a caller-supplied `turn_of`, and the
per-site certification worker is a parameter, so the synthesis backend is a parameter of the costing and not a
frozen operator bank.

Ported from the research lane (`postprocess/synthesis/src/synthback/backends/controlled_sampled_costing.py`,
`tools/staging_controlled_fallback.py` and `qualification/controlled_sampled_contract.json`, branch
`local/prepush-XC-tier3b`, commits `d6c5b73`, `7b0a8ed`, `787d888` and `429d5eb`), trimmed to the
policy-general costing: the staging lane's native binding reconciliation, its compiled shell swap and its
reduced bound witness stay in the research repository, because they name an instance. The statistical core is
`sampled_fallback`'s, so there is one Maurer-Pontil implementation in this repository and not two. Kept in
`evaluation/export_v2`, outside `evaluation/engine`, so adding it moves neither `engineRevision` nor
`dataFingerprint` -- see `pricing.py:16-25`.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from . import rotation_pool
# Re-exported deliberately: a caller catches these from the lane it called, and the rules they implement are
# this lane's rules too.
from .sampled_fallback import (CertificationRefusal, OutOfContractRefusal, RANGE_BASIS, ResourceStop,
                               SamplingConfigurationError, SamplingInfrastructureError, Stratum,
                               _runner, check_t_fields, contract_digest, stratified_total, t_fields)

SCHEMA = "forshor.controlled-sampled-contract/v1"
PROFILE = "policy-general-steps15-b3-sampled/v1"
CONTRACT = Path(__file__).parent / "controlled_sampled_contract.json"
STEPS = (1, 5)
SCOPES = ("step1", "step5")
#: B3 is repeat-until-success: a site's T is an unconditional expectation, never a worst case.
SITE_T_KIND = "expectedAdaptive"
RANKED_BASIS = "upper endpoint, larger of CLT and empirical-Bernstein, 95%, per step, summed"
RANGE_T_BASIS = RANGE_BASIS
#: Why no bound circuit exists for this route, in the numbers the research lane measured.
NO_BOUND_BASIS = ("binding the controlled plan needs a certified word for every turn, and the population is "
                  "506,756,250 sites per step with essentially every site a distinct turn; a complete bank is "
                  "roughly 1.0e9 words, so `bind_controlled_plan` refuses a sample by construction and there "
                  "is no circuit to bound")
FAILURE_BASIS = ("rule-of-three approximation (3/n, clamped at 1) over distinct sampled sites; "
                 "not a circuit-level failure probability")
UINT64 = 1 << 64

__all__ = ["CONTRACT", "IndexSpace", "PROFILE", "SCHEMA", "SCOPES", "STEPS", "CertificationRefusal",
           "OutOfContractRefusal", "ResourceStop", "SamplingConfigurationError",
           "SamplingInfrastructureError", "SampledStepPrice", "binding", "check_receipt", "combine",
           "cost_policy_controlled", "load_contract", "price_controlled_step", "pricer_fingerprint",
           "select_indices"]


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()


def load_contract(path=CONTRACT) -> Dict[str, Any]:
    """The controlled contract. It has no `round_size`: the whole sample is always completed, so there are no
    rounds to stop between -- `budget_seconds` is a deadline the run is checked against, not a round gate."""
    c = json.loads(Path(path).read_text())
    try:
        ok = (c["schema"] == SCHEMA and len(bytes.fromhex(c["salt_hex"])) == 32
              and int(c["sample_total"]) >= 2 and c["conf"] == 0.95 and Fraction(c["range_T"]) > 0
              and Fraction(c["max_halfwidth_fraction"]) > 0 and float(c["budget_seconds"]) > 0)
    except (KeyError, ValueError, TypeError):
        ok = False
    if not ok:
        raise ValueError("invalid controlled sampled-costing contract")
    return c


def pricer_fingerprint() -> str:
    """Which code priced it: this module and the estimator it delegates to, in that order."""
    from . import sampled_fallback
    return hashlib.sha256(Path(__file__).read_bytes()
                          + Path(sampled_fallback.__file__).read_bytes()).hexdigest()


# ---------------------------------------------------------------- the population


@dataclass(frozen=True)
class IndexSpace:
    """One step's population: `calls` checked native bindings, each with `weights` single-site weight groups.

    `binding` carries every field that FIXES the population and is digested, never sampled from. Whatever a
    submitter controls belongs in it, so that changing any of it reshuffles the entire sample."""
    step: int
    calls: int
    weights: int
    binding: Dict[str, Any]

    def __post_init__(self):
        if (type(self.step) is not int or self.step not in STEPS or type(self.calls) is not int
                or type(self.weights) is not int or self.calls < 1 or self.weights < 1
                or not isinstance(self.binding, dict) or not self.binding):
            raise ValueError(f"invalid controlled index space: step must be one of {list(STEPS)}, calls and "
                             f"weights positive whole counts, and `binding` a nonempty mapping")

    @property
    def scope(self) -> str:
        return f"step{self.step}"

    @property
    def population(self) -> int:
        return self.calls * self.weights

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical({"schema": SCHEMA, "step": self.step, "calls": self.calls,
                                         "weights": self.weights, "binding": self.binding})).hexdigest()

    def coordinates(self, index: int) -> Tuple[int, int]:
        if type(index) is not int or not 0 <= index < self.population:
            raise ValueError("controlled sample index out of range")
        return divmod(index, self.weights)


def select_indices(space: IndexSpace, *, salt: bytes, total: int) -> Tuple[int, ...]:
    """Deterministic simple random sample without replacement: no modulo bias, no PRNG version dependence."""
    if type(total) is not int or not 2 <= total <= space.population:
        raise SamplingConfigurationError(
            f"controlled sample size must lie between 2 and the population {space.population}; got {total!r}")
    seed = hashlib.sha256(salt + b"|population|" + bytes.fromhex(space.digest)).digest()
    limit = UINT64 - UINT64 % space.population
    picked, order, counter = set(), [], 0
    while len(order) < total:
        block = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        counter += 1
        for j in range(0, 32, 8):
            v = int.from_bytes(block[j:j + 8], "big")
            if v >= limit:
                continue                      # rejection, so every index is equally likely
            k = v % space.population
            if k not in picked:
                picked.add(k)
                order.append(k)
                if len(order) == total:
                    break
    return tuple(sorted(order))


# ---------------------------------------------------------------- the interval


@dataclass(frozen=True)
class SampledStepPrice:
    ranked_upper: Fraction           # integer-valued upper endpoint of the step's expected T
    point: float
    clt_halfwidth: float
    eb_halfwidth: float
    n_sampled: int
    population: int
    distinct_sampled_turns: int
    observed_span_T: float
    range_T: float
    out_of_contract: bool            # observed span > range_T (diagnostic mode only): NON-RANKABLE
    space_sha256: str
    sample_sha256: str
    checked_words_sha256: str = ""


def combine(values: Mapping[int, Fraction], space: IndexSpace, indices: Sequence[int], *, range_T: Fraction,
            conf: float = 0.95, allow_out_of_contract: bool = False,
            checked: Mapping[int, Any] = ()) -> SampledStepPrice:
    """`values[i]` is the expected-T upper end of the site at index `i`; each site has multiplicity one.

    A sample whose observed span of expected T exceeds `range_T` is OUT OF CONTRACT: it raises
    `OutOfContractRefusal`, because widening the empirical-Bernstein range to the observed span would leave
    the ranked endpoint resting on a range nobody calibrated. `allow_out_of_contract=True` (local diagnostic
    only) widens it and flags the price NON-RANKABLE."""
    if len(values) != len(indices) or set(values) != set(indices):
        raise ValueError("sampled values do not match the drawn indices")
    if not 0.0 < conf < 1.0:
        raise ValueError("conf must lie in (0, 1)")
    range_T = Fraction(range_T)
    if range_T <= 0:
        raise ValueError("range_T must be positive")
    samples = tuple(float(values[i]) for i in indices)
    span = max(samples) - min(samples)
    census = len(indices) == space.population
    out_of_contract = span > float(range_T) and not census
    if out_of_contract and not allow_out_of_contract:
        raise OutOfContractRefusal(
            f"out of contract: observed span {span:.6g} T of expected T over the {space.scope} index-space "
            f"sample exceeds range_T {float(range_T):.6g} T; not rankable (widening it silently is not "
            f"allowed; the local diagnostic can still price it)")
    spread = max(float(range_T), span) if out_of_contract else float(range_T)
    est = stratified_total([Stratum(space.scope, space.population, samples, spread)], conf)
    width = 0.0 if census else max(est.clt_halfwidth, est.eb_halfwidth)
    if census:                       # full enumeration: no sampling uncertainty, no float slack
        upper = Fraction(math.ceil(sum((values[i] for i in indices), Fraction(0))))
    else:
        upper = Fraction(math.ceil(est.total + width) + 1)
    return SampledStepPrice(
        upper, est.total, 0.0 if census else est.clt_halfwidth, 0.0 if census else est.eb_halfwidth,
        len(indices), space.population, len({values[i] for i in indices}), span, float(range_T),
        out_of_contract, space.digest,
        hashlib.sha256(canonical([list(indices), [str(values[i]) for i in indices]])).hexdigest(),
        hashlib.sha256(canonical([[i, repr(dict(checked).get(i))] for i in indices])).hexdigest())


def gate_met(halfwidth: float, total_T, max_fraction: Fraction = Fraction(1, 10_000)) -> bool:
    """Reported, never enforced: is the widest half-width within `max_fraction` of total T?"""
    return halfwidth <= float(Fraction(max_fraction) * Fraction(total_T))


# ---------------------------------------------------------------- running the sample


def price_controlled_step(space: IndexSpace, turn_of: Callable[[int, int], Fraction], *, eps: Fraction,
                          gradient, salt: bytes, total: int, range_T, conf: float = 0.95, workers: int = 4,
                          worker=None, clock: Callable[[], float] = time.monotonic,
                          allow_out_of_contract: bool = False) -> Tuple[SampledStepPrice, Dict[str, Any]]:
    """Draw the contract's sample for ONE step, certify every drawn site, and return (price, run).

    `turn_of(call, weight)` returns the site's exact signed turn; `worker(job=(turn, eps, gradient))` returns
    `(turn, expected-T upper)`, which is what makes this policy-general. The whole sample is always
    completed: a prefix of a simple random sample is still a simple random sample, but refusing a partial run
    keeps the reported population coverage honest."""
    if worker is None:
        raise SamplingConfigurationError(
            "no certification worker: the caller must supply worker((turn, eps, gradient)) -> (turn, T_upper)")
    if not 1 <= workers <= 16:
        raise SamplingConfigurationError("invalid worker count: workers must lie in 1..16")
    indices = select_indices(space, salt=salt, total=total)
    jobs = []
    for index in indices:
        call, weight = space.coordinates(index)
        turn = turn_of(call, weight)
        if type(turn) is not Fraction:
            raise ValueError("controlled site turn must be an exact Fraction")
        jobs.append((turn, eps, gradient))
    start = clock()
    with _runner(worker, workers) as run:
        priced = {t: hi for t, hi in run(jobs)}
    elapsed = clock() - start
    values, checked = {}, {}
    for index, (turn, _, _) in zip(indices, jobs):
        if turn not in priced and turn % 1 not in priced:
            raise CertificationRefusal(
                f"missing certified word: the drawn {space.scope} site at index {index} was not certified")
        values[index] = priced.get(turn, priced.get(turn % 1))
        checked[index] = turn
    price = combine(values, space, indices, range_T=range_T, conf=conf,
                    allow_out_of_contract=allow_out_of_contract, checked=checked)
    return price, {"elapsed_s": elapsed, "workers": workers, "certified_sites": len(priced),
                   "drawn_sites": len(indices), "population": space.population,
                   "space_sha256": space.digest}


# ---------------------------------------------------------------- the receipt


RECEIPT_BINDING = ("n_sampled", "space_sha256", "contract_sha256", "checked_words_sha256", "sample_sha256")
REQUIRED_PROVENANCE = (*RECEIPT_BINDING, "range_T", "range_basis", "observed_span_T", "priced_by", "profile",
                       "pool")
REQUIRED_INTERVAL = ("n_sampled", "population", "point_T", "clt_halfwidth", "eb_halfwidth", "basis")


def binding(receipt: Mapping[str, Any]):
    """What a retry of an accepted evaluation must reproduce: reuse the exact receipt, never re-estimate and
    keep the better of several time-varying estimates."""
    return tuple(receipt["provenance"][k] for k in RECEIPT_BINDING)


def check_receipt(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """An interval without its provenance is not evidence, and an estimate must not look like a bound."""
    for key in ("verdict", "profile", "scopes", "interval", "steps", "provenance", "run", "rankable"):
        if key not in receipt:
            raise ValueError(f"receipt is missing {key!r}")
    for key in REQUIRED_INTERVAL:
        if key not in receipt["interval"]:
            raise ValueError(f"receipt is missing interval.{key}")
    for key in REQUIRED_PROVENANCE:
        if key not in receipt["provenance"]:
            raise ValueError(f"receipt is missing provenance.{key}")
    check_t_fields(receipt["interval"])
    for step, block in receipt["steps"].items():
        check_t_fields(block)
        for key in ("n_sampled", "population", "space_sha256", "sample_sha256"):
            if key not in block:
                raise ValueError(f"receipt is missing steps.{step}.{key}")
    return receipt


def cost_policy_controlled(spaces: Mapping[int, IndexSpace], turn_of: Mapping[int, Callable], eps: Fraction, *,
                           worker, gradient, pool, qft_ceilings, gradient_prepare, run_sites: int,
                           certified_fixed=None, contract=None, workers: int = 4, budget_seconds=None,
                           clock: Callable[[], float] = time.monotonic, allow_out_of_contract: bool = False,
                           also_asked: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Cost an arbitrary policy's controlled Steps 1/5 adaptive (B3) phase from its own index spaces.

    `spaces[step]` and `turn_of[step]` describe each step's population; `eps` is the per-site half-diamond the
    reference plan charges EVERY synthesized site, and `pool`, `qft_ceilings`, `gradient_prepare` and
    `run_sites` are that plan's own budget terms, which `rotation_pool.check_partition` verifies close
    exactly. Both steps are priced against ONE partition and reported as one endpoint, so they are one
    claimant on the pool. `also_asked` names any OTHER claimant (`rotation_pool.QFT_DESIGN`, or a Step 2 or
    Step 4 scope); naming one refuses the request rather than pricing half of it.

    Returns a certified-interval receipt whose T is structurally an estimate. Raises `ResourceStop`
    (INCOMPLETE, no receipt) if the wall budget was overrun, `OutOfContractRefusal` on a range excess,
    `CertificationRefusal` if a drawn site has no certified word, `rotation_pool.PoolPartitionError` /
    `PoolDoubleSpend` if the budget is divided twice, `SamplingInfrastructureError` on worker failure and
    `SamplingConfigurationError` on a bad request."""
    rotation_pool.refuse_combination((*SCOPES, *also_asked))
    contract = load_contract() if contract is None else dict(contract)
    if contract.get("schema") != SCHEMA:
        raise SamplingConfigurationError(
            f"this lane prices the {SCHEMA} contract only; got {contract.get('schema')!r}")
    if sorted(spaces) != list(STEPS) or sorted(turn_of) != list(STEPS):
        raise SamplingConfigurationError(
            f"the controlled lane prices steps {list(STEPS)} together, in one run against one partition; got "
            f"spaces {sorted(spaces)} and turn functions {sorted(turn_of)}")
    for step, space in spaces.items():
        if not isinstance(space, IndexSpace) or space.step != step:
            raise SamplingConfigurationError(f"spaces[{step}] must be an IndexSpace for step {step}")

    scope_sites = sum(space.population for space in spaces.values())
    partition = rotation_pool.check_partition(pool=pool, qft_ceilings=qft_ceilings,
                                              gradient_prepare=gradient_prepare, per_site=eps,
                                              run_sites=run_sites, certified_fixed=certified_fixed)
    # Each step is a scope of allocated sites, and the two together must still fit the one allocation: a
    # per-step check alone would admit two halves that each fit and together do not.
    per_step = {space.scope: rotation_pool.check_scope(partition, scope=space.scope,
                                                       scope_sites=space.population, per_site=eps)
                for space in spaces.values()}
    if scope_sites > partition["run_sites"]:
        raise rotation_pool.PoolDoubleSpend(
            f"REFUSED: Steps 1 and 5 cover {scope_sites} sites together of a {partition['run_sites']}-site "
            f"rotation census; the two scopes do not both fit the reference allocation, so pricing them "
            f"against it would charge sites the run never allocated")
    allocation = {**partition, "scope": list(SCOPES), "scope_sites": scope_sites,
                  "retained_outside_scope_sites": partition["run_sites"] - scope_sites,
                  "scope_allocated": str(Fraction(eps) * scope_sites), "takes_a_second_share": False,
                  "per_step": {scope: allocation["scope_allocated"] for scope, allocation in per_step.items()}}

    budget = float(contract["budget_seconds"]) if budget_seconds is None else float(budget_seconds)
    salt, total = bytes.fromhex(contract["salt_hex"]), int(contract["sample_total"])
    range_T, conf = Fraction(contract["range_T"]), float(contract["conf"])
    prices: Dict[int, SampledStepPrice] = {}
    runs: Dict[int, Dict[str, Any]] = {}
    start = clock()
    for step in STEPS:
        prices[step], runs[step] = price_controlled_step(
            spaces[step], turn_of[step], eps=eps, gradient=gradient, salt=salt, total=total, range_T=range_T,
            conf=conf, workers=workers, worker=worker, clock=clock,
            allow_out_of_contract=allow_out_of_contract)
    elapsed = clock() - start
    run = {"elapsed_s": elapsed, "budget_seconds": budget, "workers": workers,
           "budget_exceeded": elapsed > budget, "steps": {str(s): runs[s] for s in STEPS}}
    if run["budget_exceeded"]:
        raise ResourceStop(
            f"incomplete: the sampled controlled Steps 1/5 phase overran its {budget:g} s budget "
            f"({elapsed:.6g} s); the sample is drawn in full or not at all, so no endpoint is claimed and no "
            f"success receipt is emitted", {**run, "scopes": list(SCOPES)})

    ranked = sum((p.ranked_upper for p in prices.values()), Fraction(0))
    halfwidth = max(max(p.clt_halfwidth, p.eb_halfwidth) for p in prices.values())
    n_sampled = sum(p.n_sampled for p in prices.values())
    out_of_contract = any(p.out_of_contract for p in prices.values())
    span = max(p.observed_span_T for p in prices.values())
    receipt = {
        "verdict": "ACCEPTED",
        "profile": PROFILE,
        "scopes": list(SCOPES),
        "rankable": not out_of_contract,
        "interval": {
            **t_fields("sampledEstimate", SITE_T_KIND, str(ranked)),
            "no_bound_basis": NO_BOUND_BASIS,
            "n_sampled": n_sampled, "population": scope_sites,
            "point_T": repr(math.fsum(p.point for p in prices.values())),
            "clt_halfwidth": max(p.clt_halfwidth for p in prices.values()),
            "eb_halfwidth": max(p.eb_halfwidth for p in prices.values()),
            "widest_halfwidth": halfwidth, "conf": conf, "basis": RANKED_BASIS,
            "gate_halfwidth_fraction": contract["max_halfwidth_fraction"],
            "failure_fraction_upper": min(1.0, 3 / n_sampled), "failure_fraction_basis": FAILURE_BASIS,
        },
        "steps": {str(step): {
            **t_fields("sampledEstimate", SITE_T_KIND, str(prices[step].ranked_upper)),
            "n_sampled": prices[step].n_sampled, "population": prices[step].population,
            "point_T": repr(prices[step].point), "clt_halfwidth": prices[step].clt_halfwidth,
            "eb_halfwidth": prices[step].eb_halfwidth,
            "distinct_sampled_turns": prices[step].distinct_sampled_turns,
            "observed_span_T": prices[step].observed_span_T, "range_T": prices[step].range_T,
            "out_of_contract": prices[step].out_of_contract,
            "space_sha256": prices[step].space_sha256, "sample_sha256": prices[step].sample_sha256,
            "checked_words_sha256": prices[step].checked_words_sha256,
        } for step in STEPS},
        "provenance": {
            "n_sampled": n_sampled,
            "space_sha256": hashlib.sha256(
                canonical([prices[s].space_sha256 for s in STEPS])).hexdigest(),
            "sample_sha256": hashlib.sha256(
                canonical([prices[s].sample_sha256 for s in STEPS])).hexdigest(),
            "checked_words_sha256": hashlib.sha256(
                canonical([prices[s].checked_words_sha256 for s in STEPS])).hexdigest(),
            "contract_sha256": contract_digest(contract),
            "range_T": str(range_T),
            "range_basis": RANGE_T_BASIS,
            "observed_span_T": span,
            "out_of_contract": out_of_contract,
            "half_diamond_per_site": str(eps),
            "sites": scope_sites,
            "pool": allocation,
            "profile": PROFILE,
            "priced_by": {"entry": "export_v2.controlled_sampled_fallback.cost_policy_controlled",
                          "module": "evaluation/export_v2/controlled_sampled_fallback.py",
                          "estimator": "evaluation/export_v2/sampled_fallback.py",
                          "sha256": pricer_fingerprint()},
        },
        "run": run,
    }
    if out_of_contract:         # only reachable in diagnostic mode; say so wherever the numbers go
        receipt["verdict"] = "DIAGNOSTIC-NON-RANKABLE"
        receipt["interval"]["basis"] = (f"NON-RANKABLE diagnostic: observed span {span:.6g} T exceeds range_T "
                                        f"{float(range_T):.6g} T (out of contract); " + RANKED_BASIS)
    return check_receipt(receipt)
