"""Engine output -> contract-v2 documents. The publisher's own fixture builder builds the same panels from the same raw engine
views, and `pytests/test_mapping.py` checks this module reproduces them exactly against a committed golden document."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

Raw = Dict[str, Any]
BOARDS = ("full", "pp", "ptp")
DELTA_KEYS = ("1e-2", "3e-4", "1e-4", "1e-6")
DELTA_VALUES = {"1e-2": 0.01, "3e-4": 3e-4, "1e-4": 1e-4, "1e-6": 1e-6}

#: The one delta the full-run QFT design surface is qualified at (`QFT-SURFACE-MAPPING.md` section 1). A publish request
#: prices all four of `DELTA_KEYS`; the design applies to THIS panel and the other three are priced as the design-free
#: panels of the same policy that they are -- the same policy at a different precision, which is what every published
#: panel is -- rather than refusing the whole request. `pricing.price_views` and `request._build_request` share this name
#: so the scope is stated once.
DESIGN_DELTA = "1e-2"

#: The numbers a board shows for a row, and so the only ones an overlay can override in `ranked_price_of`. An overlay
#: that prices some of them (the QFT projection prices `T`) leaves the rest at the engine's; one that prices none of
#: them cannot move a published row at all.
SHOWN = ("T", "qubits")


def ladder_of(label: str) -> List[Dict[str, Optional[int]]]:
    """'2048:8 -> 279:3 -> 35:leaf' -> [{width, k}], with k None for the base case."""
    out = []
    for part in label.split(" -> "):
        width, k = part.split(":")
        out.append({"width": int(width), "k": None if k == "leaf" else int(k)})
    return out


def _static_layout(s: Optional[Raw]) -> Optional[Raw]:
    return None if not s else {"w": s["w"], "reserveX": s["reserve_x"], "reserveZ": s["reserve_z"], "qubits": s["qubits"]}


def _full_check(c: Raw) -> Raw:
    return {"T": c["T"], "TBase": c["T_base"], "rotations": c["rotations"], "tPerRotation": c["T_per_rotation"],
            "eps": c["eps"], "label": c["label"]}


def _table_step(x: Raw) -> Raw:
    return {"W": x["W"], "k": x.get("k"), "table": x.get("table"), "digest": x.get("digest")}


# The full run's qubit count is the live-qubit peak, not the static layout. The publisher's fixture builder carries the same two
# strings, and the golden test holds them equal once golden.json has been regenerated from that builder.
#
# The text depends on ONE per-row fact, `leanPeak`: has Lean's kernel evaluated the peak for THIS policy (same method, same width rule as the
# epoch's)? It defaults to False, so a row keeps "Lean has not evaluated it on this policy" unless epoch.LEAN_PEAK lists it.
PIN = "21e129aeefc0fd1bbb8fa63402f5799af2490788"   # the ForShor merge commit of PR #80 (on top of #79's eb00c5a): the epoch's pin
_PEAK_LABEL_HEAD = "live-qubit peak of the balanced order-finding circuit (a port of ForShor's orderFindingQubitsU, "
_PEAK_RULE_HEAD = (
    "The registers (exponent, data with its carry bit, work, scratch) and the flag, 12,890 qubits at n = 2048, plus the largest number of "
    "extra qubits any step of the unconditionally balanced order-finding circuit needs: ForShor's Policy.orderFindingQubitsU "
    "(QubitCount/Statement.lean). A negation is the register's complement plus Gidney's one-clean-ancilla increment gate (ForShor's `oneQubit` "
    "negation method, the model's `negation: \"incrementer\"`), charged as ONE clean qubit with Lean's counts per w-bit negation: Toffoli "
    "12w - 28 (even w) or 10w - 18 (odd w), CNOT 17w - 22 or 17w - 5, X 5w - 3 or 5w + 1 (exact for w >= 7, upper bounds below), at ForShor "
    + PIN + ", so the Step 3 scratch no longer binds. Computed by a Python port that reproduces Lean's evaluation for ForShor's "
    "standard-table policies and Lean's kernel-checked anchor (13,449 qubits for constant k = 2 at scanned widths, 4096 exponent qubits); ")
_PEAK_RULE_TAIL = " It is not the static layout allocation, which is kept in layout.total."


# `leanPeak` is False (the port's number, not Lean-evaluated) or evidence for one PhaseProduct policy: True (the generic wording) or a dict
# {"module": "Row<id>Exact", "n": N, "ci": bool}: the kernel-checked theorem `Shor.QubitCount.PolicyNumeralsBoard.<module>.balanced_exact_oneQubit`
# (in ForShor main at the pin) says the balanced peak adds N qubits to the registers, and `ci` says whether ForShor's CI builds that module
# (library PolicyNumeralsBoardExact, at most 12.1 GB) or only a developer machine checked it (library PolicyNumeralsBoardExactHeavy, 13 to 15 GB,
# not in CI). The label differs only by that split.


def _lean_peak_local(lean_peak: Any) -> bool:
    return isinstance(lean_peak, dict) and not lean_peak.get("ci", True)


def peak_label(lean_peak: Any = False) -> str:
    if not lean_peak:
        return _PEAK_LABEL_HEAD + "not Lean-evaluated on this policy)"
    # at most 200 characters (the contract's limit): the full statement is in the rule
    return _PEAK_LABEL_HEAD + "Lean-evaluated on this policy; " + ("kernel-checked on a developer machine only, not in ForShor CI)" if _lean_peak_local(lean_peak) else "kernel-checked, built in ForShor CI)")


def peak_rule(lean_peak: Any = False) -> str:
    if not lean_peak:
        evidence = "Lean has not evaluated it on this policy."
    else:
        evidence = ("Lean's kernel has evaluated this policy's peak, and the port equals it: "
                    + (f"the theorem Shor.QubitCount.PolicyNumeralsBoard.{lean_peak['module']}.balanced_exact_oneQubit (ReferenceBalancedPeakIs: exact width "
                       f"rule, the negation method oneQubit) gives {lean_peak['n']} extra qubits, and 4095 + 8795 + {lean_peak['n']} is this Q (4095 exponent "
                       "qubits; the registers above with 4096 exponent qubits are one more). " if isinstance(lean_peak, dict) else "")
                    + "The theorem is in ForShor's main branch at the pin. "
                    + ("ForShor's CI builds this module (library PolicyNumeralsBoardExact)." if not _lean_peak_local(lean_peak) else
                       "It was kernel-checked on a developer machine only: the module needs 13 to 15 GB, lives in the library PolicyNumeralsBoardExactHeavy "
                       "and is not built by ForShor's CI."))
    return _PEAK_RULE_HEAD + evidence + _PEAK_RULE_TAIL


PEAK_LABEL = peak_label()   # the default (leanPeak = False) texts
PEAK_RULE = peak_rule()

# A PhaseTripleProduct panel's Q (view["qubits_label"] / view["qubits_rule"], the engine's wording) says "formula, not Lean-proved". For a
# board row whose Q EQUALS the kernel-checked ForShor peak (`leanPeak` = True; epoch.LEAN_PEAK["ptp"]) the wording changes, and only the wording.
PTP_LEAN_LABEL = "live-qubit peak (equals ForShor's kernel-checked upper bound ptp_peak_qubits_exact)"
_PTP_FORMULA_SENTENCE = "It is a formula, not a Lean-proved count."


def ptp_lean_rule(engine_rule: str, row_id: str) -> str:
    """The engine's PhaseTripleProduct rule with its 'formula, not Lean-proved' sentence replaced by the Lean statement for this board row."""
    if engine_rule.count(_PTP_FORMULA_SENTENCE) != 1:
        raise ValueError("the engine's PhaseTripleProduct qubit rule no longer has the sentence this wording replaces")
    return engine_rule.replace(_PTP_FORMULA_SENTENCE, (
        "For this policy the number equals ForShor's ptpPeakQubits = 3n + ptpWsTD + ptpCleanAncD at the exact widths (n = 2048), which the "
        f"kernel-checked theorem Peak.Board.Row{row_id}.peak_oneQubit_exact (ForShor {PIN}) states as an upper bound on the qubit count of a "
        "circuit following this policy: a bound the framework proves on an existential circuit, not a count of a circuit built here. No "
        "table of this policy negates, so ForShor's scratch method gives the same number."))


def full_run_of(view: Raw, lean_peak: Any = False) -> Raw:
    f = view["full"]
    cc = f["cross_checks"]
    return {
        "T": f["T"], "TBase": f["T_base"], "rotations": f["rotations"], "tPerRotation": f["T_per_rotation"], "eps": f["eps"],
        **({"readout": f["readout"]} if "readout" in f else {}),
        # Same shape as `readout` directly above: present only when `price_views` was given a full-run design, so every
        # panel priced without one -- which is every panel ever published -- is byte-identical. The panel keeps the
        # engine's own coherent price in `T` beside it; which of the two a BOARD shows is `ranked_price_of`, and since
        # the owner's Tier 2 ruling that is the projection's T, exactly as it is `readout`'s for the measured route.
        **({"qft": f["qft"]} if "qft" in f else {}),
        "toffoli": f["toffoli"], "clifford": f["clifford"], "cnot": f["cnot"],
        "qubits": f["qubits_peak"], "qubitsLayout": f["qubits_layout"], "peakExtra": f["peak_extra"],
        "qubitsLabel": peak_label(lean_peak), "qubitsRule": peak_rule(lean_peak),
        "ladder": ladder_of(f["ladder_str"]), "ladderLabel": f["ladder_str"], "label": f["label"], "mainLabel": f["main_label"],
        "layout": {
            "total": f["layout"]["total"], "flag": f["layout"]["flag"], "rule": f["layout"]["rule"],
            "registers": [{"register": r["register"], "active": r["active"], "reserve": r["reserve"], "reserveSetBy": r["reserve_set_by"]}
                          for r in f["layout"]["registers"]],
        },
        "parts": [{
            "part": p["part"], "site": p["site"], "xw": p.get("xw"), "zw": p.get("zw"), "count": p["count"], "T": p["T"],
            "rotations": p["rotations"], "leanStaticQubits": p.get("lean_static_qubits"), "reserve": p.get("reserve"),
            "share": p["share"],
            "balancedInto": ([{"site": b["site"], "xw": b["xw"], "zw": b["zw"], "count": b["count"]} for b in p["balanced_into"]]
                             if p.get("balanced_into") else None),
            "ladder": [_table_step(x) for x in p["ladder"]] if p.get("ladder") else None,
        } for p in f["parts"]],
        "crossChecks": {
            "leanGridLeaf": _full_check(cc["lean_grid_leaf"]),
            "literalLean": _full_check(cc["literal_lean"]),
            "forshorScanIr": {**_full_check(cc["forshor_scan_ir"]), "qubits": cc["forshor_scan_ir"]["qubits"],
                              "ladderLabel": cc["forshor_scan_ir"]["ladder_str"]},
        },
        "leanGridLeafT": f["lean_grid_leaf_T"], "literalLeanT": f["literal_lean_T"],
        "workWidth": f["work_width"], "widths": f["widths"], "mBits": f["m_bits"], "precisionRule": f["precision_rule"],
        "censusTrust": f["census_trust"], "trialCount": f["trial_count"], "endToEnd": f["end_to_end"],
        "usage": [{"W": str(u["W"]), "xw": u["xw"], "zw": u["zw"], "site": u["site"], "count": u["count"], "T": u["T"],
                   "share": u["share"], "ladderLabel": u["ladder_str"], "ladder": [_table_step(x) for x in u["ladder"]]}
                  for u in view["usage"]],
        "tablesUsed": [{"id": t["id"], "digest": t["digest"], "k": t["k"], "widths": t["widths"], "roots": t["roots"]}
                       for t in view["tables_used"]],
    }


def weighted_cost_of(operations) -> int:
    """STO's weighted cost of a table, `arithmetic_operation_count + 5 * parallel_phase_product_layer_count` (ShorTableOptimization BENCHMARK.md), from its operations:
    every operation that is not a phaseProduct is arithmetic (addScaled, negate, shiftL, shiftR), and a layer is a maximal run of consecutive phaseProduct operations.
    The engine reads the value from the STO archive's verification results, which exist only for archived tables; a table a participant wrote has none, and the
    contract requires a number. This reproduces the archived value for all 98 archived tables that have one (a test checks it), so a table without one gets the same
    measure computed here, not a made-up number."""
    arithmetic = layers = 0
    in_run = False
    for op in operations:
        is_phase = op[0] == "phaseProduct"
        arithmetic += not is_phase
        layers += is_phase and not in_run
        in_run = is_phase
    return arithmetic + 5 * layers


def panel_of(view: Raw, *, epoch: str, row_id: str, board: str, delta: str, verification: Raw, lean_peak: Any = False) -> Raw:
    """One `forshor.policy/v2` document from an engine `policy_view` (plus `full`, `usage`, `tables_used` for the full board).
    `lean_peak`: Lean's kernel has evaluated this policy's qubit peak (per row; False until a manifest says so); only the qubit label and
    rule depend on it: the full run's (PhaseProduct) and, on the PhaseTripleProduct board, the panel's own."""
    qubits_label, qubits_rule = view["qubits_label"], view["qubits_rule"]
    if lean_peak and board == "ptp":
        qubits_label, qubits_rule = PTP_LEAN_LABEL, ptp_lean_rule(qubits_rule, row_id)
    sc, gl = view.get("scan_cross_check"), view.get("grid_leaf_cross_check")
    tb = view["t_breakdown"]
    return {
        "schema": "forshor.policy/v2", "epoch": epoch, "rowId": row_id, "board": board, "delta": delta,
        "title": view["title"], "T": view["T"], "ladder": ladder_of(view["ladder_str"]), "ladderLabel": view["ladder_str"],
        "depth": view["depth"], "leafWidth": view["leaf_width"], "leafCalls": view["leaf_calls"], "leafShare": view["leaf_share"],
        "widthRule": view["width_rule"], "widths": view["widths"], "window": view["window"], "agree": view["agree"],
        "engine2T": view["engine2_T"], "qubits": view["qubits"], "qubitsLabel": qubits_label,
        "qubitsRule": qubits_rule, "qubitsStatic": _static_layout(view.get("qubits_static")),
        "qubitsLeanStatic": _static_layout(view.get("qubits_lean_static")),
        "qubitsQualtranPeak": view.get("qubits_qualtran_peak"),
        "levels": [{
            "level": l["level"], "width": l["width"], "k": l.get("k"), "table": l["table"], "digest": l.get("digest"),
            "childWidth": l.get("child_width"), "children": l.get("children"), "nodes": l["nodes"], "adders": l.get("adders"),
            "rotations": l["rotations"], "T": l["T"],
        } for l in view["levels"]],
        "bands": [{"threshold": b["threshold"], "k": b["k"], "table": b["table"], "digest": b["digest"], "usedAtN": b["used_at_n"]}
                  for b in view["bands"]],
        "tables": [{
            "k": t["k"], "digest": t["digest"], "points": t["points"], "operations": t["operations"], "pointCount": t["point_count"],
            "census": t["census"], "weightedCost": t["weighted_cost"] if t["weighted_cost"] is not None else weighted_cost_of(t["operations"]),
            "source": {"id": t["source"]["id"], "kind": t["source"]["kind"], "status": t["source"]["status"],
                       "pr": t["source"].get("pr"), "createdAt": t["source"].get("created_at")},
            "checkLevel": t["level"], "checkers": t["checkers"], "checkedBy": t["checked_by"],
        } for t in view["tables"]],
        "gateCounts": view["gate_counts"],
        "tBreakdown": {"tCount": tb["t_count"], "tFromToffoliClass": tb["t_from_toffoli_class"], "tFromRotations": tb["t_from_rotations"],
                       "tsPerRotation": tb["ts_per_rotation"], "rotationEps": tb["rotation_eps"], "delta": tb["delta"]},
        "tableTrust": view["trust"],
        "scanCrossCheck": None if not sc else {
            "label": sc["label"], "text": sc["text"], "T": sc["T"], "rotations": sc["rotations"], "ladderLabel": sc["ladder_str"],
            "agree": sc["agree"], "engine2T": sc["engine2_T"], "qubitsLeanStatic": sc.get("qubits_lean_static"),
            "qubitsRule": sc.get("qubits_rule")},
        "gridLeafCrossCheck": None if not gl else {
            "label": gl["label"], "text": gl["text"], "T": gl["T"], "rotations": gl["rotations"], "and": gl["and"],
            "toffoli": gl["toffoli"], "engine2T": gl["engine2_T"], "agree": gl["agree"]},
        "policyJson": {
            "name": view["policy_json"]["name"], "mode": view["policy_json"]["mode"],
            "bands": [{"threshold": b["threshold"], "mode": b["mode"], "k": b["k"], "pointOrder": b["point_order"],
                       "operations": b["operations"]} for b in view["policy_json"]["bands"]],
        },
        "verification": verification,
        "costCheck": {"claim": "counted-in-python-not-proved-in-lean", "engines": ["tcost", "forshor_qualtran"], "agree": bool(view["agree"])},
        "fullRun": full_run_of(view, lean_peak) if board == "full" else None,
        # The standalone primitive price, verbatim as `primitive_overlay.project` returned it. The same shape as
        # `fullRun.readout` and `fullRun.qft` -- present only when `price_views` was given a primitive, absent on every
        # panel priced without one, which is every panel ever published, so no published panel's bytes move. It sits at
        # panel level and not inside `fullRun` because the overlay prices the SINGLE-PRODUCT boards, whose `fullRun` is
        # null. It carries `tKind` / `rankedTKind` and exactly one of `tDeterministic` / `tExpectedAdaptive`: B3's price
        # is an unconditional EXPECTATION and nothing may read it as a worst case (see `standalone_price_of`).
        **({"primitive": view["primitive"]} if "primitive" in view else {}),
    }


def trust_of(verification: Raw) -> Raw:
    """The row's trust summary, derived from (and equal to) the evidence."""
    fp = verification.get("forshorPolicy")
    return {"stoMirror": "accepted" if verification.get("stoMirror") else "none",
            "forshorPolicy": "none" if not fp else ("verified" if fp["verified"] else "not-verified"),
            "cost": "python-not-proved-in-lean"}


def ranked_price_of(full: Raw) -> Raw:
    """The numbers a full-run board shows: the overlay's price when a panel carries one, the engine's own otherwise.

    `readout` replaces T and qubits outright, because `readout_overlay.project` returns both for the measured circuit.
    `qft` today prices T ONLY: `qft_overlay.project` states that it does not re-derive a qubit count and that the
    engine's frozen layout total is a sound UPPER bound on the banded run's qubits (banding removes a QFT's
    phase-product workspace), so the ranked qubits stays the engine's own number and only T comes from the projection.
    A projection that DOES model a qubit count carries `qubits` and that number is ranked instead: the Coppersmith band
    can only lower the count, but the catalytic arm `qft_overlay.parse_design` currently refuses "adds a catalyst
    register", which would RAISE it and make the engine's total no longer a bound. Reading `qubits` when it is offered
    is the hook that lets that arm land without publishing a qubit count nobody modelled. With neither overlay key this
    returns the panel's own full-run block unchanged -- the object itself -- which is what every published row was
    built from.

    THE TWO OVERLAYS DO NOT COMPOSE, and the combination is refused rather than ranked on a guessed precedence. Each
    re-derives the WHOLE run's census from the same frozen coherent result and returns a FINAL price, not a census
    patch; the synthesis rate `sto_currency.ts_per_rotation(delta, R)` is nonlinear in the rotation total, so two
    finals cannot be combined into one; and on the exponent register they make contradictory claims about the SAME
    site -- the semiclassical readout deletes the exponent QFT (`readout_overlay.py:132-138`, `base -= iqft.base`)
    while a design bands it (`qft_overlay.FAMILY_WIDTH["exponent"]`). The shipped intake gate reaches the same
    conclusion from the other end: `policy_options.check_no_full_run_export` refuses a compiled document that carries
    `submitted_full_run` at all, and both measured entry points in `request.py` run it before converting a selection,
    so the pair has never had a defined price anywhere in this pipeline. Ranking the readout would silently drop the
    design the submission asked for; ranking the projection would publish a coherent-census price for a measured
    circuit. `pricing.price_views` refuses the same pair one step earlier, so nothing is priced either."""
    if "readout" in full and "qft" in full:
        raise ValueError(
            "a full-run panel carries both a measured readout and a QFT design projection: the two overlays do not "
            "compose (each re-prices the whole run from the same frozen coherent census and returns a final price, the "
            "per-rotation synthesis rate is nonlinear in the rotation total, and on the exponent register the readout "
            "removes the very QFT a design bands), so the ranked price is refused, not guessed")
    if "readout" in full:
        return full["readout"]
    if "qft" in full:
        # Every number the projection prices overrides the engine's; every number it does not keeps the engine's. The
        # projection is the price now (the owner's Tier 2 ruling), exactly as `readout` is for the measured route --
        # but unlike `readout` it does not necessarily price the whole row, so it overlays rather than replaces.
        return {**full, **{key: full["qft"][key] for key in SHOWN if key in full["qft"]}}
    return full


def standalone_price_of(panel: Raw) -> Raw:
    """The numbers a SINGLE-PRODUCT board shows: the standalone primitive's price when the panel carries one, the
    engine's own otherwise. The counterpart of `ranked_price_of` for the pp and ptp boards, whose `fullRun` is null.

    Like `readout`, a primitive price replaces T and qubits outright, because `primitive_overlay.project` returns both
    for the standalone circuit; the panel keeps the engine's own default price in the rest of its fields, as a
    design-bearing full-run panel keeps the coherent comparison beside `qft`.

    XA'S STRUCTURAL GUARANTEE IS RE-ASSERTED HERE, and this is the one place it could be lost. A row's `T` is a bare
    number with no kind beside it, and the two standalone primitives do not price the same kind of number: catalyticB2's
    T is DETERMINISTIC while adaptiveB3's 143,366,613 is an UNCONDITIONAL EXPECTATION over repeat-until-success probes.
    Substituting `rankedT` alone would let every consumer of a row read an expectation as the worst case that every
    other row's T is. So the substitution refuses a result that does not carry its own kind -- `rankedTKind` set, no
    bare `T`, and exactly one of `tDeterministic` / `tExpectedAdaptive` -- and the kind itself stays on the panel beside
    the row (`panel["primitive"]["rankedTKind"]`), which is the only place the pair can carry it: the published row schema
    is CLOSED and names no field for it, so a row that grows any key is rejected outright rather than ignored, and the row
    may not carry one (lane XF's seam ruling; the accepted key set is pinned executably as `SITE_ROW_FIELDS` in
    `pytests/test_design_price_plumbing.py`). A primitive panel is consequently NOT publishable, which is exactly
    what the route promises -- it never publishes."""
    result = panel.get("primitive")
    if not result:
        return panel
    if "T" in result:
        raise ValueError("a standalone primitive result must not carry a bare T: the row would read the number "
                         "without its kind, and adaptiveB3's T is an expectation, not a worst case")
    if not result.get("rankedTKind"):
        raise ValueError("a standalone primitive result names no kind for its ranked T; a deterministic T and an "
                         "expected T must never be read as the same number")
    priced = [value for value in (result.get("tDeterministic"), result.get("tExpectedAdaptive")) if value is not None]
    if len(priced) != 1 or priced[0] != result["rankedT"]:
        raise ValueError("a standalone primitive result must price exactly one of tDeterministic and "
                         "tExpectedAdaptive, and it must be the ranked T")
    return {**panel, "T": result["rankedT"], "qubits": result["qubits"]}


def row_of(panel: Raw, *, origin: str, name: str, created_at: str, pull_request_url: Optional[str], submission_id: Optional[str],
           detail: str) -> Raw:
    """A ledger row (no rank): the numbers a board shows, taken from the panel they came from."""
    full = panel["fullRun"]
    label = full["ladderLabel"] if full else panel["ladderLabel"]
    price = ranked_price_of(full) if full else standalone_price_of(panel)
    return {
        "rowId": panel["rowId"], "origin": origin, "name": name, "T": price["T"],
        "qubits": price["qubits"], "ladder": ladder_of(label), "ladderLabel": label,
        "createdAt": created_at, "pullRequestUrl": pull_request_url, "submissionId": submission_id,
        "trust": trust_of(panel["verification"]), "detail": detail,
    }


def panel_path(epoch: str, row_id: str, board: str, delta: str) -> str:
    return f"/results/v2/e/{epoch}/policies/{row_id}/{board}-{delta}.json"


def chart_path(epoch: str, board: str, delta: str) -> str:
    return f"/results/v2/e/{epoch}/library/chart/{board}-{delta}.json"


def chart_of(series: Raw, *, epoch: str, board: str, delta: str) -> Raw:
    """The reference library for one board and delta, cut to what a chart draws."""
    return {"schema": "forshor.chart/v2", "epoch": epoch, "board": board, "delta": delta, "series": {
        name: [{"n": p["n"], "T": p["T"], "ladderLabel": p.get("ladder_str")} for p in series.get(name, [])]
        for name in ("reference", "kmy", "champions", "all_sto")}}


def benchmarks_of(series: Raw, n: int = 2048) -> List[Raw]:
    out = []
    for key, label, note in (("kmy", "Benchmark from the Kahanamoku-Meyer and Yao tables", "The planner's optimum over the explicit tables of Kahanamoku-Meyer and Yao, priced by the same engine."),
                             ("reference", "STO reference policy", "ShorTableOptimization's reference policy, priced by the same engine.")):
        p = next((x for x in series.get(key, []) if x["n"] == n), None)
        if p:
            out.append({"id": key, "label": label, "T": p["T"], "ladderLabel": p.get("ladder_str"), "note": note})
    return out
