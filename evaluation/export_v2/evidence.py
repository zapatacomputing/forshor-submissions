"""Policy-level evidence, converted from harness records into the contract's `Verification`.

Rules (the publisher's validators enforce the same on the other side, so breaking one here is rejected there, not ignored):
  * evidence is for ONE policy (its digest), so a record can never vouch for another;
  * a record counts only if the harness accepted it, at exact widths, at the model's width, for the same bands, on the exact
    policy file the envelope names;
  * evidence is valid only for the toolchain it ran on, and an epoch fixes the toolchain: the Lean version, the STO base and
    the ForShor pin must equal the model's. Evidence from any other tree is reported, never silently used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .attempt import exclusive_attempt
from .canon import policy_digest, sha256_hex

SCHEMA = "forshor.mirror-evidence/v1"
HARNESS = {"pp": "check_banded_policy", "full": "check_banded_policy", "ptp": "check_ptp_policy"}
MIRROR_CLAIM = "policy-algebra-and-exact-widths"
FORSHOR_CLAIM = "forshor-policy-submission"

Result = Tuple[Optional[Dict[str, Any]], Optional[str]]


def tree_pins(tree: Path) -> Dict[str, Optional[str]]:
    """What a Lean tree says about itself: its toolchain, its ForShor pin, and the STO commit its provenance note (`PROVENANCE.md`) says it is based on."""
    lean = (tree / "lean-toolchain").read_text().strip() if (tree / "lean-toolchain").exists() else None
    pin = None
    manifest = tree / "lake-manifest.json"
    if manifest.exists():
        for package in json.loads(manifest.read_text()).get("packages", []):
            if package.get("name") in ("Fast_multiplication", "ForShor"):
                pin = package.get("rev")
    base = None
    notes = tree / "PROVENANCE.md"
    if notes.exists():
        m = re.search(r"upstream `main` at `([0-9a-f]{7,40})`", notes.read_text())
        base = m.group(1) if m else None
    return {"lean": lean, "forshorPin": pin, "stoBase": base}


def _ran(pins: Dict[str, Optional[str]], model: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    sto = model["sto"]["commit"]
    if not pins.get("stoBase") or not sto.startswith(pins["stoBase"]):
        return None, f"the tree's STO base ({pins.get('stoBase')}) is not the model's ({sto[:9]})"
    if pins.get("lean") != model["lean"]:
        return None, f"ran on {pins.get('lean')}, the epoch is {model['lean']}"
    if pins.get("forshorPin") != model["forshor"]["pin"]:
        return None, f"ran with ForShor {str(pins.get('forshorPin'))[:9]}, the epoch pins {model['forshor']['pin'][:9]}"
    return {"lean": pins["lean"], "stoCommit": sto, "forshorPin": pins["forshorPin"]}, None


def _policy_hash_problem(envelope_hash: Any, record_hash: Any) -> Optional[str]:
    if not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in (envelope_hash, record_hash)):
        return "missing or malformed policy-file SHA-256"
    if envelope_hash != record_hash:
        return "the record checked a different file than the envelope names"
    return None


def stomirror_from_envelope(envelope: Dict[str, Any], policy_json: Dict[str, Any], model: Dict[str, Any], board: str) -> Result:
    """The contract's `stoMirror` evidence for `policy_json`, or (None, why not)."""
    if envelope.get("schema") != SCHEMA:
        return None, "not a mirror-evidence envelope"
    if envelope.get("harness") != HARNESS[board]:
        return None, f"harness {envelope.get('harness')} is not the {board} board's ({HARNESS[board]})"
    if envelope.get("policyDigest") != policy_digest(policy_json):
        return None, "the evidence is for another policy"
    rec = envelope.get("record") or {}
    if rec.get("verdict") != "accepted":
        stage = (rec.get("rejection") or {}).get("stage")
        return None, f"the harness did not accept it ({rec.get('verdict')}, stage {stage})"
    # The two harnesses name the width rule differently: check_banded_policy writes `widths_rule`, check_ptp_policy writes `widths`.
    # (Found by running both for real: the first draft read only `widths_rule` and refused a genuinely accepted PTP record.)
    rule = rec.get("widths_rule", rec.get("widths"))
    if rule != "exact" or rec.get("target_width") != model["n"]:
        return None, "the record is not an exact-width check at the model's width"
    if rec.get("mode") != policy_json["mode"] or rec.get("bands") != [{"threshold": b["threshold"], "k": b["k"]} for b in policy_json["bands"]]:
        return None, "the record's mode or bands differ from the policy's"
    file = envelope.get("policyFile")
    why = _policy_hash_problem(file.get("sha256") if isinstance(file, dict) else None, rec.get("policy_sha256"))
    if why is not None:
        return None, why
    ran, why = _ran(envelope.get("tree") or {}, model)
    if ran is None:
        return None, why
    return {"harness": envelope["harness"], "widths": "exact", "verdict": "accepted", "recordSha256": sha256_hex(rec),
            "claim": MIRROR_CLAIM, "ran": ran}, None


def forshor_from_seed(rec: Dict[str, Any], model: Dict[str, Any]) -> Result:
    """The demo's ForShor evidence on a seed record. Its runs name the ForShor commit they ran on, so it counts only at the pin."""
    lean = rec.get("lean") or {}
    if lean.get("status") != "policy-verified" or not lean.get("policy_verified"):
        return None, lean.get("reason") or f"ForShor status is {lean.get('status')}"
    run = next((r for r in lean.get("runs", []) if r.get("status") == "lean-ok"), None)
    if run is None:
        return None, "no successful ForShor run"
    commit = (run.get("forshor") or {}).get("commit")
    if commit != model["forshor"]["pin"]:
        return None, f"verified at ForShor {str(commit)[:9]}, the epoch pins {model['forshor']['pin'][:9]}"
    return {"verified": True, "bandsAgree": bool(run.get("printed_policy_matches")), "irPriceAgrees": bool(run.get("conversion_agrees")),
            "claim": FORSHOR_CLAIM, "forshorCommit": commit}, None


def stomirror_from_seed(rec: Dict[str, Any], model: Dict[str, Any]) -> Result:
    """A seed's mirror record names a Lean toolchain and a tree hash only, not the STO and ForShor revisions the contract needs."""
    m = rec.get("sto_mirror") or {}
    if m.get("status") != "accepted":
        return None, f"mirror status is {m.get('status')}"
    if m.get("lean_toolchain") != model["lean"]:
        return None, f"ran on {m.get('lean_toolchain')}, the epoch is {model['lean']}"
    return None, "the record names a tree hash but not the STO and ForShor revisions it ran on"


class EvidenceStore:
    """Envelopes found in a directory, or only the envelope of the current attempt."""

    def __init__(self, directory: Optional[Path] = None, *, envelope: Optional[Dict[str, Any]] = None):
        self.by_digest: Dict[str, List[Dict[str, Any]]] = {}
        if isinstance(envelope, dict) and envelope.get("schema") == SCHEMA and isinstance(envelope.get("policyDigest"), str):
            self.by_digest[envelope["policyDigest"]] = [envelope]
        elif envelope is None and directory:
            for path in sorted(Path(directory).glob("*.json")):
                env = json.loads(path.read_text())
                if env.get("schema") == SCHEMA:
                    self.by_digest.setdefault(env["policyDigest"], []).append(env)

    def stomirror(self, policy_json: Dict[str, Any], model: Dict[str, Any], board: str) -> Result:
        envs = self.by_digest.get(policy_digest(policy_json), [])
        if not envs:
            return None, "no mirror evidence for this policy"
        reasons = []
        for env in envs:
            evidence, why = stomirror_from_envelope(env, policy_json, model, board)
            if evidence:
                return evidence, None
            reasons.append(why)
        return None, "; ".join(dict.fromkeys(r for r in reasons if r))


def run_mirror(policy_json: Dict[str, Any], board: str, tree: Path, out_dir: Path, *, cap_gb: float = 12.0,
               timeout: int = 7200, python: str = sys.executable, stage_timeout: Optional[int] = None,
               fast: bool = False) -> Dict[str, Any]:
    """Run the tree's own harness on a policy and wrap the result as an envelope. The harness needs a BUILT Lean tree at the
    epoch's pins, and takes minutes. Large PhaseTripleProduct certificates can exceed 10 GB on Linux. The shared 12 GB
    replay cap applies to local checks, hosted verification and any replay during publication. The
    policy is written first and the harness hashes that very file, which is what binds the record to the policy. ``stage_timeout`` is the
    harness's own per-stage limit (its default is 3600 s; a PhaseProduct policy with an 8-way top band ran past it in the report stage).
    ``fast`` passes `--fast` to either harness: it skips the interpreted report stage (hours for an 8-way band: the interpreter never memoises
    the closures a `State` is built from, so the cost doubles every ~10 operations) and lets the kernel certificate prove the ladder (see
    `check_banded_policy.py` and the fork's `check_ptp_policy.py`). The tree must have the option: trees from before it reject the argument."""
    tree, out_dir = Path(tree).resolve(), Path(out_dir).resolve()
    if not tree.is_dir():
        raise NotADirectoryError(tree)
    out_dir.mkdir(parents=True, exist_ok=True)
    # PTP generates source in the supplied tree; PP must not copy that tree mid-write.
    with exclusive_attempt(tree) as tree_fd, exclusive_attempt(out_dir / ".mirror.lock") as out_fd:
        return _run_mirror(policy_json, board, tree, out_dir, cap_gb, timeout, python, stage_timeout, fast, (tree_fd, out_fd))


def _run_mirror(policy_json, board, tree, out_dir, cap_gb, timeout, python, stage_timeout, fast, lock_fds):
    digest = policy_digest(policy_json)
    policy_file = out_dir / f"{digest[:16]}.policy.json"
    record_file = out_dir / f"{digest[:16]}.record.json"
    evidence_file = out_dir / f"{digest[:16]}.evidence.json"
    # This invocation owns these outputs. A crash must not leave an earlier pass.
    evidence_file.unlink(missing_ok=True)
    record_file.unlink(missing_ok=True)
    policy_bytes = json.dumps(policy_json, indent=1, ensure_ascii=False).encode("utf-8")
    policy_file.write_bytes(policy_bytes)
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    script = "check_ptp_policy.py" if board == "ptp" else "check_banded_policy.py"
    # The harness runs lake and `lean --run` underneath it, and when its own stage times out it leaves the interpreter running (full CPU,
    # and it blocks the next check). So it gets its own process group, and the whole group is swept when it ends, however it ends.
    # Only this group is touched: never another session's Lean.
    # Its output goes to a log file, never a pipe: an orphan inherits a pipe and would hold it open, so waiting on it would block until
    # the orphan exits.
    log_file = out_dir / f"{digest[:16]}.harness.log"
    with open(log_file, "w") as log:
        proc = subprocess.Popen([python, str(tree / "scripts" / script), str(policy_file), "--target-width", "2048", "--widths", "exact",
                                 "--cap-gb", str(cap_gb), "--out", str(record_file)] + (["--timeout", str(stage_timeout)] if stage_timeout else [])
                                + (["--fast"] if fast else []),
                                cwd=tree, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, pass_fds=lock_fds)
        timed_out = None
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            timed_out = error
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # timed out, or a leaked `lean --run` outlived the harness; an empty group is not found
            except ProcessLookupError:
                pass
            proc.wait()
    if timed_out:
        record_file.unlink(missing_ok=True)
        raise timed_out
    if not record_file.exists():
        raise RuntimeError(f"the harness wrote no record (exit {proc.returncode}): {log_file.read_text()[-400:]}")
    record = json.loads(record_file.read_text())
    if not isinstance(record, dict):
        raise RuntimeError("the harness did not write a usable record")
    if proc.returncode and record.get("verdict") == "accepted":
        record_file.unlink()
        raise RuntimeError(f"the harness returned an accepted record but exited {proc.returncode}")
    if record.get("verdict") == "accepted":
        why = ("the submitted policy file changed during the mirror attempt" if policy_file.read_bytes() != policy_bytes
               else _policy_hash_problem(policy_sha256, record.get("policy_sha256")))
        if why is not None:
            record_file.unlink()
            raise RuntimeError(why)
    envelope = {"schema": SCHEMA, "policyDigest": digest,
                 "policyFile": {"name": policy_file.name, "sha256": policy_sha256},
                "harness": HARNESS[board], "record": record, "tree": tree_pins(tree)}
    evidence_file.write_text(json.dumps(envelope, indent=1, ensure_ascii=False))
    return envelope
