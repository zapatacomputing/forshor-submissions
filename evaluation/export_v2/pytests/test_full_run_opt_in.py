"""The full-run opt-in is a SEPARATE gate; the default gate is unchanged and stays the default.

`check_no_full_run_export` refuses a compiled `submitted_full_run` key on PRESENCE, in every
shipped route (export_v2/request.py:42 and :84, intake_v2.py:134, :290 and :324). That is the
behaviour the challenge ships and it is not weakened here. The dedicated nonpublishing full-run
QUALIFICATION route needs a well-formed selection to reach conversion, so it gets its own
function next to the default one, which no shipped route calls.

This enables QUALIFICATION, not publication: the publication gate (publish_verification refusing a
measured receipt, intake_v2.py) is untouched.

Every test names the production change that would make it fail. The safety property -- that the
DEFAULT gate's behaviour is byte-for-byte what it was -- is pinned first and explicitly, because
the whole point of a separate function is that the default one does not move.
"""
import ast
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL / "starter" / "pack" / "harness"))

from policy_options import allow_qualified_full_run_export, check_no_full_run_export  # noqa: E402

#: The anchor every consumer matches on, as a PREFIX. starter/test_pack_validation.py and
#: export_v2/pytests/test_readout_overlay.py both match it, and it is
#: policy_options.FULL_RUN_ROUTE, so the two cannot drift apart by a byte.
ANCHOR = "compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification"

WELL_FORMED = {"schema": "forshor.full-run-selection/v1", "profile": "split-step2-fallback-step4-kicks"}

#: Every shape of the key, and what each gate does with it. `None` means accepted (no exception).
SELECTIONS = [
    ("well formed", WELL_FORMED),
    ("well formed, another profile", {"schema": "forshor.full-run-selection/v1", "profile": "quotient-exact"}),
    ("null", None),
    ("wrong schema", {"schema": "forshor.full-run-selection/v2", "profile": "quotient-exact"}),
    ("no schema", {"profile": "quotient-exact"}),
    ("missing profile", {"schema": "forshor.full-run-selection/v1"}),
    ("empty profile", {"schema": "forshor.full-run-selection/v1", "profile": ""}),
    ("non-string profile", {"schema": "forshor.full-run-selection/v1", "profile": 7}),
    ("selection is a string", "malformed"),
    ("selection is a list", []),
    ("selection is a number", 0),
    ("selection is empty", {}),
]


def refusal(gate, document):
    """The refusal text, or None when the gate accepts."""
    try:
        gate(document)
    except ValueError as error:
        return str(error)
    return None


# --- the safety property: the DEFAULT gate did not move ----------------------------------------

@pytest.mark.parametrize("label,selection", SELECTIONS)
def test_the_default_gate_still_refuses_every_shape_of_the_key(label, selection):
    # Production change that would fail this: weakening check_no_full_run_export to let a well-formed
    # selection through -- i.e. giving the DEFAULT gate the opt-in's behaviour instead of adding a
    # second function. Every shipped route calls this one (export_v2/request.py:42 and :84,
    # intake_v2.py:134, :290 and :324), and test_readout_overlay.py:122-145 pins key presence through
    # all of them. This is THE safety property of the addition.
    reason = refusal(check_no_full_run_export, {"schema": "forshor.policy-submission/v1",
                                                "submitted_full_run": selection})
    assert reason is not None, f"{label}: the default gate stopped refusing on key presence"
    assert reason.startswith(ANCHOR), reason


def test_the_default_gate_still_accepts_absence_and_passes_a_non_dict_document_through():
    # Production change that would fail this: making the default gate refuse a document that does not
    # carry the key, or guarding isinstance(document, dict) there. Census F1 is fixed in
    # check_coherent_export only; this function is a key-presence test reached from four routes, and
    # refusing a non-dict here WOULD change the readout-qualification route's accepted set.
    assert refusal(check_no_full_run_export, {"schema": "forshor.policy-submission/v1"}) is None
    assert refusal(check_no_full_run_export, None) is None
    assert refusal(check_no_full_run_export, "not a document") is None


def test_the_default_gate_still_names_the_profile_or_says_there_is_none():
    # Production change that would fail this: dropping lane WI's cause clause from the default gate
    # while adding the opt-in. The addition must not cost the differentiation already shipped
    # (census C1).
    assert "'quotient-exact'" in refusal(check_no_full_run_export, {"submitted_full_run": WELL_FORMED | {"profile": "quotient-exact"}})
    assert "no profile" in refusal(check_no_full_run_export, {"submitted_full_run": None})


# --- the opt-in: only a well-formed selection is let through ------------------------------------

@pytest.mark.parametrize("profile", ["split-step2-fallback-step4-kicks", "quotient-exact", "reference"])
def test_the_opt_in_lets_a_well_formed_selection_through_for_conversion(profile):
    # Production change that would fail this: removing allow_qualified_full_run_export, or making it
    # refuse on key presence like the default gate. The dedicated nonpublishing qualification route
    # cannot reach the conversion in export_v2/full_run_design.py unless a well-formed selection is
    # allowed past the gate; refusing on presence is exactly why the conversion is unreachable today.
    assert refusal(allow_qualified_full_run_export,
                   {"schema": "forshor.policy-submission/v1",
                    "submitted_full_run": {"schema": "forshor.full-run-selection/v1", "profile": profile}}) is None


@pytest.mark.parametrize("label,selection", [row for row in SELECTIONS if not row[0].startswith("well formed")])
def test_the_opt_in_still_refuses_everything_that_is_not_a_well_formed_selection(label, selection):
    # Production change that would fail this: loosening the opt-in's shape test -- dropping the
    # schema check, accepting a non-string or empty profile, or accepting a non-dict selection. `null`
    # must stay refused in particular: it reads as "no full run" and must never be converted into one.
    # The opt-in is fail-closed; conversion itself is fail-closed too, and these are two gates, not one.
    reason = refusal(allow_qualified_full_run_export, {"schema": "forshor.policy-submission/v1",
                                                       "submitted_full_run": selection})
    assert reason is not None, f"{label}: the opt-in let a malformed selection through"
    assert reason.startswith(ANCHOR), reason


def test_the_opt_in_accepts_absence_exactly_as_the_default_gate_does():
    # Production change that would fail this: making the opt-in demand the key. A policy with no
    # full-run selection is an ordinary policy and the qualification route must not invent one.
    assert refusal(allow_qualified_full_run_export, {"schema": "forshor.policy-submission/v1"}) is None
    assert refusal(allow_qualified_full_run_export, {}) is None


def test_the_opt_in_does_not_crash_on_a_document_that_is_not_an_object():
    # Production change that would fail this: calling document.get() without a guard, which is exactly
    # the defect census finding F1 records for check_coherent_export (TypeError/AttributeError surface
    # as exit 2 "failed" rather than a verdict). The opt-in matches the default gate: no key, nothing
    # to allow, no exception.
    for document in (None, "not a document", 7, []):
        assert refusal(allow_qualified_full_run_export, document) is None


def test_both_gates_raise_the_same_class_and_the_byte_identical_anchor():
    # Production change that would fail this: raising a different exception type from the opt-in, or
    # rewording its anchor. This module's convention is ValueError, wrapped by the caller
    # (intake_v2.require_legacy_export -> Refused("format", ...)); a different class would escape that
    # wrapper and be reported as an infrastructure fault. The anchor must stay byte-identical so
    # every anchored pattern matching the default gate also matches the opt-in.
    default = refusal(check_no_full_run_export, {"submitted_full_run": WELL_FORMED})
    opt_in = refusal(allow_qualified_full_run_export, {"submitted_full_run": None})
    for reason in (default, opt_in):
        assert reason.startswith(ANCHOR), reason
    with pytest.raises(ValueError):
        allow_qualified_full_run_export({"submitted_full_run": None})


# --- the opt-in is not wired into anything -----------------------------------------------------

def test_no_shipped_route_calls_the_opt_in():
    # Production change that would fail this: calling allow_qualified_full_run_export from any
    # production module. It is opt-in for the dedicated nonpublishing qualification route, which does
    # not exist yet and needs an owner decision on publishing scope
    # (overnight-native-final-guard-validation/validation.json records publishable: false). Wiring it
    # into a shipped route would silently let full-run selections past a gate that every shipped route
    # relies on. The check is over the AST, not a grep, so a call cannot hide in a string or a comment.
    repo = EVAL.parent
    allowed = {Path(__file__).resolve(), (EVAL / "starter" / "pack" / "harness" / "policy_options.py").resolve()}
    callers = []
    for path in repo.rglob("*.py"):
        if path.resolve() in allowed or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            name = (node.id if isinstance(node, ast.Name) else
                    node.attr if isinstance(node, ast.Attribute) else
                    node.name if isinstance(node, ast.alias) else None)
            if name == "allow_qualified_full_run_export":
                callers.append(f"{path.relative_to(repo)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                callers += [f"{path.relative_to(repo)}:{node.lineno}" for a in node.names
                            if a.name == "allow_qualified_full_run_export"]
    assert callers == [], f"the full-run opt-in has been wired into a route: {callers}"


def test_every_shipped_route_still_reaches_the_default_gate():
    # Production change that would fail this: swapping check_no_full_run_export for the opt-in at any
    # call site. The five shipped call sites are the paths test_readout_overlay.py:122-145 exercises;
    # if one of them changed gate, the opt-in would be live in a shipped route.
    repo = EVAL.parent
    sites = []
    for relative in ("evaluation/export_v2/request.py", "evaluation/intake_v2.py"):
        tree = ast.parse((repo / relative).read_text(encoding="utf-8"))
        sites += [relative for node in ast.walk(tree)
                  if isinstance(node, ast.Name) and node.id == "check_no_full_run_export"]
    # build_measured_request and rehearse_qualified_request, plus forshor_record, the readout-qualification
    # output and the compiled-pricing route. Every one of them must still be on the DEFAULT gate.
    #
    # Lane XG, 2026-11-24: `build_primitive_request` is a THIRD site here -- the local standalone-primitive pricing
    # entry point, which runs the gate on its compiled export before binding anything. It is the DEFAULT gate, so
    # the invariant this test protects is intact and only the expected count moved.
    assert sites.count("evaluation/export_v2/request.py") == 3, sites
    # Integration 2026-11-24 (lane XE): WQ's publication-gate lift added a FOURTH intake site,
    # `publish_verification`'s measured arm (intake_v2.py:613), which rules on a measured export that names a
    # selection this checkout has no converter for. It is the DEFAULT gate, so the invariant this test exists to
    # protect is intact and only the expected count moved; `test_the_opt_in_is_wired_into_no_route` above still
    # pins `allow_qualified_full_run_export` to zero callers.
    #
    # Lane XG, 2026-11-24: the standalone-primitive gate lift added a FIFTH intake site. `policy_from_lean` now
    # picks its export checker by route -- a compiled primitive export gets `check_no_full_run_export` (it carries a
    # selector the coherent checker has no opinion on, but it must still name no full-run selection) and every other
    # route keeps the checker it had, so the conditional reads the same name twice. The `_forshor_build` site is
    # unchanged and now covers the primitive route too. All five are the DEFAULT gate.
    assert sites.count("evaluation/intake_v2.py") == 5, sites
