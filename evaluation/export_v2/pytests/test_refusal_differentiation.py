"""A refused policy must name WHICH cause refused it.

Owner priority 2026-11-24: "being able to handle and correctly differentiate incorrect policies
from correct policies is the priority". A message shared by several causes cannot tell a
PARTICIPANT ERROR (rewrite the file) from an UNSUPPORTED-BUT-VALID request (use the dedicated
route) — those need different participant actions — so each class gets its own, anchored reason.

Every test below names the production change that would make it fail. Matching is by
anchored, class-level patterns on the *cause* clause, never on incidental wording.
The two causes that used to collide here are census collisions C1 and C2, enumerated as the
module-level patterns below, one per cause, and each test drives the one it names.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))                       # evaluation/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "starter" / "pack" / "harness"))

from policy_options import check_coherent_export, check_no_full_run_export  # noqa: E402

#: The anchors the rest of the repository already matches on; they must survive every rewording.
#: starter/test_pack_validation.py and export_v2/pytests/test_readout_overlay.py assert these,
#: and they are policy_options.FULL_RUN_ROUTE / COHERENT_ROUTE, so no consumer can drift from them.
FULL_RUN_ANCHOR = "compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification"
COHERENT_ANCHOR = "is not supported by the coherent pricing route"

#: Cause classes. Each pattern is anchored on a word that names the CAUSE, not the rule.
QUALIFIED_ROUTE = re.compile(r"readout[- ]qualification route", re.I)     # unsupported-but-valid: use the other route
UNKNOWN_READOUT = re.compile(r"not a readout (?:mode )?this release offers", re.I)   # participant error
LEGACY_KEY = re.compile(r"legacy `?readout`? key", re.I)                  # wrong key


# --- C2: `check_coherent_export`, one message for three causes --------------------------------

def test_a_semiclassical_export_is_told_to_use_the_readout_qualification_route():
    # Production change that would fail this: collapsing the three branches of
    # policy_options.check_coherent_export back into one `readout` message that does not name the
    # cause. A semiclassical export is a VALID request on the wrong route, so the participant must
    # be sent to the local readout-qualification route, not told to edit the policy.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_readout": "semiclassical"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason                       # the anchor the rest of the repo matches
    assert QUALIFIED_ROUTE.search(reason), reason
    assert not UNKNOWN_READOUT.search(reason), reason      # not a participant error
    assert not LEGACY_KEY.search(reason), reason
    assert "semiclassical" in reason, reason               # the value the participant wrote


def test_an_unknown_readout_value_is_told_it_is_not_offered_and_is_not_sent_to_the_other_route():
    # Production change that would fail this: making every non-coherent `submitted_readout` report
    # the readout-qualification route. "banana" is a participant error: no route accepts it, so
    # sending the participant to the qualification harness would waste a run.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_readout": "banana"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason
    assert UNKNOWN_READOUT.search(reason), reason
    assert not QUALIFIED_ROUTE.search(reason), reason
    assert "banana" in reason, reason


def test_a_legacy_readout_key_names_the_key_and_not_a_readout_mode():
    # Production change that would fail this: folding the legacy `readout` key back into the
    # `submitted_readout` branch. The key is the cause here; there is no selected mode to report.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export({"schema": "forshor.policy-submission/v1", "readout": "coherent"})
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason
    assert LEGACY_KEY.search(reason), reason
    assert not QUALIFIED_ROUTE.search(reason), reason
    assert not UNKNOWN_READOUT.search(reason), reason


def test_the_three_coherent_route_causes_do_not_share_a_message():
    # Production change that would fail this: any rewording that makes two of the three causes
    # produce the same string again. This is the collision itself (census C2), asserted directly.
    reasons = []
    for document in ({"submitted_readout": "semiclassical"}, {"submitted_readout": "banana"}, {"readout": "coherent"}):
        with pytest.raises(ValueError) as refusal:
            check_coherent_export({"schema": "forshor.policy-submission/v1", **document})
        reasons.append(str(refusal.value))
    assert len(set(reasons)) == 3, reasons


def test_compiled_submitted_design_keeps_its_own_message():
    # Production change that would fail this: routing `submitted_design` through the readout
    # branch. It is a separate cause and the starter pack's validation asserts this exact text.
    with pytest.raises(ValueError, match=r"^compiled submitted_design metadata is not supported"):
        check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_design": {}})


# --- C1: `check_no_full_run_export`, one message for every shape of the key --------------------

@pytest.mark.parametrize("value,expected", [
    ({"schema": "forshor.full-run-selection/v1", "profile": "quotient-exact"}, "quotient-exact"),
    ({"schema": "forshor.full-run-selection/v1", "profile": "split-step2-fallback-step4-kicks"},
     "split-step2-fallback-step4-kicks"),
])
def test_a_full_run_export_names_the_profile_it_selected(value, expected):
    # Production change that would fail this: dropping the profile from
    # policy_options.check_no_full_run_export's message. The operator triaging a refused export
    # cannot otherwise tell which of the seven profiles the submission asked for.
    with pytest.raises(ValueError) as refusal:
        check_no_full_run_export({"schema": "forshor.policy-submission/v1", "submitted_full_run": value})
    reason = str(refusal.value)
    assert FULL_RUN_ANCHOR in reason
    assert re.search(rf"profile {re.escape(repr(expected))}", reason), reason


@pytest.mark.parametrize("value", [None, "malformed", {"modmul": "split_compare"}, 0, []])
def test_a_full_run_key_without_a_profile_says_so_rather_than_naming_one(value):
    # Production change that would fail this: reading the profile without checking the shape, or
    # dropping the no-profile branch. `null` and a malformed value are a different cause from a
    # real profile selection and must not be reported as one. Key presence still refuses, exactly
    # as before: the ACCEPT/REFUSE decision is unchanged, only the reason is specific.
    with pytest.raises(ValueError) as refusal:
        check_no_full_run_export({"schema": "forshor.policy-submission/v1", "submitted_full_run": value})
    reason = str(refusal.value)
    assert FULL_RUN_ANCHOR in reason
    assert re.search(r"carries a `?submitted_full_run`? key with no profile", reason), reason


def test_absent_and_non_dict_documents_are_untouched():
    # Production change that would fail this: making check_no_full_run_export refuse a document
    # that does not carry the key, or refuse a non-dict at all (census finding F1 records that a
    # non-dict is deliberately NOT refused here; changing that changes the outcome class).
    check_no_full_run_export({"schema": "forshor.policy-submission/v1"})
    check_no_full_run_export(None)
    check_no_full_run_export("not a document")


# --- F1: a non-dict compiled document was a CRASH, not a refusal -------------------------------

@pytest.mark.parametrize("document", [None, "not a document", 7, [], True])
def test_a_compiled_document_that_is_not_an_object_is_refused_not_crashed(document):
    # Production change that would fail this: removing the isinstance guard at the top of
    # policy_options.check_coherent_export. `"submitted_design" in document` raises TypeError for
    # None/int and `.get` raises AttributeError for a str/list, and intake's require_legacy_export
    # catches only ValueError -- so intake reported {"failed": true, "error": "TypeError: ..."},
    # exit 2, an INFRASTRUCTURE fault, for a document it had examined and would not price. Census
    # finding F1. Reachable whenever the Lean executable prints a bare JSON scalar, which
    # json.loads accepts.
    with pytest.raises(ValueError) as refusal:
        check_coherent_export(document)
    reason = str(refusal.value)
    assert COHERENT_ANCHOR in reason, reason                     # the anchor, so intake stages it as `format`
    assert type(document).__name__ in reason, reason             # what ForShor actually printed
    assert "JSON object" in reason, reason                       # what the route needs instead


def test_the_non_dict_refusal_does_not_leak_a_python_exception_class():
    # Production change that would fail this: re-raising the TypeError/AttributeError wrapped rather
    # than refusing. A participant must not be shown a Python traceback type as the verdict.
    for name in ("TypeError", "AttributeError"):
        with pytest.raises(ValueError) as refusal:
            check_coherent_export(None)
        assert name not in str(refusal.value)


def test_a_real_coherent_document_is_still_accepted():
    # Production change that would fail this: a guard that also rejects well-formed documents. F1
    # converts a CRASH into a refusal; it must not move the boundary for anything else. An empty
    # object and an explicitly coherent export were accepted before and must stay accepted.
    check_coherent_export({})
    check_coherent_export({"schema": "forshor.policy-submission/v1"})
    check_coherent_export({"schema": "forshor.policy-submission/v1", "submitted_readout": "coherent"})


def test_the_full_run_route_still_passes_a_non_dict_through():
    # Production change that would fail this: putting the F1 guard in check_no_full_run_export too.
    # That function is a key-presence test reached from four routes (census C1), and refusing a
    # non-dict there WOULD change the accepted set of the readout-qualification route, which is out
    # of scope. F1 is fixed only where the crash was.
    check_no_full_run_export(None)
    check_no_full_run_export("not a document")
    check_no_full_run_export(7)
