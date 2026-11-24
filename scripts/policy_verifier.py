#!/usr/bin/env python3
"""The verifier for a policy submission: `TableGeneration/Submission/Policy.lean` holding a ForShor policy (PhaseProduct or PhaseTripleProduct).

`scripts/verifier.py` hands over to this module when the submitted `Policy.lean` declares `namespace PolicySubmission` or `namespace PTPPolicySubmission`; any other file is
the older table-generator submission and goes through the older checks, unchanged. It is trusted code: it runs from the base checkout (`./forshor check` and the pull-request
job both run it from a checkout of the base commit with the submitted file copied in) and it never executes anything the submitter wrote, only compiles it with Lean.

What it does (all of it in `evaluation/intake_v2.py --no-publish`, which this file calls):

  * only `TableGeneration/Submission/Policy.lean` and the optional `TableGeneration/Submission/summary.tex` may change (no helper files; a summary alone is not a
    submission). The summary must be a regular file, UTF-8, at most 100 KiB, with no control characters other than tab/LF/CR (`evaluation/summary_tex.py` is the one
    definition). It is never compiled or executed: it is read as bytes, carried in `verification.json` (text and sha256) and archived verbatim; it cannot change the verdict;
  * the file is checked by ForShor's lexical gate, built by ForShor (`PolicySubmission` or `PTPPolicySubmission`), and the document ForShor prints is the policy;
  * the policy is checked by the STO mirror at exact widths (and, for PhaseProduct, ForShor's printed document must price to the same T as the policy);
  * nothing is priced and nothing is published: the output is `verification.json`, which the trusted publish step (`intake_v2.py --publish-from`) prices and publishes.

It writes `table-generation-submission-result.json` (`status`, `phase: "full"`, the checks, the policy's digest) and `table-generation-summary.md` in the output directory, in the
shape `./forshor check` reads, plus `verification.json`, `intake-result.json` and `policy-verifier.log`.

Environment: FORSHOR_TREE (a built ForShor checkout at or after 349849e; required), STO_TREE (a built STO tree at the epoch's pins; default: the repository itself),
ENGINE_PYTHON (a Python with `evaluation/engine/requirements.txt`; default: this interpreter), TABLE_GEN_LOGIN (the submitter's handle for the reserved-handle check; default:
`local-check`), POLICY_STAGE_TIMEOUT and POLICY_MIRROR_TIMEOUT (seconds).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SUBMISSION = "TableGeneration/Submission/Policy.lean"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evaluation"))
import summary_tex  # noqa: E402  (the shared rules for the optional summary file)
from export_v2.attempt import AttemptBusy, exclusive_attempt  # noqa: E402

SUMMARY = summary_tex.SUMMARY_PATH
CHALLENGE = "forshor-policy"

_NAMESPACES = (re.compile(r"^\s*namespace\s+PolicySubmission\b", re.M), re.compile(r"^\s*namespace\s+PTPPolicySubmission\b", re.M))


def is_policy_file(text: str) -> bool:
    """True when the file declares one of the two policy namespaces: the new format. (The older `Policy.lean` declares `TableGeneration.Submission.Policy`.)"""
    return any(p.search(text) for p in _NAMESPACES)


def applies(repo: Path) -> bool:
    path = Path(repo) / SUBMISSION
    return path.is_file() and is_policy_file(path.read_text(errors="replace"))


def submitted_readout(repo: Path) -> str:
    """Which readout design the submission in `repo` declares, so the build can be the one that design needs.

    The verifier job is the half that PRODUCES the receipt, and a measured receipt only exists if the build compiled
    the readout (`--qualify-readout`). Passing that flag unconditionally would change the ForShor build for every
    submission, so the flag is conditional on this answer -- and this answer comes from the submitted source, read
    lexically, before anything is built.

    The recogniser is `scripts/ci/policy_source.py:declared_readout`, the same module the CI source gate already
    applies to this file and the same one `evaluation/intake_v2.py` imports to classify the selector, so the verifier
    cannot disagree with either about what a line means. `ValueError` for an unrecognised declaration: the verifier
    then refuses rather than choosing a build for a file it cannot read.
    """
    sys.path.insert(0, str(HERE.parent))
    from scripts.ci.policy_source import declared_readout
    return declared_readout((Path(repo) / SUBMISSION).read_text(encoding="utf-8"))


def check(name: str, ok: bool, details: str) -> Dict[str, str]:
    return {"name": name, "status": "success" if ok else "failure", "details": details}


def changed_files_and_list(get_changed_files: Callable[[Path], Tuple[bool, List[str], str]], repo: Path) -> Tuple[Dict[str, str], List[str]]:
    """Only the submission file and its optional summary may change, and a summary alone is not a submission. An unavailable comparison is a failure, never a pass; a
    skipped one (a local run with no base) says so. Returns the check and the changed paths."""
    ok, files, detail = get_changed_files(repo)
    if not ok:
        return check("changed files", False, detail), []
    extra = sorted(f for f in files if f not in (SUBMISSION, SUMMARY))
    if extra:
        return check("changed files", False, f"only {SUBMISSION} (and the optional {SUMMARY}) may change; also changed: " + ", ".join(extra)), files
    if files and SUBMISSION not in files:
        return check("changed files", False, f"a submission needs a policy: {SUMMARY} changed but {SUBMISSION} did not. The summary goes with a policy, never alone."), files
    return check("changed files", True, detail if files else f"{detail} (no file list)"), files


def changed_files_check(get_changed_files: Callable[[Path], Tuple[bool, List[str], str]], repo: Path) -> Dict[str, str]:
    return changed_files_and_list(get_changed_files, repo)[0]


def summary_check(repo: Path, files: List[str]) -> Tuple[Dict[str, str], Optional[bytes]]:
    """The optional summary: absent is fine; present it must keep the rules of `evaluation/summary_tex.py`. The file is only ever read as bytes. Returns the check and the
    bytes (None when there is no file or it was refused); the caller reports the check only when there is a file or a refusal."""
    data, why = summary_tex.inspect_file(repo / SUMMARY)
    if why:
        return check("summary", False, why), None
    if data is None and SUMMARY in files:
        return check("summary", False, "summary.tex was deleted or renamed; a submission may only add it or edit it."), None
    if data is None:
        return check("summary", True, "no summary.tex (optional)"), None
    return check("summary", True, f"summary.tex: {len(data):,} bytes, sha256 {summary_tex.sha256(data)[:12]}; kept as text, never compiled or run"), data


def summary(result: Dict[str, Any]) -> str:
    lines = [f"# Policy submission verification", "", f"Status: **{result['status'].upper()}**", ""]
    if any(c["name"] == "memory" and c["status"] != "success" for c in result["checks"]):
        lines += ["**Stopped: memory limit reached.** This is not a verdict on your policy.",
                  "We're working towards supporting submissions that need more memory.", ""]
    if any(c["name"] == "timeout" and c["status"] != "success" for c in result["checks"]):
        lines += ["**Stopped: time limit reached.** This is not a verdict on your policy.", ""]
    pol = result.get("policy")
    if pol:
        lines += [f"Policy `{pol['digest'][:12]}`: {pol['mode']}, {pol['bands']} band(s). STO mirror: accepted. ForShor: "
                  + ("policy-verified (its printed document prices to the same T)" if pol.get("forshorPolicy") else "kernel-checked (no IR for this board)"), ""]
    for c in result["checks"]:
        lines.append(f"- **{c['name']}**: {c['status']}. {c['details']}")
    return "\n".join(lines) + "\n"


def run(repo: Path, out_dir: Path, *, get_changed_files: Callable[[Path], Tuple[bool, List[str], str]],
        env: Optional[Dict[str, str]] = None, intake: Optional[List[str]] = None, preflight: bool = False) -> int:
    """Verify the policy file in `repo`, write the artifacts, return 0 on success. `preflight`: the structural check only (which files changed), no Lean; the result's phase is
    then `preflight`. `intake` replaces the command for tests."""
    env = dict(os.environ if env is None else env)
    repo, out_dir = Path(repo).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        # The wrapper owns cleanup and artifact reads; intake owns its own distinct locks.
        with exclusive_attempt(out_dir / ".policy-verifier.lock") as lock_fd:
            return _run(repo, out_dir, get_changed_files, env, intake, preflight, lock_fd)
    except AttemptBusy as error:
        print(f"policy verifier: {error}", file=sys.stderr)
        return 2


def _run(repo, out_dir, get_changed_files, env, intake, preflight, lock_fd):
    result_path = out_dir / "intake-result.json"
    receipt_path = out_dir / "verification.json"
    # Local checks reuse the output directory; only this invocation's artifacts count.
    result_path.unlink(missing_ok=True)
    receipt_path.unlink(missing_ok=True)
    first, files = changed_files_and_list(get_changed_files, repo)
    checks: List[Dict[str, str]] = [first]
    policy = None
    summary_data: Optional[bytes] = None
    if first["status"] == "success":
        found, summary_data = summary_check(repo, files)
        if summary_data is not None or found["status"] != "success":
            checks.append(found)
    if all(c["status"] == "success" for c in checks) and not preflight:
        forshor = env.get("FORSHOR_TREE")
        sto = env.get("STO_TREE") or str(repo)
        readout, unreadable = None, None
        try:
            readout = submitted_readout(repo)
        except ValueError as error:
            unreadable = str(error)
        except OSError as error:
            unreadable = f"the submitted {SUBMISSION} could not be read: {error}"
        if not forshor or not Path(forshor).is_dir():
            checks.append(check("trees", False, "FORSHOR_TREE must name a built ForShor checkout (the trusted environment is not prepared)"))
        elif unreadable is not None:
            # An unrecognised `submittedDesign` has no build: the coherent build would ignore a declaration the
            # participant wrote, and the measured build would compile one they did not write. Refuse, before Lean.
            checks.append(check("policy-source", False, unreadable))
        else:
            cmd = intake or [env.get("ENGINE_PYTHON") or sys.executable, str(HERE.parent / "evaluation" / "intake_v2.py")]
            cmd = cmd + ["--lean-policy", str(repo / SUBMISSION), "--login", env.get("TABLE_GEN_LOGIN") or "local-check", "--no-publish", "--tree", sto, "--forshor-tree", forshor,
                         "--workdir", str(out_dir / "work"), "--verification-out", str(receipt_path), "--out", str(result_path)]
            if readout == "semiclassical":
                # The ONE conditional flag, and the one that makes the two-job flow able to carry a measured result:
                # without it the build is coherent and the receipt has no `localReadoutQualification`, so the
                # publisher has nothing to establish an origin for. A coherent submission adds nothing here and its
                # command is byte-identical to the one this job has always run.
                cmd += ["--qualify-readout"]
            if summary_data is not None:
                cmd += ["--summary", str(repo / SUMMARY)]
            if env.get("POLICY_STAGE_TIMEOUT"):
                cmd += ["--stage-timeout", env["POLICY_STAGE_TIMEOUT"]]
            if env.get("POLICY_MIRROR_TIMEOUT"):
                cmd += ["--mirror-timeout", env["POLICY_MIRROR_TIMEOUT"]]
            try:
                with open(out_dir / "policy-verifier.log", "w") as log:
                    proc = subprocess.run(cmd, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, pass_fds=(lock_fd,))
            except OSError as error:
                got = {"failed": True, "error": f"could not start intake: {error}"}
            else:
                try:
                    got = json.loads(result_path.read_text())
                except (OSError, ValueError):
                    got = None
                if not isinstance(got, dict):
                    got = {"failed": True, "error": f"the intake wrote no usable result (exit {proc.returncode}); see policy-verifier.log"}
                elif got.get("verified") and (proc.returncode or not receipt_path.is_file() or not isinstance(got.get("policy"), dict)
                        or not all(key in got["policy"] for key in ("digest", "mode", "bands"))):
                    got = {"failed": True, "error": f"the intake reported success without a successful run and complete verification (exit {proc.returncode}); see policy-verifier.log"}
            carried = None
            if got.get("verified") and summary_data is not None:   # the verification must carry exactly the file that was checked, or it is not published
                try:
                    carried = json.loads(receipt_path.read_text()).get("summarySha256")
                except (OSError, ValueError, AttributeError):
                    carried = None
                if carried != summary_tex.sha256(summary_data):
                    got = {"failed": True, "error": "the verification does not carry the summary that was checked"}
            if got.get("verified"):
                policy = got["policy"]
                checks.append(check("policy", True, f"ForShor's gate and build, the STO mirror at exact widths: accepted ({policy['mode']}, {policy['bands']} band(s))"))
            elif got.get("refused"):
                checks.append(check(str(got.get("stage") or "refused"), False, str(got.get("reason") or "refused")[:900]))
            elif got.get("failed") and got.get("stage") in ("memory", "timeout"):
                checks.append(check(got["stage"], False, str(got.get("error") or "verification could not complete")[:900]))
            else:
                checks.append(check("verification", False, "the verification could not complete (a tool failed, not a verdict on the policy): " + str(got.get("error") or "")[:900]))
    ok = all(c["status"] == "success" for c in checks)
    if not ok:
        receipt_path.unlink(missing_ok=True)
    result = {"schema_version": 1, "challenge": CHALLENGE, "kind": "policy", "phase": "preflight" if preflight else "full", "status": "success" if ok else "failure", "checks": checks,
              "policy": policy, "verification": "verification.json" if ok and not preflight else None,
              "summary": {"sha256": summary_tex.sha256(summary_data), "bytes": len(summary_data)} if summary_data is not None else None}
    # Bind the same source/run fields as the older verifier. The trusted publisher
    # checks these against GitHub, never uses them to choose a PR or commit.
    result["metadata"] = {key: env.get(variable, "") for key, variable in {
        "repository": "GITHUB_REPOSITORY", "run_id": "GITHUB_RUN_ID", "run_attempt": "GITHUB_RUN_ATTEMPT",
        "pr_number": "TABLE_GEN_PR_NUMBER", "head_sha": "TABLE_GEN_HEAD_SHA", "base_sha": "TABLE_GEN_BASE_SHA",
    }.items()}
    (out_dir / "table-generation-submission-result.json").write_text(json.dumps(result, indent=2) + "\n")
    (out_dir / "table-generation-summary.md").write_text(summary(result))
    print(f"policy verifier: {result['status']}")
    return 0 if ok else 1
