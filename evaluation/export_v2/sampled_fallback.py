"""Sampled costing of a Step 2 B3 fallback selection, from the submitted policy's OWN angle inventory.

Why sampling is allowed here.  The epoch model already costs rotations by a MEASURED MEAN:
`epoch.model_of()["rotation"]["synthesis"] == "Ross-Selinger measured mean, 3.075*log2(1/eps) + 0.86"`
(`epoch.py:97`).  The default synthesis method's price is therefore an empirical summary of measured
syntheses, not a per-site derivation.  A sampled interval for the B3 fallback is the same epistemic
standard, applied to a method whose words must be derived rather than read off a fitted mean: parity with
Ross-Selinger, not a relaxation of it.  Owner's ruling, 2026-11-24, written up in the integration readiness
note `readiness/pre-push/SAMPLED-FALLBACK-REVERSAL.md`: this reverses the earlier "defer public sampled
ranking" scope for the fallback costing question only.

Policy-generality.  Nothing here names a fixture.  The sample is a function of the exactly enumerated
inventory the policy itself declares (turns, multiplicities, sign-bit counts) and the public contract salt
only: each turn is ranked by sha256(salt | inventory digest | turn) and a stratum keeps its k lowest ranks.
No seed argument and no source bytes enter it.  Binding the rank to the whole inventory means any change to
the inventory reshuffles the entire sample (the salt is public, so a per-turn rank alone would let a
submitter steer which turns are sampled); only global grinding over whole-policy variants remains, which is
disclosed.  The per-turn certification worker is supplied by the caller, so the synthesis backend is a
parameter of the costing and not a frozen operator bank.

The three rules that make sampling honest, and where they live:

1. A RANGE EXCESS IS OUT OF CONTRACT.  `range_T` is calibrated, not proved on new inventories.  A stratum
   whose observed span of expected T exceeds it raises `OutOfContractRefusal`: refused for public ranking,
   never silently widened.  Only the explicit local diagnostic (`allow_out_of_contract=True`) prices it, and
   that price is flagged NON-RANKABLE.
2. A RESOURCE STOP IS INCOMPLETE.  Stopping after a whole round that fit the budget is in contract and
   scores.  Overrunning the budget raises `ResourceStop` with `verdict == "INCOMPLETE"`: never a refusal,
   never a pass, and no success receipt is emitted (the raise is the only exit).
3. AN INTERVAL WITHOUT ITS PROVENANCE IS NOT EVIDENCE.  `cost_policy_fallback` emits the sample count, the
   inventory digest, the interval, the contract and range bound, and which code priced it; `check_receipt`
   refuses a receipt missing any of them.

Statistical standing.  The endpoint is a statistical estimate conditional on its assumptions (the supplied
range bound, and an i.i.d. empirical-Bernstein bound applied to draws taken without replacement), not a hard
resource or success bound.  Sampling runs in whole deterministic rounds, so a slower runner stops earlier
and typically gets a wider endpoint; a retry of an accepted evaluation must reuse the exact receipt
(`RECEIPT_BINDING`), never re-estimate and keep the better of several time-varying estimates.

Ported from the research lane (`postprocess/synthesis/src/synthback/backends/step2_sampled_costing.py` and
`src/synthback/stats/{stratified,bounds}.py`, branch `local/prepush-L3`), trimmed to the Step 2 fallback and
made self-contained so `evaluation/export_v2` keeps no research dependency.  Kept here, outside
`evaluation/engine`, so adding it does not move `engineRevision` (and so the epoch) -- see `pricing.py:16-25`.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Tuple

SCHEMA = "forshor.step2-sampled-contract/v1"
PROFILE = "policy-general-step2-b3-sampled/v1"
CONTRACT = Path(__file__).parent / "step2_sampled_contract.json"
RANKED_BASIS = "upper endpoint, larger of CLT and empirical-Bernstein, 95%"
FAILURE_BASIS = ("rule-of-three approximation (3/n, clamped at 1) over distinct sampled turns; "
                 "not a circuit-level failure probability")
RANGE_BASIS = ("contract constant on the per-stratum spread of expected T, calibrated and not proved on new "
               "inventories; an observed excess is out of contract and refused, never silently widened")
MAX_DENOMINATOR_BITS = 8192
SIGN_CLASSES = ("none", "some", "all")


class CertificationRefusal(ValueError):
    """A MISSING CERTIFIED WORD: a sampled turn has no word meeting its allowance (certificate missed it, or
    the search was exhausted under its caps).  An estimate refusal; never a worker failure."""


class OutOfContractRefusal(ValueError):
    """The observed per-stratum span of expected T exceeds the contract's `range_T`: out of contract.

    Refused for public ranking, never silently widened.  The local diagnostic still prices it, flagged."""


class SamplingConfigurationError(ValueError):
    """The caller asked for an invalid run (e.g. a worker count outside 1..16): a configuration error."""


class SamplingInfrastructureError(RuntimeError):
    """WORKER FAILURE: a pricing worker died or hit an unexpected error.  Infrastructure failure, not a
    refusal, and it says nothing about whether the turn has a certified word."""


class ResourceStop(RuntimeError):
    """A wall-clock or resource stop: the evaluation is INCOMPLETE.

    Not a refusal (the policy is not shown to be wrong) and not a pass (no endpoint is claimed).  Raising is
    the only exit, so no success receipt can be emitted for a run that stopped this way."""

    verdict = "INCOMPLETE"

    def __init__(self, message: str, run: Dict[str, Any] | None = None):
        super().__init__(message)
        self.run = dict(run or {})


# ---------------------------------------------------------------- the contract


def load_contract(path=CONTRACT, schema: str = SCHEMA) -> Dict[str, Any]:
    """`schema` is a parameter so the Step 4 adaptive lane can validate its own contract with these same
    field rules (research lane `staging_step2_sampled.py`, commit `a12e431`, made the same change)."""
    c = json.loads(Path(path).read_text())
    try:
        ok = (c["schema"] == schema and len(bytes.fromhex(c["salt_hex"])) == 32 and int(c["sample_total"]) >= 2
              and int(c["round_size"]) >= 1 and float(c["budget_seconds"]) > 0 and c["conf"] == 0.95
              and Fraction(c["range_T"]) > 0 and Fraction(c["max_halfwidth_fraction"]) > 0)
    except (KeyError, ValueError, TypeError):
        ok = False
    if not ok:
        raise ValueError("invalid sampled-costing contract")
    return c


def contract_digest(contract: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def pricer_fingerprint() -> str:
    """Which code priced it: the digest of this module's own source."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


# ---------------------------------------------------------------- an estimate is not a bound

#: What kind of number a sampled T is.  A CONTRACT-FIXED ESTIMATE is the upper endpoint of an interval drawn
#: from a sample under the contract's assumptions; a CIRCUIT BOUND is a bound on a circuit that was actually
#: bound, word for word.  They are never the same claim, and `t_fields` makes them structurally
#: uncollapsible -- the same thing lane XA did for deterministic vs expected-adaptive T in
#: `primitive_overlay.py:215-217`.
T_KINDS = ("sampledEstimate", "circuitBound")
#: Orthogonal to the above, and carried alongside it: what the per-site T *is*.  XA's axis
#: (`primitive_overlay.py:15-19`).  A repeat-until-success B3 site's T is an UNCONDITIONAL EXPECTATION, so a
#: sampled estimate of a B3 phase is an estimate OF AN EXPECTATION -- twice removed from a worst case, and a
#: consumer must be able to see both steps.
SITE_T_KINDS = ("deterministic", "expectedAdaptive")
T_FIELD = {"sampledEstimate": "tSampledEstimateUpper", "circuitBound": "tCircuitBound"}


def t_fields(kind: str, site_kind: str, value) -> Dict[str, Any]:
    """`tKind` plus EXACTLY ONE of `tSampledEstimateUpper` / `tCircuitBound`, and never a bare `T`.

    A consumer that wants the number has to read a field whose name says what the number is.  There is no
    key a careless reader can pick up and rank as if it were a bound."""
    if kind not in T_KINDS:
        raise ValueError(f"unknown T kind {kind!r}: expected one of {list(T_KINDS)}")
    if site_kind not in SITE_T_KINDS:
        raise ValueError(f"unknown per-site T kind {site_kind!r}: expected one of {list(SITE_T_KINDS)}")
    return {"tKind": kind, "tSiteKind": site_kind, "estimate": kind == "sampledEstimate",
            "circuit_bound": kind == "circuitBound",
            **{field: (value if field == T_FIELD[kind] else None) for field in T_FIELD.values()}}


def check_t_fields(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Refuse a payload that carries a bare `T`, both kinds of number at once, or neither."""
    for bare in ("T", "t", "ranked_upper_T", "rankedT"):
        if bare in payload:
            raise ValueError(f"a sampled price must not carry a bare {bare!r} field: say which kind of number it is")
    kind = payload.get("tKind")
    if kind not in T_KINDS:
        raise ValueError(f"a sampled price must declare tKind in {list(T_KINDS)}; got {kind!r}")
    if payload.get("tSiteKind") not in SITE_T_KINDS:
        raise ValueError(f"a sampled price must declare tSiteKind in {list(SITE_T_KINDS)}")
    present = [f for f in T_FIELD.values() if payload.get(f) is not None]
    if present != [T_FIELD[kind]]:
        raise ValueError(f"tKind {kind!r} requires exactly {T_FIELD[kind]!r} and no other T field; got {present}")
    if payload.get("estimate") is not (kind == "sampledEstimate") or \
            payload.get("circuit_bound") is not (kind == "circuitBound"):
        raise ValueError(f"the estimate/circuit_bound labels contradict tKind {kind!r}")
    return payload


# ---------------------------------------------------------------- stratified estimation


@dataclass(frozen=True)
class Stratum:
    name: str
    population: int
    samples: Tuple[float, ...]
    range_bound: float  # a known bound on max - min of the stratum's values


@dataclass(frozen=True)
class Estimate:
    total: float
    clt_halfwidth: float
    eb_halfwidth: float
    conf: float
    n_samples: int


def empirical_bernstein_halfwidth(n: int, sample_var: float, value_range: float, conf: float) -> float:
    """Maurer-Pontil (arXiv:0907.3740) Thm 4, rescaled from [0, 1] to a range of width R:
    half-width = sqrt(2 V_n L / n) + 7 R L / (3 (n - 1)), L = ln(4 / (1 - conf)) two-sided.

    The theorem is an i.i.d. (with-replacement) result; our draws are taken WITHOUT replacement, so using it
    here is an assumption, not a theorem.  Bardenet and Maillard, Bernoulli 21(3), 2015 (unverified here)
    give the without-replacement empirical-Bernstein inequality a proof would need."""
    if n < 2:
        raise ValueError(f"need n >= 2, got {n}")
    if value_range < 0 or sample_var < 0 or not 0.0 < conf < 1.0:
        raise ValueError("need value_range >= 0, sample_var >= 0, conf in (0, 1)")
    L = math.log(4.0 / (1.0 - conf))
    return math.sqrt(2.0 * sample_var * L / n) + 7.0 * value_range * L / (3.0 * (n - 1))


def _mean_var(xs: Tuple[float, ...]) -> Tuple[float, float]:
    n = len(xs)
    m = math.fsum(xs) / n
    v = math.fsum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return m, v


def stratified_total(strata, conf: float = 0.95) -> Estimate:
    """Stratified total (Cochran 1977, ch. 5) with a CLT and a union-bounded empirical-Bernstein half-width.

    Var(T) = sum_h N_h^2 (1 - n_h/N_h) s_h^2 / n_h; the finite-population factor makes a stratum sampled in
    full exact, and such a stratum contributes no EB width and does not count toward the union bound."""
    if not strata:
        raise ValueError("no strata")
    if not 0.0 < conf < 1.0:
        raise ValueError("conf must lie in (0, 1)")
    stats = []
    for s in strata:
        n = len(s.samples)
        if s.population < 1 or n < 1 or n > s.population:
            raise ValueError(f"stratum {s.name!r}: need 1 <= n_samples <= population")
        census = n == s.population
        if n < 2 and not census:
            raise ValueError(f"stratum {s.name!r}: need >= 2 samples (or a full enumeration)")
        stats.append((s, n, census, *_mean_var(s.samples)))
    z = NormalDist().inv_cdf(0.5 + conf / 2.0)
    k = sum(1 for _, _, census, *_ in stats if not census)
    conf_h = 1.0 - (1.0 - conf) / k if k else conf
    total = math.fsum(s.population * m for s, _, _, m, _ in stats)
    var_total = math.fsum(s.population ** 2 * (1.0 - n / s.population) * v / n for s, n, _, _, v in stats)
    eb = math.fsum(0.0 if census else s.population * empirical_bernstein_halfwidth(n, v, s.range_bound, conf_h)
                   for s, n, census, _, v in stats)
    return Estimate(total, z * math.sqrt(var_total), eb, conf, sum(n for _, n, *_ in stats))


# ---------------------------------------------------------------- the policy's own census


def rank(turn: Fraction, salt: bytes) -> bytes:
    return hashlib.sha256(salt + b"|" + f"{turn.numerator}/{turn.denominator}".encode()).digest()


def inventory_digest(turns: Mapping[Fraction, int], signs: Mapping[Fraction, int]) -> bytes:
    """The digest of the WHOLE declared inventory the sample was drawn from, not of the sample."""
    rows = sorted(f"{t.numerator}/{t.denominator}\t{n}\t{signs.get(t, 0)}" for t, n in turns.items())
    return hashlib.sha256("\n".join(rows).encode()).digest()


def census_of(sites: Iterable[Tuple[Fraction, bool]]):
    """(turns, signs) from a policy's own rotation sites: (canonical turn in [0, 1), sign bit)."""
    turns: Dict[Fraction, int] = {}
    signs: Dict[Fraction, int] = {}
    for turn, sign in sites:
        turns[turn] = turns.get(turn, 0) + 1
        signs[turn] = signs.get(turn, 0) + bool(sign)
    return turns, signs


def stratum_key(n: int, sign: int) -> Tuple[str, int]:
    """(sign class, multiplicity bit length): derivable from the inventory alone."""
    return ("none" if sign == 0 else "all" if sign == n else "some", n.bit_length())


def allocate(populations: Mapping[Tuple[str, int], int], total: int) -> Dict[Tuple[str, int], int]:
    """Deterministic split of `total` draws: floor of min(pop, 2) each, rest proportional to spare capacity."""
    floors = {k: min(p, 2) for k, p in populations.items()}
    spare = {k: populations[k] - floors[k] for k in populations}
    extra = total - sum(floors.values())
    if extra <= 0:
        return floors
    capacity = sum(spare.values())
    if extra >= capacity:
        return dict(populations)
    shares = {k: extra * spare[k] / capacity for k in sorted(spare)}
    out = {k: floors[k] + int(shares[k]) for k in shares}
    leftover = extra - sum(int(s) for s in shares.values())
    for k in sorted(shares, key=lambda k: (-(shares[k] - int(shares[k])), k))[:leftover]:
        out[k] += 1
    return out


def select_sample(turns: Mapping[Fraction, int], signs: Mapping[Fraction, int], *, salt: bytes, total: int):
    """(sampled turns per stratum, stratum populations, exact eighth-turn sites with multiplicity)."""
    exact: Dict[Fraction, int] = {}
    groups: Dict[Tuple[str, int], list] = {}
    for t, n in turns.items():
        if not isinstance(t, Fraction) or not 0 <= t < 1:
            raise ValueError(f"turn must be a canonical Fraction in [0, 1): {t!r}")
        if t.denominator.bit_length() > MAX_DENOMINATOR_BITS or t.numerator.bit_length() > MAX_DENOMINATOR_BITS:
            raise ValueError("turn exceeds the bounded work envelope")
        if (8 * t).denominator == 1:
            exact[t] = n
        else:
            groups.setdefault(stratum_key(n, signs.get(t, 0)), []).append(t)
    populations = {k: len(v) for k, v in sorted(groups.items())}
    take = allocate(populations, total)
    bound = hashlib.sha256(salt + b"|inventory|" + inventory_digest(turns, signs)).digest()
    sample = {k: sorted(groups[k], key=lambda t: rank(t, bound))[:take[k]] for k in populations}
    return sample, populations, exact


def multiplicity_range(turns: Mapping[Fraction, int], signs: Mapping[Fraction, int]):
    """(n_min, n_max) over the whole stratum, known exactly from the policy's inventory."""
    out: Dict[Tuple[str, int], Tuple[int, int]] = {}
    for t, n in turns.items():
        if (8 * t).denominator != 1:
            k = stratum_key(n, signs.get(t, 0))
            lo, hi = out.get(k, (n, n))
            out[k] = (min(lo, n), max(hi, n))
    return out


# ---------------------------------------------------------------- the interval


@dataclass(frozen=True)
class SampledPrice:
    ranked_upper: Fraction  # integer-valued; exact T of eighth-turn sites plus the sampled estimate's upper endpoint
    point: float
    clt_halfwidth: float
    eb_halfwidth: float
    n_sampled: int
    n_strata: int
    exact_T: int
    failure_fraction_upper: float   # rule-of-three 3/n clamped at 1; see FAILURE_BASIS
    sample_sha256: str
    observed_span_T: float = 0.0    # widest observed max-min of E over the strata estimated from a sample
    range_T: float = 0.0            # the contract's range constant used by the EB term
    inventory_sha256: str = ""      # digest of the whole declared inventory the sample was drawn from
    checked_words_sha256: str = ""  # digest of the certified words actually checked, per priced turn
    out_of_contract: bool = False   # observed span > range_T (diagnostic mode only): NON-RANKABLE


def range_bound(values: Mapping[Fraction, Fraction], n_lo: int, n_hi: int, range_T: Fraction) -> Fraction:
    """Bound on the spread (max - min) of the quantity actually estimated, n * E, over a stratum.

    Two documented bounds enter, both conservatively.  (1) The multiplicity n of every turn in the stratum
    lies in [n_lo, n_hi]; exact, read off the whole inventory.  (2) The expected T, E, lies in some interval
    [a, a + W] with W = max(range_T, observed span) (range_T is a contract constant, not proved); that
    interval is not assumed centred on the sample, it only has to contain the observed values, so
    a is in [max(0, obs_max - W), obs_min].  Then n*E <= n_hi*(a + W) and n*E >= n_lo*a, so
        spread <= n_hi*W + (n_hi - n_lo)*a,
    largest at a = obs_min.  With constant multiplicity this is n * W."""
    lo, hi = min(values.values()), max(values.values())
    width = max(range_T, hi - lo)
    return n_hi * width + (n_hi - n_lo) * lo


def combine(sampled_values, populations, multiplicity, exact_T, *, n_range, range_T, conf: float = 0.95,
            allow_out_of_contract: bool = False) -> SampledPrice:
    """sampled_values[key][turn] is the turn's expected-T upper end; the item value is multiplicity * that.

    A stratum estimated from a sample whose observed span of E exceeds `range_T` is OUT OF CONTRACT: it
    raises `OutOfContractRefusal`, because widening the EB range to the observed span would leave the ranked
    endpoint resting on a range nobody calibrated.  `allow_out_of_contract=True` (local diagnostic only)
    widens it and flags the price NON-RANKABLE."""
    strata, exact_sum, n_sampled, every_census, names, span = [], Fraction(0), 0, True, [], Fraction(0)
    for key in sorted(populations):
        values = sampled_values[key]
        turns = sorted(values)
        if len(turns) < populations[key]:        # only estimated strata use the range constant
            span = max(span, max(values.values()) - min(values.values()))
        spread = range_bound(values, *n_range[key], range_T)
        strata.append(Stratum(str(key), populations[key],
                              tuple(float(multiplicity[t] * values[t]) for t in turns), float(spread)))
        exact_sum += sum((multiplicity[t] * values[t] for t in turns), Fraction(0))
        every_census &= len(turns) == populations[key]
        n_sampled += len(turns)
        names += [str(t) for t in turns]
    if span > range_T and not allow_out_of_contract:
        raise OutOfContractRefusal(
            f"out of contract: observed span {float(span):.6g} T of expected T within a stratum exceeds range_T "
            f"{float(range_T):.6g} T; not rankable (widening it silently is not allowed; the local diagnostic "
            f"can still price it)")
    digest = hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()
    report = dict(observed_span_T=float(span), range_T=float(range_T), out_of_contract=span > range_T)
    if not strata:
        return SampledPrice(Fraction(exact_T), 0.0, 0.0, 0.0, 0, 0, exact_T, 0.0, digest, **report)
    est = stratified_total(strata, conf)
    if every_census:  # full enumeration: no sampling uncertainty, no float slack
        return SampledPrice(Fraction(math.ceil(exact_T + exact_sum)), est.total, 0.0, 0.0, n_sampled,
                            len(strata), exact_T, 0.0, digest, **report)
    upper = exact_T + est.total + max(est.clt_halfwidth, est.eb_halfwidth)
    return SampledPrice(Fraction(math.ceil(upper) + 1), est.total, est.clt_halfwidth, est.eb_halfwidth, n_sampled,
                        len(strata), exact_T, min(1.0, 3 / n_sampled), digest, **report)


def gate_met(price: SampledPrice, total_T: Fraction, max_fraction: Fraction = Fraction(1, 10_000)) -> bool:
    """Reported, never enforced: is the larger half-width within `max_fraction` of total T?"""
    return max(price.clt_halfwidth, price.eb_halfwidth) <= float(max_fraction * total_T)


# ---------------------------------------------------------------- running the sample


@contextmanager
def _runner(worker, workers):
    """One persistent pool for all rounds.

    A ValueError or RuntimeError from the worker means the turn did not certify (CertificationRefusal); a
    dead worker or any other exception is a SamplingInfrastructureError.  ProcessPoolExecutor raises
    BrokenProcessPool when a worker dies, so a killed worker cannot hang the job."""
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    pool = None if workers == 1 else ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn"))

    def run(jobs):
        try:
            yield from (map(worker, jobs) if pool is None else pool.map(worker, list(jobs), chunksize=4))
        except BrokenProcessPool as error:
            raise SamplingInfrastructureError(f"worker failure: a pricing worker process died: {error}") from error
        except (ValueError, RuntimeError) as error:
            raise CertificationRefusal(f"missing certified word: sampled turn not certified: {error}") from error
        except Exception as error:
            raise SamplingInfrastructureError(
                f"worker failure: unexpected pricing error {type(error).__name__}: {error}") from error

    try:
        yield run
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)


def price_step2_budgeted(turns, signs, eps: Fraction, contract: Mapping[str, Any], *, budget_seconds, workers: int = 4,
                         worker=None, clock: Callable[[], float] = time.monotonic,
                         allow_out_of_contract: bool = False):
    """Price a Step 2 B3 fallback from the contract's deterministic sample, as many whole rounds as fit.

    `eps` is the per-site half-diamond; `worker(job=(turn, eps)) -> (turn, words, error, (T_lo, T_hi))` is the
    caller's certified B3 worker, which is what makes this policy-general: the synthesis backend is a
    parameter, not a frozen operator bank.  Round r targets `allocate(populations, r * round_size)` turns per
    stratum, in hash-rank order, so any prefix of whole rounds is a valid stratified random sample.  The
    first round always completes; later rounds start only when elapsed + 1.25 * slowest_round <=
    budget_seconds (None = no deadline).  Started rounds are never discarded.  A slower runner stops after
    fewer rounds and therefore typically gets a wider endpoint.
    Returns (SampledPrice, run) with run = rounds, n_cap, cap_reached, budget_exceeded, elapsed_s."""
    if worker is None:
        raise SamplingConfigurationError(
            "no certification worker: the caller must supply worker((turn, eps)) -> (turn, words, error, (T_lo, T_hi))")
    if not 1 <= workers <= 16:
        raise SamplingConfigurationError("invalid worker count: workers must lie in 1..16")
    cap, round_size = int(contract["sample_total"]), int(contract.get("round_size", 500))
    sample, populations, exact = select_sample(turns, signs, salt=bytes.fromhex(contract["salt_hex"]), total=cap)
    floors = sum(min(p, 2) for p in populations.values())
    taken = {k: 0 for k in populations}
    upper: Dict[Fraction, Fraction] = {}
    checked: Dict[Fraction, Any] = {}
    start, slowest, rounds = clock(), 0.0, 0
    with _runner(worker, workers) as run:
        while True:
            if rounds and budget_seconds is not None and (clock() - start) + 1.25 * slowest > budget_seconds:
                break
            r = rounds + 1
            if r * round_size >= cap:
                new = {k: len(sample[k]) for k in populations}
            else:
                target = allocate(populations, max(r * round_size, floors))
                new = {k: max(taken[k], min(target[k], len(sample[k]))) for k in populations}
            jobs = ([(t, eps) for t in (exact if r == 1 else ())]
                    + [(t, eps) for k in populations for t in sample[k][taken[k]:new[k]]])
            began = clock()
            for t, words, _, (_, hi) in run(jobs):
                upper[t], checked[t] = hi, words
            slowest = max(slowest, clock() - began)
            taken, rounds = new, r
            if all(taken[k] == len(sample[k]) for k in populations):
                break
    elapsed = clock() - start
    exact_T = sum((exact[t] * upper[t] for t in exact), Fraction(0))
    if exact_T.denominator != 1:
        raise ValueError("exact eighth-turn words must have integer T")
    values = {k: {t: upper[t] for t in sample[k][:taken[k]]} for k in populations}
    price = combine(values, populations, turns, int(exact_T), n_range=multiplicity_range(turns, signs),
                    range_T=Fraction(contract["range_T"]), conf=float(contract["conf"]),
                    allow_out_of_contract=allow_out_of_contract)
    price = replace(price, inventory_sha256=inventory_digest(turns, signs).hex(),
                    checked_words_sha256=hashlib.sha256(
                        "\n".join(f"{t}\t{checked[t]!r}" for t in sorted(checked)).encode()).hexdigest())
    return price, {"rounds": rounds, "n_cap": cap, "elapsed_s": elapsed,
                   "cap_reached": all(taken[k] == len(sample[k]) for k in populations),
                   "budget_exceeded": budget_seconds is not None and elapsed > budget_seconds}


# ---------------------------------------------------------------- the receipt


RECEIPT_BINDING = ("n_sampled", "inventory_sha256", "contract_sha256", "checked_words_sha256", "sample_sha256")
REQUIRED_PROVENANCE = (*RECEIPT_BINDING, "range_T", "range_basis", "observed_span_T", "priced_by", "profile")
REQUIRED_INTERVAL = ("n_sampled", "n_cap", "point_T", "ranked_upper_T", "clt_halfwidth", "eb_halfwidth", "basis")


def binding(receipt: Mapping[str, Any]):
    """What a retry of an accepted evaluation must reproduce: reuse the exact receipt, never re-estimate
    and keep the better of several time-varying estimates."""
    return tuple(receipt["provenance"][k] for k in RECEIPT_BINDING)


def check_receipt(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    """An interval without its provenance is not evidence: refuse a receipt missing any required field."""
    for key in ("verdict", "profile", "interval", "provenance", "run", "rankable"):
        if key not in receipt:
            raise ValueError(f"receipt is missing {key!r}")
    for key in REQUIRED_INTERVAL:
        if key not in receipt["interval"]:
            raise ValueError(f"receipt is missing interval.{key}")
    for key in REQUIRED_PROVENANCE:
        if key not in receipt["provenance"]:
            raise ValueError(f"receipt is missing provenance.{key}")
    return receipt


def cost_policy_fallback(turns, signs, eps: Fraction, *, worker, contract=None, workers: int = 4,
                         budget_seconds=None, clock: Callable[[], float] = time.monotonic,
                         allow_out_of_contract: bool = False) -> Dict[str, Any]:
    """Cost an arbitrary policy's Step 2 B3 fallback from its own angle inventory, within a wall budget.

    Returns a certified-interval receipt.  Raises `ResourceStop` (INCOMPLETE, no receipt) if the budget was
    overrun, `OutOfContractRefusal` if a stratum's observed span exceeds the contract range,
    `CertificationRefusal` if a sampled turn has no certified word, `SamplingInfrastructureError` on worker
    failure and `SamplingConfigurationError` on a bad request."""
    contract = load_contract() if contract is None else dict(contract)
    load_contract_fields = (int(contract["sample_total"]), int(contract["round_size"]))
    if min(load_contract_fields) < 1 or Fraction(contract["range_T"]) <= 0:
        raise SamplingConfigurationError("invalid sampled-costing contract")
    budget = float(contract["budget_seconds"]) if budget_seconds is None else float(budget_seconds)
    price, run = price_step2_budgeted(turns, signs, eps, contract, budget_seconds=budget, workers=workers,
                                      worker=worker, clock=clock, allow_out_of_contract=allow_out_of_contract)
    if run["budget_exceeded"]:
        raise ResourceStop(
            f"incomplete: the sampled Step 2 fallback overran its {budget:g} s budget after {run['rounds']} round(s) "
            f"({run['elapsed_s']:.6g} s); no endpoint is claimed and no success receipt is emitted",
            {**run, "budget_seconds": budget})
    receipt = {
        "verdict": "ACCEPTED",
        "profile": PROFILE,
        "rankable": not price.out_of_contract,
        "estimate": True,
        "circuit_bound": False,
        "interval": {
            "n_sampled": price.n_sampled, "n_strata": price.n_strata, "n_cap": run["n_cap"],
            "point_T": repr(price.point), "ranked_upper_T": str(price.ranked_upper),
            "exact_site_T": price.exact_T,
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
            "sites": sum(turns.values()),
            "profile": PROFILE,
            "priced_by": {"entry": "export_v2.sampled_fallback.cost_policy_fallback",
                          "module": "evaluation/export_v2/sampled_fallback.py",
                          "sha256": pricer_fingerprint()},
        },
        "run": {**run, "budget_seconds": budget, "workers": workers},
    }
    if price.out_of_contract:   # only reachable in diagnostic mode; say so wherever the numbers go
        receipt["verdict"] = "DIAGNOSTIC-NON-RANKABLE"
        receipt["interval"]["basis"] = (f"NON-RANKABLE diagnostic: observed span {price.observed_span_T:.6g} T "
                                        f"exceeds range_T {price.range_T:.6g} T (out of contract); " + RANKED_BASIS)
    return check_receipt(receipt)
