"""The run-wide synthesis pool, and who is allowed to divide it.

ONE POOL. A run has a single synthesis error budget: the engine's rotation-fraction share of `delta`
(`engine/sto_currency.py` -- measured share 0.5, so the pool is 1/200 at delta 1e-2). Lane WX established
the partition rule for the catalytic QFT arm; lane XF measured that the pool has NO SLACK --
`R_synth * (pool / R_total) + deltaArm == pool` to a residual of -8.7e-19, with
`R_total 3802591380 = R_cat 350171640 + R_synth 3452419740` on the real engine at delta 1e-2. Every rotation
in the run is already allocated. A second method that re-divides the pool charges the same budget twice and
hands the submission free error.

THIS MODULE DERIVES NOTHING FROM THE CURRENCY. It takes the pool, the fixed terms and the per-site allowance
as EXACT parameters, read off the caller's own reference plan, and only CHECKS that they partition. It is
not a pricer and it never chooses an allowance; the pricers in `step4_sampled_fallback.py` and
`controlled_sampled_fallback.py` receive `eps` the same way, so no sampled pricer has a route to its own
slice of the budget.

THE PARTITION RULE, transcribed from the research ledger
(`synthback/backends/step2_fallback_ledger.py:147-210`, `measured_rotation_budget` /
`check_rotation_ledger`):

    sum(QFT approximation ceilings) + gradient_prepare + per_site * run_sites == pool        (exact)
    certified fixed error + per_site * run_sites <= pool
    the run's sites partition exactly into {this scope} + {everything else}, each charged `per_site`

The allocation is made from the QFT **ceilings**, not from their certified bounds, so the identity closes on
the allowances and the certified error is at most that. Both must hold, or a phase-product scope has taken
another family's budget.

WHY A B3 PHASE SCOPE IS NOT A SECOND SPENDER, ON ITS OWN. A Step 4 adaptive or controlled Steps 1/5 scope
re-prices sites that are ALREADY inside the `per_site * run_sites` term, at the SAME `per_site` allowance. It
moves sites between routes inside one term; it does not add a term. That is why `check_scope` demands
`scope_sites <= run_sites` and an identical allowance: those two facts are exactly what makes the re-pricing
free of charge against the pool.

WHY TWO OF THEM TOGETHER IS A SECOND SPENDER. Each scope's ledger reserves EVERY OTHER site at `per_site`
(`check_rotation_ledger` puts them in `residual_direct`) and adds the whole fixed term `qft + gradient`. Two
scopes priced independently therefore each account for the other's sites and each count the fixed term once,
so their totals cannot be added: the sum double-counts the fixed term and prices the overlap twice. The same
holds for a sampled phase scope plus a QFT design -- `qft_overlay` re-divides the pool by its own rule
(`deltaArm = pool * R_cat / R_total`), which is a DIFFERENT partition of the same pool from the one the
reference plan's ceilings already closed. `refuse_combination` refuses all of these by name.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Mapping

#: Every phase-product scope that can be sampled, and the module that prices it.
SAMPLED_SCOPES = {
    "step2": "export_v2.sampled_fallback",
    "step4": "export_v2.step4_sampled_fallback",
    "step1": "export_v2.controlled_sampled_fallback",
    "step5": "export_v2.controlled_sampled_fallback",
}
#: A QFT design is the other claimant on the pool. It is not a phase-product scope and is priced elsewhere.
QFT_DESIGN = "qft_design"


class PoolPartitionError(ValueError):
    """The declared pool, fixed terms and per-site allowance do not partition: REFUSED.

    Not a sampling failure and not a resource stop. The numbers the caller brought do not describe one
    budget divided once, so nothing priced against them means anything."""


class PoolDoubleSpend(ValueError):
    """Two methods were asked to divide the same pool: REFUSED for public ranking.

    Never resolved by picking a precedence, and never widened. The same call lane WY made for readout +
    design and lane XF made for a QFT design + a Step 2 fallback, for the same reason."""


def _exact(value, name: str) -> Fraction:
    if isinstance(value, float) or isinstance(value, bool) or not isinstance(value, (str, int, Fraction)):
        raise PoolPartitionError(f"{name} must be an exact rational, not {type(value).__name__}")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise PoolPartitionError(f"{name} is not an exact rational: {value!r}") from error


def _count(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise PoolPartitionError(f"{name} must be a nonnegative whole count, got {value!r}")
    return value


def check_partition(*, pool, qft_ceilings: Iterable[Any], gradient_prepare, per_site, run_sites: int,
                    certified_fixed=None) -> Mapping[str, Any]:
    """Check that one run's synthesis pool is divided exactly once, and return the division.

    `qft_ceilings` are the per-family approximation ALLOWANCES (not the certified bounds) the reference
    allocation was made from; `per_site` is the half-diamond every one of `run_sites` synthesized or
    selectively routed sites is charged. Raises `PoolPartitionError` naming the term that does not close."""
    pool = _exact(pool, "pool")
    ceilings = [_exact(c, "qft_ceiling") for c in qft_ceilings]
    prep = _exact(gradient_prepare, "gradient_prepare")
    eps = _exact(per_site, "per_site")
    sites = _count(run_sites, "run_sites")
    if not 0 < pool <= 1:
        raise PoolPartitionError(f"the synthesis pool must lie in (0, 1]: {pool}")
    if not 0 < eps <= 1:
        raise PoolPartitionError(f"the per-site half-diamond allowance must lie in (0, 1]: {eps}")
    if prep < 0 or any(c < 0 for c in ceilings) or sites < 1:
        raise PoolPartitionError("negative fixed term or empty rotation census")
    per_site_total = eps * sites
    closed = sum(ceilings) + prep + per_site_total
    if closed != pool:
        raise PoolPartitionError(
            f"the run-wide synthesis pool is not exactly the reference allocation: QFT ceilings "
            f"{float(sum(ceilings)):.6g} + gradient prepare {float(prep):.6g} + per-site "
            f"{float(per_site_total):.6g} = {float(closed):.6g}, pool {float(pool):.6g} "
            f"(residual {float(pool - closed):.6g}); a phase scope priced against this has taken another "
            f"family's budget")
    if certified_fixed is not None:
        fixed = _exact(certified_fixed, "certified_fixed")
        if fixed < 0 or fixed + per_site_total > pool:
            raise PoolPartitionError(
                f"over-budget synthesis ledger: certified fixed error {float(fixed):.6g} + per-site "
                f"{float(per_site_total):.6g} exceeds the pool {float(pool):.6g}")
    return {"pool": str(pool), "qft_ceilings": [str(c) for c in ceilings], "gradient_prepare": str(prep),
            "per_site": str(eps), "run_sites": sites, "per_site_total": str(per_site_total),
            "closes_exactly": True}


def check_scope(partition: Mapping[str, Any], *, scope: str, scope_sites: int, per_site) -> Mapping[str, Any]:
    """Check that a sampled phase scope re-prices sites the partition has ALREADY allocated.

    A scope is free of charge against the pool exactly when it is a subset of the run's synthesized sites and
    is charged the same allowance each. Anything else is a second spender."""
    if scope not in SAMPLED_SCOPES:
        raise PoolDoubleSpend(f"unknown phase-product scope {scope!r}: expected one of {sorted(SAMPLED_SCOPES)}")
    sites = _count(scope_sites, "scope_sites")
    eps = _exact(per_site, "per_site")
    if eps != Fraction(partition["per_site"]):
        raise PoolDoubleSpend(
            f"scope {scope!r} prices its sites at {eps}, but the run's partition charges "
            f"{partition['per_site']}: a scope priced at its own allowance takes a second share of one pool")
    if not 1 <= sites <= partition["run_sites"]:
        raise PoolDoubleSpend(
            f"scope {scope!r} covers {sites} sites of a {partition['run_sites']}-site rotation census: a scope "
            f"outside the reference allocation is not re-pricing allocated sites, it is adding them")
    return {**partition, "scope": scope, "scope_sites": sites,
            "retained_outside_scope_sites": partition["run_sites"] - sites,
            "scope_allocated": str(eps * sites), "takes_a_second_share": False}


def refuse_combination(claimants: Iterable[str]) -> None:
    """Refuse, by name, any request that divides one pool twice.

    One sampled phase scope is admissible (`check_scope` says why). Two of them, or one together with a QFT
    design, is REFUSED -- not ranked, not widened and not settled by precedence. Steps 1 and 5 are the one
    exception: `controlled_sampled_fallback` prices them in a SINGLE run against ONE partition, so they are
    two scopes of one claimant rather than two claimants."""
    asked = sorted(set(claimants))
    unknown = [c for c in asked if c not in SAMPLED_SCOPES and c != QFT_DESIGN]
    if unknown:
        raise PoolDoubleSpend(f"unknown pool claimant(s) {unknown}: expected phase scopes "
                              f"{sorted(SAMPLED_SCOPES)} or {QFT_DESIGN!r}")
    pricers = {SAMPLED_SCOPES[c] for c in asked if c in SAMPLED_SCOPES}
    if QFT_DESIGN in asked and pricers:
        raise PoolDoubleSpend(
            f"REFUSED: a QFT design and the sampled phase scope(s) {sorted(set(asked) - {QFT_DESIGN})} cannot be "
            f"asked for together. Both re-divide the one run-wide synthesis pool, by different rules -- the "
            f"overlay by `deltaArm = pool * R_cat / R_total`, the phase scope by the reference plan's own QFT "
            f"ceilings -- and the pool has no slack, so honouring both charges the same budget twice and hands "
            f"the submission free error. Re-submit asking for one of them")
    if len(pricers) > 1:
        raise PoolDoubleSpend(
            f"REFUSED: the sampled phase scopes {asked} cannot be priced together. Each scope's ledger "
            f"reserves every other site at the per-site allowance and carries the whole fixed QFT and "
            f"gradient term, so two independently priced scopes double-count that fixed term and price each "
            f"other's sites. One run, one partition, one scope: {sorted(pricers)}")
