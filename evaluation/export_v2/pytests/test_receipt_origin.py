"""Trusted origin for the `--publish-from` receipt route: what now makes a measured result publishable, and what still refuses it.

PRODUCTION PUBLISHES VIA `--publish-from` (`scripts/ci/publish_policy.py:38-41`), the receipt route. The
publication gate was first lifted for the IN-RUN path only (`intake_v2.publish_verification`, guarded by `qualified_in_run`), so a
qualified measured readout could publish from one trusted run that had just verified it, but not through the normal
two-job split. The standing rule -- hosted intake takes its inputs from the trusted GitHub run and the publisher, never
from a submitter-supplied receipt -- is preserved here in spirit and sharpened: the receipt is no longer asked to
vouch for itself, it is COMPARED against the artifact of the run and attempt it names.

THE DEFECT. The trusted half already produces the proof and the consuming half discarded it:
`scripts/ci/policy_publication.js:96-98` stamps `ciVerification` (verification identity + runId + runAttempt +
pullRequest) into the file handed to `--publish-from`, and nothing under `evaluation/` ever read it. A trusted stamp
that no consumer reads is indistinguishable from no stamp at all, and nothing would have caught that;
`test_the_publisher_reads_the_trusted_stamp_the_trusted_half_already_writes` is the regression guard.

NO NETWORK. `ReceiptOrigin` injects both halves -- the identity re-derivation and the artifact fetch. The GitHub
fetcher's own decisions are proved here against a real zip and a fake transport; only the transport itself is unproven,
and wiring it into the publish job is gate B4's half.
"""
from copy import deepcopy
import gzip
from hashlib import sha256
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
import intake_v2  # noqa: E402
from export_v2 import canon, engine, epoch, evidence, receipt_origin, request  # noqa: E402
from export_v2.receipt_origin import OriginMismatch, OriginUnavailable, ReceiptOrigin  # noqa: E402

DATA = Path(__file__).parent / "data"
FROZEN_CLOCK = "2026-11-24T00:00:00Z"
HEAD = "a" * 40
RUN = "1"
PULL = 7

# A test double, as in test_publication_gate.py: an authenticated runner obtains real STO evidence.
MIRROR = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
          "claim": "policy-algebra-and-exact-widths"}

#: What `scripts/ci/verification_identity.py:identity` returns for HEAD in the publisher's own checkout. The publisher
#: re-derives this; the receipt never supplies it.
IDENTITY = {"schema": "forshor-verification/v1", "key": "k" * 64, "headSha": HEAD, "fingerprint": "f" * 64}


@pytest.fixture(scope="module")
def measured():
    compiled = json.loads(gzip.decompress((DATA / "readout_submitted_931bbf1.json.gz").read_bytes()))
    return intake_v2.policy_from_document(engine.load(), compiled["document"]), compiled["document"], compiled["source"]


@pytest.fixture
def publisher(monkeypatch):
    """The live publisher and `/v2/publish`, with every sent payload recorded."""
    model = epoch.model_of()
    sent = []
    state = {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": 1},
             "completedEvaluations": {}, "submissionIds": ["f" * 40],
             "publication": {"updatedAt": FROZEN_CLOCK, "snapshotId": "s" * 64}}

    def request_json(url, token, payload=None):
        if url.endswith("/v2/state"):
            return state
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        sent.append(payload)
        return {"snapshotId": "n" * 64, "published": True}

    monkeypatch.setattr(intake_v2, "request_json", request_json)
    monkeypatch.setattr(intake_v2, "_now", lambda: FROZEN_CLOCK)
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda self, *a: (MIRROR, None))
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (MIRROR, None))
    return SimpleNamespace(sent=sent, model=model)


def qualify(measured, monkeypatch, tmp_path, document=None):
    """The receipt the trusted verifier job writes for the recorded compiled export (`--no-publish --qualify-readout`)."""
    _, compiled, source = measured
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: document or compiled)
    return intake_v2.verify_submission(None, lean_policy=source, login="tester", tree=tmp_path,
                                       workdir=tmp_path / "verify", forshor_tree=tmp_path,
                                       run_mirror_fn=lambda *a, **kw: {"record": {"verdict": "accepted"}},
                                       qualify_readout=True, log=lambda s: None)


def stamped(v, **changes):
    """The receipt as the TRUSTED publication job hands it to `--publish-from`: through a file, with the trusted
    stamp `scripts/ci/policy_publication.js:96-98` writes into it."""
    receipt = json.loads(json.dumps(v))                     # through a file, as an artifact travels
    receipt[receipt_origin.STAMP] = {**IDENTITY, "runId": RUN, "runAttempt": 2, "pullRequest": PULL, **changes}
    return receipt


def origin_for(v, *, calls=None, fetch=None):
    """A `ReceiptOrigin` whose artifact is exactly the receipt body the verifier job wrote."""
    artifact = json.dumps({k: value for k, value in v.items() if k != receipt_origin.STAMP}).encode()

    def default_fetch(name, run_id, run_attempt):
        if calls is not None:
            calls.append((name, str(run_id), run_attempt))
        return artifact

    return ReceiptOrigin(identity=lambda head: dict(IDENTITY, headSha=head) if head == HEAD else dict(IDENTITY),
                         fetch=fetch or default_fetch)


def publish(v, tmp_path, name="p", **kw):
    return intake_v2.publish_verification(v, login="tester", pull=PULL, url="http://x", token="t",
                                          workdir=tmp_path / name, head_sha=HEAD, run_id=RUN,
                                          log=lambda s: None, **kw)


# ---------------------------------------------------------------- what is now ACCEPTED


@pytest.mark.slow
def test_the_two_job_split_publishes_a_measured_result_once_its_origin_is_established(measured, publisher, monkeypatch, tmp_path):
    """THE ACCEPTANCE TEST. REAL PATH: the recorded compiled export, the real readout binding and the real measured
    pricer, published through `--publish-from`'s own function with NO `qualified_in_run` -- the route production uses.
    Red before the origin check existed: `Refused("publishing is not enabled")`."""
    policy, compiled, source = measured
    v = qualify(measured, monkeypatch, tmp_path)
    receipt, calls = stamped(v), []
    result = publish(receipt, tmp_path, origin=origin_for(receipt, calls=calls))
    assert result["published"] and result["boards"] == ["full", "pp"]
    assert calls == [("table-generation-submission-1-2", "1", 2)], "fetched by the run and attempt the stamp names"
    payload = publisher.sent[-1]
    readout = payload["submission"]["verification"]["readout"]
    from export_v2 import readout_overlay as overlay
    assert readout == overlay.bind_compiled_readout(policy, compiled, verifier_pin=overlay.VERIFIER_PIN,
                                                    lean_source=source.encode())
    assert {p["board"] for p in payload["priced"]} == {"pp", "full"} and len(payload["priced"]) == 8


def test_the_established_origin_is_recorded_and_names_the_run_attempt_and_artifact(measured, publisher, monkeypatch, tmp_path):
    """The proof the publisher keeps: re-derived identity, the run AND attempt, and a digest of what it actually
    fetched -- gate B4's "run AND attempt, immutable artifact identity and digest"."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: _payload())
    v = qualify(measured, monkeypatch, tmp_path)
    receipt = stamped(v)
    proof = receipt_origin.establish(receipt, head_sha=HEAD, run_id=RUN, pull=PULL, origin=origin_for(receipt))
    assert proof["artifact"] == "table-generation-submission-1-2" and proof["runAttempt"] == 2
    assert proof["headSha"] == HEAD and proof["fingerprint"] == IDENTITY["fingerprint"] and proof["key"] == IDENTITY["key"]
    body = {k: value for k, value in receipt.items() if k != receipt_origin.STAMP}
    assert proof["receiptSha256"] == canon.sha256_hex(body), "the digest is of what was FETCHED, not of the receipt"


def _payload():
    row = {"name": "tester_sub0", "T": 1, "qubits": 1}
    return {"schema": "forshor.publish/v2", "evaluationKey": "d" * 64, "baseGeneration": "1", "model": epoch.model_of(),
            "evaluation": {"headSha": HEAD}, "archive": {"policyId": HEAD},
            "submission": {"id": HEAD, "boards": ["pp", "full"], "verification": {}},
            "priced": [{"board": "pp", "delta": "1e-2", "row": row, "panel": {}}]}


# ---------------------------------------------------------------- the retained refusals


def test_the_publisher_reads_the_trusted_stamp_the_trusted_half_already_writes(measured, publisher, monkeypatch, tmp_path):
    """RETAINED, and the regression guard for the defect: `ciVerification` was written by
    `scripts/ci/policy_publication.js:96-98` and read by NOTHING under `evaluation/`. A trusted stamp no consumer reads
    is indistinguishable from no stamp at all. Red if the publisher stops reading it."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("an unstamped receipt prices nothing"))
    v = qualify(measured, monkeypatch, tmp_path)
    receipt = stamped(v)
    unstamped = {k: value for k, value in receipt.items() if k != receipt_origin.STAMP}
    with pytest.raises(intake_v2.Refused, match="carries no trusted verification receipt"):
        publish(unstamped, tmp_path, name="unstamped", origin=origin_for(receipt))
    assert publisher.sent == []


def test_a_receipt_whose_named_artifact_is_not_what_the_publisher_fetches_is_refused_naming_the_mismatch(
        measured, publisher, monkeypatch, tmp_path):
    """RETAINED. The COMPARISON authorises, never the receipt. Every field the gates below rule on is in the body, so
    a receipt that differs from its artifact anywhere is refused and the differing key is named. Red if the body
    comparison is dropped."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("an unmatched receipt prices nothing"))
    v = qualify(measured, monkeypatch, tmp_path)
    artifact = stamped(v)
    other_source = v["leanSource"] + "\n-- a different submission\n"
    changes = [({"leanSource": other_source, "leanSha256": sha256(other_source.encode()).hexdigest()}, "leanSha256, leanSource"),
               ({"digest": "0" * 64}, "digest"),
               ({"login": "someone-else"}, "login"),
               ({"localReadoutQualification": {"mode": "semiclassical", "evidence": None}}, "localReadoutQualification"),
               ({"createdAt": "2020-01-01T00:00:00Z"}, "createdAt")]
    for n, (change, named) in enumerate(changes):
        tampered = {**deepcopy(artifact), **change}
        with pytest.raises(intake_v2.Refused, match="is not the artifact") as refusal:
            publish(tampered, tmp_path, name=f"body{n}", origin=origin_for(artifact))
        assert named in refusal.value.reason, f"the mismatch is named: {refusal.value.reason}"
    assert publisher.sent == []


@pytest.mark.parametrize("field,value,reason", [
    ("runId", "999", "not this publication's run"),
    ("runAttempt", 1, "is not the artifact"),
    ("headSha", "b" * 40, "verification headSha is not this checkout"),
    ("fingerprint", "0" * 64, "verification fingerprint is not this checkout"),
    ("key", "0" * 64, "verification key is not this checkout"),
    ("pullRequest", 8, "names pull request"),
])
def test_a_receipt_from_another_run_attempt_head_or_fingerprint_is_refused(
        field, value, reason, measured, publisher, monkeypatch, tmp_path):
    """RETAINED. Run, attempt, head commit and verification fingerprint are each pinned, and the first three are
    checked against the TRUSTED workflow's own values, not the receipt's. A changed attempt reaches the fetch and is
    caught there, because the attempt is part of the artifact name. Red if any one comparison is removed."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a foreign receipt prices nothing"))
    v = qualify(measured, monkeypatch, tmp_path)
    artifact = stamped(v)

    def fetch(name, run_id, run_attempt):
        if name != "table-generation-submission-1-2":
            return b'{"schema": "forshor.policy-verification/v1", "digest": "another run"}'
        return json.dumps({k: val for k, val in artifact.items() if k != receipt_origin.STAMP}).encode()

    with pytest.raises(intake_v2.Refused, match=reason):
        publish(stamped(v, **{field: value}), tmp_path, name=f"foreign-{field}",
                origin=origin_for(artifact, fetch=fetch))
    assert publisher.sent == []


@pytest.mark.parametrize("attempt", [0, -1, "2", 2.0, True, None])
def test_a_stamp_whose_run_attempt_is_not_a_positive_integer_is_refused_not_merely_unfetchable(
        attempt, measured, publisher, monkeypatch, tmp_path):
    """RETAINED. A malformed attempt is an INVALID RECEIPT, which is how the trusted half already treats it
    (`scripts/ci/publication.js:assertReceipt`, `verification_identity.valid_receipt`), so it is refused by name --
    not left to fail the fetch and be reported as a publisher outage, which would tell the operator to retry something
    that can never succeed. `True` is excluded explicitly because `isinstance(True, int)` holds in Python. Red if the
    attempt's shape stops being checked before the artifact name is built."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a malformed stamp prices nothing"))
    v = qualify(measured, monkeypatch, tmp_path)
    artifact = stamped(v)

    def fetch(name, run_id, run_attempt):
        pytest.fail(f"a malformed attempt must be refused before any fetch (asked for {name!r})")

    with pytest.raises(intake_v2.Refused, match="names no positive run attempt") as refusal:
        publish(stamped(v, runAttempt=attempt), tmp_path, name=f"attempt{attempt!r}",
                origin=origin_for(artifact, fetch=fetch))
    assert refusal.value.stage == "origin", "a verdict on the receipt, not a publisher outage"
    assert publisher.sent == []


def test_a_participant_authored_receipt_is_refused_even_with_a_copied_stamp(measured, publisher, monkeypatch, tmp_path):
    """RETAINED, and the point of the whole lane. The stamp's fields are public, so copying a valid one onto a receipt
    of one's own must not work: re-fetching by identity is what makes trust possible, not the receipt's say-so. Red if
    the fetch-and-compare is replaced by trusting a well-formed stamp."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a participant receipt prices nothing"))
    genuine = stamped(qualify(measured, monkeypatch, tmp_path))
    forged = deepcopy(genuine)                             # a locally qualified receipt, with a real stamp copied on
    forged["localReadoutQualification"] = {"mode": "semiclassical", "evidence": dict(
        genuine["localReadoutQualification"]["evidence"], sourceSha256="0" * 64)}
    assert forged[receipt_origin.STAMP] == genuine[receipt_origin.STAMP], "the stamp itself is valid and unaltered"
    with pytest.raises(intake_v2.Refused, match="is not the artifact") as refusal:
        publish(forged, tmp_path, name="forged", origin=origin_for(genuine))
    assert "localReadoutQualification" in refusal.value.reason
    assert publisher.sent == []


def test_a_receipt_with_no_origin_to_check_against_is_refused_as_before(measured, publisher, monkeypatch, tmp_path):
    """RETAINED, byte-for-byte: with no origin wired in, the blanket refusal is exactly what happens. It is why the
    lift is safe: no fetcher, no publication."""
    v = qualify(measured, monkeypatch, tmp_path)
    for n, record in enumerate((stamped(v), v)):
        with pytest.raises(intake_v2.Refused, match="published only from the trusted verification run"):
            publish(record, tmp_path, name=f"noorigin{n}")
    assert publisher.sent == []


def test_a_fetch_failure_or_timeout_is_incomplete_never_a_refusal_and_publishes_nothing(
        measured, publisher, monkeypatch, tmp_path):
    """RETAINED. A publisher-side fault is not a verdict on the policy: `Incomplete` at stage `publisher`, which
    `scripts/ci/notify_submitters.js` LIMIT_STAGES already reports as incomplete/limits. Red if a fetch failure becomes
    a refusal (which would tell the participant their policy is what is wrong) or, worse, a pass."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a stopped run prices nothing"))
    v = qualify(measured, monkeypatch, tmp_path)
    artifact = stamped(v)
    failures = [TimeoutError("timed out"), OriginUnavailable("the artifact expired"),
                OSError("connection reset"), ValueError("502 Bad Gateway")]
    for n, error in enumerate(failures):
        def fetch(name, run_id, run_attempt, error=error):
            raise error
        with pytest.raises(intake_v2.Incomplete) as stop:
            publish(artifact, tmp_path, name=f"stop{n}", origin=origin_for(artifact, fetch=fetch))
        assert stop.value.stage == "publisher", "an infrastructure fault, reported as incomplete/limits"
        assert "not a verdict on the policy" in str(stop.value)
    assert publisher.sent == []


def test_an_unreadable_artifact_is_incomplete_not_a_refusal(measured, publisher, monkeypatch, tmp_path):
    """RETAINED. Truncated or non-JSON artifact bytes say nothing about the submission either."""
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a stopped run prices nothing"))
    artifact = stamped(qualify(measured, monkeypatch, tmp_path))
    for n, raw in enumerate((b"", b"not json", b"[]", b'{"truncated": ')):
        with pytest.raises(intake_v2.Incomplete) as stop:
            publish(artifact, tmp_path, name=f"unreadable{n}",
                    origin=origin_for(artifact, fetch=lambda *a, raw=raw: raw))
        assert stop.value.stage == "publisher"
    assert publisher.sent == []


# ---------------------------------------------------------------- the coherent path is untouched


@pytest.mark.parametrize("stamp", [False, True])
def test_the_coherent_receipt_route_asks_for_no_origin_at_all(stamp, publisher, monkeypatch, tmp_path):
    """The COHERENT path stays byte-identical: it never reaches the origin check, so no stamp, no fetch and no
    identity re-derivation can change what it prices -- with or without a `ciVerification` stamp on the receipt, and
    even when both halves of the origin would fail the test outright. (The published digests themselves are pinned by
    test_design_price_plumbing.py, test_full_run_chain.py and test_full_run_design.py.)"""
    sent = {}
    monkeypatch.setattr(request, "build_request", lambda policy_json, **kw: sent.update(kw) or _payload())
    monkeypatch.setattr(request, "build_measured_request", lambda *a, **kw: pytest.fail("a coherent receipt is priced coherently"))
    policy_doc = json.loads((DATA / "real/1bc92ac6e1e82ba3.policy.json").read_text())
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "verify",
                                    run_mirror_fn=lambda *a, **kw: {"record": {"verdict": "accepted"}}, log=lambda s: None)
    assert "localReadoutQualification" not in v and v["printed"] is None
    receipt = stamped(v) if stamp else json.loads(json.dumps(v))

    def refuse(*a, **kw):
        pytest.fail("the coherent route must not re-derive an identity or fetch an artifact")

    publish(receipt, tmp_path, name=f"coherent{stamp}", origin=ReceiptOrigin(identity=refuse, fetch=refuse))
    assert sent and publisher.sent, "the coherent receipt published, with no origin asked for"


# ---------------------------------------------------------------- re-deriving the identity (real, no network)


def test_the_publisher_re_derives_the_identity_with_the_TRUSTED_HALFS_OWN_function():
    """REAL PATH, no network: `checkout_identity` must be `scripts/ci/verification_identity.py:identity` itself, not a
    second spelling of it. The stamp records a fingerprint of that file's `INPUTS` tree, so a reimplementation that
    drifted by one path would make every GENUINE receipt fail to match -- the gate would be unfalsifiable, not strict.
    Red if the fingerprint is ever recomputed locally instead of reused."""
    sys.path.insert(0, str(EVAL.parent / "scripts" / "ci"))
    import verification_identity

    mine = receipt_origin.checkout_identity(HEAD)
    theirs = verification_identity.identity(EVAL.parent, HEAD)
    assert mine == theirs, "the publisher and the trusted stamp must agree by construction"
    assert mine["headSha"] == HEAD and len(mine["fingerprint"]) == 64 and mine["schema"] == "forshor-verification/v1"
    assert set(mine) == set(receipt_origin.IDENTITY_FIELDS), "every field is compared; none is ignored"


def test_a_re_derived_identity_at_another_head_does_not_match_this_one():
    """The head commit is inside the key, so a receipt stamped for another head cannot pass this checkout's check."""
    assert receipt_origin.checkout_identity(HEAD)["key"] != receipt_origin.checkout_identity("b" * 40)["key"]
    with pytest.raises(ValueError, match="Invalid submission commit"):
        receipt_origin.checkout_identity("not-a-sha")


# ---------------------------------------------------------------- the GitHub fetcher, offline


def _zip(member, payload):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, payload)
    return buffer.getvalue()


def test_the_github_fetcher_selects_the_named_unexpired_artifact_and_reads_its_verification():
    """Every decision the production fetcher makes, against a real zip and a fake transport. UNPROVEN: the transport
    itself -- wiring a real opener into the publish job is gate B4's half."""
    body = b'{"schema": "forshor.policy-verification/v1"}'
    urls = []

    def opener(url, accept):
        urls.append(url)
        if "/artifacts?" in url:
            return json.dumps({"artifacts": [
                {"id": 1, "name": "table-generation-submission-1-1", "expired": False},
                {"id": 2, "name": "table-generation-submission-1-2", "expired": True},
                {"id": 3, "name": "table-generation-submission-1-2", "expired": False},
            ]}).encode()
        return _zip("verification.json", body)

    fetch = receipt_origin.github_fetcher("tok", opener=opener)
    assert fetch("table-generation-submission-1-2", "1", 2) == body
    assert urls[0].endswith("/actions/runs/1/artifacts?per_page=100")
    assert urls[1].endswith("/actions/artifacts/3/zip"), "the unexpired artifact of that exact name"


@pytest.mark.parametrize("listing,reason", [
    ({"artifacts": []}, "no unexpired artifact"),
    ({"artifacts": [{"id": 1, "name": "table-generation-submission-1-2", "expired": True}]}, "no unexpired artifact"),
    ({"artifacts": [{"id": 1, "name": "table-generation-submission-1-2", "expired": False},
                    {"id": 2, "name": "table-generation-submission-1-2", "expired": False}]}, "2 artifacts named"),
    ({"nope": []}, "could not be fetched"),
])
def test_the_github_fetcher_reports_a_missing_or_ambiguous_artifact_as_unavailable(listing, reason):
    """Never a refusal: the submission is not what is wrong when its artifact has expired or the API answers oddly."""
    fetch = receipt_origin.github_fetcher("tok", opener=lambda url, accept: json.dumps(listing).encode())
    with pytest.raises(OriginUnavailable, match=reason):
        fetch("table-generation-submission-1-2", "1", 2)


def test_the_github_fetcher_refuses_an_artifact_without_a_verification_or_over_the_size_bound(monkeypatch):
    def opener_for(blob):
        def opener(url, accept):
            if "/artifacts?" in url:
                return json.dumps({"artifacts": [{"id": 3, "name": "n", "expired": False}]}).encode()
            return blob
        return opener

    with pytest.raises(OriginUnavailable, match="readable verification.json"):
        receipt_origin.github_fetcher("tok", opener=opener_for(_zip("other.json", b"{}")))("n", "1", 2)
    with pytest.raises(OriginUnavailable, match="readable verification.json"):
        receipt_origin.github_fetcher("tok", opener=opener_for(b"not a zip"))("n", "1", 2)
    monkeypatch.setattr(receipt_origin, "MAX_MEMBER_BYTES", 4)
    with pytest.raises(OriginUnavailable, match="larger than 4 bytes"):
        receipt_origin.github_fetcher("tok", opener=opener_for(_zip("verification.json", b"{}" * 100)))("n", "1", 2)


def test_a_receipt_above_the_old_eight_mebibyte_bound_is_read():
    """A catalytic full-run design receipt is about 9.6 MB; the origin proof must still read it."""
    body = b"x" * (9 * 1024 * 1024)
    assert receipt_origin.read_member(_zip("verification.json", body)) == body
