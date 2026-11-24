"""END TO END: a compiled document's `submitted_full_run` -> a priced panel carrying a `qft` projection.

This is lane WP's deliverable: the join of lane WO (`full_run_design`, the `design=` threading) and lane WM
(`qft_overlay`, the Coppersmith-banded QFT price) into ONE chain, exercised from the verifier's own output.

    compiled document
      -> full_run_design.selection_of   (key PRESENCE is the test)
      -> full_run_design.design_of      (profile -> per-family QFT design, or None, or REFUSED by name)
      -> pricing.price_views(design=)   (delta 1e-2 and the full board only)
      -> qft_overlay.project            (the hook at pricing.py:52-54, mirroring `measured` at :48-51)
      -> mapping.panel_of               (the panel carries `qft` beside the engine's own price)

Lane WY then removed the two structural obstacles this chain still ran into -- a design-bearing publish request
raised partway through its four-delta loop, and the projection was never the ranked price. See
`test_design_price_plumbing.py`; the chain tests below are unchanged except where they stated an obstacle as a
permanent fact.

WHAT THIS LANE FOUND, and what these tests therefore pin
--------------------------------------------------------------------------------------------------
The two lanes were written against DIFFERENT contracts and only one of them landed:

* WO threaded the design into the ENGINE (`price_full(..., design=design)`), per a brief that expected lane
  WM to add that parameter -- the brief whose name `prepush/WM-engine-design-param` still carries.
* WM did not, and documented why: `evaluation/engine` is an epoch input through BOTH `engineRevision`
  (`epoch.py:91`) and `dataFingerprint` (`full_frontier.py:43-50`, a byte hash of the pricer sources), so an
  additive `design=` parameter could not be default-preserving at the epoch level however carefully written
  (measured: one comment line appended to `site_engine/fullalg.py` moves ab1f8887b1b50e56 to
  d3bda33d5ddd5dca). WM qualified the axis as an out-of-engine OVERLAY instead.

So `price_full` still has its 792464f signature and WO's engine-side call raised
`TypeError: price_full() got an unexpected keyword argument 'design'` against the real engine. WP kept all of
WO's threading -- the signature, the 1e-2 guard, the full-board guard, the refuse-before-pricing ordering in
`request._build_request` -- and routed the design to the overlay instead. `test_the_chain_never_asks_the_engine_
for_a_design` pins that, so nobody re-adds the engine-side call.

EVERY PROFILE LABEL REFUSES; THE EXPLICIT DESIGN IS WHAT SHIPS (integration wave 2, lane XF).
`full_run_design` once converted three of the seven profiles to a bare per-family mapping. Lane WR found
that this DROPPED the multiplier topology: all seven non-reference profiles select `modmul` =
`quotient_stream` or `split_compare` (`audit_native_fullrun.py:25-30`, QFT-SURFACE-MAPPING.md section 1.1),
the topology drives both the T cost (`policy_design.py:139-142`) and the qubit base (`:160-162`), and
`qft_overlay.project` substitutes QFT sites into the engine's frozen REFERENCE census and has no topology
parameter at all. Converting a profile therefore priced a quotientStream run on reference-topology numbers
-- reporting the reference price as if the option had been honoured, the one silent default this release
forbids. WR's TOPOLOGY GATE refuses it instead, by name.

The owner has DROPPED quotient customization from this release (deferred), so this is settled policy, not a
gap: every `quotient-*` and every `split-*` profile label REFUSES. What ships is the EXPLICIT per-family
design on the reference topology (`full_run_design`'s second path, `Design.json`'s wire shape), which
prices any combination of the four QFT families. The tests below that need a real design to thread are
therefore stated against the explicit path; `test_every_profile_label_refuses_and_the_explicit_design_prices`
states the position as a fact rather than leaving it to be discovered.

AND THE CHAIN DIFFERENTIATES (lane WX), ON THAT PATH. WX priced the catalytic arm, which `qft_overlay` used
to refuse as "a second axis": the arm's own proportional share of the run-wide synthesis pool, the shared
catalyst register charged to Q, and `catalyst_prep_T` as a separate unconditional charge. So a design naming
`backend: "catalytic"` produces a price that DIFFERS from the engine's own -- a genuinely differentiated
full-run price, which is this release's deliverable.

WX stated that over the two catalytic PROFILE LABELS, which the topology gate refuses. The two facts are
not in conflict, because the label and the design are different things: what the labels named was a
per-family QFT combination ATTACHED TO a quotientStream topology, and it is only the topology that cannot
be honoured. Every one of the three combinations those labels named -- exact/exact, catalytic/catalytic,
banded-data/catalytic-work -- still prices when expressed as an EXPLICIT reference-topology design, the
catalytic two differentiating exactly as WX measured. WX's two tests are re-pointed there rather than
dropped, and WR's gate is not weakened:
`test_the_catalytic_designs_price_a_differentiated_full_run` and
`test_the_explicit_design_prices_every_combination_the_profile_labels_named`.

"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, full_run_design, mapping, pricing, qft_overlay, request  # noqa: E402

DATA = Path(__file__).parent / "data"
POLICY = DATA / "real/1bc92ac6e1e82ba3.policy.json"

#: Panel sha256 at 792464f for `POLICY` at delta 1e-2, recorded by lane WO and re-verified on this merged
#: branch. The non-negotiable number: anything that moves it has changed a published price.
HEAD_PANELS = {"full": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
               "pp": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f"}

#: REFUSED ON THEIR MULTIPLIER TOPOLOGY (lane WR's gate). These three name only QFT families, so they once
#: converted; they select `modmul: quotient_stream`, which `qft_overlay` cannot honour, so they refuse by name.
REFUSED_BY_TOPOLOGY = ("quotient-exact", "quotient-catalytic", "quotient-banded-data-catalytic-work")
#: Of those three, the two whose family combination names `backend: "catalytic"`. The LABELS refuse; the
#: same combinations as EXPLICIT reference-topology designs price, and differentiate (lane WX's arm).
CATALYTIC_COMBINATIONS = ("quotient-catalytic", "quotient-banded-data-catalytic-work")
#: Refused by `full_run_design` itself: they select a rotation or phase-product backend, not a QFT. Two of
#: these also select `split_compare`; they are refused on the selector, which is checked first.
REFUSED_BY_DESIGN = ("quotient-fallback-data-catalytic-work", "quotient-step2-fallback",
                     "split-step4-kicks", "split-step2-fallback-step4-kicks")
#: No profile label prices in this release. Kept as an explicit empty tuple so the fact is stated, not implied.
PRICES = ()

#: The per-family choices the explicit path carries, in `QftChoice.json`'s shape (`Gate5Design.lean:39-44`).
RS_EXACT = {"backend": "ross_selinger", "target": "exact"}
BANDED = {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}

#: The compiled document the verifier prints for a non-reference profile: the measured (semiclassical) schema,
#: because `PolicyMain.lean:444-447` refuses a full-run selection unless the saved readout IS semiclassical,
#: plus the selection object `PolicyMain.lean:462-465` merges in.
MEASURED_SCHEMA = "forshor.policy-submission-semiclassical/v1"


def document(profile=None):
    doc = {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical"}
    if profile is not None:
        doc["submitted_full_run"] = {"schema": "forshor.full-run-selection/v1", "profile": profile}
    return doc


def explicit_document(qft, modmul="reference"):
    """The compiled document carrying an EXPLICIT per-family design BODY -- `full_run_design`'s second path,
    in `Gate5Design.Design.json`'s wire shape, inside the envelope the recorded exports already use.

    This is the path that prices in this release. Every profile LABEL refuses on its multiplier topology
    (lane WR), so the chain tests that need a design to thread drive it from here instead."""
    return {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical",
            "submitted_full_run": {"schema": "forshor.full-run-selection/v1",
                                   "design": {"schema": "forshor.design-local/v1",
                                              "modmul": modmul, "qft": qft}}}


def design_from(doc):
    """The two WO steps of the chain, in order, exactly as `request._build_request` runs them."""
    return full_run_design.design_of(full_run_design.selection_of(doc))


@pytest.fixture(scope="module")
def policy():
    from export_v2 import engine
    return engine.load().tables.Policy.from_json(json.loads(POLICY.read_text()))


@pytest.fixture(scope="module")
def epoch_id():
    return canon.epoch_of(json.loads((DATA / "golden.json").read_text())["model"])


def panel(view, epoch_id, board):
    return mapping.panel_of(view, epoch=epoch_id, row_id="a" * 40, board=board, delta="1e-2",
                            verification={"stoMirror": None, "forshorPolicy": None})


# ------------------------------------------------------------------ the chain, with the real engine


@pytest.mark.slow
@pytest.mark.parametrize("qft", [{"data": BANDED}, {"work": BANDED}, {"data": BANDED, "work": RS_EXACT}],
                         ids=["data-banded", "work-banded", "data-banded-work-exact"])
def test_a_compiled_document_prices_a_panel_carrying_a_qft_projection(policy, epoch_id, qft):
    """THE DELIVERABLE. Start at the verifier's compiled document, end at a panel whose full-run price carries
    a `qft` projection, through all four production steps.

    RE-POINTED (wave 2, lane XF): this was parametrized over `PRICES`, the profile labels that used to
    convert. WR's topology gate refuses every label, so the deliverable is exercised over EXPLICIT
    reference-topology designs instead -- which is both what ships and a strictly wider test, since the
    explicit path prices arbitrary per-family combinations rather than three fixed ones.

    Fails if the hook at `pricing.py:52-54` is reverted (then `view["full"]` has no `qft` and the panel has
    none either), if `mapping.full_run_of` stops carrying `qft`, or if either conversion step stops working."""
    doc = explicit_document(qft)

    selection = full_run_design.selection_of(doc)
    assert selection["design"]["modmul"] == "reference"
    design = full_run_design.design_of(selection)
    assert design is not None and set(design) <= set(qft_overlay.FAMILIES)

    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    projection = views["full"]["full"]["qft"]
    assert projection["pricingRule"] == qft_overlay.RULE == "qft-design-axis-v2"
    assert projection["epoch"] == qft_overlay.EPOCH == "ab1f8887b1b50e56"
    assert projection["ruleSha256"] == qft_overlay.rule_fingerprint()
    assert set(projection["families"]) == set(design)

    built = panel(views["full"], epoch_id, "full")
    assert built["fullRun"]["qft"] == projection, "the panel must carry the projection the pricer produced"


@pytest.mark.slow
def test_an_all_exact_design_is_bit_identical_to_the_no_selection_baseline(policy, epoch_id):
    """THE REGRESSION GUARD. `target: "exact"` means "leave this family's QFT exactly as the engine priced it"
    (k_max = None, no substitution), never "substitute the exact ladder" -- lane WM measured that substituting
    the exact Draper ladder at w = 4095 takes (37_598_400, 178_191) to (33_533_953, 8_289_333), a 14.8x
    rotation-driven blow-up in T. If `qft_overlay.project` ever starts substituting on an exact target this
    test goes red.

    RE-POINTED (wave 2, lane XF). This drove `quotient-exact`, which WR's topology gate now refuses. The
    guard it encodes is about the EXACT TARGET, not about that label, so it is restated over an all-exact
    EXPLICIT design and nothing is lost -- both halves are now checked, at the two layers that own them:

      1. THE CONVERTER collapses an all-exact design to None ("exact means untouched": a design in which no
         family asks for an approximate target asks for nothing at all), so the request takes the engine's
         untouched default path and the panels are the recorded 792464f bytes exactly.
      2. THE OVERLAY, driven through the production hook with that same all-exact mapping handed to
         `price_views` directly -- which is what the converter would have passed before the collapse --
         still reports `identity: True`, substitutes nothing, and reproduces the engine's census as the
         SAME float; and the priced panel differs from the baseline panel in NOTHING except the added
         `qft` evidence block. This is the assertion that actually pins the no-substitution rule; the
         collapse above would otherwise hide a regression in it.

    That last point is the honest form of "bit-identical" once a panel can carry the projection at all: no
    PRICE moves, and the only new bytes are an evidence block saying nothing was substituted. A plain hash
    compare could not distinguish "nothing moved" from "two things moved and cancelled".
    """
    all_exact = {"data": RS_EXACT, "work": RS_EXACT}

    # 1. the converter: all-exact asks for nothing, so the default path and the recorded bytes.
    assert design_from(explicit_document(all_exact)) is None
    baseline = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=None)
    assert "qft" not in baseline["full"]["full"]

    # 2. the overlay, through the production hook: the mapping is still the identity, so the collapse
    #    above hides nothing.
    priced = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=all_exact)
    engine_price, projection = baseline["full"]["full"], priced["full"]["full"]["qft"]

    assert projection["identity"] is True and projection["substituted"] == []
    assert all(f["target"] == "exact" and f["kMax"] is None and f["banded"] is False
               for f in projection["families"].values())
    assert projection["T"] == engine_price["T"]
    assert projection["TBase"] == engine_price["T_base"]
    assert projection["rotations"] == engine_price["rotations"]

    built, base_panel = panel(priced["full"], epoch_id, "full"), panel(baseline["full"], epoch_id, "full")
    assert built["fullRun"].pop("qft") == projection
    assert built == base_panel, "a design must change nothing but add its own evidence block"
    assert canon.sha256_hex(built) == canon.sha256_hex(base_panel) == HEAD_PANELS["full"]


@pytest.mark.slow
@pytest.mark.parametrize("doc,label", [(document(), "absent-selection"), (document("reference"), "explicit-reference")],
                         ids=["absent-selection", "explicit-reference"])
def test_absent_and_explicit_reference_are_design_none_and_the_byte_identical_baseline(policy, epoch_id, doc, label):
    """Fails if `reference` becomes an all-defaults record (`{}`) instead of None, if `selection_of` stops using
    key PRESENCE as the test, or if the threading perturbs the default price by one byte. Both panels, both
    boards, against the hashes lane WO recorded at 792464f."""
    design = design_from(doc)
    assert design is None, label
    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["pp", "full"], design=design)
    for board in ("pp", "full"):
        assert "qft" not in views[board].get("full", {}), "no design means no projection at all"
        assert canon.sha256_hex(panel(views[board], epoch_id, board)) == HEAD_PANELS[board], (label, board)


# ------------------------------------------------------------------ refusals: by name, pricing nothing


@pytest.mark.parametrize("profile", REFUSED_BY_DESIGN)
def test_the_four_non_qft_profiles_refuse_by_name_at_the_design_step(profile):
    """Fails if `full_run_design.design_of` prices a selection the QFT design cannot express -- silently at the
    default, which would publish a cheaper price than the submission asked for. The refusal must NAME the
    profile and what it selects beyond the QFT families."""
    with pytest.raises(ValueError) as caught:
        design_from(document(profile))
    message = str(caught.value)
    assert profile in message, "a refusal that does not name the profile is not actionable"
    assert full_run_design.BEYOND_QFT[profile] in message


@pytest.mark.parametrize("profile", REFUSED_BY_DESIGN)
def test_a_refused_selection_prices_nothing_and_builds_no_request(monkeypatch, profile):
    """Fails if `request._build_request` converts the selection AFTER the evidence lookup or the pricing loop.
    A refusal must not load the engine, not price a board, and not emit any part of a request."""
    monkeypatch.setattr(request.engine, "load", lambda: pytest.fail("a refused selection must not load the engine"))
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selection must not be priced"))
    args = dict(model=None, store=None, head_sha="a" * 40, pull_number=1, github_login="t", run_id="1",
                submitted_at="x", evaluated_at="y", archive_commit="b" * 40, accepted_ids=[], evaluation_key="e",
                base_generation="1", number=1)
    with pytest.raises(ValueError, match=profile):
        request._build_request({}, full_run=full_run_design.selection_of(document(profile)), **args)


@pytest.mark.parametrize("profile", REFUSED_BY_TOPOLOGY)
def test_the_three_qft_only_profiles_refuse_on_their_multiplier_topology(profile):
    """LANE WR'S GATE, and wave 2's headline resolution. These three name only QFT families, so they once
    converted to a bare per-family mapping -- silently dropping the `modmul: quotient_stream` they also
    select. The topology drives both the T cost and the qubit base, and `qft_overlay` substitutes into the
    engine's frozen REFERENCE census, so that conversion priced a quotientStream run on reference numbers.

    The refusal must NAME the profile and the topology. Fails if the gate is weakened to let a profile label
    price again -- which is exactly what wave 2 refused to do to keep two catalytic tests green."""
    with pytest.raises(ValueError) as caught:
        design_from(document(profile))
    message = str(caught.value)
    assert profile in message, "a refusal that does not name the profile is not actionable"
    assert "quotient_stream" in message
    assert full_run_design.PROFILE_TOPOLOGY[profile] == "quotient_stream"


def test_every_profile_label_refuses_and_the_explicit_design_prices(policy=None):
    """THE RELEASE-DECISION FACT, kept as an assertion so it cannot rot.

    REWRITTEN in wave 2 (lane XF), from `test_only_quotient_exact_prices_today`. That test said six of the
    seven profiles refuse and the one that prices is the identity. The position now: ALL SEVEN refuse -- the
    four selector profiles at `full_run_design`'s `BEYOND_QFT` check, the three QFT-only ones at WR's
    topology gate -- and `reference` alone converts, to None. Quotient customization is DROPPED from this
    release by owner decision, deferred; what ships is the explicit per-family design on the reference
    topology, which prices arbitrary combinations of the four QFT families.

    Fails the moment a profile label starts converting again, which is the signal to rewrite this."""
    assert set(full_run_design.PROFILES) == {"reference", *REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN}
    assert set(full_run_design.QFT_ONLY) == set(REFUSED_BY_TOPOLOGY)
    assert PRICES == (), "no profile label prices in this release"

    # Every non-reference label refuses; `reference` is the only one that converts, and it converts to None.
    for profile in (*REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN):
        with pytest.raises(ValueError):
            design_from(document(profile))
    assert design_from(document("reference")) is None
    assert design_from(document()) is None

    # Every profile label selects a non-reference topology; `reference` names none at all. This is WHY they
    # all refuse, stated once, from the converter's own table.
    assert all(full_run_design.PROFILE_TOPOLOGY[p] in ("quotient_stream", "split_compare")
               for p in (*REFUSED_BY_TOPOLOGY, *REFUSED_BY_DESIGN))
    assert full_run_design.PROFILE_TOPOLOGY["reference"] is None

    # And the explicit reference-topology path does price: an approximate target converts to a real design.
    design = design_from(explicit_document({"data": BANDED}))
    assert design == {"data": BANDED}
    plan = qft_overlay.parse_design(design)
    assert plan and all(target == "approximate" for _backend, target, _max_error in plan.values())


@pytest.mark.slow
@pytest.mark.parametrize("named", CATALYTIC_COMBINATIONS)
def test_the_catalytic_designs_price_a_differentiated_full_run(policy, epoch_id, named):
    """THE RELEASE DELIVERABLE. The catalytic arm was refused at `qft_overlay.parse_design` as "a second
    axis"; lane WX priced that axis, so a catalytic design reaches a real price through the same four
    production steps. It must differ from the engine's own price, must carry a catalyst block with all
    three of its charges, and must charge the shared register to Q.

    RE-POINTED (wave 2, lane XF) -- from `test_the_catalytic_profiles_price_a_differentiated_full_run`.
    WX drove this from the two catalytic PROFILE LABELS, which WR's topology gate now refuses: they also
    select `modmul: quotient_stream`, and the overlay prices against the frozen REFERENCE census. The gate
    is NOT weakened to keep this green. What the labels named was a per-family QFT COMBINATION attached to
    a quotientStream topology, and only the topology is unhonourable -- so the identical combination is
    driven here as an EXPLICIT reference-topology design, taken from the converter's own `QFT_ONLY` table
    so the two cannot drift. Every assertion WX wrote is preserved verbatim; only the way the design is
    obtained moved onto the path that ships.

    Fails if the catalytic arm is reverted (back to a refusal, or to the Ross-Selinger default), if the
    catalyst register stops being charged to the qubit total, or if the panel stops carrying the block."""
    design = design_from(explicit_document(full_run_design.QFT_ONLY[named]))
    assert design is not None
    assert any(choice["backend"] == "catalytic" for choice in design.values())

    views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", "1e-2", ["full"], design=design)
    full, projection = views["full"]["full"], views["full"]["full"]["qft"]
    catalyst = projection["catalyst"]

    assert projection["identity"] is False
    assert projection["T"] != full["T"], "a catalytic design must not price at the engine's own number"
    assert projection["catalysed"] == sorted(f for f, c in design.items() if c["backend"] == "catalytic")
    # All three charges present and nonzero.
    assert catalyst["b"] >= qft_overlay.B_MIN and catalyst["deltaArm"] > 0.0      # its own budget share
    assert catalyst["registerQubits"] == catalyst["b"]                            # the shared register
    assert projection["qubitsPeak"] == full["qubits_peak"] + catalyst["b"]
    assert catalyst["prepT"] > 0.0                                               # the prep allowance
    assert catalyst["T"] == catalyst["cellsT"] + catalyst["prepT"]
    assert projection["rotationsCatalysed"] > 0

    built = panel(views["full"], epoch_id, "full")
    assert built["fullRun"]["qft"] == projection, "the panel must carry the projection the pricer produced"


def test_the_explicit_design_prices_every_combination_the_profile_labels_named(policy=None):
    """The other half of the release-decision fact: what REFUSES is the label, not the design.

    RE-POINTED (wave 2, lane XF) -- from WX's `test_three_profiles_price_and_four_refuse`, which asserted
    that three profile labels price. Under WR's topology gate none of them do. The substance of WX's claim
    survives at the level it is actually true: all three per-family COMBINATIONS those labels named still
    convert and price as EXPLICIT reference-topology designs, with the two catalytic ones selecting the
    catalytic backend and `quotient-exact`'s all-exact combination collapsing to the engine's untouched
    default ("exact means untouched"). So nothing about the PRICING was lost when the labels were refused;
    only the quotientStream topology they also carried was, which is the deferred part.

    Fails if the explicit path stops honouring any combination the qualified labels named."""
    for named, qft in full_run_design.QFT_ONLY.items():
        design = design_from(explicit_document(qft))
        if named in CATALYTIC_COMBINATIONS:
            plan = qft_overlay.parse_design(design)
            assert plan and any(backend == "catalytic" for backend, _t, _m in plan.values())
        else:
            # quotient-exact's combination is all-exact, so it asks for nothing: the engine's own price.
            assert design is None, named
            assert all(choice["backend"] == "ross_selinger" and choice["target"] == "exact"
                       for choice in qft.values())


# ------------------------------------------------------------------ the engine is never asked for a design


def test_the_chain_never_asks_the_engine_for_a_design(monkeypatch):
    """Pins lane WP's merge resolution. `evaluation/engine` is an epoch input, `price_full` still has its
    792464f signature `(P, n, delta, detail, rule, negation)`, and WO's `price_full(..., design=design)` raised
    TypeError against it. The design reaches the OVERLAY. Fails if the engine-side call is ever re-added."""
    import inspect
    from export_v2 import engine

    assert "design" not in inspect.signature(engine.load().fullalg.price_full).parameters

    calls, seen = [], []

    class Fullalg:
        @staticmethod
        def price_full(P, n, delta, **kw):
            calls.append(kw)
            return {"usage": [], "tables_used": []}

    class Tables:
        PAIR, TRIPLE = "PhaseProduct", "PhaseTripleProduct"

    class PolicyView:
        @staticmethod
        def view(P, n, delta, title):
            return {"levels": [], "bands": []}

        @staticmethod
        def table_view(t):
            return t

    class Eng:
        fullalg, tables, policy_view = Fullalg, Tables, PolicyView

    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    monkeypatch.setattr(pricing.engine, "load", lambda: Eng)
    monkeypatch.setattr(qft_overlay, "project", lambda P, full, design: seen.append(copy.deepcopy(design)) or {"ok": True})
    # RE-POINTED (wave 2): driven from the EXPLICIT reference-topology design, since WR's topology gate
    # refuses every profile label. What is pinned -- the design reaches the overlay and the engine's pricer
    # is called exactly as it was at 792464f -- is unchanged.
    design = design_from(explicit_document({"data": BANDED}))
    views = pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=design)

    assert calls == [{"detail": True}], "the full-run pricer is called exactly as it was at 792464f"
    assert seen == [design], "the design reaches the overlay instead"
    assert views["full"]["full"]["qft"] == {"ok": True}


@pytest.mark.parametrize("delta", ["3e-4", "1e-4", "1e-6"])
def test_a_design_is_refused_at_every_delta_but_1e_2(delta):
    """WO's guard, still a guard. When this lane wrote it, `request._build_request` passed the design at all
    four deltas, so this refusal fired partway through the pricing loop and a design-bearing document could not
    complete a publish request at all -- the STRUCTURAL obstacle lane WY then removed by scoping the design to
    `mapping.DESIGN_DELTA` in the request and leaving this guard exactly as it is. It stays a precondition on
    the caller rather than a silent drop; `test_design_price_plumbing.py` pins the scoping and the byte
    identity of the other three panels. Fails if the 1e-2 guard in `price_views` is relaxed."""
    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    with pytest.raises(ValueError, match="delta 1e-2"):
        pricing.price_views(FakePolicy(), "t", delta, ["full"],
                            design=design_from(explicit_document({"data": BANDED})))


# ------------------------------------------------- the QFT design and a Step 2 fallback share one budget


def test_a_qft_design_and_a_step_2_fallback_cannot_be_asked_for_together(policy=None):
    """HANDOFF 2 from integration wave 1, settled in wave 2 (lane XF). WT's sampled Step 2 B3 fallback
    (`export_v2/sampled_fallback.py`) merges without textual conflict -- it adds only new files -- but it
    overlaps this chain SEMANTICALLY, because both re-divide the same run-wide synthesis pool
    `sto_currency.ROTATION_FRACTION * delta` (measured: ROTATION_FRACTION = 0.5, so the pool is 1/200 at
    delta 1e-2). Establishing the relationship from the code, rather than settling it by merge order:

    1. WT DOES NOT TAKE ITS OWN SHARE. `sampled_fallback.py` contains no reference to `sto_currency`,
       `ROTATION_FRACTION`, `rotation_eps` or `ts_per_rotation`. It receives `eps` -- "the per-site
       half-diamond" -- as a PARAMETER from its caller, and its `budget_seconds` is a wall-clock budget,
       not an error budget. It also has no production caller anywhere in the tree: `cost_policy_fallback`
       and `price_step2_budgeted` are referenced only by `test_sampled_fallback.py`. So there is no live
       composition today, and WT cannot double-spend on its own.

    2. BUT THE POOL HAS NO SLACK, so a second spender WOULD double-spend. The overlay partitions the pool
       EXACTLY over the whole rotation census: the catalytic arm takes `deltaArm = pool * R_cat / R_total`
       and the survivors keep the engine's unchanged eps, which satisfies
       `R_synth * (pool / R_total) + deltaArm == pool` (measured on the real engine at delta 1e-2:
       R_total 3802591380 = R_cat 350171640 + R_synth 3452419740, and the identity holds to a residual of
       -8.7e-19). Every rotation in the run is already allocated. Step 2's phase-product rotations are part
       of that same census, so pricing them a SECOND time against their own per-site eps would charge the
       same budget twice and hand the submission free error -- the same defect the catalytic arm's own
       docstring refuses ("would spend the catalysed rotations' slices twice ... and would hand catalysis
       free error").

    3. SO THE COMBINATION IS REFUSED, and already was, by name, at three independent places. This test pins
       all three together with the reason, so none can be relaxed without confronting the arithmetic above.
       It is the same call lane WY made for readout + design, for the same reason: two methods that each
       re-divide one nonlinear budget cannot be composed by picking a precedence.

    EXTENDED BY LANE XL, which ported the Tier 3b Step 4 adaptive and controlled Steps 1/5 sampled costings
    into `step4_sampled_fallback.py` and `controlled_sampled_fallback.py`. Both draw on this same pool -- the
    research plans they came from declare `error_pool` 1/200, which IS the engine's pool at delta 1e-2 -- so
    the family below is now three pricers, not one, and the fourth clause covers all four phase-product
    steps rather than two. The Tier 3b arithmetic, the per-scope partition rule and the refusal of every
    double-spending combination are in `test_tier3b_sampled_costing.py`; this test keeps the chain-side
    statement: nothing in the pricing chain composes with any of them.
    """
    import ast
    import re
    from pathlib import Path as _Path

    def _imports(text):
        """Every module name a file actually imports, including deferred imports inside functions.

        Parsed rather than grepped (lane XL): the substring form of this clause could not tell a CALL from a
        MENTION, and it fired on `qft_overlay`'s own docstring once that docstring started explaining which
        module prices each out-of-scope selector. Naming the refusal is the opposite of composing with it."""
        found = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                found |= {alias.name.rsplit(".", 1)[-1] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    found.add(node.module.rsplit(".", 1)[-1])
                found |= {alias.name for alias in node.names}
        return found

    # 1. No sampled pricer reads the currency, and nothing in production calls one.
    #    `rotation_pool.py` is the one module allowed to NAME the pool: it is not a pricer, it imports
    #    nothing from the engine and derives no allowance -- it only checks that the caller's own terms
    #    partition. Lane XL's `test_no_sampled_pricer_derives_its_own_allowance_from_the_engine_currency`
    #    pins that distinction.
    SAMPLED = ("sampled_fallback.py", "step4_sampled_fallback.py", "controlled_sampled_fallback.py")
    for name in SAMPLED:
        source = _Path(qft_overlay.__file__).parent.joinpath(name).read_text()
        for token in ("sto_currency", "ROTATION_FRACTION", "rotation_eps", "ts_per_rotation"):
            assert token not in source, \
                f"{name} now reads {token}: the budgets may no longer be independent"
    production = [p for p in _Path(qft_overlay.__file__).parent.glob("*.py")
                  if p.name not in (*SAMPLED, "rotation_pool.py")]
    assert production, "the production module list cannot be empty"
    for path in production:
        called = _imports(path.read_text()) & {name[:-3] for name in SAMPLED}
        assert not called, \
            f"{path.name} now imports {sorted(called)}: the double-spend analysis above must be redone"
    # The sampled pricers may share one estimator between them -- there is one Maurer-Pontil implementation
    # in this repository, not three -- but none of them may reach back into the chain.
    for name in SAMPLED[1:]:
        imported = _imports(_Path(qft_overlay.__file__).parent.joinpath(name).read_text())
        assert "sampled_fallback" in imported, f"{name} no longer shares the one estimator"
        assert not imported & {"qft_overlay", "full_run_design", "pricing", "mapping", "request"}, \
            f"{name} now reaches into the pricing chain: it is no longer outside the publish path"

    # 2. The profile that selects a Step 2 fallback is refused by name, at the design step.
    with pytest.raises(ValueError) as profile_refusal:
        design_from(document("quotient-step2-fallback"))
    assert "step2 := fallback" in str(profile_refusal.value)

    # 3. An explicit design naming a phase-product step is refused, naming the step.
    for step in ("step1", "step2", "step4", "step5"):
        body = {"schema": "forshor.design-local/v1", "modmul": "reference",
                "qft": {"data": BANDED}, "phase_product": {step: {"backend": "fallback"}}}
        with pytest.raises(ValueError) as explicit_refusal:
            full_run_design.design_of({"schema": "forshor.full-run-selection/v1", "design": body})
        assert step in str(explicit_refusal.value)

    # 4. And the overlay refuses the selector fields outright, so neither path can reach a price.
    #    All four phase-product steps, as of lane XL: `step1` and `step5` were fail-closed before (the
    #    unknown-key check caught them) but were refused as unrecognised FAMILIES rather than as scopes
    #    needing their own qualification, and they now have one.
    assert qft_overlay.OUT_OF_SCOPE == ("step1", "step2", "step4", "step5")
    assert set(qft_overlay.OUT_OF_SCOPE) == set(full_run_design.PHASE_STEPS), \
        "the overlay's out-of-scope selectors and the explicit path's phase steps must be the same set"
    for field in qft_overlay.OUT_OF_SCOPE:
        with pytest.raises(qft_overlay.QftOverlayRefused, match=re.escape(field)):
            qft_overlay.parse_design({"data": BANDED, field: {"backend": "fallback"}})
