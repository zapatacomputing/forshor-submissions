"""Trusted origin for a verification receipt this process did not produce itself.

`intake_v2.publish_verification` prices a receipt that another job wrote (`--publish-from`, the production route). The
standing rule is that hosted intake takes its inputs from the trusted GitHub run and the publisher, NEVER from a
receipt the submitter supplies (`request.rehearse_qualified_request`'s docstring), so a receipt authorised nothing by
itself and a qualified measured readout was refused outright on this route -- publishable only from a single trusted
run that had just verified it.

THE PROOF ALREADY EXISTS AND WAS DISCARDED. `scripts/ci/publication.js` binds the downloaded artifact to the exact
source run, attempt, head commit and pull request (`inspect`, and `archive`'s
`result.metadata` comparison), and `scripts/ci/policy_publication.js:96-98` then stamps

    verification.ciVerification = {...identity, runId, runAttempt, pullRequest}

into the very file handed to `--publish-from`, where `identity` is `scripts/ci/verification_identity.py`'s
`{schema, key, headSha, fingerprint}`. Nothing under `evaluation/` ever read that stamp.

READING IT IS NOT ENOUGH. A stamp is a claim ABOUT a receipt, not a property OF one: the fields are public, so a
submitter can copy a valid stamp onto a receipt of their own. Two things turn it into a proof, and neither is the
receipt's say-so:

  * the publisher RE-DERIVES the verification identity from ITS OWN checkout, so a head commit, fingerprint or key
    that the receipt merely asserts cannot pass; and
  * the publisher RE-FETCHES the artifact the stamp names -- the recorded run and attempt, under the artifact name
    `publication.js` assigns -- and requires the receipt's body to BE that artifact.

WHAT THAT PROVES, AND WHAT IT DOES NOT. It proves the receipt is byte for byte the artifact the named run uploaded, so a
swapped, edited or foreign receipt is refused. It does not prove the artifact's content is true: on the fresh path the
stamp, the identity and the artifact all come from the same trusted publish job, so the comparison fails only if that job's
`prepare` step disagrees with itself. What the content has to satisfy is ruled on separately, by the gates in
`intake_v2.publish_verification` (a measured receipt's Lean file must declare the semiclassical readout, the export must be
the one the file declares, and so on); the origin proof says where the receipt came from, not that it is right.

This is `archive_policy.write_policy`'s principle applied one level up. That gate compares the verification's
`leanSha256` AND `leanSource` against the file at the pull request's head commit rather than believing either of them;
the hosted path applies exactly that agreement check because the gates were otherwise ruling on text that
need not be the submission. Here the whole receipt is compared against the artifact of the recorded run and attempt.

A fetch that does not answer is not a verdict. `OriginUnavailable` is a resource stop the caller reports as
`Incomplete` at stage `publisher` -- already in `scripts/ci/notify_submitters.js`'s `LIMIT_STAGES`, so a submitter is
told "publishing did not finish", never that their policy was rejected.
"""
from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

#: The fields `scripts/ci/verification_identity.py:identity` produces. Every one is re-derived by the publisher and
#: compared; the receipt supplies none of them.
IDENTITY_FIELDS = ("schema", "key", "headSha", "fingerprint")

#: Where the trusted half records the proof (`scripts/ci/policy_publication.js:98`).
STAMP = "ciVerification"

#: How `scripts/ci/publication.js:108` names the verifier job's artifact. The ATTEMPT is part of the name, so asking
#: for this exact name is what pins the attempt: GitHub's artifact list is per run, not per attempt.
ARTIFACT = "table-generation-submission-{run_id}-{run_attempt}"

#: The file inside that artifact which `--publish-from` is pointed at (`publish_policy.py:39`).
MEMBER = "verification.json"

#: A receipt carries the submitted Lean source (at most 1 MB), the mirror envelope and the compiled document. A
#: full-run design's compiled document is large: a catalytic-design receipt measured 9.6 MB, over the earlier eight
#: mebibytes, and was reported as an incomplete publish. Thirty-two mebibytes is above any real receipt and still
#: bounded (the wire bound below is the same size), so a broken artifact cannot exhaust the publisher.
MAX_MEMBER_BYTES = 32 * 1024 * 1024

#: The same bound one level down, on the bytes that come off the wire, so a hostile or broken response cannot exhaust
#: the publisher before `read_member` ever gets to bound the member it unpacks.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

REPOSITORY = "zapatacomputing/forshor-submissions"


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the publish job's `GH_TOKEN` out of a redirect it did not intend, in the two ways stock urllib allows it.

    Carried across from the superseded `ebf21b0`, the one control that branch had and this transport
    did not. It is not hypothetical hardening -- both holes are in CPython 3.12's own defaults:

      * `HTTPRedirectHandler.redirect_request` copies every header except `content-length`/`content-type` onto the
        new request, so `Authorization: Bearer <GH_TOKEN>` is carried to WHATEVER host the redirect names; and
      * `http_error_302`'s scheme allowlist is `('http', 'https', 'ftp', '')`, so a redirect may DOWNGRADE off TLS
        and send that header in cleartext.

    So a 302 from the API to another host would hand the trusted publish job's credential away. The token is the one
    input here that is neither re-derived nor compared, so losing it is not something a later comparison can catch.
    Refusing the downgrade outright (returning None) and dropping the header on any cross-host hop closes both.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            return None                                   # never downgrade off TLS
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (urllib.parse.urlsplit(newurl).hostname
                                != urllib.parse.urlsplit(req.full_url).hostname):
            new.remove_header("Authorization")
        return new


class OriginUnavailable(RuntimeError):
    """The trusted artifact could not be fetched (transport, timeout, expiry). NOT a verdict on the submission."""


class OriginMismatch(ValueError):
    """The receipt is not what the trusted origin holds. A verdict: the submission does not qualify on this route."""


def artifact_name(run_id: Any, run_attempt: Any) -> str:
    return ARTIFACT.format(run_id=run_id, run_attempt=run_attempt)


def _body(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """The receipt as the verifier job wrote it: everything except the stamp the trusted half added afterwards."""
    return {key: value for key, value in receipt.items() if key != STAMP}


def _differences(receipt_body: Dict[str, Any], fetched: Dict[str, Any]) -> Iterable[str]:
    for key in sorted(set(receipt_body) | set(fetched)):
        if key not in fetched:
            yield f"{key} (not in the artifact)"
        elif key not in receipt_body:
            yield f"{key} (only in the artifact)"
        elif receipt_body[key] != fetched[key]:
            yield key


def read_member(blob: bytes, member: str = MEMBER) -> bytes:
    """`member` out of an artifact zip, with its uncompressed size bounded before anything is read."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            info = archive.getinfo(member)
            if info.file_size > MAX_MEMBER_BYTES:
                raise OriginUnavailable(f"the trusted artifact's {member} is larger than {MAX_MEMBER_BYTES} bytes")
            with archive.open(info) as handle:
                return handle.read(MAX_MEMBER_BYTES + 1)[:MAX_MEMBER_BYTES]
    except (KeyError, zipfile.BadZipFile, OSError) as error:
        raise OriginUnavailable(f"the trusted artifact does not hold a readable {member}: {error}") from error


def github_fetcher(token: str, *, opener: Callable[[str, str], bytes], repository: str = REPOSITORY,
                   timeout: int = 30) -> Callable[[str, Any, Any], bytes]:
    """A fetcher over GitHub's artifact API. `opener(url, accept) -> bytes` is the transport, so every decision this
    makes is testable without a network: which artifact the name selects, that an expired one is not it, and that the
    zip yields `verification.json`.

    UNPROVEN HERE: the transport itself. Wiring a real opener into the publish job is gate B4's half.
    """
    def fetch(name: str, run_id: Any, run_attempt: Any) -> bytes:
        del run_attempt                                   # the attempt is in `name`; the listing is per run
        try:
            listing = json.loads(opener(
                f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
                "application/vnd.github+json"))
            artifacts = [a for a in listing["artifacts"] if a.get("name") == name and not a.get("expired")]
            if not artifacts:
                raise OriginUnavailable(f"the trusted run holds no unexpired artifact named {name}")
            if len(artifacts) != 1:
                raise OriginUnavailable(f"the trusted run holds {len(artifacts)} artifacts named {name}")
            blob = opener(f"https://api.github.com/repos/{repository}/actions/artifacts/{artifacts[0]['id']}/zip",
                          "application/vnd.github+json")
        except OriginUnavailable:
            raise
        except Exception as error:                        # noqa: BLE001  transport, JSON shape, rate limits
            raise OriginUnavailable(f"the trusted artifact could not be fetched: {type(error).__name__}: {error}") from error
        return read_member(blob)

    del timeout                                           # the opener owns its own timeout
    return fetch


def urllib_opener(token: str, *, timeout: int = 30, urlopen: Optional[Callable[..., Any]] = None,
                  build: Optional[Callable[..., Any]] = None) -> Callable[[str, str], bytes]:
    """`github_fetcher`'s transport, over urllib. The token travels in the Authorization header only -- never in the
    URL, never in argv -- which is the same rule `archive_policy._env` and `publish.refresh_archive` follow.

    `urlopen`/`build` are the two urllib calls, injected so everything this decides is provable without a network:
    that the token is a header and not part of the URL, that the response is read under `MAX_RESPONSE_BYTES`, and
    that a transport failure reaches `github_fetcher` as a failure (which it turns into `OriginUnavailable`, never a
    verdict). WHAT IS STILL UNPROVEN AND IS GATE B4: a real HTTPS request to api.github.com. No test makes one.

    The URL must be HTTPS and redirects go through `_StripAuthOnRedirect`, so the token cannot leave TLS or reach
    another host (carried across from the superseded `ebf21b0`). The default transport is therefore
    a built opener rather than bare `urlopen`; injecting `urlopen` replaces it, which is what the offline tests do.
    """
    request_for = build or urllib.request.Request
    send = urlopen or urllib.request.build_opener(_StripAuthOnRedirect).open

    def open_url(url: str, accept: str) -> bytes:
        if urllib.parse.urlsplit(url).scheme != "https":
            # Refused before the request is built, so the header carrying the token is never constructed for a URL
            # that would not protect it. A transport problem, not a verdict on the submission.
            raise OriginUnavailable(f"refusing a non-HTTPS artifact URL: {url!r}")
        request = request_for(url, headers={"Authorization": f"Bearer {token}", "Accept": accept,
                                            "X-GitHub-Api-Version": "2022-11-28"})
        with send(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise OriginUnavailable(f"the trusted artifact response is larger than {MAX_RESPONSE_BYTES} bytes")
        return body

    return open_url


def checkout_identity(head_sha: str, root: Optional[Any] = None) -> Dict[str, Any]:
    """The verification identity of THIS checkout at `head_sha`, re-derived by the very function the trusted stamp was
    made with (`scripts/ci/verification_identity.py:identity`).

    It must be that function and not a second spelling of it: the stamp records a fingerprint of the `INPUTS` tree, so
    a reimplementation that drifted by one path would make every genuine receipt fail to match, and the gate would be
    unfalsifiable rather than strict.
    """
    import sys
    repo = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    ci = str(repo / "scripts" / "ci")
    if ci not in sys.path:
        sys.path.insert(0, ci)
    from verification_identity import identity           # the trusted half's own definition
    return identity(repo, head_sha)


class ReceiptOrigin:
    """Where the publisher independently obtains what a receipt claims.

    `identity(head_sha) -> {schema, key, headSha, fingerprint}` re-derives the verification identity from the
    publisher's own checkout (`scripts/ci/verification_identity.py:identity`).
    `fetch(name, run_id, run_attempt) -> bytes` returns the trusted artifact's `verification.json`.
    Both are injected so the rule can be proved offline against fakes.
    """

    def __init__(self, *, identity: Callable[[str], Dict[str, Any]], fetch: Callable[[str, Any, Any], bytes]):
        self.identity, self.fetch = identity, fetch


def trusted_origin(root: Optional[Any] = None, *, token: Optional[Callable[[], str]] = None,
                   repository: str = REPOSITORY, timeout: int = 30) -> ReceiptOrigin:
    """The production `ReceiptOrigin`: the identity re-derived from the checkout at `root`, the artifact fetched from
    GitHub. This is the injection point `scripts/ci/publish_policy.py` wires in, through `intake_v2 --trusted-origin`.

    `token()` is called only when a fetch actually happens. That is not a convenience: the coherent path never reaches
    the origin check, so an ordinary publication must not need a token to exist at all -- and a test can pass a
    `token` that fails, to prove the path was never taken.
    """
    read_token = token if token is not None else (lambda: os.environ["GH_TOKEN"])

    def fetch(name: str, run_id: Any, run_attempt: Any) -> bytes:
        secret = read_token()
        return github_fetcher(secret, opener=urllib_opener(secret, timeout=timeout), repository=repository,
                              timeout=timeout)(name, run_id, run_attempt)

    return ReceiptOrigin(identity=lambda head: checkout_identity(head, root), fetch=fetch)


def establish(receipt: Dict[str, Any], *, head_sha: Optional[str], run_id: Optional[str], pull: int,
              origin: ReceiptOrigin) -> Dict[str, Any]:
    """Prove that `receipt` IS the trusted verifier job's artifact for this run, attempt, head and pull request.

    Returns the proof to record. Raises `OriginMismatch` (a refusal, naming what disagreed) or `OriginUnavailable`
    (a resource stop, never a verdict). `head_sha`, `run_id` and `pull` are the TRUSTED workflow's own values, which
    `scripts/ci/publish_policy.py` takes from `evaluation-metadata.json`, not from the receipt.
    """
    if not head_sha or not run_id:
        raise OriginMismatch("establishing a receipt's trusted origin needs the run's own --head-sha and --run-id")
    stamp = receipt.get(STAMP)
    if not isinstance(stamp, dict):
        raise OriginMismatch("the receipt carries no trusted verification receipt (ciVerification): it did not come "
                             "from the trusted publication job")
    expected = origin.identity(head_sha)                   # re-derived HERE, from the publisher's own checkout
    for field in IDENTITY_FIELDS:
        if stamp.get(field) != expected.get(field):
            raise OriginMismatch(f"the receipt's verification {field} is not this checkout's "
                                 f"({stamp.get(field)!r} against {expected.get(field)!r})")
    if str(stamp.get("runId")) != str(run_id):
        raise OriginMismatch(f"the receipt names run {stamp.get('runId')!r}, not this publication's run {run_id!r}")
    attempt = stamp.get("runAttempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise OriginMismatch(f"the receipt names no positive run attempt ({attempt!r})")
    if stamp.get("pullRequest") != pull:
        raise OriginMismatch(f"the receipt names pull request {stamp.get('pullRequest')!r}, not {pull!r}")

    name = artifact_name(run_id, attempt)
    try:
        raw = origin.fetch(name, run_id, attempt)
    except OriginUnavailable:
        raise
    except Exception as error:                            # noqa: BLE001  a fetcher failure is never a verdict
        raise OriginUnavailable(f"the trusted artifact {name} could not be fetched: "
                                f"{type(error).__name__}: {error}") from error
    try:
        fetched = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise OriginUnavailable(f"the trusted artifact {name} is not readable JSON: {error}") from error
    if not isinstance(fetched, dict):
        raise OriginUnavailable(f"the trusted artifact {name} does not hold a verification object")

    body = _body(receipt)
    differing = list(_differences(body, fetched))
    if differing:
        raise OriginMismatch(f"the receipt is not the artifact {name} the trusted run produced; it disagrees on "
                             f"{', '.join(differing[:6])}")
    from .canon import sha256_hex
    return {"schema": stamp["schema"], "artifact": name, "runId": str(run_id), "runAttempt": attempt,
            "pullRequest": pull, "headSha": expected["headSha"], "fingerprint": expected["fingerprint"],
            "key": expected["key"], "receiptSha256": sha256_hex(fetched)}
