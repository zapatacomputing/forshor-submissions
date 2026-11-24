"""The compiled QFT design axis -- construction band x synthesis backend -- priced on the frozen coherent engine.

Local qualification only: this module does not grant publication authority, and it is NOT an epoch
input. It lives outside `evaluation/engine` for the same reason `pricing._all_tables` does -- so that
pricing a new design axis does not move `engineRevision` (`epoch.py:91`, `git log -1 -- evaluation/engine`)
nor `dataFingerprint` (`full_frontier.py:43-50`, a hash of the SOURCE BYTES of `site_engine/fullalg.py`
and of every file under `engine/fullalg` and `engine/paper_model`). Both move on ANY engine edit,
including a comment-only one, so an additive `design=` parameter on `price_full` could not have been
default-preserving at the epoch level however carefully it was written. Measured: appending one comment
line to `site_engine/fullalg.py` moves the epoch from ab1f8887b1b50e56 to d3bda33d5ddd5dca.

WHAT IS PRICED, and why it is a like-for-like substitution
-----------------------------------------------------------------------------------------------------
The engine prices every `qft` site as `policy_shor_price.PolicyQFT(policy, w, spec)` -- the native Lean
Cooley-Tukey template whose twiddles are policy phase products (`policy_shor_price.py:87-118`, reached
through the three-way dispatch `site_bloq` at `policy_shor_price.py:121-130`). `PolicyQFT` takes NO
banding parameter: its fields are exactly `policy`, `w`, `convention`.

This overlay does NOT touch that dispatch. Like `readout_overlay.project`, which constructs `PolicyQFT`
directly at `readout_overlay.py:136`, it enumerates the QFT sites itself and constructs the in-epoch
bloq it wants with its own parameters. The bloq it wants is `ReferenceQFT` (`l2q_full/bloqs/qft.py:440`),
whose `k_max` field (`:452`) is ALREADY inside the live epoch, defaults to `None` = the exact full
ladder, and drops controlled rotations of turn distance `d > k_max` through the mechanical guard at
`:461` (`d_stop = w if k_max is None else min(w, k_max + 1)`).

Both bloqs are costed by the SAME functional the engine uses for its own sites,
`cost_conventions.split` (`:215-219`) = `get_cost_value(bloq, QECGatesCost())` reported as
`(rotation-free T, rotation count)`. It is generic over `Bloq` and needs no convention field. The
substitution therefore replaces one `(base, rotations)` pair with another in the same accounting, and
the whole run's synthesis budget is then re-divided over the new rotation total exactly as the engine
divides it (`sto_currency.ts_per_rotation(delta, R_total)`), which is what `readout_overlay.py:142`
does for the readout axis. It is not a second accounting.

A CONSEQUENCE THAT FIXES THE CONTRACT'S SEMANTICS. The exact ladder is not cost-equal to the engine's
Lean template -- at w = 4095 `PolicyQFT` splits as (37_598_400, 178_191) and `ReferenceQFT(4095)` as
(33_533_953, 8_289_333). So `target: "exact"` means "LEAVE THIS FAMILY'S QFT EXACTLY AS THE ENGINE
PRICED IT" (k_max = None, no substitution at all), never "substitute the exact ladder". That is what
makes the all-exact design bit-identical to today's price, and it is what `qft.py:446` already says:
default `None` keeps the exact full ladder, "the previous behaviour, unchanged". Only an `approximate`
target substitutes anything.

TWO ORTHOGONAL AXES, per family (owner ruling 2026-11-24). `target` chooses the CONSTRUCTION
(`exact` = the engine's native `PolicyQFT` template untouched, `approximate` = a Coppersmith-banded
`ReferenceQFT`); `backend` chooses the CURRENCY its surviving rotations are paid in (`ross_selinger` =
the engine's own synthesis law, `catalytic` = merged catalysis). All four corners price, and all four
price differently. The quotient/split PROFILE customization is not part of this release (deferred),
which is why no topology or `modmul` field appears anywhere below.

THE CATALYTIC ARM, and the three things that make it harder than a band
-----------------------------------------------------------------------------------------------------
Catalysis replaces Ross-Selinger for the rotations it covers: instead of synthesising each rotation to
operator-norm tolerance eps, every rotation site becomes an ADDITION of its rounded turn into one
shared `PhaseGradientState(b)` catalyst, which is restored afterwards. The overlay reads only
`(rotation-free T, rotation count)` from `cost_conventions.split` -- it has no per-site turn, no row
geometry, no resolution -- so the ONLY sound price for a catalysed site is
`spec.other_site_cost(b)`'s "turn unknown -> worst case": one `CtrlAddConstIntoCatalyst(b, k)` of
`b - 1` Ands, at `spec.AND_T` = 4 T each. That is an upper bound on every site the engine could have
been pricing, including the merged twiddle rows that would in fact be cheaper.

Three charges, all three priced here. Pricing catalytic on Ross-Selinger currency, or omitting any of
these, would be the silent default the release rule forbids:

  1. ITS OWN SHARE OF THE RUN-WIDE SYNTHESIS BUDGET. There is one pool, `ROTATION_FRACTION * delta`
     (`sto_currency.py`: 0.5 * delta, = 1/200 at delta = 1e-2), and the engine divides it EVENLY over
     the run's rotation census (`sto_currency.rotation_eps(delta, R) = 0.5 delta / R`). The catalytic
     arm takes exactly the slices belonging to the rotations it absorbs:

         deltaArm = pool * R_catalysed / R_total

     and the surviving Ross-Selinger rotations keep the engine's OWN unchanged per-rotation eps. The
     sum is exactly the pool: `R_synth * (pool / R_total) + deltaArm == pool`. This is a PARTITION, not
     a re-division. Re-dividing the pool over the survivors only (`ts_per_rotation(delta, R_total -
     R_catalysed)`) would spend the catalysed rotations' slices twice -- once widening the survivors'
     eps and again buying the catalyst's b -- and would hand catalysis free error.
     A consequence worth knowing: because the share is proportional, `pi R_cat / deltaCells` is
     independent of R_cat, so b is set by the run-wide census and delta alone and is the same whether
     one family or four are catalysed. One shared register, one b, one prep charge.

  2. THE SHARED CATALYST REGISTER IS CHARGED TO Q. The b-qubit gradient is live for the whole run and Q
     feeds the ranked Q x T, so a T-only catalytic price would be wrong. `qubitsPeak` and
     `qubitsUpperBound` both carry `+ catalystQubits`, and `qubitTimesT` is reported so the product is
     not left to the reader. The register is SHARED across catalytic families, so it is charged once.

  3. `catalyst_prep_T` IS A SEPARATE, UNCONDITIONAL CHARGE. Preparing the gradient is a separately
     certified allowance -- the `b - 3` genuine ZPow rotations of `PhaseGradientState(b)` plus its 1 T,
     Ross-Selinger-synthesised against the arm's own prep share (`PREP_FRACTION` of `deltaArm`, the
     `prep_fraction` of the spec's own `choose_b_v1`). It is charged whenever a catalyst exists, even
     though the kicks are paid in Ands and even when the rounding they perform is free.

WHICH ERROR METRIC. The overlay charges the DETERMINISTIC arm, `other_site_cost(b, mixing=False)`:
operator-norm `pi 2^-b` per site. That is the metric the engine's delta is stated in (an RS tolerance
is an operator-norm tolerance) and it is exactly the bound `spec.per_site_b` inverts
(`R pi 2^-b <= deltaCells`). The mixed arm (D5) would admit a smaller b, but it buys that with a
classical draw per kick and reports a diamond/TV bound instead -- a different metric. For every
b >= 2 the deterministic charge is the larger, so the arm taken here is the worst case of the two; the
receipt reports both so the choice is visible.

SCOPE. QFT families only: `data`, `work`, `scratch`, `exponent`. The four phase-product steps --
`step1`, `step2 := fallback`, `step4 := selectiveKick` and `step5` -- are fields of the same compiled
selector record but are not symbolic, deterministic or closed-form (they need certified word banks),
and they also re-divide the one run-wide synthesis pool this overlay divides, so they are explicitly
out of scope; a design mapping carrying any of them is REFUSED, not quietly ignored. Their own sampled
costings live in `sampled_fallback.py` (Step 2), `step4_sampled_fallback.py` (Step 4 adaptive) and
`controlled_sampled_fallback.py` (Steps 1/5), none of which composes with a design here -- see
`rotation_pool.py` for why the pool cannot be divided twice.

FAIL CLOSED. An unrecognised backend, an unparseable `max_error`, an out-of-scope selector, or a family
this overlay cannot honour RAISES and names the family and the offending value. A request that cannot
be honoured is reported, never priced at the default.
"""
from hashlib import sha256
from pathlib import Path
import math
import re

from . import engine, epoch
from .canon import epoch_of
from .mapping import DELTA_VALUES

EPOCH = "ab1f8887b1b50e56"
RULE = "qft-design-axis-v2"

#: The QFT register families this overlay prices, and the layout width each one's QFT site runs at
#: (`site_engine/fullalg.py:187-199` `_site_label`: "QFT on data+1", "QFT on work", "Step 4 cmpLtNW:
#: QFT on scratch", "final QFT on the exponent register").
FAMILY_WIDTH = {"data": lambda w: w["yW"] + 1, "work": lambda w: w["workW"],
                "scratch": lambda w: w["scratchW"], "exponent": lambda w: w["xW"]}
FAMILIES = tuple(FAMILY_WIDTH)
BACKENDS = ("ross_selinger", "catalytic")
TARGETS = ("exact", "approximate")

#: Selector fields of the same compiled record that this overlay must not price (see SCOPE above).
#: All four phase-product steps of `Design.json:84`, which is the set `full_run_design.PHASE_STEPS` already
#: refuses on the explicit path. `step1` and `step5` were absent here until lane XL: they were still
#: fail-closed, because the unknown-key check catches them, but the refusal named them as unrecognised
#: families rather than as phase-product scopes needing their own qualification. They now have a qualified
#: costing of their own (`controlled_sampled_fallback.py`), so the specific refusal is the honest one.
OUT_OF_SCOPE = ("step1", "step2", "step4", "step5")

#: The share of the catalytic arm's error budget spent preparing the gradient rather than rounding
#: turns into it. This is the spec's OWN number: the `prep_fraction` default of
#: `catalysis/spec.py:393 choose_b_v1`, which splits a catalytic budget the same way.
PREP_FRACTION = 1e-3
#: The catalyst width range the spec itself qualifies: `choose_b_v1` searches `b` in [4, 126], and
#: `per_site_b` floors at 4. A budget that lands `b` outside this is outside the certified closed form
#: and is REFUSED, not extrapolated.
B_MIN = 4
B_MAX = 126


def _catalysis_spec():
    """`l2q_full.catalysis.spec`, the in-epoch reference specification for merged catalysis.

    Importable only once `engine.load()` has put `fullalg` on `sys.path`, exactly as `project` already
    imports `l2q_full.bloqs.qft.ReferenceQFT`. READING engine modules does not move the epoch; only
    committing a change under `evaluation/engine` does. Everything used from it -- `AND_T`,
    `per_site_b`, `other_site_cost`, `mixing_diamond_max`, `catalyst_prep_T`, `ross_selinger_t` -- is a
    pure closed form of its arguments: no cache, no certified word bank, no table lookup. That is the
    whole reason this arm can be priced from `export_v2` at all, and `test_the_four_closed_forms_are_
    pure_and_live_where_the_brief_says` re-derives each one rather than trusting the claim."""
    engine.load()
    try:
        from l2q_full.catalysis import spec
    except ImportError as error:                     # pragma: no cover - the vendored tree always has it
        _refuse(f"backend 'catalytic' needs the in-epoch l2q_full.catalysis.spec, which did not "
                f"import ({error}); the arm is reported as unpriced, never priced as 'ross_selinger'")
    return spec

#: `max_error` arrives as the exact string form the compiled selector emits: `<units>e-<scale>`, both
#: non-negative decimal integers, e.g. "1e-6" or "25e-9". Nothing else is accepted -- not a float, not
#: "1.5e-6", not "1E-6", not "1e+6", not a padded or signed variant.
MAX_ERROR_RE = re.compile(r"([0-9]+)e-([0-9]+)")


class QftOverlayRefused(ValueError):
    """A QFT design request this overlay will not price. Never a fallback to the default price."""


def _refuse(message):
    raise QftOverlayRefused(message)


def _require(condition, message):
    if not condition:
        _refuse(message)


def rule_fingerprint():
    """This overlay's own identity. The epoch fingerprint cannot cover `export_v2`, so a receipt that
    carries an overlay price must carry the bytes of the rule that produced it."""
    return sha256(Path(__file__).read_bytes()).hexdigest()


# ----------------------------------------------------------------------------- the precision band


def _engine_aqft_k_max():
    """`precision_policy.aqft_k_max`, the repo's single source for the QFT precision band, if it can be
    loaded. It lives at `engine/paper_model/py/precision_policy.py:84` -- inside the epoch, and already
    hashed into it -- but `engine/paper_model/py` is NOT on the site engine's `sys.path`, so a plain
    import fails; it is loaded by file path instead. Reading an engine file does not move the epoch.
    Returns None when absent, and `aqft_k_max` then falls back to the mirror below."""
    import importlib.util

    path = Path(engine.load().engine_dir) / "paper_model" / "py" / "precision_policy.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_qft_overlay_precision_policy", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "aqft_k_max", None)
    except Exception:
        return None


def aqft_k_max(w, eta):
    """Coppersmith band for a width-`w` QFT held to per-operation precision `eta`.

    A QFT banded to keep only controlled rotations of turn distance `d <= k_max` has induced error
    `~ w * 2^-k_max` (Coppersmith 1994; Beauregard S2.5). Requiring that error `<= eta` inverts to

        k_max = max(1, ceil(log2(w / eta))).

    Delegates to the in-epoch `precision_policy.aqft_k_max` when it can be loaded, so there is one
    definition in the repo and not two; the expression here is the identical documented closed form,
    kept as a mirror for the case where `paper_model/py` is not shipped. `k_max_source` reports which
    one priced a receipt, and the tests require the two to agree."""
    engine_form = _engine_aqft_k_max()
    if engine_form is not None:
        return int(engine_form(int(w), float(eta)))
    return max(1, math.ceil(math.log2(float(w) / float(eta))))


def k_max_source():
    return "engine:paper_model/py/precision_policy.aqft_k_max" if _engine_aqft_k_max() is not None \
        else "overlay:documented closed form max(1, ceil(log2(w/eta)))"


def parse_max_error(family, value):
    """`<units>e-<scale>` as a float, or refuse naming the family and the value."""
    _require(isinstance(value, str) and MAX_ERROR_RE.fullmatch(value),
             f"QFT family {family!r}: max_error {value!r} is not the compiled form '<units>e-<scale>' "
             f"(two non-negative decimal integers, e.g. '1e-6'); it is not priced by guesswork")
    units, scale = MAX_ERROR_RE.fullmatch(value).groups()
    eta = int(units) * 10.0 ** -int(scale)
    _require(0.0 < eta < 1.0,
             f"QFT family {family!r}: max_error {value!r} is {eta!r}, which is not a per-operation "
             f"precision in (0, 1)")
    return eta


# ----------------------------------------------------------------------------- the design mapping


def parse_design(design):
    """Validate a design mapping and return `{family: (backend, target, max_error_or_None)}` for the
    families that ask for something, with absent and None families dropped (they mean "unchanged").

    Refuses, naming the family and the value: an out-of-scope selector, an unknown key, a non-mapping
    value, an unrecognised backend, an unrecognised target, a missing or unexpected `max_error`."""
    if design is None:
        return {}
    _require(hasattr(design, "keys") and hasattr(design, "__getitem__"),
             f"QFT design must be a mapping with per-family keys {list(FAMILIES)}; got {type(design).__name__}")
    for field in OUT_OF_SCOPE:
        _require(field not in design,
                 f"QFT overlay refuses the selector field {field!r}: it is not symbolic, deterministic or "
                 f"closed-form and requires separate qualification with a certified word bank. This overlay "
                 f"prices the QFT families {list(FAMILIES)} only; re-submit without {field!r}")
    unknown = sorted(set(design) - set(FAMILIES))
    _require(not unknown,
             f"QFT design has keys this overlay cannot honour: {unknown} (known families: {list(FAMILIES)})")
    out = {}
    for family in FAMILIES:
        if family not in design or design[family] is None:
            continue                                   # absent or None: unchanged from the engine default
        value = design[family]
        _require(hasattr(value, "keys") and hasattr(value, "__getitem__"),
                 f"QFT family {family!r}: expected None or a mapping with 'backend' and 'target'; "
                 f"got {value!r}")
        extra = sorted(set(value) - {"backend", "target", "max_error"})
        _require(not extra, f"QFT family {family!r}: unexpected keys {extra} in {dict(value)!r}")
        _require("backend" in value, f"QFT family {family!r}: no 'backend' in {dict(value)!r}")
        _require("target" in value, f"QFT family {family!r}: no 'target' in {dict(value)!r}")
        backend, target = value["backend"], value["target"]
        _require(backend in BACKENDS,
                 f"QFT family {family!r}: unrecognised backend {backend!r} (known: {list(BACKENDS)}); "
                 f"it is reported, not priced at the default")
        _require(target in TARGETS,
                 f"QFT family {family!r}: unrecognised target {target!r} (known: {list(TARGETS)}); "
                 f"it is reported, not priced at the default")
        if target == "approximate":
            _require("max_error" in value,
                     f"QFT family {family!r}: target 'approximate' needs a 'max_error'; without one there "
                     f"is no band to derive and the request is refused, not priced as exact")
            max_error = value["max_error"]
        else:
            _require("max_error" not in value,
                     f"QFT family {family!r}: target 'exact' takes no 'max_error' (got "
                     f"{dict(value).get('max_error')!r}); an exact QFT has no band")
            max_error = None
        # `backend: "catalytic"` IS honoured, on both targets (lane WX; it was refused here as "a
        # second axis" before the three charges in the module docstring were priced). The axes are
        # orthogonal: `target` picks the construction, `backend` picks the currency. The arm itself can
        # still refuse at pricing time -- a family with no rotations to catalyse, or a catalyst width
        # outside the spec's qualified range -- and does so by name.
        out[family] = (backend, target, max_error)
    return out


# ----------------------------------------------------------------------------- the price


def _family_sites(fa, coherent, families):
    """`{family: (w, count)}` for the families being substituted, from the frozen coherent parts.

    Refuses when a family's width is shared with another family (the site could not then be attributed)
    or when the coherent result does not hold exactly one QFT part at that width."""
    widths = coherent["widths"]
    want = {f: int(FAMILY_WIDTH[f](widths)) for f in families}
    for family, w in want.items():
        clash = sorted(g for g, v in want.items() if g != family and v == w)
        _require(not clash,
                 f"QFT family {family!r}: its width {w} is shared with {clash}, so a banded site could "
                 f"not be attributed to one family; refusing rather than guessing")
    out = {}
    for family, w in want.items():
        rows = [p for p in coherent["parts"] if p["site"] == "qft" and p["xw"] == w and p["zw"] == 0]
        _require(len(rows) == 1,
                 f"QFT family {family!r}: the coherent reference holds {len(rows)} QFT parts at width "
                 f"{w}, expected exactly one")
        count = int(rows[0]["count"])
        _require(count >= 1, f"QFT family {family!r}: QFT part at width {w} has count {count}")
        out[family] = (w, count, rows[0])
    return out


def _catalysed_rotations(family, site, banded):
    """The rotation count the catalyst must absorb for one family.

    It is the count AFTER any band substitution: banding happens first and removes rotations the
    catalyst then never sees, so catalysing the native template's count would over-charge the arm."""
    _w, count, _row, exact, band = site
    return int((band.rot if family in banded else exact.rot) * count)


def _catalytic_arm(cs, sc, families, r_catalysed, rotations, delta):
    """Price the shared catalytic arm: choose b from the arm's own share of the synthesis pool, then
    charge the worst-case kicks, the gradient preparation, and the register. See the module docstring's
    three numbered charges; every number below is `catalysis/spec.py`'s or `sto_currency`'s own."""
    named = sorted(families)
    _require(rotations >= r_catalysed,
             f"QFT families {named}: the catalytic arm absorbs {r_catalysed} rotations but the run's "
             f"census is only {rotations}")

    # CHARGE 1: the arm's own proportional share of the ONE run-wide synthesis pool.
    pool = sc.ROTATION_FRACTION * float(delta)
    delta_arm = pool * r_catalysed / rotations
    prep_budget = PREP_FRACTION * delta_arm
    delta_cells = delta_arm - prep_budget
    _require(0.0 < delta_cells < 1.0,
             f"QFT families {named}: the catalytic arm's share of the synthesis pool is "
             f"{delta_arm!r}, leaving {delta_cells!r} for turn rounding, which is not a usable "
             f"error budget; the arm is reported rather than priced")

    b = int(cs.per_site_b(r_catalysed, delta_cells))
    _require(B_MIN <= b <= B_MAX,
             f"QFT families {named}: backend 'catalytic' needs a catalyst width b = {b}, outside the "
             f"range [{B_MIN}, {B_MAX}] that catalysis/spec.py qualifies (choose_b_v1); it is reported, "
             f"not extrapolated")

    # The worst-case price of ONE catalysed site: the turn is unknown to this overlay, so a lone
    # `CtrlAddConstIntoCatalyst(b, k)` of `b - 1` Ands at AND_T = 4 T each (spec.py:719, :549).
    lone = cs.other_site_cost(b, mixing=False)
    t_per_site = int(cs.AND_T) * int(lone.ands)
    cells_t = r_catalysed * t_per_site
    err_op = r_catalysed * lone.err_op
    _require(err_op <= delta_cells,
             f"QFT families {named}: a catalyst of width {b} rounds {r_catalysed} turns to a total "
             f"operator-norm error {err_op!r}, which exceeds its budget {delta_cells!r}")

    # CHARGE 3: gradient preparation, charged because a catalyst exists -- not because anything rounded.
    # `catalyst_prep_T` synthesises its `b - 3` genuine ZPow rotations at `prep_budget / (b - 3)` each and
    # `ross_selinger_t` requires that to be a tolerance in (0, 1). It always is here (`prep_budget` is at
    # most `PREP_FRACTION * pool`), but a budget that broke it would raise a bare `ValueError` out of the
    # engine rather than a named refusal, so it is checked and named first.
    prep_rotations = max(0, b - 3)
    _require(prep_rotations >= 1 and 0.0 < prep_budget / prep_rotations < 1.0,
             f"QFT families {named}: a catalyst of width {b} would prepare {prep_rotations} rotations "
             f"against a prep budget of {prep_budget!r}, which is not a usable per-rotation tolerance")
    prep_t = float(cs.catalyst_prep_T(b, prep_budget))
    _require(prep_t > 0.0,
             f"QFT families {named}: catalyst_prep_T({b}, {prep_budget!r}) is {prep_t!r}; a catalyst "
             f"that costs nothing to prepare has not been priced")
    return {
        "backend": "catalytic", "families": named,
        "spec": "l2q_full/catalysis/spec.py (AND_T:549, per_site_b:410, mixing_diamond_max:603, "
                "other_site_cost:719, catalyst_prep_T:736)",
        "b": b, "bRule": "per_site_b(R, deltaCells) = max(4, ceil(log2(pi R / deltaCells)))",
        # CHARGE 2: the shared register, charged ONCE however many families are catalytic.
        "registerQubits": b,
        "registerNote": "one shared PhaseGradientState(b), live for the whole run and restored after "
                        "every kick; charged once, not per family",
        "rotationsCatalysed": r_catalysed,
        "andsPerSite": int(lone.ands), "tPerSite": t_per_site,
        "cellsT": cells_t, "prepT": prep_t, "T": cells_t + prep_t,
        "prepRotations": prep_rotations,
        "pool": pool, "deltaArm": delta_arm, "prepBudget": prep_budget, "deltaCells": delta_cells,
        "mixing": False,
        "errOpCharged": err_op,
        "errMixedTV": r_catalysed * 0.5 * cs.mixing_diamond_max(b),
        "metricNote": "deterministic round-to-nearest, operator norm pi 2^-b per site -- the metric the "
                      "engine's delta is stated in and the bound per_site_b inverts. The mixed (D5) "
                      "arm's TV bound is reported beside it and is the smaller of the two, so this is "
                      "the worst case.",
        "realization": "spec.other_site_cost(b, mixing=False): turn unknown -> worst case, one "
                       "CtrlAddConstIntoCatalyst(b, k) per catalysed rotation site. An upper bound on "
                       "every site the engine could have been pricing, merged twiddle rows included.",
    }


def project(policy, coherent, design=None):
    """Price `policy`'s run with the QFT design axis applied to the frozen coherent result.

    `design=None`, an all-exact Ross-Selinger design, and a design whose families are all absent or
    None are BIT-IDENTICAL to `coherent`: nothing is substituted, no arm is opened, the rotation total
    is unchanged, so the budget re-division returns the same rate and `base + rotations * rate` is the
    same float expression the engine evaluated. An `approximate` target substitutes a banded
    `ReferenceQFT`; a `catalytic` backend re-prices that family's surviving rotations on the catalyst.
    """
    eng = engine.load()
    fa = eng.fullalg
    # EPOCH GUARD (`readout_overlay.py:95`). export_v2 is not an epoch input, so an overlay price is
    # only honest against the engine model it was qualified on. The owner is deliberately NOT moving
    # the epoch, so this holds; if it ever fails, the overlay refuses rather than prices.
    _require(epoch_of(epoch.model_of()) == EPOCH,
             f"QFT overlay is qualified against engine model {EPOCH} only; this tree's model is "
             f"{epoch_of(epoch.model_of())}, so the band is reported as unpriced")
    _require(coherent["n"] == fa.HERO_N and coherent["width_rule"] == "exact"
             and coherent["negate"] == fa.HEADLINE.negate and coherent["delta"] in DELTA_VALUES.values(),
             "QFT overlay requires the current exact reference price")
    _require("parts" in coherent and coherent["parts"],
             "QFT overlay needs the per-part census; price with detail=True")
    _require("qubits_peak" in coherent and "qubits" in coherent,
             "QFT overlay restates a qubit total (the catalyst register is charged to it), so it needs "
             "the coherent result's own `qubits_peak` and `qubits`")

    plan = parse_design(design)
    banded = {f: v for f, v in plan.items() if v[1] == "approximate"}
    catalytic = {f: v for f, v in plan.items() if v[0] == "catalytic"}
    touched = sorted(set(banded) | set(catalytic))
    delta = coherent["delta"]
    base = int(coherent["T_base"])
    rotations = int(coherent["rotations"])
    families, sites = {}, {}

    if touched:
        lp = fa.to_l2q_full(policy, "exact")
        spec = fa.HEADLINE.with_(negate=coherent["negate"])
        from l2q_full.bloqs.qft import ReferenceQFT

        for family, (w, count, row) in _family_sites(fa, coherent, touched).items():
            # The engine's own price for this site, recomputed from the in-epoch bloq and cross-checked
            # against the frozen part row -- the like-for-like check `readout_overlay.py:135` makes.
            # It is made for every TOUCHED family, catalytic-exact ones included: the catalyst's
            # rotation count comes from this split, so it must reproduce the census too.
            exact = fa.cc.split(fa.pr.PolicyQFT(lp, w, spec))
            # Scale with the engine's own `TCost.scale` and recombine in the engine's own association
            # (`fullalg.parts`: `sc_ = split(bloq).scale(k)`, then `T = sc_.base + sc_.rot * tpr`).
            # `(base + rot*tpr) * k` is a DIFFERENT float from `base*k + rot*k*tpr` and would reject a
            # census the overlay in fact reproduces exactly.
            scaled = exact.scale(count)
            _require(int(row["rotations"]) == scaled.rot
                     and row["T"] == scaled.base + scaled.rot * coherent["T_per_rotation"],
                     f"QFT family {family!r}: the coherent part at width {w} disagrees with the exact "
                     f"cost split of PolicyQFT({w}); the overlay will not substitute into a census it "
                     f"cannot reproduce")
            band = None
            if family in banded:
                backend, _target, max_error = banded[family]
                eta = parse_max_error(family, max_error)
                k = int(aqft_k_max(w, eta))
                band = fa.cc.split(ReferenceQFT(w, k_max=k))
                base += (band.base - exact.base) * count
                rotations += (band.rot - exact.rot) * count
                families[family] = {
                    "width": w, "count": count, "backend": backend, "target": "approximate",
                    "currency": backend, "maxError": max_error, "eta": eta, "kMax": k,
                    "banded": bool(k < w - 1),
                    "exactTBase": int(exact.base), "exactRotations": int(exact.rot),
                    "bandedTBase": int(band.base), "bandedRotations": int(band.rot),
                    "realization": "ReferenceQFT(w, k_max) -- Coppersmith-banded Draper ladder, "
                                   "textbook, NOT the Lean lowerQFT term; it REPLACES the native "
                                   "PolicyQFT template at this site, so the delta is construction + "
                                   "band, not band alone",
                }
            sites[family] = (w, count, row, exact, band)
    for family, (backend, target, max_error) in plan.items():
        if target == "exact":
            families[family] = {"width": int(FAMILY_WIDTH[family](coherent["widths"])), "backend": backend,
                                "target": "exact", "currency": backend, "maxError": None, "eta": None,
                                "kMax": None, "banded": False,
                                "count": sites[family][1] if family in sites else None,
                                "exactRotations": int(sites[family][3].rot) if family in sites else None,
                                "realization": "unchanged: the engine's native PolicyQFT template, the "
                                               "exact full ladder (qft.py:446 default k_max=None)"}

    _require(base >= 0 and rotations >= 0,
             f"QFT overlay produced a negative census (T_base={base}, rotations={rotations})")

    # The CURRENCY axis. The rate below is the engine's own -- the pool divided evenly over the census
    # -- and the surviving Ross-Selinger rotations keep it unchanged; the catalytic arm takes exactly
    # the slices of the rotations it absorbs. Charge 1 of the module docstring.
    rate = fa.sc.ts_per_rotation(delta, rotations) if rotations else 0.0
    eps = fa.sc.rotation_eps(delta, rotations) if rotations else None
    arm, r_catalysed = None, 0
    if catalytic:
        cs = _catalysis_spec()
        for family in sorted(catalytic):
            n = _catalysed_rotations(family, sites[family], set(banded))
            _require(n >= 1,
                     f"QFT family {family!r}: backend 'catalytic' asks for a catalyst on a family that "
                     f"contributes 0 rotations for it to absorb, so there is no arm to price; it is "
                     f"reported, not priced as the untouched default")
            r_catalysed += n
        arm = _catalytic_arm(cs, fa.sc, catalytic, r_catalysed, rotations, delta)
    synthesised = int(rotations) - int(r_catalysed)

    # When nothing is catalysed, `synthesised == rotations` and this is the identical float expression
    # the engine evaluated -- which is what keeps the default path bit-identical.
    total = base + synthesised * rate
    if arm is not None:
        total += arm["T"]
    catalyst_qubits = int(arm["registerQubits"]) if arm is not None else 0
    peak = int(coherent["qubits_peak"]) + catalyst_qubits
    return {
        "mode": "qft-design-axis", "pricingRule": RULE, "ruleSha256": rule_fingerprint(),
        "epoch": EPOCH, "kMaxSource": k_max_source(),
        "T": total, "TBase": int(base), "rotations": int(rotations),
        "rotationsSynthesised": synthesised, "rotationsCatalysed": int(r_catalysed),
        "tPerRotation": rate, "eps": eps, "epsSynthesised": eps,
        "families": families, "substituted": sorted(banded), "catalysed": sorted(catalytic),
        "catalyst": arm,
        "identity": not banded and not catalytic,
        "tNote": "T = TBase + rotationsSynthesised * tPerRotation + catalyst.T (catalyst.T = 0 when no "
                 "family is catalytic, and then rotationsSynthesised == rotations)",
        # Banding removes a QFT's phase-product workspace, so the frozen layout is a sound UPPER bound
        # on the banded run's qubits. The catalyst register is a real ADDITION to both totals and is
        # charged: it is live for the whole run and Q feeds the ranked Q x T (charge 2).
        "catalystQubits": catalyst_qubits,
        "qubitsPeak": peak,
        "qubitsUpperBound": coherent["qubits"] + catalyst_qubits,
        "qubitTimesT": peak * total,
        "qubitsNote": "the engine's frozen peak and layout plus catalystQubits. Banding only REMOVES "
                      "phase-product workspace, so the frozen totals stay an upper bound for it and "
                      "are not re-derived; the shared catalyst register is added because it is not.",
        # THE QUBIT HOOK (integration wave 2, lane XF). `mapping.ranked_price_of` overlays onto the row
        # every key of `mapping.SHOWN` == ("T", "qubits") that the projection offers, and documents the
        # `qubits` key as "the hook that lets that arm land without publishing a qubit count nobody
        # modelled" -- written for exactly this arm. But the arm reports its qubits as `qubitsPeak` and
        # `qubitsUpperBound`, NOT as `qubits`, so the hook never fired: measured on the merged tree, a
        # catalytic design ranked at the engine's 44930 while the projection's own bound was 44972, i.e.
        # charge 2 (the shared catalyst register, "live for the whole run", feeding the ranked Q x T) did
        # not reach the ledger row at all. The generalisation was inert for the one projection it was
        # generalised for.
        #
        # So the projection names `qubits` -- and only when a catalyst actually exists, which is exactly
        # when the engine's frozen total stops being an upper bound. A band-only projection still offers
        # no `qubits`, so the engine's number is ranked for it, which is what `ranked_price_of` argues for
        # (banding can only LOWER the count). No edit to `mapping.py` is needed; this closes WY's hook
        # through WY's own mechanism.
        #
        # THE VALUE IS THE PEAK, not the layout total. `ranked_price_of` overlays onto the PANEL's
        # full-run block, where `mapping.full_run_of:130` sets `"qubits": f["qubits_peak"]` and carries
        # the layout total separately as `qubitsLayout`. `readout_overlay.py:151` follows the same
        # convention for the other overlay. Offering `coherent["qubits"]` (the layout total) here instead
        # would have substituted a layout total into a peak field and moved the ranked count from 13406 to
        # 44972 on the measured policy -- a 3.4x misreport, in the wrong unit.
        **({"qubits": peak} if catalyst_qubits else {}),
    }
