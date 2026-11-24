"""Price one policy with the engine: the raw views `mapping.panel_of` turns into panels."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from . import engine
from .mapping import DELTA_VALUES, DESIGN_DELTA

N = 2048
FULL_KEYS = ("n", "T", "T_base", "rotations", "T_per_rotation", "eps", "toffoli", "clifford", "cnot", "qubits", "layout",
             "qubits_layout", "qubits_peak", "peak_base_registers", "peak_extra", "negate", "ladder_str",
             "label", "main_label", "parts", "cross_checks", "lean_grid_leaf_T", "literal_lean_T", "work_width", "widths", "m_bits",
             "precision_rule", "census_trust", "trial_count", "end_to_end")


def _all_tables(eng: Any, P: Any) -> list:
    """Every distinct table of the policy (by digest), arity descending, ties in band order. The engine's view keeps one table per
    arity, which drops tables when two bands use different tables of the same `k` (PR #132: 11 tables, 7 arities), while its trust
    counts every distinct table, and the contract requires the two to agree. For a policy with one table per arity this is the
    engine's own list, in the same order, so panels already published are unchanged. Kept here, outside `evaluation/engine`, so the
    fix does not move `engineRevision` (and so the epoch)."""
    seen: Dict[str, Any] = {}
    for b in P.bands:
        seen.setdefault(b.table.digest, b.table)
    return [eng.policy_view.table_view(t) for t in sorted(seen.values(), key=lambda t: -t.k)]


def _fix_bands_used(view: Dict[str, Any]) -> None:
    """`bands[].used_at_n` from the levels the view itself priced: a band is used at n when its table is on the chain. The engine
    derives it from one table per arity, so a second table of the same k that is on the chain was marked unused. Same meaning,
    computed by digest; kept outside `evaluation/engine` for the same reason as `_all_tables`."""
    used = {l["digest"] for l in view["levels"] if l.get("digest")}
    for b in view["bands"]:
        b["used_at_n"] = b["digest"] in used


def price_views(policy: Any, title: str, delta_key: str, boards: Iterable[str], *, measured: bool = False,
                design: Optional[Dict[str, Any]] = None, pricer: Optional[Callable[..., Any]] = None,
                primitive: Optional[Dict[str, Any]] = None,
                preparation: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """Views for a `tcost` Policy at one delta: 'pp' or 'ptp' is the single-product view; 'full' adds the whole run's pricing.
    The policy is priced at the exact widths the boards use.

    `design` is the compiled full-run QFT design (`full_run_design.design_of`), or None for no design at all, which is the
    engine's default and reproduces every published price byte for byte: the full-run pricer is ALWAYS called with exactly
    the arguments it was called with before this parameter existed, not with `design=None`, and with None nothing else
    happens either. `pricer` replaces `fullalg.price_full` (a test seam).

    A design is NEVER passed to the full-run pricer. Lane WM qualified the design axis as an out-of-engine OVERLAY
    (`qft_overlay.project`, constructing in-epoch `ReferenceQFT` with a Coppersmith `k_max` band) precisely because any edit
    to `evaluation/engine` -- including an additive `design=` parameter, including a comment -- moves `dataFingerprint` and
    `engineRevision` and so the epoch (`qft_overlay` module docstring: appending one comment line to `site_engine/fullalg.py`
    moves ab1f8887b1b50e56 to d3bda33d5ddd5dca). So the engine prices the coherent reference exactly as it always did, the
    design is applied on top of that frozen result -- mirroring the `measured` readout hook directly above -- and the
    overlay's answer is reported as `view["full"]["qft"]`. `fullalg.price_full` has no `design` parameter and must not grow
    one.

    `primitive` is the compiled `submitted_primitive` selector (lane XA) and `preparation` its attested catalyst words,
    for the STANDALONE B2/B3 primitive on the single-product boards. Both default to None, and with None the
    single-product branch is the call it has always been -- no key is added to the view, so every panel ever published is
    byte-identical. Like `measured` and `design`, the primitive is priced OUTSIDE the engine, by
    `primitive_overlay.project`, on top of the frozen default view; the engine's own default price in that view is not
    touched, which is what lets XA's baseline digest stand unmoved."""
    eng = engine.load()
    delta = DELTA_VALUES[delta_key]
    P = policy.with_widths("exact")
    boards = list(boards)
    if design is not None:
        # The QFT design surface is qualified at one precision only. This stays a
        # PRECONDITION on the caller, not a silent drop: scoping the design to the panel it belongs to is
        # `request._build_request`'s job (it asks for `design=` at `DESIGN_DELTA` and asks for the other three deltas
        # with no `design` keyword at all, which is the call 792464f made), and a design arriving here at another
        # delta is a programming error. Dropping it instead would price one thing and report another.
        if delta_key != DESIGN_DELTA:
            raise ValueError(f"a full-run design is priced at delta {DESIGN_DELTA} only, not {delta_key}")
        if "full" not in boards:
            raise ValueError("a full-run design belongs to the full order-finding board, which is not being priced")
        # The measured readout and the QFT design do NOT compose; see `mapping.ranked_price_of` for the three reasons
        # and for the shipped intake gate that reaches the same conclusion. Refused here so that a view carrying both
        # overlays is never built and nothing is priced on a precedence nobody has ruled on.
        if measured:
            raise ValueError("a measured readout and a full-run QFT design do not compose: each re-prices the whole "
                             "run from the same frozen coherent census, the per-rotation synthesis rate is nonlinear "
                             "in the rotation total, and on the exponent register the readout removes the very QFT a "
                             "design bands. The combination is reported, not priced on a guessed precedence")
    price_full = pricer or eng.fullalg.price_full
    out: Dict[str, Dict[str, Any]] = {}
    for board in boards:
        if board == "full":
            if P.mode != eng.tables.PAIR:
                raise ValueError("only a PhaseProduct policy is priced on the full order-finding board")
            full = price_full(P, N, delta, detail=True)
            view = eng.policy_view.view(P, N, delta, f"{title}, full order finding, n = {N}")
            view["full"] = {k: full.get(k) for k in FULL_KEYS}
            if measured:
                from .readout_overlay import project
                view["full"]["readout"] = project(P, full)
            if design is not None:
                from .qft_overlay import project
                view["full"]["qft"] = project(P, full, design)
            view["usage"], view["tables_used"] = full.get("usage"), full.get("tables_used")
            view["tables"] = _all_tables(eng, P)
            _fix_bands_used(view)
            out[board] = view
        else:
            want = eng.tables.PAIR if board == "pp" else eng.tables.TRIPLE
            if P.mode != want:
                raise ValueError(f"a {P.mode} policy does not belong on the {board} board")
            view = eng.policy_view.view(P, N, delta, title)
            if primitive:
                # LANE XA's HOOK, applied in integration wave 2. The same shape as the two full-board hooks
                # above: the engine prices the default exactly as it always did, and the overlay is applied
                # ON TOP of that frozen view, so a request naming no primitive is byte-identical. The
                # overlay prices the standalone B2/B3 primitive on the single-product boards only, which is
                # why the hook is here and not in the `full` branch.
                from .primitive_overlay import project
                view["primitive"] = project(P, view, primitive, preparation=preparation)
            view["tables"] = _all_tables(eng, P)
            _fix_bands_used(view)
            out[board] = view
    return out
