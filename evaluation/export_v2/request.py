"""A `forshor.publish/v2` request for one accepted policy: what policy intake in CI will send to the publisher.

The policy is priced at all four deltas on the boards it enters (a PhaseProduct policy: pp and full; a PhaseTripleProduct
policy: ptp only). The request is refused here, before any compute is spent on a hopeless one, if the policy has no valid
STO mirror evidence: only an accepted policy is ever published.

A compiled full-run QFT design, where one is present, applies to the `mapping.DESIGN_DELTA` panel alone -- the one
precision that surface is qualified at -- and the other three deltas are priced exactly as they are without it."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Callable, Dict, List, Optional

from . import engine, epoch, full_run_design, mapping, pricing
from .canon import epoch_of, policy_digest, sha256_hex
from .evidence import EvidenceStore, forshor_from_seed, stomirror_from_envelope  # noqa: F401  (ForShor evidence for new runs comes with policy intake)

SUBMISSIONS_REPO = "zapatacomputing/forshor-submissions"


def boards_for(mode: str) -> List[str]:
    eng = engine.load()
    return ["pp", "full"] if mode == eng.tables.PAIR else ["ptp"]


def build_request(policy_json: Dict[str, Any], *, model: Dict[str, Any], store: EvidenceStore, head_sha: str, pull_number: int,
                  github_login: str, run_id: str, submitted_at: str, evaluated_at: str, archive_commit: str,
                  accepted_ids: List[str], evaluation_key: str, base_generation: str, number: int,
                  forshor: Optional[Dict[str, Any]] = None, primitive_from: Optional[str] = None,
                  log: Callable[[str], None] = lambda s: None) -> Dict[str, Any]:
    return _build_request(policy_json, model=model, store=store, head_sha=head_sha, pull_number=pull_number,
                          github_login=github_login, run_id=run_id, submitted_at=submitted_at, evaluated_at=evaluated_at,
                          archive_commit=archive_commit, accepted_ids=accepted_ids, evaluation_key=evaluation_key,
                          base_generation=base_generation, number=number, forshor=forshor,
                          primitive_from=primitive_from, log=log)


def build_measured_request(policy_json: Dict[str, Any], *, compiled, verifier_pin, lean_source, model,
                           primitive_from=None, **request_args) -> Dict[str, Any]:
    """The measured request: `publish_verification` prices a qualified measured receipt through this entry point.

    A compiled full-run selection is admitted here, and only here: the verifier emits one
    only beside a saved semiclassical readout. It must be well formed (`check_measured_full_run_export`), bound to
    the submitted source (`check_full_run_binding`), and convert through `readout_qft_overlay.design_of_measured`,
    which refuses by name a non-reference topology, a named profile, a rotation or phase-step backend and the
    `exponent` family. The design is then priced TOGETHER with the readout (`readout_qft_overlay.project`)."""
    from intake_v2 import Refused, check_full_run_binding
    from policy_options import check_measured_full_run_export
    from .readout_overlay import EPOCH, bind_compiled_readout
    from .readout_qft_overlay import design_of_measured

    check_measured_full_run_export(compiled)
    try:
        check_full_run_binding(lean_source.decode("utf-8"), compiled)
    except Refused as error:
        raise ValueError(error.reason) from error
    selection = full_run_design.selection_of(compiled)
    design_of_measured(selection)
    eng = engine.load()
    policy = eng.tables.Policy.from_json(policy_json)
    if policy.mode != eng.tables.PAIR or epoch_of(model) != EPOCH:
        raise ValueError("measured pricing supports only PhaseProduct under the current epoch")
    evidence = bind_compiled_readout(policy, compiled, verifier_pin=verifier_pin, lean_source=lean_source)
    return _build_request(policy_json, model=model, readout=evidence, full_run=selection,
                          primitive_from=primitive_from, **request_args)


def build_primitive_request(policy_json: Dict[str, Any], *, compiled, verifier_pin, lean_source, preparation=None,
                            model, **request_args) -> Dict[str, Any]:
    """Local standalone-primitive qualification only: this entry point grants no publication authority, and the
    production publish path has no caller for it (`intake_v2.publish_verification` refuses a primitive outright).

    The compiled `submitted_primitive` selector is BOUND here -- by `primitive_overlay.bind_compiled_primitive`,
    against the document and the submitted source -- before anything is priced, so a selector bound to another policy,
    another board or another verifier prices nothing. The policy is then priced on its OWN single-product board alone:
    the full order-finding board takes no primitive, so a primitive request must not carry it (the overlay refuses the
    full board by name, and `price_views` hooks the overlay into the single-product branch only; without this the full
    row would be published as if the standalone option had been honoured).

    `preparation` is the attested catalyst words KEYED BY DELTA (`{"1e-2": {...}, ...}`), or None for a primitive that
    prepares no catalyst. It is per-delta because the uniform catalyst width b is a function of delta
    (`primitive_overlay.uniform_b`: the per-site rounding over every site must fit half the rotation pool), so one
    bank cannot serve four precisions -- the recorded qualification ledger attests one bank per delta for exactly this
    reason. A delta with no attested bank is not quietly priced at the default: the overlay refuses, because
    catalyticB2 with `preparation=None` fails its "one word per resolution 4..b" check. adaptiveB3 measures and resets,
    so it attests none at any delta and is refused if offered one."""
    from intake_v2 import check_legacy_full_run_source, require_primitive_epoch
    from policy_options import check_no_full_run_export
    from .primitive_overlay import bind_compiled_primitive

    check_legacy_full_run_source(lean_source.decode("utf-8"))
    check_no_full_run_export(compiled)
    # An engine at another epoch cannot rule on a primitive submission at all. That is an OPERATOR-side stop
    # (`Incomplete` at stage `epoch`, as for the readout) and not a verdict on the policy, and it is checked here --
    # before the binding, whose own epoch `ValueError` would surface as a refusal of the participant's file.
    require_primitive_epoch(model)
    eng = engine.load()
    policy = eng.tables.Policy.from_json(policy_json)
    selection = compiled["submitted_primitive"]
    primitive = bind_compiled_primitive(policy, compiled, verifier_pin=verifier_pin, lean_source=lean_source)
    return _build_request(policy_json, model=model, primitive=selection, preparation=preparation,
                          primitive_evidence=primitive, **request_args)


def build_coherent_variant_request(policy_json: Dict[str, Any], *, model, primitive_from, **request_args) -> Dict[str, Any]:
    """Local inverse-order rehearsal: add coherent full pricing without replacing the primitive."""
    from .readout_overlay import EPOCH

    if policy_json.get("mode") != engine.load().tables.PAIR or epoch_of(model) != EPOCH or primitive_from is None:
        raise ValueError("a coherent variant requires PhaseProduct and a prior primitive in the current epoch")
    return _build_request(policy_json, model=model, primitive_from=primitive_from, **request_args)


def rehearse_qualified_request(receipt, *, source: bytes, compiled_sha256, index, published_ids, model,
                               head_sha, github_login, store, **request_args) -> Dict[str, Any]:
    """Bind a local qualification receipt to independently supplied source and ledger data.

    No publication authority is granted here. Hosted intake must obtain those
    inputs from the trusted GitHub run and publisher, not from this receipt.
    """
    from archive_policy import plan_readout_variant
    from intake_v2 import (VERIFICATION_SCHEMA, Refused, check_full_run_binding, check_legacy_full_run_source,
                           forshor_record, policy_from_document, require_coherent_export)
    from policy_options import check_measured_full_run_export, check_no_full_run_export
    from .readout_qft_overlay import design_of_measured
    from .readout_overlay import EPOCH, VERIFIER_PIN, bind_compiled_readout

    if (not isinstance(receipt, dict) or not isinstance(source, bytes)
            or receipt.get("schema") != VERIFICATION_SCHEMA or receipt.get("login") != github_login
            or receipt.get("leanSha256") != sha256(source).hexdigest()
            or receipt.get("leanSource") != source.decode("utf-8")):
        raise ValueError("qualification receipt does not match the trusted author or source")
    check_legacy_full_run_source(source.decode("utf-8"))
    qualification = receipt.get("localReadoutQualification")
    document = receipt.get("printed")
    if not isinstance(qualification, dict) or not isinstance(document, dict):
        raise ValueError("missing local readout qualification or its compiled export")
    if qualification.get("mode") == "semiclassical":
        # The measured route admits a bound reference-topology design, as `build_measured_request` does.
        check_measured_full_run_export(document)
        try:
            check_full_run_binding(source.decode("utf-8"), document)
        except Refused as error:
            raise ValueError(error.reason) from error
        design_of_measured(full_run_design.selection_of(document))
    else:
        check_no_full_run_export(document)
    if model != epoch.model_of() or receipt.get("model") != model or epoch_of(model) != EPOCH:
        raise ValueError("qualification receipt was made for a different model")
    if sha256_hex(document) != compiled_sha256:
        raise ValueError("qualification receipt differs from the separately recorded compiled export")
    mode = qualification.get("mode")
    if mode not in ("coherent", "semiclassical") or document.get("submitted_readout") != mode:
        raise ValueError("readout qualification does not match the compiled selection")
    eng = engine.load()
    raw_policy = receipt.get("policy")
    if not isinstance(raw_policy, dict):
        raise ValueError("qualification receipt lacks a policy")
    policy = eng.tables.Policy.from_json(raw_policy)
    policy_json = policy.to_json()
    digest = policy_digest(policy_json)
    if (policy.mode != eng.tables.PAIR or receipt.get("digest") != digest
            or policy_digest(policy_from_document(eng, document).to_json()) != digest):
        raise ValueError("qualification receipt and compiled export disagree on the policy")
    envelope = receipt.get("envelope")
    if not isinstance(envelope, dict):
        raise ValueError("qualification receipt lacks this attempt's mirror envelope")
    current_mirror, _ = stomirror_from_envelope(envelope, policy_json, model, "pp")
    stored_mirror, _ = store.stomirror(policy_json, model, "pp")
    if current_mirror is None or current_mirror != stored_mirror:
        raise ValueError("qualification receipt's mirror does not match the accepted mirror record")
    readout = None
    if mode == "semiclassical":
        readout = bind_compiled_readout(policy, document, verifier_pin=VERIFIER_PIN, lean_source=source)
        if qualification.get("evidence") != readout:
            raise ValueError("qualified readout evidence does not match the compiled export")
    else:
        if document.get("schema") != "forshor.policy-submission/v1" or qualification.get("evidence") is not None:
            raise ValueError("coherent qualification must not carry measured evidence")
        require_coherent_export(document)
        if receipt.get("forshor") != forshor_record(eng, policy, document, VERIFIER_PIN):
            raise ValueError("coherent qualification does not bind the operational verifier pin")
    primitive_from = plan_readout_variant(index, digest=digest, login=github_login, head_sha=head_sha,
                                         readout=mode, published_ids=published_ids)
    return _build_request(policy_json, model=model, head_sha=head_sha, github_login=github_login, store=store,
                          readout=readout, full_run=full_run_design.selection_of(document),
                          primitive_from=primitive_from, **request_args)


def _build_request(policy_json, *, model, store, head_sha, pull_number, github_login, run_id, submitted_at, evaluated_at,
                   archive_commit, accepted_ids, evaluation_key, base_generation, number, forshor=None,
                   readout=None, full_run=None, primitive_from=None, primitive=None, preparation=None,
                   primitive_evidence=None, log=lambda s: None):
    # First, before any evidence lookup or any pricing: a compiled selection this pipeline cannot convert is
    # reported, never priced as the default, and no part of a request is built for it. A selection that DOES
    # convert is threaded to the pricing loop below, at `mapping.DESIGN_DELTA` and nowhere else: the QFT design
    # surface is qualified at that one precision, and the other three deltas of a publish request are the
    # design-free panels of the same policy that they already are -- the same policy at a different precision,
    # which is what every panel ever published is. They are priced with no `design` keyword at all, the exact
    # call 792464f made, so their bytes cannot move. Refusing the whole request instead (which is what a design
    # at every delta did) meant a design-bearing document could never complete one.
    design = full_run_design.design_of(full_run)
    eng = engine.load()
    policy = eng.tables.Policy.from_json(policy_json)
    if primitive_from is not None and (not isinstance(primitive_from, str) or not re.fullmatch(r"[0-9a-f]{40}", primitive_from)
                                        or primitive_from == head_sha or primitive_from not in accepted_ids):
        raise ValueError("a full-only variant needs a different accepted primitive entry")
    if primitive and primitive_from:
        raise ValueError("a standalone primitive and a full-only variant of a prior entry are different submissions")
    boards = ["full"] if primitive_from else boards_for(policy.mode)
    if primitive:
        # The standalone board alone. `boards_for` adds `full` for a PhaseProduct policy, and the full order-finding
        # board takes no primitive at all, so including it would publish the plain coherent run beside a standalone
        # price -- announcing the default as if the option had been honoured on the whole run.
        boards = ["pp" if policy.mode == eng.tables.PAIR else "ptp"]
    mirror_board = "ptp" if "ptp" in boards else "pp"
    mirror, why = store.stomirror(policy_json, model, mirror_board)
    if mirror is None:
        raise ValueError(f"not publishable: no valid STO mirror evidence for this policy ({why})")
    verification = {"stoMirror": mirror, "forshorPolicy": forshor if mirror_board == "pp" else None}
    if readout:
        if forshor is not None:
            raise ValueError("measured proof must not claim coherent IR agreement")
        verification["readout"] = readout
    if primitive_evidence is not None:
        if forshor is not None or readout:
            raise ValueError("a standalone primitive proof claims no coherent IR agreement and no measured readout")
        verification["primitive"] = primitive_evidence
    epoch = epoch_of(model)
    pr_url = f"https://github.com/{SUBMISSIONS_REPO}/pull/{pull_number}"
    priced = []
    for delta in mapping.DELTA_KEYS:
        views = pricing.price_views(policy, f"Submission {head_sha[:12]}", delta, boards,
                                    **({"measured": True} if readout else {}),
                                    **({"design": design} if design and delta == mapping.DESIGN_DELTA else {}),
                                    # Unlike the QFT design, the standalone primitive is qualified at every delta the
                                    # boards publish, so it is passed to all four -- but with THIS delta's own
                                    # attested catalyst bank, because the uniform catalyst width moves with delta.
                                    # With no primitive no keyword is passed at all -- the exact call 792464f made --
                                    # so a default request's bytes cannot move.
                                    **({"primitive": primitive,
                                        "preparation": (preparation or {}).get(delta)} if primitive else {}))
        for board in boards:
            log(f"priced {board} {delta}")
            detail = mapping.panel_path(epoch, head_sha, board, delta)
            panel = mapping.panel_of(views[board], epoch=epoch, row_id=head_sha, board=board, delta=delta, verification=verification)
            row = mapping.row_of(panel, origin="submission", name=f"{github_login}_sub{number}", created_at=submitted_at, pull_request_url=pr_url,
                                 submission_id=head_sha, detail=detail)
            priced.append({"board": board, "delta": delta, "row": row, "panel": panel})
    return {
        "schema": "forshor.publish/v2", "evaluationKey": evaluation_key, "baseGeneration": base_generation,
        "evaluation": {"repository": SUBMISSIONS_REPO, "headSha": head_sha, "archiveCommit": archive_commit, "runId": run_id,
                       "githubLogin": github_login, "pullRequestUrl": pr_url, "evaluatedAt": evaluated_at, "submittedAt": submitted_at},
        "archive": {"policyId": head_sha, "runId": run_id, "pullRequestNumber": str(pull_number), "acceptedIds": accepted_ids},
        "model": model,
        "submission": {"id": head_sha, "pullRequestUrl": pr_url, "submittedAt": submitted_at,
                       "contributors": [{"githubLogin": github_login, "role": "submitter", "evidenceUrl": pr_url}],
                       "policyDigest": policy_digest(policy_json), "boards": boards, "verification": verification,
                       **({"primitiveFrom": primitive_from} if primitive_from else {})},
        "priced": priced,
    }
