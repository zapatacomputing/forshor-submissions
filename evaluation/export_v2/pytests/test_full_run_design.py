"""The compiled full-run selection -> the engine's `design` argument.

Which production change each test pins is named in the test's docstring. The tests split in two:

* seam tests (no engine): they inject a fake pricer into `pricing.price_views` and record exactly what
  reached it. `fullalg.price_full` never takes `design=` -- see below.
* real-engine tests (marked slow): they price the real policy
  `pytests/data/real/1bc92ac6e1e82ba3.policy.json` through the vendored engine and assert the panels are
  byte-identical to 792464f. They never pass a design; what they prove is that the default path is untouched.

LANE WP UPDATE. This file was written expecting lane WM to add a `design=` parameter to the ENGINE's
`fullalg.price_full`. WM did not: `evaluation/engine` feeds both `engineRevision` and `dataFingerprint`, so
any edit there -- even a comment -- moves the epoch, and WM qualified the design axis as an out-of-engine
overlay (`qft_overlay`) instead. `price_full` therefore still has its 792464f signature and the design is
routed to the overlay by the hook at `pricing.py:52-54`. The seam test below pins that. The end-to-end chain
this file's two halves were building toward lives in `test_full_run_chain.py`.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, full_run_design, mapping, pricing, request  # noqa: E402

DATA = Path(__file__).parent / "data"

# The shape the verifier's compiled document actually uses, transcribed from the recorded fixtures: the
# recorded compiled export for each native profile carries, at the top level,
#   "submitted_full_run": {"schema": "forshor.full-run-selection/v1", "profile": "<label>"}
# and the sibling export for `explicit-reference` carries no such key at all. The audit and replay scripts
# that produced those records assert the same pair.
RECORDED = {
    "quotient-exact", "quotient-catalytic", "quotient-banded-data-catalytic-work",
    "quotient-fallback-data-catalytic-work", "quotient-step2-fallback", "split-step4-kicks",
    "split-step2-fallback-step4-kicks",
}


def selection(profile):
    return {"schema": "forshor.full-run-selection/v1", "profile": profile}


def explicit_banded(family="data", max_error="1e-6"):
    """An EXPLICIT reference-topology design. Since the modmul fix no profile converts to a design (all
    seven select a non-reference multiplier the overlay cannot honour), so the seam tests below -- which
    need an actual design to thread -- state themselves against the explicit path."""
    return {"schema": "forshor.full-run-selection/v1",
            "design": {"modmul": "reference",
                       "qft": {family: {"backend": "ross_selinger", "target": "approximate",
                                        "max_error": max_error}}}}


# --------------------------------------------------------------------------------------------------
# the compiled key and shape
# --------------------------------------------------------------------------------------------------

def test_the_module_reads_the_key_and_schema_the_compiled_document_uses():
    """Fails if full_run_design.KEY / SELECTION_SCHEMA drift from the recorded compiled exports."""
    assert full_run_design.KEY == "submitted_full_run"
    assert full_run_design.SELECTION_SCHEMA == "forshor.full-run-selection/v1"
    assert set(full_run_design.PROFILES) == RECORDED | {"reference"}


def test_a_document_without_the_key_has_no_selection_and_an_explicit_null_is_refused():
    """Fails if full_run_design.selection_of stops treating key PRESENCE (not its value) as the test, the rule
    the shipped intake gate uses (starter/pack/harness/policy_options.py:10)."""
    assert full_run_design.selection_of({"schema": "forshor.policy-submission-semiclassical/v1"}) is None
    assert full_run_design.selection_of({}) is None
    assert full_run_design.selection_of({"submitted_full_run": selection("quotient-exact")}) == selection("quotient-exact")
    with pytest.raises(ValueError, match="names no selection"):
        full_run_design.selection_of({"submitted_full_run": None})


# --------------------------------------------------------------------------------------------------
# the conversion
# --------------------------------------------------------------------------------------------------

def test_no_selection_and_the_explicit_reference_are_both_no_design_at_all():
    """Fails if full_run_design.design_of returns an all-defaults record ({}) for `reference` instead of None.
    THE EMPTY SELECTION IS NOT A DESIGN: `reference` means the selection is ABSENT, not an empty design."""
    assert full_run_design.design_of(None) is None
    got = full_run_design.design_of(selection("reference"))
    assert got is None and got != {}


def test_the_profile_transcription_still_reproduces_the_research_bridge():
    """Fails if full_run_design's tables stop reproducing the existing research<->production bridge -- the
    audit script's own EXACT / CAT / BAND constants and its case table.

    This asserts the TABLE, not design_of's answer: since the modmul fix, every one of these profiles is
    refused on its topology (see test_no_profile_is_priceable_because_the_overlay_cannot_honour_a_topology),
    so there is no converted mapping to compare. The transcription still has to be right, because it is what
    becomes priceable the moment a topology can be honoured."""
    exact = {"backend": "ross_selinger", "target": "exact"}
    catalytic = {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}
    banded = {"backend": "ross_selinger", "target": "approximate", "max_error": "25e-5"}
    assert full_run_design.QFT_ONLY["quotient-exact"] == {"data": exact, "work": exact}
    assert full_run_design.QFT_ONLY["quotient-catalytic"] == {"data": catalytic, "work": catalytic}
    assert full_run_design.QFT_ONLY["quotient-banded-data-catalytic-work"] == {"data": banded, "work": catalytic}


def test_every_profile_carries_the_multiplier_topology_the_audit_bridge_gives_it():
    """THE MODMUL REGRESSION (lane WS). The audit bridge builds every bundle through its quotient / split
    constructors, both of which emit `modmul` BESIDE `qft`, and the research mapping gives the same
    multiplier field for all seven. The topology drives both the T cost and the qubit base, so dropping it
    prices a quotientStream or splitCompare run on REFERENCE numbers -- a silent default.

    Fails if the conversion ever stops carrying `modmul`, which is exactly the defect this test was added
    for: before the fix, `design_of` returned a bare family mapping and the topology was lost."""
    assert full_run_design.PROFILE_TOPOLOGY == {
        "reference": None,
        "quotient-exact": "quotient_stream",
        "quotient-catalytic": "quotient_stream",
        "quotient-banded-data-catalytic-work": "quotient_stream",
        "quotient-fallback-data-catalytic-work": "quotient_stream",
        "quotient-step2-fallback": "quotient_stream",
        "split-step4-kicks": "split_compare",
        "split-step2-fallback-step4-kicks": "split_compare",
    }
    assert set(full_run_design.PROFILE_TOPOLOGY) == set(full_run_design.PROFILES)
    assert full_run_design.topology_of(selection("reference")) is None
    for profile in full_run_design.PROFILES:
        assert full_run_design.topology_of(selection(profile)) == full_run_design.PROFILE_TOPOLOGY[profile]
    # and the body handed to the reader carries it, rather than a bare family mapping
    for profile, body in full_run_design.PROFILE_DESIGN.items():
        assert body["modmul"] == full_run_design.PROFILE_TOPOLOGY[profile]
        assert body["qft"] == full_run_design.QFT_ONLY[profile]


def test_no_profile_is_priceable_because_the_overlay_cannot_honour_a_topology():
    """THE RELEASE QUESTION, pinned. All seven non-reference profiles select a non-reference multiplier, and
    `qft_overlay` prices the QFT band only -- it has no topology parameter and `parse_design` refuses a
    `modmul` key outright. So no profile converts to a design today and NONE of them can produce a price
    different from the default. `reference` alone resolves, to None.

    Fails if a profile ever starts converting again while the overlay still cannot honour its topology --
    which would mean a quotientStream run was being priced on reference-topology numbers."""
    assert full_run_design.design_of(selection("reference")) is None
    for profile in full_run_design.PROFILES:
        if profile == "reference":
            continue
        with pytest.raises(ValueError) as caught:
            full_run_design.design_of(selection(profile))
        assert profile in str(caught.value), profile


def test_a_converted_design_is_a_fresh_mapping_the_caller_cannot_poison():
    """Fails if the reader hands out the module's own table (a caller mutating it would change every later
    price). Stated against the explicit path, the only path that converts to a mapping today."""
    body = {"modmul": "reference", "qft": {"data": {"backend": "ross_selinger", "target": "approximate",
                                                    "max_error": "1e-6"}}}
    sel = {"schema": "forshor.full-run-selection/v1", "design": body}
    first = full_run_design.design_of(sel)
    first["data"]["max_error"] = "9e-1"
    assert full_run_design.design_of(sel) == {"data": {"backend": "ross_selinger", "target": "approximate",
                                                      "max_error": "1e-6"}}
    # and the module's own profile tables survive a conversion attempt
    assert full_run_design.QFT_ONLY["quotient-exact"] == {
        "data": {"backend": "ross_selinger", "target": "exact"},
        "work": {"backend": "ross_selinger", "target": "exact"}}


def test_every_transcribed_design_satisfies_the_engine_contract():
    """Fails if a typo in full_run_design's tables emits a backend, target, max_error or family the contract
    does not define -- the engine would RAISE on it, and a silent default is never acceptable."""
    for profile in full_run_design.PROFILES:
        design = full_run_design.QFT_ONLY.get(profile)
        if design is None:
            continue
        assert set(design) <= {"data", "work", "scratch", "exponent"}
        assert "exponent" not in design, "semiclassical readout replaces qft.exponent in all seven bundles"
        for choice in design.values():
            assert choice["backend"] in ("ross_selinger", "catalytic")
            if choice["target"] == "exact":
                assert set(choice) == {"backend", "target"}
            else:
                assert choice["target"] == "approximate" and set(choice) == {"backend", "target", "max_error"}
                units, _, scale = choice["max_error"].partition("e-")
                assert units.isdigit() and scale.isdigit()


@pytest.mark.parametrize("profile, names", [
    ("quotient-fallback-data-catalytic-work", "fallback rotation backend"),
    ("quotient-step2-fallback", "Step 2"),
    ("split-step4-kicks", "Step 4"),
    ("split-step2-fallback-step4-kicks", "Step 2"),
])
def test_a_selection_beyond_the_qft_families_is_refused_and_names_itself(profile, names):
    """Fails if full_run_design.design_of prices a selection the design contract cannot express -- the release
    rule is that unsupported scopes are reported, not priced as defaults."""
    with pytest.raises(ValueError) as caught:
        full_run_design.design_of(selection(profile))
    assert profile in str(caught.value) and names in str(caught.value)


@pytest.mark.parametrize("bad", [
    selection("quotient-reference"), selection("Quotient-Exact"), selection(""),
    {"schema": "forshor.full-run-selection/v2", "profile": "quotient-exact"},
    {"profile": "quotient-exact"}, {"schema": "forshor.full-run-selection/v1"},
    {"schema": "forshor.full-run-selection/v1", "profile": None},
    "quotient-exact", ["quotient-exact"], 7,
])
def test_an_unknown_or_malformed_selection_is_refused(bad):
    """Fails if full_run_design.design_of falls back to a default for anything it does not recognise."""
    with pytest.raises(ValueError, match="full-run selection"):
        full_run_design.design_of(bad)


# --------------------------------------------------------------------------------------------------
# the pricing call (seam: an injected pricer, because the engine has no `design=` until lane WM lands)
# --------------------------------------------------------------------------------------------------

class FakePolicy:
    mode = "PhaseProduct"
    bands = ()

    def with_widths(self, _):
        return self


@pytest.fixture
def seam(monkeypatch):
    """`price_views` with the engine replaced: records exactly what reached the full-run pricer."""
    calls = []

    class Fullalg:
        @staticmethod
        def price_full(P, n, delta, **kw):
            calls.append({"n": n, "delta": delta, **kw})
            return {"usage": [], "tables_used": []}

    class Tables:
        PAIR = "PhaseProduct"
        TRIPLE = "PhaseTripleProduct"

    class PolicyView:
        @staticmethod
        def view(P, n, delta, title):
            return {"levels": [], "bands": []}

        @staticmethod
        def table_view(t):
            return t

    class Eng:
        fullalg, tables, policy_view = Fullalg, Tables, PolicyView

    monkeypatch.setattr(pricing.engine, "load", lambda: Eng)
    return calls


def test_no_selection_passes_no_design_argument_at_all(seam):
    """Fails if pricing.price_views starts sending `design=None` to price_full: the vendored engine has no such
    parameter until lane WM lands, and the default path must call it exactly as 792464f did."""
    pricing.price_views(FakePolicy(), "t", "1e-2", ["full"])
    pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=None)
    assert seam == [{"n": pricing.N, "delta": 0.01, "detail": True}] * 2
    assert all("design" not in call for call in seam)


def test_a_design_is_threaded_to_the_qft_overlay_and_never_to_the_engines_pricer(monkeypatch, seam):
    """Fails if pricing.price_views drops the design on the floor, or sends it to `price_full`.

    RESOLVED INTEGRATION CONFLICT (lanes WP and WR, reconciled in wave 2). Two things were settled here.

    (1) WO wrote this test against a superseded brief in which lane WM would add `design=` to
    `fullalg.price_full`. WM instead qualified the axis as an out-of-engine overlay, because
    `dataFingerprint` hashes the pricer's SOURCE BYTES and `engineRevision` is a git log over
    `evaluation/engine`, so ANY edit there -- even a comment -- starts a new epoch. `git diff <base> --
    evaluation/engine` is empty on the merged branch and `price_full` still has the 792464f signature
    `(P, n, delta, detail, rule, negation)`, so the engine-side call raised `TypeError: price_full() got
    an unexpected keyword argument 'design'`. The design reaches `qft_overlay.project(P, full, design)`
    instead -- the hook mirroring `measured` -- and is reported as `view["full"]["qft"]`.

    (2) WP drove this through the PROFILE label 'quotient-banded-data-catalytic-work'. WR's topology gate
    now refuses every profile label, because all seven non-reference profiles select a non-reference
    multiplier topology the overlay cannot honour, so `design_of` on a profile raises instead of
    returning a design. The threading is therefore exercised through the EXPLICIT per-family design on
    the reference topology, which is what this release ships. What is being tested -- that a design
    reaches the overlay and never the engine's pricer -- is unchanged; only the way a design is obtained
    moved onto the path that still prices."""
    seen = []
    monkeypatch.setattr(pricing, "_all_tables", lambda eng, P: [])
    monkeypatch.setitem(sys.modules, "export_v2.qft_overlay", type(sys)("export_v2.qft_overlay"))
    sys.modules["export_v2.qft_overlay"].project = lambda P, full, design: seen.append(design) or {"ok": True}
    design = full_run_design.design_of(explicit_banded("work"))
    views = pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], design=design)
    assert seam == [{"n": pricing.N, "delta": 0.01, "detail": True}]
    assert all("design" not in call for call in seam)
    assert seen == [design]
    assert views["full"]["full"]["qft"] == {"ok": True}


def test_a_design_is_refused_at_every_delta_but_1e_2(seam):
    """Fails if pricing.price_views prices a design outside the only precision the QFT surface is qualified
    at: delta = 1e-2 only."""
    design = full_run_design.design_of(explicit_banded())
    for delta in ("3e-4", "1e-4", "1e-6"):
        with pytest.raises(ValueError, match="delta 1e-2"):
            pricing.price_views(FakePolicy(), "t", delta, ["full"], design=design)
    assert seam == []


def test_a_design_never_reaches_a_board_that_is_not_the_full_run(seam):
    """Fails if pricing.price_views lets a full-run design through on the pp board, where price_full is not called
    at all and the design would be silently ignored."""
    with pytest.raises(ValueError, match="full order-finding board"):
        pricing.price_views(FakePolicy(), "t", "1e-2", ["pp"], design=full_run_design.design_of(explicit_banded()))
    assert seam == []


def test_a_refused_selection_prices_nothing_and_builds_no_request(monkeypatch):
    """Fails if request._build_request converts the compiled selection after the evidence lookup or after the
    pricing loop: a refusal must not price a board or emit any part of a request."""
    monkeypatch.setattr(request.engine, "load", lambda: pytest.fail("a refused selection must not load the engine"))
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selection must not be priced"))
    args = dict(model=None, store=None, head_sha="a" * 40, pull_number=1, github_login="t", run_id="1",
                submitted_at="x", evaluated_at="y", archive_commit="b" * 40, accepted_ids=[], evaluation_key="e",
                base_generation="1", number=1)
    with pytest.raises(ValueError, match="quotient-step2-fallback"):
        request._build_request({}, full_run=selection("quotient-step2-fallback"), **args)


# --------------------------------------------------------------------------------------------------
# byte identity against 792464f (the real engine)
# --------------------------------------------------------------------------------------------------

# Recorded at 792464f with the HEAD signature `price_full(P, N, delta, detail=True)`, for the real policy
# pytests/data/real/1bc92ac6e1e82ba3.policy.json at delta 1e-2.
#
# PIN RESTORED (integration wave 2, lane XF). Lane WR replaced the `full` value below with
# c773f5022e63194d3f95eba8edcd9cf407f846648fd8b03bae98ce53c180b55e, as a "CORRECTED PIN", on the finding
# that 998ad142... "does NOT reproduce" at 792464f. That finding does not hold, and the original value is
# restored here. WR's own capture command (kept below) was re-run, unmodified, in four checkouts:
#
#     submissions-INT @ 792464f  -> full 998ad142...   (the base itself)
#     submissions-XE  @ f56bbc0  -> full 998ad142...   (integration wave 1)
#     submissions-WR  @ cac3ac9  -> full 998ad142...   (WR'S OWN WORKTREE)
#     submissions-XF  (this tree, WP+WR merged) -> full 998ad142...
#
# It reproduces everywhere, including in the worktree that declared it irreproducible, and c773f502...
# reproduces nowhere. The consequence of the re-pin was that these two tests, and the four in
# `test_full_run_design_explicit.py` that import this constant, shipped RED on `prepush/WR-orthogonal-converter`
# itself (measured: 6 failed, 82 passed with --runslow at cac3ac9). They are `@pytest.mark.slow`, so WR's
# fast-suite count never collected them and the breakage was invisible to it.
#
# 998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3 is also the release's HARD GATE for the
# no-design request, quoted verbatim in the wave 2 brief alongside the `pp` value, so the re-pin would have
# failed the gate it was meant to protect. The `pp` value was never in dispute and is unchanged.
# The variants WR ruled out are retained as useful context: `boards=["full"]` alone gives the same value,
# and `measured=True` gives 2a96be24908dbf8ceeec34abd904d0f2ea80e00eb8744694de267dff5d8cbb2d, a third value.
# Capture command, from the repo root of a 792464f checkout:
#   python -c "import json,sys;from pathlib import Path;sys.path.insert(0,'evaluation');\
#   from export_v2 import canon,engine,mapping,pricing;D=Path('evaluation/export_v2/pytests/data');\
#   m=json.loads((D/'golden.json').read_text())['model'];\
#   p=engine.load().tables.Policy.from_json(json.loads((D/'real/1bc92ac6e1e82ba3.policy.json').read_text()));\
#   v=pricing.price_views(p,'Submission aaaaaaaaaaaa','1e-2',['pp','full']);\
#   print({b:canon.sha256_hex(mapping.panel_of(v[b],epoch=canon.epoch_of(m),row_id='a'*40,board=b,delta='1e-2',\
#   verification={'stoMirror':None,'forshorPolicy':None})) for b in ('pp','full')})"
HEAD_PANELS = {"full": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
               "pp": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f"}


@pytest.mark.slow
@pytest.mark.parametrize("document", [
    {"schema": "forshor.policy-submission-semiclassical/v1"},
    {"schema": "forshor.policy-submission-semiclassical/v1",
     "submitted_full_run": {"schema": "forshor.full-run-selection/v1", "profile": "reference"}},
], ids=["no-selection", "explicit-reference"])
def test_a_real_policy_with_no_selection_or_the_explicit_reference_prices_byte_identically_to_792464f(document):
    """THE non-negotiable test. Fails if the design threading perturbs the default price by one byte -- for
    instance if price_views started sending `design={}` for the explicit reference, or if design_of turned
    `reference` into an all-defaults record."""
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
