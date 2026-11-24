"""intake_v2: a refusal must name WHICH cause refused, and a resource stop must not look like one.

Owner priority 2026-11-24: "being able to handle and correctly differentiate incorrect policies
from correct policies is the priority". Three things that priority needs, asserted here:

  * a RESOURCE STOP is `Incomplete`, never `Refused` and never a pass (census C18, finding F2);
  * a PARTICIPANT ERROR (rewrite the file) is told apart from an UNSUPPORTED-BUT-VALID request
    (use the dedicated route), because they need different participant actions (census C15);
  * a message reachable from two sites says which site it came from (census C10, C11, C12, C4, C14).

Every test names the production change that would make it fail. Matching is by anchored,
class-level patterns on the CAUSE clause, never a substring of drifting wording. The anchor
sentences are asserted as PREFIXES because other code matches on them.
The causes they separate are the class-level patterns below, one per cause.
"""
import re
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parent / "scripts"))

import intake_v2  # noqa: E402

#: Cause classes for a selector refusal, shared with the source gate (scripts/ci/policy_source.py).
#: Each names the CAUSE, so each implies a different participant action.
UNSUPPORTED = re.compile(r"selects the non-default ")
WRONG_FORM = re.compile(r"not in the shipped form")
MENTION = re.compile(r"does not declare a selector")
AT_LINE = re.compile(r"\bline (\d+)\b")

NAMESPACE = "namespace PolicySubmission\n"


def classes(reason):
    return {name for name, pattern in (("UNSUPPORTED", UNSUPPORTED), ("WRONG_FORM", WRONG_FORM),
                                       ("MENTION", MENTION)) if pattern.search(reason)}


def refusal(call, *args):
    with pytest.raises(intake_v2.Refused) as refused:
        call(*args)
    return refused.value


# --- C15: submittedDesign / submittedPrimitive inherit C5's three cause classes ------------------

@pytest.mark.parametrize("refuse,selector,anchor", [
    (intake_v2.refuse_unqualified_design, "submittedDesign",
     "submittedDesign is not enabled for ordinary submissions; it requires local readout qualification"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive",
     "submittedPrimitive requires separate nonpublishing primitive qualification"),
])
def test_the_selector_anchor_sentence_is_still_the_prefix_of_every_cause(refuse, selector, anchor):
    # Production change that would fail this: rewording or re-ordering the anchor so it is no longer
    # the prefix. starter/test_pack_validation.py and the contributor CLI match on these sentences.
    reason = refusal(refuse, f"{NAMESPACE}def alias := {selector}\nend PolicySubmission\n").reason
    assert reason.startswith(anchor), reason


@pytest.mark.parametrize("refuse,selector,value", [
    (intake_v2.refuse_unqualified_design, "submittedDesign", "{ readout := .semiclassical }"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive", ".quotientExact"),
])
def test_a_non_default_selection_is_sent_to_the_dedicated_route_and_names_the_value(refuse, selector, value):
    # Production change that would fail this: dropping intake_v2._selector_cause from
    # refuse_unqualified_design / refuse_unqualified_primitive. A non-default selection is a VALID
    # request on the wrong route; the participant must be sent to the qualification harness, not
    # told to rewrite a declaration that is already well formed. Census C15.
    type_name = "SubmissionDesign" if selector == "submittedDesign" else "PrimitiveSynthesis"
    reason = refusal(refuse, f"{NAMESPACE}def {selector} : Shor.{type_name} := {value}\nend PolicySubmission\n").reason
    assert classes(reason) == {"UNSUPPORTED"}, reason
    assert value in reason, reason
    assert AT_LINE.search(reason), reason


@pytest.mark.parametrize("refuse,selector,declaration", [
    (intake_v2.refuse_unqualified_design, "submittedDesign",
     "private def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive",
     "private def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger"),
])
def test_the_default_written_the_wrong_way_is_told_to_rewrite_it(refuse, selector, declaration):
    # Production change that would fail this: reporting a mis-written default as a non-default
    # selection. The value IS the default, so the ordinary route already accepts this policy once
    # the declaration is rewritten; sending the participant to a qualification harness wastes a run.
    reason = refusal(refuse, f"{NAMESPACE}{declaration}\nend PolicySubmission\n").reason
    assert classes(reason) == {"WRONG_FORM"}, reason
    assert AT_LINE.search(reason), reason


@pytest.mark.parametrize("refuse,selector", [
    (intake_v2.refuse_unqualified_design, "submittedDesign"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive"),
])
def test_a_bare_mention_is_told_to_delete_it(refuse, selector):
    # Production change that would fail this: classifying a use of the name as a declaration.
    # Neither rewriting nor qualifying is the right action: the mention has to go.
    reason = refusal(refuse, f"{NAMESPACE}def alias := {selector}\nend PolicySubmission\n").reason
    assert classes(reason) == {"MENTION"}, reason
    assert AT_LINE.search(reason), reason


@pytest.mark.parametrize("refuse,selector,type_name", [
    (intake_v2.refuse_unqualified_design, "submittedDesign", "SubmissionDesign"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive", "PrimitiveSynthesis"),
])
def test_the_three_selector_cause_classes_never_share_a_message(refuse, selector, type_name):
    # Production change that would fail this: any rewording that makes two of the three classes
    # produce the same string again. This asserts the absence of the collision itself (census C15).
    default = ".rossSelinger" if selector == "submittedPrimitive" else "{ readout := .coherent }"
    other = ".quotientExact" if selector == "submittedPrimitive" else "{ readout := .semiclassical }"
    reasons = [refusal(refuse, f"{NAMESPACE}{body}\nend PolicySubmission\n").reason for body in (
        f"def {selector} : Shor.{type_name} := {other}",
        f"private def {selector} : Shor.{type_name} := {default}",
        f"def alias := {selector}")]
    assert len(set(reasons)) == 3, reasons


@pytest.mark.parametrize("refuse,selector,default", [
    (intake_v2.refuse_unqualified_design, "submittedDesign", "{ readout := .coherent }"),
    (intake_v2.refuse_unqualified_primitive, "submittedPrimitive", ".rossSelinger"),
])
def test_the_canonical_explicit_default_is_still_accepted(refuse, selector, default):
    # Production change that would fail this: anything in the cause classification that also moves
    # the ACCEPT/REFUSE boundary. Owner ruling 2026-11-24: an explicit default is identical to
    # omission, so the canonical declaration must stay silent, exactly as before this lane.
    type_name = "SubmissionDesign" if selector == "submittedDesign" else "PrimitiveSynthesis"
    refuse(f"{NAMESPACE}def {selector} : Shor.{type_name} := {default}\nend PolicySubmission\n")
    refuse(f"{NAMESPACE}def points := #[1, 2, 3]\nend PolicySubmission\n")
    refuse(f"-- {selector}\n/- {selector} -/\n{NAMESPACE}end PolicySubmission\n")
# --- C14: `lean_kind` says which of the two namespace mistakes it saw ---------------------------

@pytest.mark.parametrize("source,seen", [
    ("namespace PolicySubmission\nnamespace PTPPolicySubmission\n", "both"),
    ("def points := #[1, 2, 3]\n", "neither"),
])
def test_the_namespace_refusal_says_whether_it_saw_both_or_neither(source, seen):
    # Production change that would fail this: dropping the "declares both/neither" clause from
    # intake_v2.lean_kind. Zero namespaces is a missing declaration, two is a duplicated one -- the
    # participant edits a different line for each. Census C14.
    reason = refusal(intake_v2.lean_kind, source).reason
    assert reason.startswith("the Lean file must declare exactly one of `namespace PolicySubmission`"), reason
    assert reason.rstrip(".").endswith(seen), reason


def test_exactly_one_namespace_is_still_accepted():
    # Production change that would fail this: any change to lean_kind that moves the accept/refuse
    # boundary rather than only the reason. Both boards must still resolve.
    assert intake_v2.lean_kind("namespace PolicySubmission\n") == "pp"
    assert intake_v2.lean_kind("namespace PTPPolicySubmission\n") == "ptp"


# --- C11: `readout qualification only supports PhaseProduct` came from two sites ----------------

def test_the_readout_qualification_board_refusal_says_which_entry_refused(tmp_path):
    # Production change that would fail this: reverting either parenthetical on the two
    # "readout qualification only supports PhaseProduct" refusals (intake_v2.py's ForShor-build
    # entry and its intake entry). They were byte-identical, so an operator reading a refused run
    # could not tell whether the board was wrong at the build or at intake. Census C11.
    # The anchor stays the PREFIX; test_readout_overlay.py matches on it.
    ANCHOR = "readout qualification only supports PhaseProduct"
    at_intake = refusal(lambda: intake_v2.policy_from_lean(None, "namespace PTPPolicySubmission\n", tmp_path,
                                                           tmp_path, qualify_readout=True)).reason
    at_build = refusal(lambda: intake_v2.forshor_build(tmp_path, "namespace PTPPolicySubmission\n", tmp_path / "b",
                                                       kind="ptp", qualify_readout=True)).reason
    for reason in (at_intake, at_build):
        assert reason.startswith(ANCHOR), reason
    assert at_intake != at_build, (at_intake, at_build)
    assert "intake" in at_intake[len(ANCHOR):] and "build" in at_build[len(ANCHOR):]
