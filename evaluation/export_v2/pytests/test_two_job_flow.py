"""The TWO-JOB flow end to end: the verifier job writes a measured receipt, the publish job publishes it.

Lane XH proved the publisher half -- `publish_verification` accepts a receipt once `receipt_origin.establish` proves
it IS the trusted verifier job's artifact. Two links were missing and both are closed here:

  * `scripts/policy_verifier.py` never passed `--qualify-readout`, so the verifier job never PRODUCED a measured
    receipt for the publisher to accept. It now passes it exactly when the submission declares the measured readout,
    decided before the build by `scripts/ci/policy_source.py:declared_readout` -- the source gate's own recogniser.
  * no caller passed `origin`, so the origin check was implemented and inert. `scripts/ci/publish_policy.py` now
    passes `--trusted-origin`, which builds `receipt_origin.trusted_origin()`.

WHAT IS REAL HERE: the verifier job's own `run`, its own argv, the real `intake_v2` behind it, the real readout
binding, the real measured pricer, the real origin check, and the real recorded compiled export and Lean source of the
measured submission (`data/readout_submitted_931bbf1.json.gz`).

WHAT IS STOOD IN FOR, exactly as in `test_receipt_origin.py`: ForShor's build and the STO mirror (the recorded
document and the authenticated-runner double), and the publisher.

WHAT IS STILL UNPROVEN AND IS GATE B4: one HTTPS request. `receipt_origin.urllib_opener` is the production transport
and nothing in any suite calls it against api.github.com.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import intake_v2  # noqa: E402
import policy_verifier as pv  # noqa: E402
from export_v2 import epoch, evidence, receipt_origin  # noqa: E402
from export_v2.receipt_origin import OriginUnavailable, ReceiptOrigin  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
DATA = Path(__file__).parent / "data"
DOCUMENT = DATA / "readout_submitted_931bbf1.json.gz"
FROZEN_CLOCK = "2026-11-24T00:00:00Z"
HEAD = "a" * 40
RUN = "1"
PULL = 7
ATTEMPT = 2
IDENTITY = {"schema": "forshor-verification/v1", "key": "k" * 64, "headSha": HEAD, "fingerprint": "f" * 64}
MIRROR = {"harness": "check_banded_policy", "widths": "exact", "verdict": "accepted", "recordSha256": "b" * 64,
          "claim": "policy-algebra-and-exact-widths"}


@pytest.fixture
def publisher(monkeypatch):
    model = epoch.model_of()
    sent = []

    def request_json(url, token, payload=None):
        if url.endswith("/v2/state"):
            return {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": 1},
                    "completedEvaluations": {}, "submissionIds": ["f" * 40],
                    "publication": {"updatedAt": FROZEN_CLOCK, "snapshotId": "s" * 64}}
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        sent.append(payload)
        return {"snapshotId": "n" * 64, "published": True}

    monkeypatch.setattr(intake_v2, "request_json", request_json)
    monkeypatch.setattr(intake_v2, "_now", lambda: FROZEN_CLOCK)
    monkeypatch.setattr(evidence.EvidenceStore, "stomirror", lambda self, *a: (MIRROR, None))
    monkeypatch.setattr(evidence, "stomirror_from_envelope", lambda *a: (MIRROR, None))
    return SimpleNamespace(sent=sent, model=model)


def shim(tmp_path: Path) -> list:
    """The verifier job's intake command, as the job really runs it: a separate process running `intake_v2.main` on
    the argv `policy_verifier` built, with only ForShor's build and the STO mirror stood in for.

    This is the point of the test. `policy_verifier.run` decides the argv -- including whether `--qualify-readout` is
    on it -- and nothing here can put it there.
    """
    script = tmp_path / "intake_shim.py"
    script.write_text(
        "import gzip, json, sys\n"
        f"sys.path.insert(0, {str(REPO / 'evaluation')!r})\n"
        "import intake_v2\n"
        "from export_v2 import evidence\n"
        f"open({str(tmp_path / 'argv.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"document = json.loads(gzip.decompress(open({str(DOCUMENT)!r}, 'rb').read()))['document']\n"
        "intake_v2.forshor_build = lambda *a, **kw: document\n"
        "evidence.run_mirror = lambda *a, **kw: {'record': {'verdict': 'accepted'}}\n"
        f"evidence.stomirror_from_envelope = lambda *a: ({MIRROR!r}, None)\n"
        "raise SystemExit(intake_v2.main(sys.argv[1:]))\n")
    return [sys.executable, str(script)]


def verifier_job(tmp_path: Path, source: str) -> tuple:
    """Run the trusted VERIFIER job over `source` and return (its result, the receipt it wrote, its intake argv)."""
    repo = tmp_path / "repo"
    (repo / "TableGeneration/Submission").mkdir(parents=True)
    (repo / pv.SUBMISSION).write_text(source)
    (tmp_path / "forshor").mkdir(exist_ok=True)
    out = tmp_path / "out"
    code = pv.run(repo, out, get_changed_files=lambda r: (True, [pv.SUBMISSION], "Compared base...head."),
                  env={"FORSHOR_TREE": str(tmp_path / "forshor"), "STO_TREE": str(tmp_path),
                       "TABLE_GEN_LOGIN": "tester", "ENGINE_PYTHON": sys.executable},
                  intake=shim(tmp_path))
    result = json.loads((out / "table-generation-submission-result.json").read_text())
    receipt = json.loads((out / "verification.json").read_text()) if (out / "verification.json").is_file() else None
    log = (out / "policy-verifier.log").read_text() if (out / "policy-verifier.log").is_file() else ""
    argv = json.loads((tmp_path / "argv.json").read_text()) if (tmp_path / "argv.json").is_file() else None
    return SimpleNamespace(code=code, result=result, receipt=receipt, log=log, argv=argv)


def stamped(receipt: dict, **changes) -> dict:
    """The receipt as `scripts/ci/policy_publication.js:96-98` hands it to `--publish-from`."""
    out = json.loads(json.dumps(receipt))
    out[receipt_origin.STAMP] = {**IDENTITY, "runId": RUN, "runAttempt": ATTEMPT, "pullRequest": PULL, **changes}
    return out


def origin_for(receipt: dict, *, calls=None, fetch=None) -> ReceiptOrigin:
    body = json.dumps({k: v for k, v in receipt.items() if k != receipt_origin.STAMP}).encode()

    def default_fetch(name, run_id, run_attempt):
        if calls is not None:
            calls.append((name, str(run_id), run_attempt))
        return body

    return ReceiptOrigin(identity=lambda head: dict(IDENTITY, headSha=head), fetch=fetch or default_fetch)


@pytest.fixture(scope="module")
def measured_source():
    return json.loads(gzip.decompress(DOCUMENT.read_bytes()))["source"]


@pytest.fixture(scope="module")
def coherent_source(measured_source):
    """The same submission with the readout declaration deleted: an ordinary submission, by omission."""
    return measured_source.replace("def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical\n", "")


# ---------------------------------------------------------------- the flow, end to end


@pytest.mark.slow
def test_the_verifier_job_now_produces_the_measured_receipt_and_the_publisher_publishes_it(
        measured_source, publisher, tmp_path):
    """THE ACCEPTANCE TEST FOR THE WHOLE FLOW. Two jobs, the real route: the verifier job compiles and writes the
    receipt, the publish job establishes its origin and publishes it.

    Red before this lane at the FIRST assertion: `scripts/policy_verifier.py` passed no `--qualify-readout`, so the
    receipt carried no `localReadoutQualification` -- there was no measured receipt in existence for the publisher to
    accept, and XH's whole gate could never fire on a real submission.
    """
    job = verifier_job(tmp_path, measured_source)
    assert job.code == 0 and job.result["status"] == "success", job.log
    assert job.receipt["localReadoutQualification"]["mode"] == "semiclassical", job.log
    assert job.receipt["printed"]["schema"] == "forshor.policy-submission-semiclassical/v1"

    receipt, calls = stamped(job.receipt), []
    result = intake_v2.publish_verification(receipt, login="tester", pull=PULL, url="http://x", token="t",
                                            workdir=tmp_path / "publish", head_sha=HEAD, run_id=RUN,
                                            origin=origin_for(receipt, calls=calls), log=lambda s: None)
    assert result["published"] and result["boards"] == ["full", "pp"]
    assert calls == [(f"table-generation-submission-{RUN}-{ATTEMPT}", RUN, ATTEMPT)]
    assert {p["board"] for p in publisher.sent[-1]["priced"]} == {"pp", "full"}


@pytest.mark.slow
def test_the_flag_is_what_selects_the_checker_and_the_measured_build_is_not_taken_by_omission(
        measured_source, coherent_source, publisher, tmp_path):
    """The SAME submission with the readout declaration deleted takes the coherent build, and the coherent route then
    refuses the measured document rather than pricing it as coherent.

    Two things at once. First, the flag is conditional on the SOURCE, not on what came back from the build: with the
    declaration gone the job asks for no qualification even though the build (stood in for here) still answers with a
    measured document. Second, the coherent checker is the one that rules on it, so the only way a measured document
    is ever priced is the route that asked for it.

    Production change that would fail this: passing `--qualify-readout` unconditionally -- which is precisely the
    change XH declined to make, because it changes the ForShor build for every submission.
    """
    job = verifier_job(tmp_path, coherent_source)
    assert "--qualify-readout" not in job.argv
    assert (job.code, job.result["status"]) == (1, "failure")
    assert job.receipt is None, "a refused submission leaves no receipt"
    assert "coherent pricing route" in job.result["checks"][-1]["details"]
    assert publisher.sent == []

    measured = verifier_job(tmp_path / "measured", measured_source)
    assert "--qualify-readout" in measured.argv, "the declaration, and only the declaration, asks for it"


@pytest.mark.slow
def test_a_fetch_failure_on_the_real_receipt_is_incomplete_and_publishes_nothing(measured_source, publisher, tmp_path):
    """A resource stop on the receipt the verifier job really wrote: INCOMPLETE at stage `publisher`, never a refusal
    and never a pass, and no success receipt is sent."""
    job = verifier_job(tmp_path, measured_source)
    receipt = stamped(job.receipt)

    def timeout(name, run_id, run_attempt):
        raise OriginUnavailable("the trusted artifact could not be fetched: TimeoutError: timed out")

    with pytest.raises(intake_v2.Incomplete) as stop:
        intake_v2.publish_verification(receipt, login="tester", pull=PULL, url="http://x", token="t",
                                       workdir=tmp_path / "publish", head_sha=HEAD, run_id=RUN,
                                       origin=origin_for(receipt, fetch=timeout), log=lambda s: None)
    assert stop.value.stage == "publisher"
    assert publisher.sent == []


# ---------------------------------------------------------------- the caller wiring (link 2)


def test_the_publish_job_builds_the_production_origin_and_reads_the_token_only_to_fetch(monkeypatch):
    """`trusted_origin` is the injection point `--trusted-origin` wires in. The identity half is the TRUSTED HALF'S
    OWN function, and the token is read only when a fetch actually happens.

    Production change that would fail this: constructing the fetcher eagerly from `os.environ['GH_TOKEN']`, which
    would make every ordinary coherent publication need a token for a check it never reaches.
    """
    origin = receipt_origin.trusted_origin(REPO, token=lambda: pytest.fail("the token was read without a fetch"))
    assert origin.identity(HEAD) == receipt_origin.checkout_identity(HEAD, REPO)

    seen = {}
    monkeypatch.setattr(receipt_origin, "urllib_opener", lambda token, **kw: seen.setdefault("token", token) and None)
    monkeypatch.setattr(receipt_origin, "github_fetcher", lambda token, **kw: lambda *a: seen.setdefault("fetched", a))
    receipt_origin.trusted_origin(REPO, token=lambda: "gh-token").fetch("n", RUN, ATTEMPT)
    assert seen["token"] == "gh-token" and seen["fetched"] == ("n", RUN, ATTEMPT)


def test_the_production_transport_keeps_the_token_in_the_header_and_bounds_what_it_reads(monkeypatch):
    """Everything `urllib_opener` itself decides, against an injected transport. UNPROVEN AND GATE B4: a real HTTPS
    request. Nothing in any suite makes one."""
    seen = {}

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, size):
            return self.body[:size]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def build(url, headers):
        seen.update(url=url, headers=headers)
        return SimpleNamespace(url=url)

    opener = receipt_origin.urllib_opener("gh-secret", urlopen=lambda r, timeout: Response(b"{}"), build=build)
    assert opener("https://api.github.com/x", "application/vnd.github+json") == b"{}"
    assert seen["headers"]["Authorization"] == "Bearer gh-secret"
    assert "gh-secret" not in seen["url"], "the token is never in a URL"

    monkeypatch.setattr(receipt_origin, "MAX_RESPONSE_BYTES", 4)
    big = receipt_origin.urllib_opener("t", urlopen=lambda r, timeout: Response(b"x" * 100), build=build)
    with pytest.raises(OriginUnavailable, match="larger than 4 bytes"):
        big("https://api.github.com/x", "application/json")


def test_the_transport_refuses_a_non_https_url_before_it_builds_the_token_header():
    """Integration XQ, carried across from the superseded `ebf21b0`. The scheme is checked BEFORE the request is
    built, so the header carrying `GH_TOKEN` is never constructed for a URL that would not protect it.

    A transport problem, so `OriginUnavailable` and not a verdict. Production change that would fail this: dropping
    the scheme check, which is how lane XN's transport shipped."""
    def never(url, headers):
        pytest.fail(f"the token header was built for {url!r}")

    for url in ("http://api.github.com/x", "ftp://api.github.com/x", "file:///etc/passwd"):
        opener = receipt_origin.urllib_opener("gh-secret", urlopen=lambda r, timeout: None, build=never)
        with pytest.raises(OriginUnavailable, match="refusing a non-HTTPS artifact URL"):
            opener(url, "application/json")


def test_a_redirect_can_neither_downgrade_off_tls_nor_carry_the_token_to_another_host():
    """Integration XQ, carried across from the superseded `ebf21b0`: the ONE control that branch had and lane XN's
    transport did not. Both holes are CPython 3.12's own defaults, not hypotheticals --
    `HTTPRedirectHandler.redirect_request` copies every header but `content-length`/`content-type` onto the new
    request, and `http_error_302` allows `http`, so stock urllib would hand `Authorization: Bearer <GH_TOKEN>` to
    whatever host a 302 names, in cleartext if it names `http`.

    The token is the one input in this whole path that is neither re-derived nor compared against the trusted run, so
    no later comparison can notice it leaked. Production change that would fail this: building the opener with bare
    `urllib.request.urlopen`, or dropping `_StripAuthOnRedirect` from `build_opener`.

    Exercised against the handler directly, with no socket: GATE B4 is still the one real HTTPS request, and nothing
    in any suite makes one."""
    import urllib.request

    handler = receipt_origin._StripAuthOnRedirect()
    original = urllib.request.Request("https://api.github.com/repos/x/actions/artifacts/1/zip",
                                      headers={"Authorization": "Bearer gh-secret", "Accept": "application/json"})

    # 1. A redirect off TLS is refused outright rather than followed without the header.
    for downgrade in ("http://api.github.com/x", "ftp://api.github.com/x"):
        assert handler.redirect_request(original, None, 302, "Found", {}, downgrade) is None, downgrade

    # 2. A cross-host HTTPS redirect is followed, but the credential does not go with it.
    crossed = handler.redirect_request(original, None, 302, "Found", {}, "https://evil.example/x")
    assert crossed is not None
    assert crossed.get_header("Authorization") is None, "the token followed the redirect to another host"
    assert "gh-secret" not in str(dict(crossed.headers)), "the token survived the cross-host redirect"

    # 3. A same-host HTTPS redirect is the ordinary case GitHub actually serves for an artifact zip, and it must
    #    still carry the credential or every genuine fetch would 401.
    same = handler.redirect_request(original, None, 302, "Found", {}, "https://api.github.com/elsewhere")
    assert same is not None
    assert same.get_header("Authorization") == "Bearer gh-secret"


def test_the_production_transport_installs_the_redirect_guard_rather_than_bare_urlopen(monkeypatch):
    """The guard above is only worth anything if `urllib_opener`'s DEFAULT transport is the opener carrying it. With
    no `urlopen` injected, `build_opener` must be called with `_StripAuthOnRedirect` and its `open` used to send.

    Production change that would fail this: reverting `send` to `urllib.request.urlopen`, which is what lane XN's
    transport did and is why the token could have followed a redirect."""
    import urllib.request

    passed, sent = [], []

    class FakeOpener:
        def open(self, request, timeout=None):
            sent.append(request)
            return _Body(b"{}")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: passed.extend(handlers) or FakeOpener())
    opener = receipt_origin.urllib_opener("gh-secret")
    assert receipt_origin._StripAuthOnRedirect in passed, \
        "urllib_opener did not build its opener with the redirect guard"
    assert opener("https://api.github.com/x", "application/json") == b"{}"
    assert sent and sent[0].get_header("Authorization") == "Bearer gh-secret"


class _Body:
    def __init__(self, body):
        self.body = body

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_transport_failure_reaches_the_publisher_as_unavailable_and_not_as_a_verdict():
    """A dead socket is not a verdict on a submission. Production change that would fail this: letting a transport
    exception escape `github_fetcher`, where `publish_verification` would see it as neither refusal nor pass."""
    def dead(request, timeout):
        raise OSError("connection reset")

    opener = receipt_origin.urllib_opener("t", urlopen=dead, build=lambda url, headers: url)
    with pytest.raises(OriginUnavailable):
        receipt_origin.github_fetcher("t", opener=opener)("n", RUN, ATTEMPT)


def test_the_intake_cli_threads_the_origin_through_and_refuses_as_before_without_it(monkeypatch, tmp_path):
    """`--trusted-origin` is the ONLY thing that supplies an origin, and without it lane WQ's blanket refusal stands
    (XH's R9). Production change that would fail this: building an origin whenever GH_TOKEN happens to be set, which
    would make a local rehearsal try to fetch."""
    seen = []
    monkeypatch.setattr(intake_v2, "publish_verification", lambda v, **kw: seen.append(kw["origin"]) or {})
    monkeypatch.setattr(receipt_origin, "trusted_origin", lambda *a, **kw: "THE-ORIGIN")
    path = tmp_path / "verification.json"
    path.write_text(json.dumps({"schema": "forshor.policy-verification/v1"}))
    base = ["--publish-from", str(path), "--login", "tester", "--pull", str(PULL), "--publisher-url", "http://x",
            "--head-sha", HEAD, "--run-id", RUN, "--workdir", str(tmp_path / "w")]

    intake_v2.main(base)
    assert seen == [None], "no flag, no origin: the receipt route refuses a qualified readout as before"
    intake_v2.main(base + ["--trusted-origin"])
    assert seen == [None, "THE-ORIGIN"]


def test_the_origin_flag_belongs_to_the_receipt_route_only(tmp_path):
    """`--trusted-origin` establishes where a receipt came from, so it is meaningless without one. A misconfigured
    caller is told so rather than silently publishing with no origin."""
    with pytest.raises(SystemExit):
        intake_v2.main(["--trusted-origin", "--policy", str(tmp_path / "p.json"), "--login", "tester",
                        "--tree", str(tmp_path), "--no-publish"])


# ---------------------------------------------------------------- what the archive records about a PUBLISHED result


@pytest.mark.slow
def test_a_published_measured_submission_is_archived_as_the_measured_result(measured_source, publisher, tmp_path):
    """OWNER RULING 2026-11-24 (ruling 1), end to end on the receipt the verifier job really wrote.

    `publish_verification` called `archive_write(v)` with NO result kind, so `archive_policy.archive_policy` fell
    back to its own defaults (`readout="coherent"`, no primitive, no design) and this -- a published MEASURED
    submission, the only kind this flow produces -- was archived as a LEGACY entry. `entry_kind` reads a legacy entry
    as the coherent readout, so the one durable record of the result said the wrong thing, and
    `plan_readout_variant` compared this submission against the default it is not: the participant's own coherent
    copy of the same policy was a REPEAT of it rather than the variant pair lane WA built the planner for.

    The production change that fails this test is reverting the kind arguments at `intake_v2.py`'s `archive_write`
    call: the entry then carries neither `readout` nor `boards` and the first assertion below reads `coherent`.

    The whole chain is exercised, not a spy's argument list: the receipt goes through `publish_verification`, the
    spy forwards exactly what it was handed to the real `archive_policy.write_policy`, and the assertions read the
    index entry that lands on disk."""
    import archive_policy

    job = verifier_job(tmp_path, measured_source)
    assert job.receipt is not None, job.log
    receipt = stamped(job.receipt)

    checkout = tmp_path / "archive"
    (checkout / archive_policy.INDEX_PATH).parent.mkdir(parents=True)
    handed = {}

    def write(v, **kind):
        handed.update(kind)
        archive_policy.write_policy(checkout, v, login="tester", pull=PULL, head_sha=HEAD, run_id=RUN,
                                    source=v["leanSource"], **kind)
        return {"path": f"{archive_policy.DIR}/{HEAD}", "commit": "c" * 40, "added": True}

    result = intake_v2.publish_verification(receipt, login="tester", pull=PULL, url="http://x", token="t",
                                            workdir=tmp_path / "publish", head_sha=HEAD, run_id=RUN,
                                            origin=origin_for(receipt), archive_write=write, log=lambda s: None)
    assert result["published"]

    entry = next(p for p in archive_policy.read_index(checkout)["policies"] if p["policy_id"] == HEAD)
    assert entry["readout"] == "semiclassical", "the archive records the readout that was actually published"
    assert archive_policy.entry_kind(entry)["readout"] == "semiclassical"
    assert entry["boards"] == ["pp", "full"], "a first copy of the policy is its own primitive, on both its boards"
    # The other two axes, as this flow really supplies them. `design` is None because this document names no
    # full-run selection; `primitive` is None because the primitive route never reaches the archive at all
    # (ruling 3), and the refusal that guarantees it is pinned in test_primitive_gate.py.
    assert handed == {"readout": "semiclassical", "primitive": None, "design": None}
    assert "design" not in entry and "primitive" not in entry and "tKind" not in entry
    assert not [k for k in archive_policy.BARE_PRICE_KEYS if k in entry], "never the number"


# ---------------------------------------------------------------- the ARCHIVE-REUSE branch (assessment, not a fix)


@pytest.mark.slow
def test_the_archive_reuse_branch_fails_CLOSED_for_a_measured_receipt_and_cannot_publish_one(
        measured_source, publisher, tmp_path):
    """THE ASSESSMENT XH ASKED FOR, recorded. A measured receipt CAN reach the archive-reuse branch -- nothing on it
    refuses one (`scripts/test_policy_publication.js`: "a MEASURED verification reaches the archive-reuse branch").
    On that branch `publication.js:119` sets download=false and `policy_publication.prepare` rebuilds the
    verification from `submission-results`, carrying the ORIGINAL run's stamp, so the origin check asks GitHub for
    the artifact of a run whose artifact is precisely the thing that is no longer being downloaded.

    THE VERDICT IS NOT A SECURITY HOLE. The branch fails CLOSED: an absent artifact is `OriginUnavailable`, which the
    publisher reports as `Incomplete` at stage `publisher` -- never a refusal and never a pass, and nothing is sent.
    It IS a liveness hole and it is ESCALATED, not fixed here: the authority on that branch is the archive, which only
    the trusted publish job writes, not an artifact, so re-fetching is asking the wrong question there. Choosing what
    establishes origin from the archive is a design decision, not a defect with a simple resolution.
    """
    job = verifier_job(tmp_path, measured_source)
    reused = stamped(job.receipt, runId="111", runAttempt=1)        # the archive's own, older, stamp

    def expired(name, run_id, run_attempt):
        raise OriginUnavailable(f"the trusted run holds no unexpired artifact named {name}")

    with pytest.raises(intake_v2.Incomplete) as stop:
        intake_v2.publish_verification(reused, login="tester", pull=PULL, url="http://x", token="t",
                                       workdir=tmp_path / "reuse", head_sha=HEAD, run_id="111",
                                       origin=origin_for(reused, fetch=expired), log=lambda s: None)
    assert stop.value.stage == "publisher" and "no unexpired artifact" in str(stop.value)
    assert "table-generation-submission-111-1" in str(stop.value), "the OLD run and attempt, from the archive's stamp"
    assert publisher.sent == [], "never a pass"

    # And while the artifact does still survive, the rebuilt receipt and the artifact agree, so the branch publishes
    # normally: what it turns on is artifact retention, not evidence.
    ok = intake_v2.publish_verification(reused, login="tester", pull=PULL, url="http://x", token="t",
                                        workdir=tmp_path / "reuse-ok", head_sha=HEAD, run_id="111",
                                        origin=origin_for(reused), log=lambda s: None)
    assert ok["published"]


def test_the_production_publish_job_passes_the_flag(monkeypatch):
    """The last link: `scripts/ci/publish_policy.py` is the production caller, and it is where the TRUSTED workflow
    supplies the origin. Pinned here as well as in `scripts/ci/test_publish_policy.py`, because this is the file that
    explains why the whole flow works."""
    source = (REPO / "scripts/ci/publish_policy.py").read_text()
    assert '"--trusted-origin"' in source
    assert '"--publish-from"' in source and '"--head-sha", metadata["headSha"]' in source
