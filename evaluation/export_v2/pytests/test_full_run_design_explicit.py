"""The ORTHOGONAL conversion path: a compiled selection carrying an EXPLICIT per-family QFT design.

The profile path (`test_full_run_design.py`) resolves 8 closed-enum labels. This file covers the second
path, which accepts an arbitrary per-family design and so unlocks the combinations the enum cannot name.

Every test names the production change in `export_v2/full_run_design.py` that would fail it.

THE WIRE SHAPE, and the evidence for it. Nothing here is invented; each key is a wire key transcribed from
the serializer that emits it, so renaming one silently stops matching a compiled selection.

* the envelope is the one already in the recorded compiled exports -- `submitted_full_run` carrying
  `{"schema": "forshor.full-run-selection/v1", ...}` (see the `test_full_run_design.py` header);
* the design BODY mirrors the research record's only serializer:
  `{"modmul": <label>, "schema": "forshor.design-local/v1", "qft": {<family>: <choice>},
  "phase_product": {<step>: {"backend": <label>}}}`, with `phase_product` emitted only when non-empty and
  only `some` families present under `qft`;
* a family value is exactly that record's per-family choice: `{"backend": ..., "target":
  "exact"}` or `{"backend": ..., "target": "approximate", "max_error": "<units>e-<scale>"}`. That is
  already `qft_overlay.parse_design`'s contract, so this path does not restate it -- it DELEGATES value
  validation to the overlay, keeping one validator in the repo and not two;
* the families and the per-family `rotation_backend`/`step*` fields are the ones the production-side design
  record proposes, which it states mirrors the research record;
* `readout` is NOT part of the body: the research record takes it as a PARAMETER rather than a field,
  because that document has no separate key, while production carries `submitted_readout` as its own
  top-level key beside `submitted_full_run`, and the production-side record has no readout field at all.

WHAT LEAN MUST STILL EMIT. The production-side design record's optional label is refused when absent, so
today the verifier emits a profile LABEL and never a design body. This path is therefore qualified but not
yet reachable from production; see the lane report's Port A dependency.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, full_run_design, mapping, pricing  # noqa: E402

DATA = Path(__file__).parent / "data"

EXACT = {"backend": "ross_selinger", "target": "exact"}


def band(max_error="1e-6", backend="ross_selinger"):
    return {"backend": backend, "target": "approximate", "max_error": max_error}


def explicit(qft, **body):
    """A compiled selection carrying an explicit design body, in the research record's shape."""
    design = {"modmul": "reference", "schema": "forshor.design-local/v1", "qft": qft}
    design.update(body)
    return {"schema": "forshor.full-run-selection/v1", "design": design}


# --------------------------------------------------------------------------------------------------
# the wire shape is the Lean serializer's, not a new one
# --------------------------------------------------------------------------------------------------

def test_the_module_names_the_keys_and_labels_the_lean_serializer_emits():
    """Fails if the explicit path drifts from the research record's serializer. `modmul`, the three
    multiplier labels and the `phase_product` step names are all transcribed wire keys, not local choices;
    `forshor.design-local/v1` is the schema string at `:86`."""
    assert full_run_design.DESIGN_KEY == "design"
    assert full_run_design.PROFILE_KEY == "profile"
    assert full_run_design.DESIGN_SCHEMA == "forshor.design-local/v1"
    assert full_run_design.QFT_KEY == "qft"
    assert full_run_design.MULTIPLIER_KEY == "modmul"
    assert full_run_design.MULTIPLIER_REFERENCE == "reference"
    assert set(full_run_design.MULTIPLIERS) == {"reference", "split_compare", "quotient_stream"}
    assert full_run_design.PHASE_KEY == "phase_product"
    assert set(full_run_design.PHASE_STEPS) == {"step1", "step2", "step4", "step5"}
    assert full_run_design.ROTATION_FIELD == "rotation_backend"


def test_a_family_value_is_exactly_the_two_forms_the_research_record_emits():
    """Fails if the explicit path accepts a family shape the research record's per-family choice cannot
    produce, or rejects one it can: exact carries no `max_error`, approximate carries `<units>e-<scale>`."""
    assert full_run_design.design_of(explicit({"data": band()})) == {"data": band()}
    assert full_run_design.design_of(explicit({"data": band(), "work": EXACT})) == {"data": band(), "work": EXACT}
    with pytest.raises(ValueError, match="'exact' takes no 'max_error'"):
        full_run_design.design_of(explicit({"data": {**EXACT, "max_error": "1e-6"}}))
    with pytest.raises(ValueError, match="needs a 'max_error'"):
        full_run_design.design_of(explicit({"data": {"backend": "ross_selinger", "target": "approximate"}}))


# --------------------------------------------------------------------------------------------------
# arbitrary combinations convert
# --------------------------------------------------------------------------------------------------

ARBITRARY = {
    "data-banded-work-exact": ({"data": band(), "work": EXACT}, {"data": band(), "work": EXACT}),
    "all-four-banded": ({f: band() for f in ("data", "work", "scratch", "exponent")},
                        {f: band() for f in ("data", "work", "scratch", "exponent")}),
    "scratch-only": ({"scratch": band()}, {"scratch": band()}),
    "exponent-only": ({"exponent": band()}, {"exponent": band()}),
    "mixed-ceilings": ({"data": band("1e-6"), "work": band("25e-9"), "scratch": EXACT},
                       {"data": band("1e-6"), "work": band("25e-9"), "scratch": EXACT}),
    "explicit-nulls-are-unchanged-families": ({"data": band(), "work": None},
                                              {"data": band(), "work": None}),
}


@pytest.mark.parametrize("qft,want", list(ARBITRARY.values()), ids=list(ARBITRARY))
def test_an_arbitrary_per_family_combination_converts(qft, want):
    """THE TIER 3a UNLOCK. Fails if full_run_design.design_of still only resolves profile labels: these
    combinations have no label in the closed enum and so could not be converted at all before this path
    existed."""
    assert full_run_design.design_of(explicit(qft)) == want


def test_a_converted_explicit_design_is_a_fresh_mapping_the_caller_cannot_poison():
    """Fails if design_of hands back the compiled document's own nested objects, which a later mutation
    (or a mutation by the engine) would then write back into the attested selection."""
    selection = explicit({"data": band()})
    got = full_run_design.design_of(selection)
    got["data"]["max_error"] = "9e-1"
    got["work"] = band()
    assert selection["design"]["qft"] == {"data": band()}
    assert full_run_design.design_of(selection) == {"data": band()}


def test_the_absent_and_empty_qft_object_are_both_no_design_at_all():
    """Fails if an explicit design naming no family converts to `{}` instead of None.

    THE EMPTY SELECTION IS NOT A DESIGN. An absent selection, an explicit `reference`, and a design body
    that names no family are all the same thing: the engine's untouched default, `design = None` -- never
    an all-defaults record. This assertion is the public, executable statement of that rule."""
    assert full_run_design.design_of(explicit({})) is None
    assert full_run_design.design_of({"schema": "forshor.full-run-selection/v1",
                                      "design": {"modmul": "reference"}}) is None


# --------------------------------------------------------------------------------------------------
# exact means UNTOUCHED: an all-exact design is no design at all
# --------------------------------------------------------------------------------------------------

ALL_EXACT = {f: EXACT for f in ("data", "work", "scratch", "exponent")}


@pytest.mark.parametrize("qft", [
    ALL_EXACT, {"data": EXACT}, {"data": EXACT, "work": None}, {"work": EXACT, "exponent": EXACT},
], ids=["all-four", "one-family", "one-family-and-a-null", "two-families"])
def test_an_all_exact_explicit_design_requests_nothing_and_converts_to_no_design(qft):
    """THE CRITICAL SEMANTICS (qft_overlay.py:34-40, measured by lane WM). `target: "exact"` means LEAVE
    THIS FAMILY AS THE ENGINE PRICED IT (k_max = None, no substitution), never "substitute the exact
    ladder" -- substituting it costs 14.8x T. A design in which NO family asks for an approximate target
    therefore asks for nothing, and `design_of` must collapse it to None exactly as `reference` collapses.
    Fails if the explicit path returns a mapping here. What that costs is measured by
    `test_an_all_exact_explicit_design_is_priceable_at_every_delta_a_publish_request_prices`: the design
    would be threaded into pricing, and `price_views` refuses any design outside delta 1e-2, so a design
    that asks for nothing could not be priced at 3 of the 4 deltas a publish request prices."""
    assert full_run_design.design_of(explicit(qft)) is None


# --------------------------------------------------------------------------------------------------
# fail closed: every refusal names itself and prices nothing
# --------------------------------------------------------------------------------------------------

REFUSALS = {
    # --- the overlay's own value rules, delegated to qft_overlay.parse_design / parse_max_error
    "unknown-backend": (explicit({"data": {"backend": "magic_state_cultivation", "target": "exact"}}),
                        "magic_state_cultivation"),
    "unknown-target": (explicit({"data": {"backend": "ross_selinger", "target": "cheap"}}), "'cheap'"),
    # NOTE: `catalytic-approximate` and `catalytic-exact` were refusal cases here until lane WX priced the
    # catalytic arm. They are now CONVERSION cases, re-pointed to
    # `test_a_catalytic_design_converts_on_both_targets_and_is_never_collapsed` below.
    "max-error-float": (explicit({"data": {**band(), "max_error": 1e-6}}), "max_error"),
    "max-error-decimal-point": (explicit({"data": band("1.5e-6")}), "1.5e-6"),
    "max-error-capital-e": (explicit({"data": band("1E-6")}), "1E-6"),
    "max-error-positive-exponent": (explicit({"data": band("1e+6")}), r"1e\+6"),
    "max-error-signed": (explicit({"data": band("-1e-6")}), "-1e-6"),
    "max-error-bare": (explicit({"data": band("0.000001")}), "0.000001"),
    "max-error-not-below-one": (explicit({"data": band("5e-0")}), "5e-0"),
    # --- shape rules this path owns
    "step2": (explicit({"data": band()}, phase_product={"step2": {"backend": "fallback"}}), "step2"),
    "step4": (explicit({"data": band()}, phase_product={"step4": {"backend": "selective_kick"}}), "step4"),
    "step1": (explicit({"data": band()}, phase_product={"step1": {"backend": "fallback"}}), "step1"),
    "step5": (explicit({"data": band()}, phase_product={"step5": {"backend": "fallback"}}), "step5"),
    "rotation-backend": (explicit({"data": {**band(), "rotation_backend": "fallback"}}), "rotation_backend"),
    "rotation-backend-alone": (explicit({"data": {"rotation_backend": "fallback"}}), "rotation_backend"),
    "unknown-family": (explicit({"data": band(), "ancilla": band()}), "ancilla"),
    "multiplier-quotient-stream": (explicit({"data": band()}, modmul="quotient_stream"), "quotient_stream"),
    "multiplier-split-compare": (explicit({"data": band()}, modmul="split_compare"), "split_compare"),
    "multiplier-unknown": (explicit({"data": band()}, modmul="karatsuba"), "karatsuba"),
    "readout-in-the-body": (explicit({"data": band()}, readout="semiclassical"), "readout"),
    "wrong-body-schema": (explicit({"data": band()}, schema="forshor.design-local/v2"),
                          "forshor.design-local/v2"),
    "qft-not-a-mapping": ({"schema": "forshor.full-run-selection/v1",
                           "design": {"qft": ["data"]}}, "qft"),
    "design-not-a-mapping": ({"schema": "forshor.full-run-selection/v1", "design": "quotient-exact"},
                             "design"),
    "both-profile-and-design": ({"schema": "forshor.full-run-selection/v1", "profile": "reference",
                                 "design": {"qft": {"data": band()}}}, "both"),
    "neither-profile-nor-design": ({"schema": "forshor.full-run-selection/v1"}, "neither"),
}


@pytest.mark.parametrize("selection,names", list(REFUSALS.values()), ids=list(REFUSALS))
def test_an_unconvertible_explicit_design_is_refused_and_names_what_it_refused(selection, names):
    """FAIL CLOSED. Fails if full_run_design.design_of ever answers one of these with a design mapping --
    which would be priced at the Ross-Selinger default, the silent fallback the release forbids -- or
    refuses it without naming the offending value, which makes the refusal unactionable."""
    with pytest.raises(ValueError, match=names):
        full_run_design.design_of(selection)


@pytest.mark.parametrize("choice,target", [(band(backend="catalytic"), "approximate"),
                                           ({"backend": "catalytic", "target": "exact"}, "exact")],
                         ids=["catalytic-approximate", "catalytic-exact"])
def test_a_catalytic_design_converts_on_both_targets_and_is_never_collapsed(choice, target):
    """RE-POINTED (wave 2, lane XF). These two were REFUSAL cases -- `catalytic` was a refusal class,
    because the arm "re-prices every rotation in the run and adds a catalyst register, which is a separate
    axis from the QFT band". Lane WX priced that axis (its own share of the synthesis pool, the shared
    register charged to Q, and `catalyst_prep_T`), so `catalytic` is a CONVERTIBLE backend and the refusal
    class is gone. They are re-pointed rather than deleted, because what they guard still needs guarding:
    a catalytic design must not be answered with anything other than a catalytic design.

    The `exact` case is the one that matters, and it caught a real integration defect. `backend` and
    `target` are ORTHOGONAL: target picks the construction, backend picks the currency. So a catalytic
    family changes the price even on an exact target -- `qft_overlay.project` counts it as `touched` and
    the arm re-prices its rotations. WR's "exact means untouched" collapse, written when the band was the
    only axis, returned None for it, which would have priced a catalysis request at the Ross-Selinger
    default: measured 1628644042547.186 (engine) against 1641978984650.562 (overlay) at delta 1e-2. The
    collapse now tests both axes, and this test is what pins that.

    Fails if either the catalytic backend stops converting, or the collapse starts eating it again."""
    design = full_run_design.design_of(explicit({"data": choice}))
    assert design is not None, "a catalytic design must never collapse to the engine's default"
    assert design == {"data": choice}

    from export_v2 import qft_overlay
    plan = qft_overlay.parse_design(design)
    assert plan["data"][0] == "catalytic"
    assert plan["data"][1] == target


def test_the_refusals_are_the_complete_documented_list():
    """Fails if a refusal class is added to the code without a case above, or dropped from REFUSAL_CLASSES
    without being made convertible. Keeps the lane report's refusal list honest.

    `catalytic` was dropped from this list in wave 2 because it WAS made convertible, by lane WX; the two
    cases that covered it are now conversion cases immediately above."""
    assert set(full_run_design.REFUSAL_CLASSES) == {
        "unknown-backend", "unknown-target", "max-error", "phase-product",
        "rotation-backend", "unknown-family", "multiplier", "unknown-body-key", "body-schema",
        "malformed-shape", "profile-and-design",
    }


# --------------------------------------------------------------------------------------------------
# the profile path is untouched
# --------------------------------------------------------------------------------------------------

def test_the_eight_profile_labels_all_still_resolve_and_the_seven_refuse_by_name():
    """Fails if widening the converter perturbs an already-qualified fixture. These 8 labels are the whole
    accepted set today -- the qualified set of the production-side design record; `reference` is the
    only one that converts, and the other seven must refuse BY NAME -- four on the feature beyond the QFT
    families, three on the multiplier topology."""
    profile = lambda p: {"schema": "forshor.full-run-selection/v1", "profile": p}  # noqa: E731
    assert full_run_design.design_of(profile("reference")) is None
    for label in ("quotient-fallback-data-catalytic-work", "quotient-step2-fallback", "split-step4-kicks",
                  "split-step2-fallback-step4-kicks"):
        with pytest.raises(ValueError, match=f"{label}.*cannot be priced here"):
            full_run_design.design_of(profile(label))
    for label in ("quotient-exact", "quotient-catalytic", "quotient-banded-data-catalytic-work"):
        with pytest.raises(ValueError, match=f"{label}.*multiplier topology 'quotient_stream'"):
            full_run_design.design_of(profile(label))


# --------------------------------------------------------------------------------------------------
# the multiplier topology (lane WS's defect)
# --------------------------------------------------------------------------------------------------

def test_the_overlay_cannot_honour_a_topology_so_a_non_reference_one_is_refused():
    """POINT 2, pinned as an assertion rather than a claim. `qft_overlay` prices the QFT band only:
    its families are exactly the four QFT registers, `project` takes (policy, coherent, design) with no
    topology parameter, and `parse_design` refuses a `modmul` key outright -- even `modmul: "reference"`.
    Fails if the overlay grows a topology and this gate is not revisited, or if the gate is removed while
    the overlay still cannot honour one."""
    from export_v2 import qft_overlay

    assert qft_overlay.FAMILIES == ("data", "work", "scratch", "exponent")
    assert "design" in qft_overlay.project.__code__.co_varnames
    with pytest.raises(ValueError, match=r"cannot honour: \['modmul'\]"):
        qft_overlay.parse_design({"modmul": "reference", "data": EXACT})
    for topology in ("quotient_stream", "split_compare"):
        with pytest.raises(ValueError, match=f"multiplier topology '{topology}'"):
            full_run_design.design_of(explicit({"data": band()}, modmul=topology))


@pytest.mark.parametrize("topology", ["quotient_stream", "split_compare"])
def test_an_all_exact_design_on_a_NON_reference_topology_is_NOT_the_default_price(topology):
    """POINT 3. An all-exact design on the REFERENCE topology is bit-identical to no design, and that is
    correct: nothing is substituted and the topology is the one the engine already priced. An all-exact
    design on a DIFFERENT topology must NOT collapse to the default -- the topology alone changes the T
    cost and the qubit base, so returning None there would price a quotientStream run at reference numbers
    and the identity test would be masking the bug instead of proving correctness.

    Fails if the exact-means-untouched collapse is applied before the topology gate: the design would
    convert to None and be priced as the reference default, silently."""
    assert full_run_design.design_of(explicit(ALL_EXACT)) is None               # reference: the default
    with pytest.raises(ValueError, match=f"multiplier topology '{topology}'"):  # not the default
        full_run_design.design_of(explicit(ALL_EXACT, modmul=topology))


# --------------------------------------------------------------------------------------------------
# arbitrary combinations PRICE (the real engine, through the overlay)
# --------------------------------------------------------------------------------------------------

#: `fullalg.price_full(sto_reference_policies()[PAIR], 2048, 1e-2, detail=True)` at 792464f
#: (test_qft_overlay.py:17-20, lane WM's frozen baseline).
BASELINE_T = 1938733902110.921

#: Lane WM's measured bands for the four families at eta = 1e-6 on the reference topology.
K_MAX_AT_1E6 = {"data": 31, "work": 32, "scratch": 33, "exponent": 32}


@pytest.fixture(scope="module")
def priced():
    from export_v2 import engine

    eng = engine.load()
    policy = eng.library.sto_reference_policies()[eng.tables.PAIR]
    return policy, eng.fullalg.price_full(policy, 2048, 1e-2, detail=True)


@pytest.mark.slow
@pytest.mark.parametrize("qft,substituted", [
    ({"data": band(), "work": EXACT}, ["data"]),
    ({f: band() for f in K_MAX_AT_1E6}, sorted(K_MAX_AT_1E6)),
    ({"scratch": band()}, ["scratch"]),
    ({"exponent": band()}, ["exponent"]),
    ({"data": band("1e-6"), "work": band("25e-9")}, ["data", "work"]),
], ids=["data-banded-work-exact", "all-four-banded", "scratch-only", "exponent-only", "mixed-ceilings"])
def test_an_arbitrary_combination_prices_through_the_overlay(priced, qft, substituted):
    """THE UNLOCK, end to end. Fails if a design this path converts cannot actually be priced -- i.e. if the
    converted mapping is not the shape `qft_overlay.project` consumes. Asserts a REAL price: the band moves
    T, every substituted family reports lane WM's measured k_max, and an exact family is reported untouched."""
    from export_v2 import qft_overlay

    policy, full = priced
    design = full_run_design.design_of(explicit(qft))
    assert design is not None
    out = qft_overlay.project(policy, full, design)
    assert out["substituted"] == substituted
    assert out["identity"] is False
    assert out["T"] != BASELINE_T
    for family in substituted:
        assert out["families"][family]["kMax"] == K_MAX_AT_1E6[family] or qft[family]["max_error"] != "1e-6"
        assert out["families"][family]["banded"] is True
    for family, choice in qft.items():
        if choice == EXACT:
            assert out["families"][family]["kMax"] is None
            assert out["families"][family]["target"] == "exact"


@pytest.mark.slow
def test_the_four_families_band_at_the_widths_and_k_max_lane_WM_measured(priced):
    """Pins the unlock's numbers so a silent re-band goes red here. Fails if the converted design stops
    reaching all four families -- the combination the closed enum can never name."""
    from export_v2 import qft_overlay

    policy, full = priced
    design = full_run_design.design_of(explicit({f: band("1e-6") for f in K_MAX_AT_1E6}))
    out = qft_overlay.project(policy, full, design)
    assert {f: out["families"][f]["kMax"] for f in K_MAX_AT_1E6} == K_MAX_AT_1E6
    assert sorted(out["families"]) == sorted(K_MAX_AT_1E6)


@pytest.mark.slow
def test_an_all_exact_explicit_design_prices_identically_to_the_engine(priced):
    """The pricing half of "exact means untouched". Fails if an all-exact design ever substitutes anything:
    `project` must report identity and return the engine's own T, unchanged."""
    from export_v2 import qft_overlay

    policy, full = priced
    out = qft_overlay.project(policy, full, {f: EXACT for f in K_MAX_AT_1E6})
    assert out["identity"] is True and out["substituted"] == []
    assert out["T"] == full["T"] == BASELINE_T


# --------------------------------------------------------------------------------------------------
# bit identity against 792464f
# --------------------------------------------------------------------------------------------------

#: The 792464f panels for pytests/data/real/1bc92ac6e1e82ba3.policy.json at delta 1e-2. Imported rather
#: than re-transcribed so the two files cannot drift: see that file's PIN RESTORED note -- lane WO's
#: `full` value 998ad142... is correct, reproduces at 792464f (and in WR's own worktree), and is the
#: release's hard gate; WR's re-pin to c773f502... reproduces nowhere and shipped these tests red.
from test_full_run_design import HEAD_PANELS  # noqa: E402


@pytest.mark.slow
@pytest.mark.parametrize("document", [
    {"schema": "forshor.policy-submission-semiclassical/v1"},
    {"schema": "forshor.policy-submission-semiclassical/v1", "submitted_full_run": explicit(ALL_EXACT)},
    {"schema": "forshor.policy-submission-semiclassical/v1", "submitted_full_run": explicit({"data": EXACT})},
    {"schema": "forshor.policy-submission-semiclassical/v1",
     "submitted_full_run": explicit({"data": EXACT, "work": None})},
], ids=["no-selection", "explicit-all-exact", "explicit-one-exact", "explicit-exact-and-null"])
def test_an_all_exact_explicit_design_prices_bit_identically_to_no_selection_at_792464f(document):
    """THE non-negotiable test, re-verified for the NEW path, at delta 1e-2 against the 792464f pins."""
    from export_v2 import engine

    design = full_run_design.design_of(full_run_design.selection_of(document))
    assert design is None
    model = json.loads((DATA / "golden.json").read_text())["model"]
    policy_json = json.loads((DATA / "real/1bc92ac6e1e82ba3.policy.json").read_text())
    policy = engine.load().tables.Policy.from_json(policy_json)
    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["pp", "full"], design=design)
    for board in ("pp", "full"):
        panel = mapping.panel_of(views[board], epoch=canon.epoch_of(model), row_id="a" * 40, board=board,
                                 delta="1e-2", verification={"stoMirror": None, "forshorPolicy": None})
        assert canon.sha256_hex(panel) == HEAD_PANELS[board], board


@pytest.mark.slow
def test_an_all_exact_explicit_design_is_priceable_at_every_delta_a_publish_request_prices():
    """WHY THE COLLAPSE IS LOAD-BEARING, and the honest statement of what it buys.

    Panel bytes alone do NOT detect the collapse: `mapping.panel_of` does not carry `view["full"]["qft"]`,
    and the overlay returns `identity: True` for an all-exact mapping, so T is unchanged either way
    (measured). The observable consequence is REACHABILITY. A publish request prices all four of
    `mapping.DELTA_KEYS`, and `pricing.price_views` refuses any design outside 1e-2 -- the one precision
    the QFT surface is qualified at. So without the collapse an all-exact design is refused at 3e-4, 1e-4
    and 1e-6 and cannot be priced AT ALL, while "no selection" prices everywhere. With the collapse it is
    the default path at every delta, byte for byte.

    Fails if the collapse is removed (the three non-1e-2 deltas then raise) or if it ever starts swallowing
    a design that really does ask for a band (the banded comparison at the end would stop differing)."""
    from export_v2 import engine

    model = json.loads((DATA / "golden.json").read_text())["model"]
    policy_json = json.loads((DATA / "real/1bc92ac6e1e82ba3.policy.json").read_text())
    policy = engine.load().tables.Policy.from_json(policy_json)
    design = full_run_design.design_of(explicit(ALL_EXACT))
    assert design is None

    def panels(delta, design):
        views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", delta, ["pp", "full"], design=design)
        return {b: canon.sha256_hex(mapping.panel_of(
            views[b], epoch=canon.epoch_of(model), row_id="a" * 40, board=b, delta=delta,
            verification={"stoMirror": None, "forshorPolicy": None})) for b in ("pp", "full")}

    assert mapping.DELTA_KEYS == ("1e-2", "3e-4", "1e-4", "1e-6")
    for delta in mapping.DELTA_KEYS:
        assert panels(delta, design) == panels(delta, None), delta

    # Non-vacuity: a design that DOES ask for a band is refused outside 1e-2, which is what makes the
    # all-exact case above a real exemption and not a no-op assertion.
    banded = full_run_design.design_of(explicit({"data": band()}))
    assert banded is not None
    for delta in ("3e-4", "1e-4", "1e-6"):
        with pytest.raises(ValueError, match="delta 1e-2"):
            pricing.price_views(policy, "t", delta, ["pp", "full"], design=banded)


@pytest.mark.slow
def test_a_banded_explicit_design_does_NOT_price_identically(priced):
    """NON-VACUITY for the identity tests above. If banding a family produced the default price too, the
    identity assertions would be measuring nothing. Fails if the overlay stops being able to move T."""
    from export_v2 import qft_overlay

    policy, full = priced
    out = qft_overlay.project(policy, full, full_run_design.design_of(explicit({"data": band()})))
    assert out["T"] != full["T"] and out["identity"] is False
