"""Frozen-engine oracle and real compiled-readout fixture; nothing publishes."""
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from export_v2 import engine, epoch, readout_overlay as overlay, request
from export_v2.canon import epoch_of, policy_digest, sha256_hex
import intake_v2
from archive_policy import ArchiveRefused, DuplicatePolicy, plan_readout_variant
from intake_v2 import policy_from_document

DATA = Path(__file__).parent / "data"


def load_fixture(name):
    return json.loads(gzip.decompress((DATA / f"{name}.json.gz").read_bytes()))


@pytest.fixture(scope="module")
def measured():
    compiled = load_fixture("readout_submitted_931bbf1")
    return policy_from_document(engine.load(), compiled["document"]), compiled["document"], compiled["source"].encode()


@pytest.mark.parametrize("key", ["readout", "submittedDesign", "submitted_design", "submitted_readout",
                                "qft", "modmul", "phase_product", "synthesis", "synthesis_backend",
                                "submittedFullRunDesign", "submitted_full_run"])
def test_table_json_cannot_silently_drop_a_circuit_selection(measured, key):
    policy = measured[0].to_json()
    for doc in ({**policy, key: "requested"}, {"policies": [{**policy, key: "requested"}]},
                {"policy": policy, key: "requested"},
                {**policy, "bands": [{**policy["bands"][0], key: "requested"}, *policy["bands"][1:]]}):
        with pytest.raises(intake_v2.Refused, match=rf"table policy JSON cannot select.*{key}"):
            intake_v2.normalise(doc)
    assert intake_v2.normalise({**policy, "note": {key: "unrelated metadata"}, "negation": "oneQubit"}) == policy


def test_json_scratch_is_not_silently_priced_as_one_qubit_negation(measured):
    with pytest.raises(intake_v2.Refused, match="boards price oneQubit only"):
        intake_v2.normalise({**measured[0].to_json(), "negation": "scratch"})


@pytest.mark.parametrize("design", [".coherent", ".semiclassical"])
def test_legacy_verification_and_publication_refuse_an_explicit_design(design, tmp_path, monkeypatch):
    source = f"namespace PolicySubmission\ndef submittedDesign := {design}\nend PolicySubmission\n"
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("no legacy compiler fallback"))
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("no publisher request"))
    with pytest.raises(intake_v2.Refused, match="submittedDesign is not enabled"):
        intake_v2.policy_from_lean(None, source, tmp_path, tmp_path)
    with pytest.raises(intake_v2.Refused, match="submittedDesign is not enabled"):
        intake_v2.publish_verification({"leanSource": source}, login="tester", pull=1, url="http://unused",
                                       token="unused", workdir=tmp_path)
    intake_v2.refuse_unqualified_design("/- example: submittedDesign -/\n-- submittedDesign")


def test_readout_qualification_never_falls_back_to_the_ptp_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("PTP is not a readout profile"))
    with pytest.raises(intake_v2.Refused, match="only supports PhaseProduct"):
        intake_v2.policy_from_lean(None, "namespace PTPPolicySubmission\n", tmp_path, tmp_path, qualify_readout=True)


def test_legacy_receipt_cannot_relabel_a_measured_attestation_as_coherent(tmp_path, monkeypatch):
    compiled = {"schema": "forshor.policy-submission/v1", "submitted_readout": "semiclassical"}
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: compiled)
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("no publisher request for a mismatched attestation"))
    with pytest.raises(intake_v2.Refused, match="not supported by the coherent pricing route"):
        intake_v2.policy_from_lean(None, "namespace PolicySubmission\n", tmp_path, tmp_path)
    with pytest.raises(intake_v2.Refused, match="not supported by the coherent pricing route"):
        intake_v2.publish_verification({"printed": compiled}, login="tester", pull=1, url="http://unused",
                                       token="unused", workdir=tmp_path)


LEGACY_SOURCE = "import ForShor.ShorVerification.Framework.Policy\nnamespace PolicySubmission\nend PolicySubmission\n"
FULL_RUN_SOURCE = LEGACY_SOURCE.replace("end PolicySubmission", "def submittedFullRunDesign : Nat := 0\nend PolicySubmission")


@pytest.mark.parametrize("qualify_readout", [False, True])
def test_full_run_declaration_using_old_imports_is_refused_before_legacy_compile(qualify_readout, tmp_path, monkeypatch):
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("no old compiler fallback"))
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("no publisher request"))
    with pytest.raises(intake_v2.Refused, match="submittedFullRunDesign requires dedicated"):
        intake_v2.policy_from_lean(None, FULL_RUN_SOURCE, tmp_path, tmp_path, qualify_readout=qualify_readout)
    with pytest.raises(intake_v2.Refused, match="submittedFullRunDesign requires dedicated"):
        intake_v2.publish_verification({"leanSource": FULL_RUN_SOURCE, "printed": {"schema": "forshor.policy-submission/v1"}},
                                       login="tester", pull=1, url="http://unused", token="unused", workdir=tmp_path)


def test_full_run_refusal_clears_an_old_readout_receipt(tmp_path, monkeypatch):
    source = tmp_path / "Policy.lean"
    source.write_text(FULL_RUN_SOURCE)
    receipt = tmp_path / "verification.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    out = tmp_path / "result.json"
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: pytest.fail("no old compiler fallback"))
    assert intake_v2.main(["--qualify-readout", "--no-publish", "--login", "tester", "--lean-policy", str(source),
                           "--tree", str(tmp_path), "--workdir", str(tmp_path),
                           "--verification-out", str(receipt), "--out", str(out)]) == 1
    assert not receipt.exists()
    assert "submittedFullRunDesign requires dedicated" in json.loads(out.read_text())["reason"]


@pytest.mark.parametrize("qualify_readout", [False, True])
def test_comments_and_omission_keep_the_legacy_compiler_flow(qualify_readout, measured, tmp_path, monkeypatch):
    _, measured_doc, _ = measured
    compiled = measured_doc if qualify_readout else {**measured_doc, "schema": "forshor.policy-submission/v1"}
    if not qualify_readout:
        compiled.pop("readout")
        compiled.pop("submitted_readout")
    seen = []
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, *a, **kw: seen.append(text) or compiled)
    sources = [LEGACY_SOURCE, "-- submittedFullRunDesign\n/- def submittedFullRunDesign /- nested -/ -/\n" + LEGACY_SOURCE]
    outputs = [intake_v2.policy_from_lean(engine.load(), src, tmp_path, tmp_path, qualify_readout=qualify_readout) for src in sources]
    assert seen == sources and outputs[0] == outputs[1]


@pytest.mark.parametrize("metadata", [None, "malformed", {}, {"schema": "forshor.full-run-selection/v1"}])
def test_compiled_full_run_key_is_not_ignored_in_any_old_route(metadata, tmp_path, monkeypatch):
    for qualify_readout in (False, True):
        compiled = {"schema": overlay.MEASURED_SCHEMA if qualify_readout else "forshor.policy-submission/v1",
                    "submitted_full_run": metadata}
        monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: compiled)
        with pytest.raises(intake_v2.Refused, match="compiled submitted_full_run"):
            intake_v2.policy_from_lean(None, LEGACY_SOURCE, tmp_path, tmp_path, qualify_readout=qualify_readout)
    with pytest.raises(intake_v2.Refused, match="compiled submitted_full_run"):
        intake_v2.publish_verification({"printed": {"submitted_full_run": metadata}}, login="tester", pull=1,
                                       url="http://unused", token="unused", workdir=tmp_path)
    source = LEGACY_SOURCE.encode()
    with pytest.raises(ValueError, match="compiled submitted_full_run"):
        request.build_measured_request({}, compiled=compiled, verifier_pin=overlay.VERIFIER_PIN, lean_source=source, model=None)
    receipt = {"schema": intake_v2.VERIFICATION_SCHEMA, "login": "tester", "leanSource": LEGACY_SOURCE,
               "leanSha256": sha256(source).hexdigest(), "printed": compiled, "localReadoutQualification": {}}
    args = dict(source=source, compiled_sha256=sha256_hex(compiled), index={}, published_ids=[], model=None,
                head_sha="a" * 40, github_login="tester", store=None)
    with pytest.raises(ValueError, match="compiled submitted_full_run"):
        request.rehearse_qualified_request(receipt, **args)
    source = FULL_RUN_SOURCE.encode()
    with pytest.raises(ValueError, match="submittedFullRunDesign requires dedicated"):
        request.build_measured_request({}, compiled={}, verifier_pin=overlay.VERIFIER_PIN, lean_source=source, model=None)
    with pytest.raises(ValueError, match="submittedFullRunDesign requires dedicated"):
        request.rehearse_qualified_request({**receipt, "leanSource": FULL_RUN_SOURCE, "leanSha256": sha256(source).hexdigest(),
                                           "printed": {"schema": "forshor.policy-submission/v1"}}, **{**args, "source": source})


@pytest.mark.slow
def test_frozen_epoch_overlay_matches_all_board_oracles():
    eng = engine.load()
    gold = load_fixture("readout_board_20261120")
    policies = {key: eng.tables.Policy.from_json(doc) for key, doc in gold["policies"].items()}
    assert len(gold["panels"]) == 68
    for row in gold["panels"]:
        policy = policies[row["policy"]]
        old = eng.fullalg.price_full(policy, 2048, float(row["delta"]), detail=True)
        assert all(old[k] == row[k] for k in ("T", "T_base", "rotations", "qubits_peak"))
        measured = overlay.project(policy, old)
        assert (measured["T"], measured["qubits"]) == (row["T_after"], row["qubits_after"])
        assert "readout" not in old
    assert epoch_of(epoch.model_of()) == overlay.EPOCH


def test_real_submitted_readout_binds_to_the_compiled_policy(measured):
    policy, document, source = measured
    proof = overlay.bind_compiled_readout(policy, document, verifier_pin=overlay.VERIFIER_PIN, lean_source=source)
    assert proof["mode"] == "semiclassical" and proof["irSha256"] == sha256_hex(document)
    # Was `== overlay.rule_fingerprint()`, which `bind_compiled_readout` assigns this field FROM: a self-referential
    # assertion that held for any value and so could never catch the module drifting away from the literal the
    # published contract pins on the publisher side. Found by lane WU. The expected
    # value lives in `test_operational_pin.py` with the other cross-repo pins, as the single source of truth.
    from test_operational_pin import READOUT_RULE_SHA256

    assert proof["ruleSha256"] == READOUT_RULE_SHA256 == overlay.rule_fingerprint()
    with pytest.raises(ValueError, match="pinned verifier"):
        overlay.bind_compiled_readout(policy, document, verifier_pin="0" * 40, lean_source=source)


@pytest.mark.parametrize("change,reason", [
    (lambda x: x.pop("submitted_readout"), "malformed measured export"),
    (lambda x: x["readout"].update(control="flag"), "loop schedule"),
    (lambda x: x["ir"]["width_rule"].update(rule="scan"), "exact measured IR"),
    (lambda x: x["bands"][0].update(threshold=41), "policy mismatch"),
])
def test_bad_exports_are_refused(measured, change, reason):
    policy, document, source = measured
    tampered = deepcopy(document)
    change(tampered)
    with pytest.raises(ValueError, match=reason):
        overlay.bind_compiled_readout(policy, tampered, verifier_pin=overlay.VERIFIER_PIN, lean_source=source)


def test_extra_arithmetic_in_the_compiled_round_is_refused(measured):
    policy, document, source = measured
    tampered = deepcopy(document)
    rnd = next(t for t in tampered["ir"]["templates"] if t["name"] == "readout_round")
    gate = {"op": "Toffoli", "regs": ["ctrl", "flag", "y[0]"], "nats": [], "angle": None, "flags": []}
    rnd["body"] = {"op": "seq", "body": [rnd["body"], gate]}
    with pytest.raises(ValueError, match="different toffoli count"):
        overlay.bind_compiled_readout(policy, tampered, verifier_pin=overlay.VERIFIER_PIN, lean_source=source)


def test_local_readout_intake_and_publisher_refusal(measured, monkeypatch, tmp_path):
    policy, document, source = measured
    from export_v2 import evidence

    def compile_policy(*args, **opts):
        assert opts["qualify_readout"] is True
        return document

    monkeypatch.setattr(intake_v2, "forshor_build", compile_policy)
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: ({"verdict": "accepted"}, None))
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **kw: pytest.fail("local receipts must never hit a publisher"))
    v = intake_v2.verify_submission(None, lean_policy=source.decode(), login="tester", tree=tmp_path,
        workdir=tmp_path / "work", forshor_tree=tmp_path, run_mirror_fn=lambda *a, **kw: {}, qualify_readout=True)
    assert v["policy"] == policy.to_json()
    assert v["forshor"] is None and v["localReadoutQualification"]["mode"] == "semiclassical"
    assert v["localReadoutQualification"]["evidence"]["sourceSha256"] == v["leanSha256"]
    for record in (v, {k: value for k, value in v.items() if k != "localReadoutQualification"}):
        with pytest.raises(intake_v2.Refused, match="publishing is not enabled"):
            intake_v2.publish_verification(record, login="tester", pull=3, url="http://example", token="local", workdir=tmp_path)


def test_coherent_qualification_binds_the_operational_pin_not_the_model_pin(measured, monkeypatch, tmp_path):
    policy, document, source = measured
    from export_v2 import evidence

    printed = {**document, "schema": "forshor.policy-submission/v1", "submitted_readout": "coherent"}
    printed.pop("readout")
    mirror = {"verdict": "accepted"}
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda *a, **kw: (policy.to_json(), printed))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda eng, p, doc, pin: {"forshorCommit": pin})
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (mirror, None))
    monkeypatch.setattr(request, "stomirror_from_envelope", lambda *a: (mirror, None))
    monkeypatch.setattr(request, "_build_request", lambda policy_json, **route: route)
    args = dict(lean_policy=source.decode(), login="tester", tree=tmp_path, workdir=tmp_path / "work",
                run_mirror_fn=lambda *a, **kw: {})
    verified = intake_v2.verify_submission(None, **args, qualify_readout=True)
    ordinary = intake_v2.verify_submission(None, **args)
    model = verified["model"]
    assert model["forshor"]["pin"] != overlay.VERIFIER_PIN
    assert verified["forshor"]["forshorCommit"] == overlay.VERIFIER_PIN
    assert ordinary["forshor"]["forshorCommit"] == model["forshor"]["pin"]
    replay = dict(source=source, compiled_sha256=sha256_hex(printed), index={"policies": []}, published_ids=set(),
                  model=model, head_sha="a" * 40, github_login="tester", store=SimpleNamespace(stomirror=lambda *a: (mirror, None)))
    route = request.rehearse_qualified_request(verified, **replay)
    assert route["readout"] is None and "forshor" not in route  # the operational claim is local only
    with pytest.raises(ValueError, match="operational verifier pin"):
        request.rehearse_qualified_request({**verified, "forshor": ordinary["forshor"]}, **replay)


def test_missing_compiled_attestation_refuses_before_the_mirror(measured, monkeypatch, tmp_path):
    _, document, source = measured
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: {k: v for k, v in document.items() if k != "submitted_readout"})
    with pytest.raises(intake_v2.Refused, match="missing or mismatched compiled readout"):
        intake_v2.verify_submission(None, lean_policy=source.decode(), login="tester", tree=tmp_path, workdir=tmp_path,
            forshor_tree=tmp_path, run_mirror_fn=lambda *a, **kw: pytest.fail("a missing attestation must refuse before the mirror"),
            qualify_readout=True)


def test_verify_reads_this_attempts_envelope_not_an_older_pass(measured, monkeypatch, tmp_path):
    from export_v2 import evidence

    _, document, source = measured
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: document)
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda *a: pytest.fail("must not scan previous attempts"))
    failed = {"schema": evidence.SCHEMA, "harness": "check_banded_policy", "policyDigest":
              policy_digest(policy_from_document(engine.load(), document).to_json()),
              "record": {"verdict": "rejected", "rejection": {"stage": "threshold", "reason": "did not shrink"}}}
    with pytest.raises(intake_v2.Refused, match="threshold: did not shrink"):
        intake_v2.verify_submission(None, lean_policy=source.decode(), login="tester", tree=tmp_path,
            workdir=tmp_path / "work", forshor_tree=tmp_path, run_mirror_fn=lambda *a, **kw: failed,
            qualify_readout=True)


@pytest.mark.parametrize("error,code", [(RuntimeError("crash"), 2), (subprocess.TimeoutExpired("test", 1), 2),
                                       (intake_v2.Refused("threshold", "did not shrink"), 1)])
def test_failed_direct_qualification_clears_a_stale_receipt(tmp_path, monkeypatch, error, code):
    result = tmp_path / "result.json"
    receipt = tmp_path / "verification.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    policy = tmp_path / "Policy.lean"
    policy.write_text("namespace PolicySubmission\n")
    monkeypatch.delenv("PUBLISHER_URL", raising=False)

    def fail(*a, **kw):
        assert not receipt.exists()
        raise error

    monkeypatch.setattr(intake_v2, "verify_submission", fail)
    assert intake_v2.main(["--qualify-readout", "--no-publish", "--login", "tester", "--lean-policy", str(policy),
                           "--tree", str(tmp_path), "--verification-out", str(receipt), "--out", str(result)]) == code
    assert not receipt.exists() and "verification" not in json.loads(result.read_text())


def local_request(policy, document, source, *, full_only=False, coherent=False):
    model = epoch.model_of()
    # Test double only; an authenticated runner must obtain real STO evidence.
    mirror = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
              "claim": "policy-algebra-and-exact-widths", "ran": {"lean": model["lean"],
              "stoCommit": model["sto"]["commit"], "forshorPin": model["forshor"]["pin"]}}
    ancestor = "e" * 40 if full_only else None
    common = dict(model=model, primitive_from=ancestor, store=SimpleNamespace(stomirror=lambda *a: (mirror, None)),
        head_sha="a" * 40, pull_number=42,
        github_login="readoutfixture", run_id="9001", submitted_at="2026-11-23T12:00:00Z", evaluated_at="2026-11-23T12:01:00Z",
        archive_commit="c" * 40, accepted_ids=["a" * 40] + ([ancestor] if ancestor else []), evaluation_key="d" * 64,
        base_generation="1", number=2 if full_only else 1)
    if coherent:
        return request.build_coherent_variant_request(policy.to_json(), **common)
    return request.build_measured_request(policy.to_json(), compiled=document, verifier_pin=overlay.VERIFIER_PIN,
                                          lean_source=source, **common)


@pytest.mark.slow
@pytest.mark.parametrize("full_only", [False, True])
def test_local_request_is_additive_and_ranks_the_measured_circuit(measured, full_only):
    req = local_request(*measured, full_only=full_only)
    assert epoch_of(req["model"]) == overlay.EPOCH
    assert req["submission"]["boards"] == (["full"] if full_only else ["pp", "full"])
    assert req["submission"].get("primitiveFrom") == ("e" * 40 if full_only else None)
    assert len(req["priced"]) == (4 if full_only else 8)
    for item in req["priced"]:
        panel, row = item["panel"], item["row"]
        assert panel["verification"] == req["submission"]["verification"]
        if item["board"] == "full":
            run = panel["fullRun"]
            assert row["T"] == run["readout"]["T"] and row["qubits"] == run["readout"]["qubits"]
            assert run["T"] != row["T"] and run["qubits"] > row["qubits"]
        else:
            assert panel["fullRun"] is None and row["T"] == panel["T"] and row["qubits"] == panel["qubits"]


@pytest.mark.slow
def test_coherent_variant_reuses_primitive_without_changing_price(measured):
    policy, _, _ = measured
    req = local_request(*measured, full_only=True, coherent=True)
    assert req["submission"]["primitiveFrom"] == "e" * 40
    assert req["submission"]["boards"] == ["full"]
    assert "readout" not in req["submission"]["verification"]
    fa = engine.load().fullalg
    for item in req["priced"]:
        expected = fa.price_full(policy, 2048, float(item["delta"]), detail=False)
        assert item["row"]["T"] == expected["T"] and item["row"]["qubits"] == expected["qubits_peak"]


@pytest.mark.parametrize("first,second", [("coherent", "semiclassical"), ("semiclassical", "coherent")])
def test_duplicate_routing_reads_legacy_archive_without_a_backfill(first, second):
    digest, head, earlier = "b" * 64, "a" * 40, "e" * 40
    entry = {"policy_id": earlier, "digest": digest, "mode": "PhaseProduct",
             "source": {"pr_number": 1, "github_login": "Submitter", "created_at": "2026-11-23T00:00:00Z"}}
    if first != "coherent":
        entry.update(readout=first, boards=["pp", "full"])
    index = {"policies": [entry]}
    expected = deepcopy(index)
    binding = dict(digest=digest, login="submitter", head_sha=head)
    assert plan_readout_variant(index, **binding, readout=second, published_ids={earlier}) == earlier
    with pytest.raises(DuplicatePolicy):
        plan_readout_variant(index, **binding, readout=first, published_ids={earlier})
    assert plan_readout_variant(index, **binding, readout=first, published_ids=set()) is None
    assert plan_readout_variant(index, **{**binding, "login": "different"}, readout=first, published_ids={earlier}) is None
    assert index == expected
    with pytest.raises(ArchiveRefused, match="unknown readout design"):
        plan_readout_variant(index, **binding, readout="unsupported", published_ids={earlier})


def test_readout_rehearsal_binds_receipt_before_suggesting_a_publication_route(measured, monkeypatch):
    policy, document, source = measured
    digest = policy_digest(policy.to_json())
    head, earlier = "a" * 40, "e" * 40
    model = epoch.model_of()
    proof = overlay.bind_compiled_readout(policy, document, verifier_pin=overlay.VERIFIER_PIN, lean_source=source)
    mirror = {"recordSha256": "b" * 64}
    envelope = {"record": {"verdict": "accepted"}}
    receipt = {"schema": intake_v2.VERIFICATION_SCHEMA, "login": "tester", "leanSource": source.decode(),
               "leanSha256": sha256(source).hexdigest(), "model": model, "policy": policy.to_json(), "digest": digest,
               "printed": document, "envelope": envelope,
               "localReadoutQualification": {"mode": "semiclassical", "evidence": proof}}
    archive = {"policies": [{"policy_id": earlier, "digest": digest, "mode": "PhaseProduct",
                            "source": {"github_login": "tester"}}]}
    kwargs = dict(source=source, compiled_sha256=sha256_hex(document), index=archive, published_ids={earlier}, model=model,
                  head_sha=head, github_login="tester", store=SimpleNamespace(stomirror=lambda *a: (mirror, None)))
    monkeypatch.setattr(request, "stomirror_from_envelope", lambda *a: (mirror, None))
    monkeypatch.setattr(request, "_build_request", lambda policy_json, **route: route)
    result = request.rehearse_qualified_request(receipt, **kwargs)
    assert result["primitive_from"] == earlier and result["readout"] == proof

    for change, reason in (({"source": source + b"\n"}, "author or source"), ({"github_login": "other"}, "author or source"),
                           ({"model": {**model, "engineRevision": "0" * 40}}, "different model"),
                           ({"compiled_sha256": "0" * 64}, "separately recorded compiled export"),
                           ({"store": SimpleNamespace(stomirror=lambda *a: ({"recordSha256": "c" * 64}, None))}, "mirror does not match")):
        with pytest.raises(ValueError, match=reason):
            request.rehearse_qualified_request(receipt, **{**kwargs, **change})
    def no_route(*a, **kw):
        pytest.fail("a mismatched receipt must not reach the request builder")
    monkeypatch.setattr(request, "_build_request", no_route)
    tampered = deepcopy(receipt)
    tampered["localReadoutQualification"]["evidence"]["irSha256"] = "0" * 64
    with pytest.raises(ValueError, match="evidence does not match"):
        request.rehearse_qualified_request(tampered, **kwargs)
