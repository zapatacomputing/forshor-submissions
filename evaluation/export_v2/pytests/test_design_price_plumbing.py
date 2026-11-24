"""A design-bearing document reaches a PUBLISHED price: the four-delta request, and the ranked projection.

Lane WP joined the QFT chain and proved, by execution, that it still publishes nothing. Two STRUCTURAL
obstacles stood between a compiled design and a price on a board, and this module pins both resolutions.

OBSTACLE 1 -- THE FOUR-DELTA CONFLICT. `pricing.price_views` refuses a design at any delta but 1e-2, and
`request._build_request` prices ALL FOUR deltas of `mapping.DELTA_KEYS`, so a publish request carrying a design
raised partway through its own pricing loop and no request was ever completed. The release rule is that only
delta = 1e-2 is in scope for the design surface, and reading the code
confirms the other three deltas are legitimately DESIGN-FREE PANELS OF THE SAME POLICY, not an error: they are
the same `price_views` call on the same policy at a different precision, they carry no `qft` block, and every
panel ever published is one of them. So the request now scopes the design to the 1e-2 panel
(`mapping.DESIGN_DELTA`) and prices the other three exactly as it does today -- with no `design` keyword at
all, which is the same call 792464f made, so byte identity is by construction and not by careful arithmetic.

OBSTACLE 2 -- THE PROJECTION WAS NOT THE PRICE. `mapping.row_of` read `full.get("readout", full)`, so the `qft`
projection was carried as evidence BESIDE the engine's price and never became the ranked number. Lane WP left
that for an owner decision; the owner has ruled to proceed with Tier 2, so `mapping.ranked_price_of` now takes
the projection's T as the ranked T for the full board, following the `readout` precedent exactly.

WHICH TESTS USE A REAL PRICE AND WHICH USE A SEAM
--------------------------------------------------------------------------------------------------------
Until lane WX makes the catalytic arm priceable, the ONLY design that survives both gates is `quotient-exact`,
which is by construction the engine's own price (`target: "exact"` means "leave this family's QFT exactly as
the engine priced it"). So a real design cannot yet move a number, and a test that only ever saw the real
design could not tell "the projection is ranked" from "nothing is ranked".

* REAL PRICE (`@pytest.mark.slow`, real engine, real `quotient-exact` design): that a four-delta publish
  request COMPLETES at all; that the 1e-2 full row's T is the projection's T; that the three non-1e-2 panels
  and rows are byte-identical to 792464f; that the no-design path is byte-identical to 792464f.
* SEAM (`qft_overlay.project` monkeypatched to return a DIFFERENTIATED T): that the ranked T actually MOVES
  with the projection and the engine's own panel numbers do not, which is the statement that cannot be made
  with an identity price. `_differentiated` is the single seam, and it returns the real projection with one
  field changed so the shape stays the production shape.

Every sha256 below was measured on the merge base 792464f itself, not inherited: `HEAD_PANELS`/`HEAD_ROWS` at the
`price_views` level (the form lane WO recorded) and `REQUEST_*` at the whole-request level.
"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_v2 import canon, epoch as epoch_module, full_run_design, mapping, pricing, qft_overlay, request  # noqa: E402

DATA = Path(__file__).parent / "data"
POLICY = DATA / "real/1bc92ac6e1e82ba3.policy.json"

HEAD_SHA = "a" * 40

#: `canon.sha256_hex(mapping.panel_of(pricing.price_views(...)[board], verification={"stoMirror": None,
#: "forshorPolicy": None}))` for `POLICY` at 792464f, every delta and both boards. The 1e-2 pair is exactly the
#: number lane WO recorded and lane WP re-verified; the other six were measured the same way on 792464f itself.
HEAD_PANELS = {
    "pp-1e-2": "c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f",
    "full-1e-2": "998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3",
    "pp-3e-4": "5af4557b3be87db2251274b13a9f85bd1b03ecf2191c8dc108eeebce05c764fd",
    "full-3e-4": "016d176056e82d2810b01c7e761440a9caa27887fbd5118e1dea863b07a6ba8a",
    "pp-1e-4": "048f46cb0b7e411dff742b069c783f43e7e78d3e05c689ddbd2651ffc2d38b2d",
    "full-1e-4": "0604b6b2d862386676ef2363d9b26a1441f40bd130a1023191011d392a8af14c",
    "pp-1e-6": "f972809b1fd37cf62cb401b54013d01879f91fa7dd404495b985d83a4dc1ce76",
    "full-1e-6": "55f99bd4f948a0c2d3f3d2857c4ca7b8a2ef29b14972b46d2c15e2d5341df832",
}

#: The same, for the LEDGER ROW -- the artifact obstacle 2 changes, and the numbers ranks are derived from
#: downstream (see `epoch.py`). Measured on 792464f with the row arguments `_row` builds below.
HEAD_ROWS = {
    "pp-1e-2": "8ce88ac2dd805194dc6c1b754105cb018bd3418520cdff3d053981568caf6e45",
    "full-1e-2": "5f4dbd9c3499f72593d578880f052dfcfc42feef6976f21385e9f9e1d44d44e9",
    "pp-3e-4": "63f5312d2fd1182c273e12baec93d9d35888eaa60b7276eed3117c2c9d482b3c",
    "full-3e-4": "95c11507a2565ed120be9a72208135c5309311f3d01ffd4caf36802b4fb143fd",
    "pp-1e-4": "bf640f6950da8cf0effb987666917a8d323b58294b434a6d158ef236064ebc4a",
    "full-1e-4": "49126d519e2150123d3f2c1e15a01f4b411159811ba13a6e75cb6ccd6aed7e23",
    "pp-1e-6": "eb0a017991fc48c573e9ad16a78c945f38bb24cc46f94dd4fd42e3d16b7b3ab3",
    "full-1e-6": "a8c6eca606d821fbf2d1287f04e9c4722a9c8fa88498a742ac2c13c2c2c39dcd",
}

#: THE REQUEST-LEVEL baseline: `_build_request(POLICY, **_request_args())` on 792464f, where `verification`
#: carries the rehearsal mirror rather than None, so the bytes differ from `HEAD_PANELS` by that evidence and
#: by nothing else. `REQUEST_SHA256` is the whole `forshor.publish/v2` object -- the end-to-end statement.
REQUEST_SHA256 = "6641a96743ab92aedea0f65d9b883fa745985dfc5931e9e4c4a22967ce86896d"
REQUEST_PANELS = {
    "pp-1e-2": "edac74a6569d5ebc9ad21488ca1d61aa4d9e331b5214f1c4eab78efba5bcbcc8",
    "full-1e-2": "2fd5f75bdb294307c8adfb11f51561ca13e7945dbf524a8a72e41eae1e32bde1",
    "pp-3e-4": "55f7486fd551be8c1942004dc2490a674103cb2bf08f0403356b8942eb5a3a21",
    "full-3e-4": "76a96fea03012805bb5b97f91a774fd09debfc3f995d8fcc62bef0b69a6cef11",
    "pp-1e-4": "47e2a19fbf075c1d41aef96cc35e86ff0c3a9090887375a11054fc572b5697e9",
    "full-1e-4": "511ba7f7fb9d83f2157f12b6459de3bcf327da68a380039c50ef96bab1c41f8a",
    "pp-1e-6": "768129fdc1ca84918e774078ea6bc5ee1ab66c64fedff67f78c3a673fb0515be",
    "full-1e-6": "9252c58221493d30475e124f573406a45640842e2189ecb1fa09192a69445d4b",
}
REQUEST_ROWS = {
    "pp-1e-2": "8da326563205ebc3490ed166f2b9565c15d36e77019a5b688bc27459b593a04a",
    "full-1e-2": "591ecb71fd498433c9def90b886a60dbaea1b773953dbbc031a3c3484051f416",
    "pp-3e-4": "3bca3b31edc23f3dc03ee963831cef6b7cf772a286e7bf45392d217102760ba0",
    "full-3e-4": "b6b6dab9bbea8fdd27c354d2a66f1966840c5a08fe3559b4ff85d1d93e2242df",
    "pp-1e-4": "880b5452583a3b7e915b4bd5298bae35191ec861a98b2a737f9cb5c4f17a5831",
    "full-1e-4": "fb05d3f94b777a9303c98593ede1a614f1757821b20fe26c63923fa746594c07",
    "pp-1e-6": "59cfa10ca8b9dd9061ddb21fddd441f205f240dc8985c45af6b86f3ba6ef6d99",
    "full-1e-6": "7748513e3fc7cc81119230cf96ca107fcbb1c64bf84945c8ee1b2b7855c91f94",
}

MEASURED_SCHEMA = "forshor.policy-submission-semiclassical/v1"

#: RE-POINTED (integration wave 2, lane XF). This file was written when `quotient-exact` was the one
#: selection that converted, and its design was the identity by construction. Two things moved underneath it:
#:
#:   * lane WR's TOPOLOGY GATE refuses every profile LABEL (all seven select a non-reference `modmul`), so
#:     `selection("quotient-exact")` no longer converts at all; and
#:   * "exact means untouched" now collapses an all-exact EXPLICIT design to None, so no converting design
#:     is the identity any more -- the identity-and-converting combination was only ever reachable through
#:     the label this release refuses.
#:
#: So the design driven here is an EXPLICIT reference-topology one naming the catalytic backend. That is
#: exactly the substitution this file's own `_differentiated` seam anticipated ("lane WX is making the
#: catalytic arm priceable; when it lands this seam can be replaced by a real differentiated design"), so the
#: plumbing is now exercised against a design that REALLY moves the ranked price rather than a scaled fake.
PRICED_DESIGN_QFT = {"data": {"backend": "catalytic", "target": "approximate", "max_error": "25e-5"}}


def document(profile=None):
    doc = {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical"}
    if profile is not None:
        doc["submitted_full_run"] = {"schema": "forshor.full-run-selection/v1", "profile": profile}
    return doc


def explicit_document(qft=None, modmul="reference"):
    """A compiled document carrying an EXPLICIT per-family design body on the reference topology."""
    return {"schema": MEASURED_SCHEMA, "submitted_readout": "semiclassical",
            "submitted_full_run": {"schema": "forshor.full-run-selection/v1",
                                   "design": {"schema": "forshor.design-local/v1", "modmul": modmul,
                                              "qft": PRICED_DESIGN_QFT if qft is None else qft}}}


def selection(profile):
    return full_run_design.selection_of(document(profile))


def priced_selection(qft=None):
    """The selection this file drives: an explicit reference-topology design that converts AND prices."""
    return full_run_design.selection_of(explicit_document(qft))


@pytest.fixture(scope="module")
def policy_json():
    return json.loads(POLICY.read_text())


@pytest.fixture(scope="module")
def policy(policy_json):
    from export_v2 import engine
    return engine.load().tables.Policy.from_json(policy_json)


@pytest.fixture(scope="module")
def epoch_id():
    return canon.epoch_of(json.loads((DATA / "golden.json").read_text())["model"])


def _panel(view, epoch_id, board, delta):
    return mapping.panel_of(view, epoch=epoch_id, row_id=HEAD_SHA, board=board, delta=delta,
                            verification={"stoMirror": None, "forshorPolicy": None})


def _row(panel, epoch_id, board, delta):
    return mapping.row_of(panel, origin="submission", name="t_sub1", created_at="2026-11-23T12:00:00Z",
                          pull_request_url="u", submission_id=HEAD_SHA,
                          detail=mapping.panel_path(epoch_id, HEAD_SHA, board, delta))


# --------------------------------------------------------------- the request, built against the real engine


def _request_args(policy_json):
    """`_build_request` arguments for a local rehearsal. Test double only: no real STO evidence, nothing
    publishes. Mirrors `test_readout_overlay.local_request`."""
    model = epoch_module.model_of()
    mirror = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
              "claim": "policy-algebra-and-exact-widths",
              "ran": {"lean": model["lean"], "stoCommit": model["sto"]["commit"], "forshorPin": model["forshor"]["pin"]}}
    return dict(model=model, store=SimpleNamespace(stomirror=lambda *a: (mirror, None)), head_sha=HEAD_SHA,
                pull_number=42, github_login="designfixture", run_id="9001", submitted_at="2026-11-23T12:00:00Z",
                evaluated_at="2026-11-23T12:01:00Z", archive_commit="c" * 40, accepted_ids=[HEAD_SHA],
                evaluation_key="d" * 64, base_generation="1", number=1)


def _by_key(req):
    return {f"{item['board']}-{item['delta']}": item for item in req["priced"]}


@pytest.fixture(scope="module")
def no_design_request(policy_json):
    return request._build_request(policy_json, **_request_args(policy_json))


@pytest.fixture(scope="module")
def design_request(policy_json):
    return request._build_request(policy_json, full_run=priced_selection(), **_request_args(policy_json))


@pytest.mark.slow
def test_a_design_bearing_document_completes_a_four_delta_publish_request(design_request):
    """OBSTACLE 1, THE DELIVERABLE. Before this lane this call raised `ValueError: a full-run design is priced
    at delta 1e-2 only, not 3e-4` from inside `_build_request`'s own pricing loop, so a design-bearing document
    could not produce a request at all. Fails if the design is passed at a delta it is not qualified at, or if
    the 1e-2 panel stops carrying the projection.

    REAL PRICE: the real `quotient-exact` design through the real engine."""
    assert len(design_request["priced"]) == 8
    priced = _by_key(design_request)
    assert sorted(priced) == sorted(f"{b}-{d}" for d in mapping.DELTA_KEYS for b in ("pp", "full"))
    assert design_request["schema"] == "forshor.publish/v2"

    for delta in mapping.DELTA_KEYS:
        full_run = priced[f"full-{delta}"]["panel"]["fullRun"]
        assert ("qft" in full_run) is (delta == mapping.DESIGN_DELTA), delta
        assert priced[f"pp-{delta}"]["panel"]["fullRun"] is None
    assert mapping.DESIGN_DELTA == "1e-2"


@pytest.mark.slow
def test_the_three_non_design_deltas_are_byte_identical_to_792464f_with_and_without_a_design(
        design_request, no_design_request):
    """OBSTACLE 1's non-negotiable. The other three deltas are design-free panels of the same policy, so a
    design present anywhere in the request must not move one byte of them -- panel OR row. Fails if the design
    is threaded into every delta, or if the scoping is done by filtering the result instead of by not asking.

    REAL PRICE: both requests are the real engine on the real policy."""
    with_design, without = _by_key(design_request), _by_key(no_design_request)
    for delta in mapping.DELTA_KEYS:
        if delta == mapping.DESIGN_DELTA:
            continue
        for board in ("pp", "full"):
            key = f"{board}-{delta}"
            assert canon.sha256_hex(with_design[key]["panel"]) == REQUEST_PANELS[key], key
            assert canon.sha256_hex(with_design[key]["row"]) == REQUEST_ROWS[key], key
            assert with_design[key]["panel"] == without[key]["panel"], key
            assert with_design[key]["row"] == without[key]["row"], key


@pytest.mark.slow
def test_a_request_without_a_design_is_byte_identical_to_792464f_end_to_end(no_design_request):
    """The whole no-design request, every delta and both boards, against the hashes measured on 792464f. This
    is the statement that neither obstacle's fix perturbs a published price: `ranked_price_of` with neither
    overlay key present must return the engine's own panel, and `_build_request` must call `price_views`
    exactly as it did. The last assertion is the whole `forshor.publish/v2` object, so no byte anywhere in a
    no-design request -- panel, row, verification, submission block -- can move unnoticed."""
    priced = _by_key(no_design_request)
    assert set(priced) == set(REQUEST_PANELS)
    for key, item in priced.items():
        assert canon.sha256_hex(item["panel"]) == REQUEST_PANELS[key], key
        assert canon.sha256_hex(item["row"]) == REQUEST_ROWS[key], key
        assert "qft" not in (item["panel"]["fullRun"] or {}), key
        assert "readout" not in (item["panel"]["fullRun"] or {}), key
    assert canon.sha256_hex(no_design_request) == REQUEST_SHA256


@pytest.mark.slow
def test_the_recorded_panel_hashes_still_price_at_792464f(policy, epoch_id):
    """Lane WP's and lane WO's own measurement, re-verified on this branch and extended to all four deltas:
    `price_views` with no design at all, panelled with no mirror evidence. The 1e-2 pair is the recorded
    998ad142... (full) and c83bf7a5... (pp). Fails if anything in this lane reaches the pricer itself."""
    for delta in mapping.DELTA_KEYS:
        views = pricing.price_views(policy, "Submission aaaaaaaaaaaa", delta, ["pp", "full"])
        for board in ("pp", "full"):
            key = f"{board}-{delta}"
            assert canon.sha256_hex(_panel(views[board], epoch_id, board, delta)) == HEAD_PANELS[key], key
            assert canon.sha256_hex(_row(_panel(views[board], epoch_id, board, delta), epoch_id, board, delta)) \
                == HEAD_ROWS[key], key


@pytest.mark.slow
def test_the_1e_2_full_row_is_ranked_on_the_qft_projection(design_request):
    """OBSTACLE 2, at the real price. The 1e-2 full row's T and qubits are the PROJECTION's, not the panel's
    own engine numbers.

    RE-POINTED (integration wave 2, lane XF). This asserted `identity is True` and that the row hashed to the
    no-design row, because the only design that converted was `quotient-exact` -- the engine's own number by
    construction -- so the test could state the WIRING but not that it MOVES. The design driven here is now a
    real catalytic one (lane WX), so the wiring is exercised against a price that genuinely differs and the
    assertions are strictly stronger: the row does not merely read the projection, it reads a DIFFERENT
    number from the panel's. The no-design row remains pinned byte-for-byte, at every delta and on both
    boards, by `test_a_request_without_a_design_is_byte_identical_to_792464f_end_to_end`.

    This also pins the QUBIT HOOK. `ranked_price_of` overlays each key of `SHOWN` the projection offers, and
    the catalytic arm charges a shared catalyst register that is live for the whole run, so the ranked qubits
    must be the projection's -- the engine's frozen total is no longer an upper bound once a register is
    added to it. Fails if the arm stops naming `qubits`, which is what made charge 2 reach the ledger."""
    item = _by_key(design_request)[f"full-{mapping.DESIGN_DELTA}"]
    full_run, row = item["panel"]["fullRun"], item["row"]
    projection = full_run["qft"]

    assert projection["identity"] is False, "a catalytic design is not the engine's own price"
    assert row["T"] == projection["T"], "the ranked T is the projection's"
    assert row["T"] != full_run["T"], "and it is NOT the panel's own engine price"

    # charge 2 reaches the row: the catalyst register is in the ranked qubit count. The ranked field is a
    # PEAK (`full_run_of:130` sets the panel's `qubits` from `qubits_peak`), so the projection offers the
    # peak-plus-catalyst and the layout total stays alongside it as `qubitsLayout`.
    assert projection["catalystQubits"] > 0
    assert row["qubits"] == projection["qubits"] == projection["qubitsPeak"]
    assert row["qubits"] == full_run["qubits"] + projection["catalystQubits"]
    assert projection["qubitsUpperBound"] >= row["qubits"], "the overlay's bound must cover the ranked qubits"

    assert canon.sha256_hex(item["panel"]) != REQUEST_PANELS[f"full-{mapping.DESIGN_DELTA}"], \
        "the panel gains the projection evidence block"
    assert canon.sha256_hex(row) != REQUEST_ROWS[f"full-{mapping.DESIGN_DELTA}"], \
        "a differentiated projection must NOT rank to the no-design row"


# --------------------------------------------------------------- the seam: a projection that really differs


def _differentiated(factor=0.5):
    """THE SEAM. The real projection with its T scaled, so the plumbing is exercised against a KNOWN
    displacement of the ranked price.

    Wave 2 note (lane XF): this seam was written because "the only design that prices today is
    `quotient-exact`, which is by construction the engine's own number". That is no longer so -- the design
    driven here is a real catalytic one that differentiates on its own (lane WX), which is the replacement
    this docstring anticipated. The seam is KEPT anyway, because it does something the real design cannot:
    it displaces T by an exactly known factor, so the test can assert the ranked price tracks the projection
    QUANTITATIVELY rather than merely differing from the engine's.

    Because the unscaled projection is no longer equal to the engine's own T, the pre-scaling value is
    recorded on the block; the assertions below are stated against that rather than against the engine's T.
    The shape otherwise stays the production shape: every other field is whatever `qft_overlay.project`
    really returned."""
    real = qft_overlay.project

    def fake(policy, coherent, design=None):
        projection = dict(real(policy, coherent, design))
        projection["seamUnscaledT"] = projection["T"]
        projection["T"] = projection["T"] * factor
        projection["seam"] = "differentiated-for-test"
        return projection

    return fake


@pytest.mark.slow
def test_a_differentiated_projection_moves_the_ranked_price_and_nothing_else(monkeypatch, policy_json):
    """OBSTACLE 2's real statement, through the seam. The 1e-2 full ROW tracks the projection; the 1e-2
    PANEL's own engine numbers do not move; the three other deltas do not move; the pp board does not move.

    Fails if `row_of` goes back to `full.get("readout", full)` (the row's T is then the panel's T, not the
    projection's), and fails if the ranked price is taken by replacing the panel rather than overlaying it."""
    monkeypatch.setattr(qft_overlay, "project", _differentiated())
    priced = _by_key(request._build_request(policy_json, full_run=priced_selection(),
                                            **_request_args(policy_json)))
    item = priced[f"full-{mapping.DESIGN_DELTA}"]
    full_run, row, projection = item["panel"]["fullRun"], item["row"], item["panel"]["fullRun"]["qft"]

    assert projection["seam"] == "differentiated-for-test"
    assert row["T"] == projection["T"], "the ranked T is the projection's"
    assert row["T"] != full_run["T"], "a differentiated projection must not rank at the engine's price"
    assert row["T"] == pytest.approx(projection["seamUnscaledT"] * 0.5), "the ranked T tracks the scaling"
    assert projection["seamUnscaledT"] != full_run["T"], \
        "the real catalytic projection already differs from the engine's own T, before the seam scales it"
    # The catalytic arm adds its shared register to the ranked peak (charge 2); the ladder is untouched.
    assert row["qubits"] == full_run["qubits"] + projection["catalystQubits"]
    assert row["ladderLabel"] == full_run["ladderLabel"]

    for key in REQUEST_PANELS:
        if key == f"full-{mapping.DESIGN_DELTA}":
            continue
        assert canon.sha256_hex(priced[key]["panel"]) == REQUEST_PANELS[key], key
        assert canon.sha256_hex(priced[key]["row"]) == REQUEST_ROWS[key], key


# --------------------------------------------------------------- ranked_price_of, unit, no engine


ENGINE_FULL = {"T": 11.0, "qubits": 7, "ladderLabel": "2048:8 -> 279:3 -> 35:leaf"}


def test_ranked_price_of_is_the_engine_panel_when_no_overlay_is_present():
    """The byte-identity hinge, as a unit: neither key present returns the panel object ITSELF, which is what
    `full.get("readout", full)` did, so no published row can move."""
    full = dict(ENGINE_FULL)
    assert mapping.ranked_price_of(full) is full


def test_ranked_price_of_follows_the_readout_precedent_untouched():
    """The measured route is unaffected: `readout` carries both T and qubits and replaces both outright."""
    readout = {"T": 3.0, "qubits": 2}
    assert mapping.ranked_price_of({**ENGINE_FULL, "readout": readout}) == readout


def test_ranked_price_of_takes_T_from_the_projection_and_qubits_from_the_engine():
    """The `qft` projection prices T ONLY -- `qft_overlay.project` returns `qubitsUpperBound` and a note saying
    it does not re-derive a qubit count -- so the ranked qubits stays the engine's own number, which the
    overlay certifies is a sound upper bound. Fails if the projection is substituted wholesale (the row would
    then have no `qubits` at all) or if T is left at the engine's."""
    priced = mapping.ranked_price_of({**ENGINE_FULL, "qft": {"T": 4.0, "qubitsUpperBound": 7}})
    assert priced["T"] == 4.0
    assert priced["qubits"] == ENGINE_FULL["qubits"]
    assert priced["ladderLabel"] == ENGINE_FULL["ladderLabel"]


def test_a_projection_that_does_model_qubits_ranks_on_them():
    """THE HOOK FOR LANE WX. `qft_overlay` refuses `backend: "catalytic"` today partly because it "adds a shared
    catalyst register", which would RAISE the qubit count and make the engine's layout total no longer an upper
    bound -- so ranking the engine's number would then publish a count nobody modelled. `ranked_price_of`
    overrides every `mapping.SHOWN` number the projection prices and leaves the rest at the engine's, so WX can
    add `qubits` to the projection and have it ranked without editing this file. Fails if the ranked qubits is
    hard-wired to the engine's."""
    assert mapping.SHOWN == ("T", "qubits")
    priced = mapping.ranked_price_of({**ENGINE_FULL, "qft": {"T": 4.0, "qubits": 90}})
    assert priced["T"] == 4.0 and priced["qubits"] == 90
    assert priced["ladderLabel"] == ENGINE_FULL["ladderLabel"], "a projection cannot restate the ladder"


def test_row_of_ranks_a_full_board_on_the_projection_without_the_engine():
    """OBSTACLE 2 at the row, as a unit, so the wiring is pinned without `--runslow`: a full-run panel carrying
    a projection produces a row whose T is the projection's and whose qubits are the engine's. Fails the moment
    `row_of` goes back to `full.get("readout", full)` -- which with an identity projection no slow test can
    see, because `quotient-exact` ranks to the same float either way."""
    full = {**ENGINE_FULL, "qft": {"T": 4.0, "qubitsUpperBound": 7}}
    panel = {"rowId": HEAD_SHA, "fullRun": full, "verification": {"stoMirror": None, "forshorPolicy": None}}
    row = mapping.row_of(panel, origin="submission", name="t", created_at="x", pull_request_url=None,
                         submission_id=None, detail="d")
    assert row["T"] == 4.0 and row["qubits"] == ENGINE_FULL["qubits"]
    assert mapping.row_of({**panel, "fullRun": dict(ENGINE_FULL)}, origin="submission", name="t", created_at="x",
                          pull_request_url=None, submission_id=None, detail="d")["T"] == ENGINE_FULL["T"]


#: The complete set of keys the published row schema accepts. That schema is CLOSED: a row carrying any key not
#: named here is REJECTED, not ignored. This set is the authority used in this repository -- it is transcribed
#: here deliberately so the constraint is checkable without reference to the publisher's source.
#: `organizerBaseline` is optional and is set by the planner, not by `row_of`; `rank` is added by the
#: publisher after ranking ("A ledger row (no rank)").
SITE_ROW_FIELDS = {"rowId", "origin", "organizerBaseline", "name", "T", "qubits", "ladder", "ladderLabel",
                   "createdAt", "pullRequestUrl", "submissionId", "trust", "detail"}


def test_the_row_names_no_design_and_no_readout_because_the_site_contract_has_no_field_for_either():
    """THE ROW/DESIGN SEAM (lane XD's finding), settled in integration wave 2 by lane XF.

    XD found that the leaderboard bot reads a design off the published ROW while `row_of` copies no design key
    onto it, so the bot can never see a design-bearing row. The proposed fix was to add the design identity to
    the row, "mirroring how the row already carries what it needs for `readout`".

    IT MUST NOT BE ADDED, and this test is the record of why. Established from the behaviour on both sides:

      1. The published row schema is CLOSED: it rejects unrecognised keys rather than dropping them, and it
         names no `design` and no `readout`. So adding either to `row_of` would make EVERY published row fail
         the contract lane XB pinned -- not just design-bearing ones. `SITE_ROW_FIELDS` above is the key set.
      2. There is no key-shaped precedent to mirror. The row carries what it needs for `readout` NOT as a
         key but through `ranked_price_of`, which substitutes the readout's own T and qubits into the row's
         existing `T` / `qubits` fields. `row_of` has never copied a `readout` key, and there is no published
         field for one.
      3. The bot cannot read it from the panel either: the panel's `fullRun` is closed in the same way, its
         only optional price is `readout`, and it has NO `qft` key at all. A design-bearing panel is
         unpublishable for the same reason a design-bearing row would be.

    So the absent-key branch of the bot's `_design` (absent means `reference`, which is statable) is always
    the correct answer, because a design-bearing row cannot be published at all -- intake refuses the
    compiled `submitted_full_run` key outright and `intake_v2.py`'s source gate stays closed.
    `_design` is already fail-closed for anything else: an unrecognised design is kept verbatim and NOT
    stated, rather than read as the default.

    WHAT DID CHANGE, and why this guard now matters more than the comment it replaces. The bot justifies
    itself partly on "the ranked price at mapping.py:234 still reads `readout`, never `qft`". That is now
    STALE: lane WY's `ranked_price_of` does read `qft` (the owner's Tier 2 ruling), so a design-bearing
    panel's row WOULD silently carry the projection's T and qubits with nothing on the row naming the
    design -- exactly the "announce an option's score as if it were the plain one" harm the bot guards
    against. The only things preventing it are the intake refusals and the absent site field, so this test
    pins the field set executably instead of leaving it to a comment that has already drifted once.

    Fails if `row_of` grows a `design` or `readout` key -- which would break the published contract -- or any
    other key that contract does not accept."""
    for full in ({**ENGINE_FULL, "qft": {"T": 4.0, "qubits": 11}},
                 {**ENGINE_FULL, "readout": {**ENGINE_FULL, "T": 5.0, "qubits": 12}},
                 dict(ENGINE_FULL)):
        panel = {"rowId": HEAD_SHA, "fullRun": full, "verification": {"stoMirror": None, "forshorPolicy": None}}
        row = mapping.row_of(panel, origin="submission", name="t", created_at="x", pull_request_url=None,
                             submission_id=None, detail="d")
        assert "design" not in row, "the published row schema is closed and has no design field"
        assert "readout" not in row, "nor a readout field; the readout reaches the row as T and qubits"
        assert set(row) <= SITE_ROW_FIELDS, sorted(set(row) - SITE_ROW_FIELDS)

    # And the overlays reach the row the only way they can: by moving the row's own ranked numbers.
    design_row = mapping.row_of(
        {"rowId": HEAD_SHA, "fullRun": {**ENGINE_FULL, "qft": {"T": 4.0, "qubits": 11}},
         "verification": {"stoMirror": None, "forshorPolicy": None}},
        origin="submission", name="t", created_at="x", pull_request_url=None, submission_id=None, detail="d")
    assert (design_row["T"], design_row["qubits"]) == (4.0, 11)


def test_a_readout_and_a_design_together_refuse_rather_than_pick_a_precedence():
    """WS found that the two overlays do not compose, and the code says why: each re-derives the WHOLE run's
    census from the same frozen coherent result and returns a FINAL price rather than a census patch, the
    synthesis rate `ts_per_rotation(delta, R)` is nonlinear in the rotation total so two finals cannot be
    added, and on the exponent register they contradict each other about one site -- `readout_overlay.project`
    DELETES the exponent QFT while `qft_overlay.FAMILY_WIDTH["exponent"]` bands it.

    So the combination is REFUSED, at the ranked price and at the pricer. Fails if a precedence is ever
    guessed: ranking the readout would silently drop the design the submission asked for, and ranking the
    projection would silently publish a coherent-census price for a measured circuit."""
    both = {**ENGINE_FULL, "readout": {"T": 3.0, "qubits": 2}, "qft": {"T": 4.0}}
    with pytest.raises(ValueError, match="do not compose"):
        mapping.ranked_price_of(both)
    with pytest.raises(ValueError, match="do not compose"):
        mapping.row_of({"rowId": HEAD_SHA, "fullRun": both, "verification": {}}, origin="submission", name="t",
                       created_at="x", pull_request_url=None, submission_id=None, detail="d")


def test_the_pricer_refuses_a_measured_readout_and_a_design_in_the_same_call():
    """The same refusal one step earlier, so nothing is priced at all. `policy_options.check_no_full_run_export`
    already refuses a compiled document that carries `submitted_full_run`, and both measured entry points in
    `request.py` run it before converting a selection, so this pair has never had a defined price anywhere.
    Fails if `price_views` starts producing a view carrying both overlays."""
    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    with pytest.raises(ValueError, match="do not compose"):
        pricing.price_views(FakePolicy(), "t", "1e-2", ["full"], measured=True,
                            design=full_run_design.design_of(priced_selection()))


# --------------------------------------------------------------- the design is scoped, not filtered


def test_the_request_asks_for_a_design_at_1e_2_only_and_asks_as_it_always_did_elsewhere(monkeypatch, policy_json):
    """OBSTACLE 1's mechanism, with the engine replaced: the three non-1e-2 deltas are priced with NO `design`
    keyword at all -- the exact call 792464f made -- rather than with `design=None`. That is what makes their
    byte identity structural. Fails if the design is passed everywhere and filtered afterwards, or if a
    `design=None` keyword creeps into the design-free calls."""
    seen = []
    monkeypatch.setattr(pricing, "price_views",
                        lambda policy, title, delta, boards, **kw: seen.append((delta, dict(kw))) or
                        {b: {"full": {}} for b in boards})
    monkeypatch.setattr(mapping, "panel_of", lambda view, **kw: {"fullRun": None, "verification": {}, "rowId": HEAD_SHA,
                                                                 "T": 1, "qubits": 1, "ladderLabel": "l"})
    monkeypatch.setattr(mapping, "row_of", lambda panel, **kw: {"T": 1})
    mirror = {"verdict": "accepted"}
    request._build_request(policy_json, full_run=priced_selection(), model=epoch_module.model_of(),
                           store=SimpleNamespace(stomirror=lambda *a: (mirror, None)), head_sha=HEAD_SHA,
                           pull_number=1, github_login="t", run_id="1", submitted_at="x", evaluated_at="y",
                           archive_commit="b" * 40, accepted_ids=[], evaluation_key="e", base_generation="1",
                           number=1)
    design = full_run_design.design_of(priced_selection())
    assert [delta for delta, _ in seen] == list(mapping.DELTA_KEYS)
    assert dict(seen) == {"1e-2": {"design": design}, "3e-4": {}, "1e-4": {}, "1e-6": {}}


@pytest.mark.parametrize("delta", ["3e-4", "1e-4", "1e-6"])
def test_price_views_still_refuses_a_design_at_a_delta_it_is_not_qualified_at(delta):
    """The guard stays: scoping the design is the CALLER's job, and `price_views` asked for a design at an
    unqualified delta is a programming error, not a panel to produce. Fails if the fix was made by silently
    dropping the design inside the pricer, which would price one thing and report another."""
    class FakePolicy:
        mode, bands = "PhaseProduct", ()

        def with_widths(self, _):
            return self

    with pytest.raises(ValueError, match="delta 1e-2"):
        pricing.price_views(FakePolicy(), "t", delta, ["full"],
                            design=full_run_design.design_of(priced_selection()))


# --------------------------------------------------------------- a refusal still prices and builds nothing


@pytest.mark.parametrize("profile", sorted(full_run_design.BEYOND_QFT))
def test_a_refused_selection_still_prices_nothing_and_builds_nothing(monkeypatch, profile):
    """The refuse-before-pricing ordering survives both fixes. Scoping the design to one delta happens INSIDE
    the pricing loop, so the conversion must still run before it -- before the engine is loaded, before any
    evidence lookup, before any board is priced. Fails if the design conversion is moved into the loop."""
    monkeypatch.setattr(request.engine, "load", lambda: pytest.fail("a refused selection must not load the engine"))
    monkeypatch.setattr(pricing, "price_views", lambda *a, **kw: pytest.fail("a refused selection must not be priced"))
    monkeypatch.setattr(mapping, "panel_of", lambda *a, **kw: pytest.fail("a refused selection must build no panel"))
    monkeypatch.setattr(mapping, "row_of", lambda *a, **kw: pytest.fail("a refused selection must build no row"))
    args = dict(model=None, store=None, head_sha=HEAD_SHA, pull_number=1, github_login="t", run_id="1",
                submitted_at="x", evaluated_at="y", archive_commit="b" * 40, accepted_ids=[], evaluation_key="e",
                base_generation="1", number=1)
    with pytest.raises(ValueError, match=profile):
        request._build_request({}, full_run=selection(profile), **args)


@pytest.mark.slow
def test_an_overlay_refusal_still_stops_the_whole_request(monkeypatch, policy_json):
    """The second gate reaches the request too: a design that converts and that `qft_overlay` then refuses
    must abort the publish request, not fall back to a design-free four-delta price. Fails if scoping the
    design to 1e-2 were implemented as a try/except that skips the panel the overlay refused.

    RE-POINTED (integration wave 2, lane XF), and the refusal is now INJECTED rather than provoked. This was
    parametrized over the two catalytic profile labels, on the basis that they converted at
    `full_run_design` and were refused at `qft_overlay`. Both halves of that moved: WR's topology gate means
    they no longer convert (which is covered by
    `test_a_refused_selection_still_prices_nothing_and_builds_nothing`), and WX's arm means `catalytic` is no
    longer refused at the overlay at all -- it is priced. Those were the only inputs that reached an overlay
    refusal from a valid selection; measured on the merged tree, every family and every `max_error` tried
    prices, with the catalyst width b landing in [41, 42] against a qualified range of [4, 126]. The
    overlay's surviving refusals are defensive (epoch drift, a census it cannot reproduce, a width shared
    between families) and are not reachable from a well-formed design on this policy.

    So the refusal is injected at the overlay boundary, which is precisely what this test is about: that it
    PROPAGATES out of `_build_request` rather than being swallowed into a design-free price. The conditions
    under which the overlay refuses are `test_qft_overlay.py`'s subject, not this file's."""
    def refuse(policy, coherent, design=None):
        raise qft_overlay.QftOverlayRefused("QFT family 'data': injected overlay refusal")

    monkeypatch.setattr(qft_overlay, "project", refuse)
    with pytest.raises(qft_overlay.QftOverlayRefused, match="injected overlay refusal"):
        request._build_request(policy_json, full_run=priced_selection(), **_request_args(policy_json))


def test_the_ranked_projection_did_not_change_the_evidence_shape():
    """`qft_overlay` belongs to lane WX and `full_run_design` to lane WR; this lane changed neither. The
    projection is ranked by READING the shape WP plumbed, so a copy of a projection still round-trips."""
    projection = {"T": 1.5, "TBase": 1, "rotations": 2, "identity": False, "qubitsUpperBound": 9}
    full = {**ENGINE_FULL, "qft": copy.deepcopy(projection)}
    assert mapping.ranked_price_of(full)["T"] == projection["T"]
    assert full["qft"] == projection, "ranking must not mutate the evidence block"
