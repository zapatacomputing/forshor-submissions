"""Trusted v2 publishing entrypoint; source identity comes from publication.js."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "evaluation"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    from publish import request_json
    from publish_v2 import already_published
    metadata = json.loads(Path("evaluation-metadata.json").read_text())
    if metadata.get("kind") != "policy":
        raise ValueError("Missing trusted policy publication metadata")
    url = os.environ["PUBLISHER_URL"].rstrip("/")
    if args.check:
        state = request_json(url + "/v2/state", os.environ["PUBLISHER_TOKEN"])
        metadata["epoch"] = state["epoch"]
        Path("evaluation-metadata.json").write_text(json.dumps(metadata))
        done = already_published(state, metadata)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"complete={'true' if done else 'false'}\n")
        if done:
            Path("publication-result.json").write_text(json.dumps({"schema": "forshor.publication/v2", "kind": "policy",
                "alreadyPublished": True, "evaluationKey": metadata["evaluationKey"], "policy": {
                    "headSha": metadata["headSha"], "login": metadata["githubLogin"], "pull": metadata["pullRequest"],
                    "digest": metadata["policyDigest"]}}))
        return 0
    # `--trusted-origin` is where the trusted workflow supplies the origin the receipt cannot supply for itself: the
    # publisher re-derives this checkout's verification identity and re-fetches the artifact the receipt's
    # ciVerification stamp names, and the COMPARISON is what authorises. It is unconditional because it is inert for
    # every ordinary submission -- only a receipt carrying a qualified readout reaches the check at all -- and because
    # the alternative, deciding here whether the receipt is measured, would be trusting the receipt to say so.
    command = [os.environ["ENGINE_PYTHON"], str(REPO / "evaluation/intake_v2.py"),
               "--publish-from", "artifacts/verification.json", "--login", metadata["githubLogin"],
               "--pull", str(metadata["pullRequest"]), "--head-sha", metadata["headSha"], "--run-id", metadata["runId"],
               "--trusted-origin",
               "--publisher-url", url, "--archive", "archive", "--workdir", "policy-publication-work", "--out", "publication-result.json"]
    if metadata.get("organizerBaseline"):
        command.extend(["--organizer-baseline", metadata["organizerBaseline"]])
    return subprocess.run(command, cwd=REPO).returncode


if __name__ == "__main__":
    raise SystemExit(main())
