"""Table JSON is not a circuit design selection format."""

DESIGN_KEYS = {"readout", "submittedDesign", "submitted_design", "submitted_readout", "qft", "modmul",
               "submittedPrimitive", "submitted_primitive",
               "submittedFullRunDesign", "submitted_full_run",
               "phase_product", "synthesis", "synthesis_backend"}


# A refusal has to say WHICH cause refused, because the causes need different participant actions:
# an unsupported-but-valid request belongs on another route, a malformed one has to be rewritten.
# The leading sentence of each message is the anchor the rest of the repository matches on and
# never changes; the cause follows it after a colon. Nothing here changes what is refused.
COHERENT_ROUTE = "is not supported by the coherent pricing route"
#: Readouts a dedicated local route qualifies (never published). Anything else is a participant error.
QUALIFIED_READOUTS = ("semiclassical",)
#: The anchor both full-run gates below raise, so the two cannot drift apart by a byte. Matched as a PREFIX by
#: starter/test_pack_validation.py and export_v2/pytests/test_readout_overlay.py, so the leading
#: sentence must survive any rewording.
FULL_RUN_ROUTE = "compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification"
#: The only selection object shape the qualification opt-in below will let through.
FULL_RUN_SELECTION_SCHEMA = "forshor.full-run-selection/v1"


def _full_run_selection(document):
    """(the key is present, the selection) for a compiled document, tolerating a document that is not an object."""
    if not isinstance(document, dict) or "submitted_full_run" not in document:
        return False, None
    return True, document["submitted_full_run"]


def check_no_full_run_export(document):
    """The DEFAULT gate, for every shipped route: the key is refused on PRESENCE, whatever it holds.

    Do not weaken this. export_v2/request.py:42 and :84 and intake_v2.py:134, :290 and :324 all call it, and
    export_v2/pytests/test_readout_overlay.py pins key presence through all of them. The dedicated nonpublishing
    full-run qualification route uses `allow_qualified_full_run_export` below instead; it is a separate function so
    that no shipped route can reach the looser test by accident."""
    present, selected = _full_run_selection(document)
    if present:
        profile = selected.get("profile") if isinstance(selected, dict) else None
        cause = (f"the export selects the full-run profile {profile!r}" if isinstance(profile, str)
                 else "the export carries a `submitted_full_run` key with no profile")
        raise ValueError(f"{FULL_RUN_ROUTE}: {cause}")


def allow_qualified_full_run_export(document):
    """The dedicated nonpublishing full-run QUALIFICATION route only, and nothing else calls it.

    The same gate as above, except that a well-formed selection object is let through for conversion
    (`evaluation/export_v2/full_run_design.py`), which the presence test makes unreachable. `null`, a wrong schema, a
    missing or empty profile and a non-dict selection stay refused here, and the conversion itself is fail-closed, so
    this is one of two gates rather than the only one. Absence is accepted, exactly as the default gate accepts it: a
    policy with no selection is an ordinary policy and this route must not invent one for it.

    This enables QUALIFICATION, not publication. The publication gate is elsewhere and is untouched."""
    present, selected = _full_run_selection(document)
    if present and not (isinstance(selected, dict) and selected.get("schema") == FULL_RUN_SELECTION_SCHEMA
                        and isinstance(selected.get("profile"), str) and selected["profile"]):
        profile = selected.get("profile") if isinstance(selected, dict) else None
        cause = (f"the export selects the full-run profile {profile!r}, but not as a well-formed "
                 f"`{FULL_RUN_SELECTION_SCHEMA}` selection" if isinstance(profile, str) else
                 f"the export carries a `submitted_full_run` key that is not a `{FULL_RUN_SELECTION_SCHEMA}` "
                 "selection naming a profile")
        raise ValueError(f"{FULL_RUN_ROUTE}: {cause}")


def check_coherent_export(document):
    check_no_full_run_export(document)
    # Finding F1: `submitted_design in document` raised TypeError for a non-dict and `.get` raised AttributeError,
    # and `require_legacy_export` catches only ValueError, so intake reported {"failed": true} exit 2 -- an
    # infrastructure fault -- for a document it had examined and would not price. A document that is not an object is
    # refused here, in the same class as every other thing this route will not price. `check_no_full_run_export` keeps
    # its key-presence test and still passes a non-dict through, so the full-run route's accepted set is untouched.
    if not isinstance(document, dict):
        raise ValueError(f"the compiled export {COHERENT_ROUTE}: ForShor printed {type(document).__name__} where a "
                         "policy document was expected; this route reads a JSON object")
    if "submitted_design" in document:
        raise ValueError(f"compiled submitted_design metadata {COHERENT_ROUTE}")
    if "readout" in document:
        raise ValueError(f"the compiled readout choice {COHERENT_ROUTE}: the export carries a legacy `readout` key; "
                         "this route reads the compiled `submitted_readout` instead")
    selected = document.get("submitted_readout", "coherent")
    if selected != "coherent":
        cause = ("it is qualified only by the local readout-qualification route, which does not publish"
                 if selected in QUALIFIED_READOUTS else
                 "it is not a readout mode this release offers; the coherent route prices 'coherent' only")
        raise ValueError(f"the compiled readout choice {COHERENT_ROUTE}: the export selects "
                         f"submitted_readout {selected!r} and {cause}")


def check_table_options(document):
    # Follow policy wrappers and tables, not unrelated metadata or notes.
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            keys = sorted(DESIGN_KEYS.intersection(item))
            if keys:
                raise ValueError(f"table policy JSON cannot select circuit design options ({', '.join(keys)}); use the supported Lean policy interface")
            if item.get("negation", "oneQubit") != "oneQubit":
                raise ValueError(f"the policy asks for negation {item['negation']!r}; the boards price oneQubit only")
            for key in ("policy", "policies", "recursion", "bands", "band_tables"):
                if isinstance(wrapped := item.get(key), (dict, list)):
                    pending.append(wrapped)
