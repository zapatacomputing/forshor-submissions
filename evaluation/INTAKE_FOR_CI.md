# Wiring `intake_v2.py` into GitHub CI (what the job has to do, and nothing more)

`intake_v2.py` is the whole of "a participant's policy JSON becomes a row on the boards". It was run end to end on a local publisher (a challenger-written policy, the Lean mirror,
the publisher, the page) before this was written; the workflow below only has to call it. Nothing here is wired into any workflow yet.

## The call

A submission is the Lean file `TableGeneration/Submission/Policy.lean` (ForShor's PolicyTemplate format, see the starter pack's `templates/Policy.lean`), optionally with a LaTeX summary, `TableGeneration/Submission/summary.tex` (`--summary`; text for people, never compiled or run: see `docs/POLICY_CI.md`). Those are the only files a PR may change:

```sh
python evaluation/intake_v2.py --lean-policy "$POLICY_LEAN" --login "$PR_AUTHOR" --pull "$PR_NUMBER" --head-sha "$PR_HEAD_SHA" --run-id "$GITHUB_RUN_ID" \
  --tree "$STO_TREE" --forshor-tree "$FORSHOR_TREE" --archive "$ARCHIVE_CHECKOUT" --publisher-url "$PUBLISHER_URL" \
  --workdir "$RUNNER_TEMP/intake" --stage-timeout 21600 --mirror-timeout 43200 --out result.json
```
with `PUBLISHER_TOKEN` in the environment (the identity token the publisher's IAM check wants) and `GH_TOKEN` for the archive fetch, as `publish_v2.py` already uses them.

| input | what it is |
|---|---|
| `$POLICY_LEAN` | the participant's `TableGeneration/Submission/Policy.lean` from the PR. It is only compiled by ForShor (after ForShor's lexical gate), never run by us. `--forshor-tree` is required with it. `--policy FILE.json` (site policy JSON: `mode` and `bands`) is the other input, for rehearsals and for a PhaseTripleProduct policy, which has no Lean template yet; give exactly one of the two. |
| `$SUMMARY_TEX` | optional: the PR's `TableGeneration/Submission/summary.tex`, with `--lean-policy` only. Checked against `evaluation/summary_tex.py` (regular UTF-8 file, 1 byte to 100 KiB, no control characters but tab/LF/CR), read as bytes, carried in the verification (`summarySource`, `summarySha256`, `summaryBytes`) and archived verbatim. It never reaches Lean, ForShor or the mirror and cannot change the digest. |
| `$PR_AUTHOR`, `$PR_NUMBER`, `$PR_HEAD_SHA` | from the PR event. `forshor` is a reserved handle and is refused. The row is named `<login>_sub<N>`, N assigned by the publisher's ledger. |
| `$STO_TREE` | a **built** Lean tree for the STO mirror: this repository (synced from the STO fork's `main` at `2558595`). Build it with the targets in `evaluation/policy-trees.json` (`sto_build`: `BandedPolicy`, `BridgeClosure`, `BandedPolicy.Plan`, `Instances.RefPP`, `PTPPlan.Plan`, `PTPCheck.AxiomAudit`); `lake build TableGeneration` alone does not build them. |
| `$FORSHOR_TREE` | a built ForShor checkout at `21e129a` (`lake build PolicySubmission forshor_policy_submission`). Copied per run (it is written into), so use a cache and a copy-on-write or a throwaway checkout. Optional: without it, no row has the `ForShor` tag. |
| `$ARCHIVE_CHECKOUT` | a checkout of the `submission-results` branch (a clone with an `origin` it may push to). With `--publish-from` the policy is archived there first (see "The archive"), then published. Omit only for a local rehearsal. |

The epoch's pins are the ones in this repo's `lake-manifest.json` / `lean-toolchain` and the publisher's `/v2/state` model; a publisher on a different epoch is refused before any Lean runs.

## What it does, and what comes back

Order: reserved handle and epoch check (before any compute) → normalise the policy → STO mirror at exact widths → ForShor's build and printed document (a PhaseProduct policy whose k are all at most 8, when
a ForShor tree is given; always for a Lean submission) → price once → publish through the recovery client (a 409 or a transport error re-reads the live state and refreshes only what depends on it).

| exit | meaning | `result.json` |
|---|---|---|
| 0 | published, or already published | the publisher's answer (`snapshotId`, ...) and `policy` (digest, whether the mirror and ForShor evidence were attached) |
| 1 | refused: the policy does not qualify | `{"refused": true, "stage": ..., "reason": ...}`; stages: `format`, a mirror stage (`threshold`, `report`, `cert`, ...), `forshor`; a reserved handle or epoch mismatch |
| 2 | the run could not complete (a tool failed, the harness wrote no record, the publisher is unreachable after retries) | `{"failed": true, "error": ...}`: re-run, it is not a verdict |

A stage that hit its time limit is exit 1 with a `report`/timeout reason: a resource limit, not a mathematical rejection, and it yields no evidence. Measured: most policies take seconds to minutes in the mirror; a
first band of k = 8 took about 2.4 hours in the report stage when that stage ran. Since `check_banded_policy.py --fast` (the intake passes it for both boards) the report stage is skipped and the same policy takes about 2 minutes (see `export_v2/README.md`, "`run-mirror --fast`"); the limits above still bound a tool that hangs.

## What the job must also do (not part of the script)

* Run only on the participant's PR with the trusted code from the base repository; the script runs Lean on the policy's data only, but the PR's files must never be executed.
* Cache the two Lean trees (they are several GB and take tens of minutes cold), and set `TMPDIR` to a directory the job controls: a 2.4-hour run once lost a file in the system temporary directory.
* Post `result.json` back on the PR (the CLI follows publication, `./forshor status`), and re-run on exit 2.

## The Lean trees for the submission check

Both boards are checked by ForShor, with the same lexical gate; the STO mirror checks the policy itself. Two trees:

* **The STO mirror's tree is this repository.** Its `TableGeneration/` is the STO fork's `main` at `2558595`, byte for byte (branch `sync/sto-fork-2558595`; `lake-manifest.json` and `lean-toolchain` were already identical), and so are the harness scripts (`scripts/check_ptp_policy.py` with `--fast`,
  `PTPPolicyReport.lean`, `ptp_exact_width.py`, `ptp_policy_json_to_lean.py`, `trusted/`), and `PROVENANCE.md` (the evidence records the tree's base from it). The older in-repo `TableGeneration/RecursiveCost/PTP/` (59 files and `PTP.lean`) is gone, as in the fork: the PTP proofs are ForShor's. Verified: the documented mirror targets build in the synced repo, and `scripts/verifier.py` with `STO_TREE` = the repo took both the PhaseProduct and the PhaseTripleProduct example to a successful full-phase result. Nothing private is cloned.
* **ForShor** is a public clone at `82e2274` (ForShor PR #85): `349849e` (PR #83: the pin `21e129a` plus documentation and the PhaseTripleProduct submission files) plus a smaller lookup budget for the run-time IR extraction, retried once at the former budget if anything fails. The epoch's pin stays `21e129a`, because the added files change no theorem or definition the pricing or the evidence depends on: on every board policy the printed document is byte-identical to `349849e`'s.

Measured: a PhaseTripleProduct file with bands k = 8 and k = 4 went from the Lean file to a published row in 2.5 minutes (gate, ForShor build and kernel check about 25 s, mirror with `--fast` about a minute);
a PhaseProduct k = 3 file in 4 minutes; a PhaseProduct k = 8, 4, 3 file builds in ForShor in 40 s and prints in about 5.5 minutes.

## Open

* A PhaseTripleProduct row carries no `forshorPolicy` record: ForShor has no IR for it, so there is no agreement tier. ForShor's kernel check is still a gate (a failed build is a refusal).
* `scripts/verifier.py` allows `Policy.lean` and, through `HELPER_PREFIX`, helper files under `TableGeneration/Submission/Policy/`; if only `Policy.lean` is to be accepted, the new workflow must refuse the rest.

## How the pieces connect (`./forshor check`, the pull-request job, the publish job)

A policy submission is checked by `scripts/verifier.py`, unchanged for the older table-generator submissions: when `TableGeneration/Submission/Policy.lean` declares `namespace PolicySubmission` or
`namespace PTPPolicySubmission` it hands over to `scripts/policy_verifier.py`, which refuses any changed file other than `Policy.lean` and the optional `summary.tex` (and a summary alone), validates the summary, runs `evaluation/intake_v2.py --no-publish` and writes the
`table-generation-submission-result.json` (`status`, `phase: "full"`, the checks) and `verification.json` that `./forshor check` and the workflow artifacts already carry.

| Step | Who runs it | What |
|---|---|---|
| `./forshor check` | the participant, on their machine | `scripts/verifier.py` from a checkout of the base commit with the submitted file copied in. `./forshor setup` (and the first `check`) clone and build the two Lean trees named in `evaluation/policy-trees.json` and a Python environment for the engine, under `.git/forshor`. Needs no publisher, no token, no pull request. |
| pull-request job (`table-generation-submission.yml`) | CI, no secrets | the same `scripts/verifier.py` with `FORSHOR_TREE`, `STO_TREE`, `ENGINE_PYTHON` set (and `TABLE_GEN_LOGIN` the PR author); upload `verification.json` with the other artifacts. `--qualify-readout` is added only when the submitted file declares the measured readout in its shipped form (`scripts/ci/policy_source.py:declared_readout`, read before the build; an omitted or explicitly default `{ readout := .coherent }` declaration takes the ordinary build, and an unrecognised one is refused before Lean). |
| publish job (`publish-results.yml`, trusted) | CI, with the publisher identity | for a result whose `kind` is `policy`: `python evaluation/intake_v2.py --publish-from verification.json --login "$PR_AUTHOR" --pull "$PR_NUMBER" --head-sha "$PR_HEAD_SHA" --run-id "$RUN_ID" --trusted-origin --publisher-url "$PUBLISHER_URL" --archive "$ARCHIVE_CHECKOUT"` with `PUBLISHER_TOKEN` and a `GH_TOKEN` that can push to `submission-results`. No Lean; archive push and publish take about 10 seconds; everything in the verification is checked again. `--trusted-origin` is how the trusted workflow says where the receipt must have come from: the publisher re-derives this checkout's verification identity and re-fetches the artifact the receipt's `ciVerification` stamp names, and the COMPARISON authorises (`evaluation/export_v2/receipt_origin.py`). It is inert for an ordinary submission -- only a receipt carrying a qualified readout reaches the check. |

## The archive

Every accepted policy is archived on the `submission-results` branch, by the same call that publishes it (`intake_v2.py --publish-from ... --archive CHECKOUT`; `evaluation/archive_policy.py`), **before** the publisher is called: the publisher requires the archive's accepted ids to include the submission's head commit.

* `results/policy-submissions/<head>/Policy.lean` (the submitted file, byte for byte), `summary.tex` (only when the PR has one: byte for byte, never compiled; the publish job fetches it at the head commit and refuses unless the verification carries the same file), `policy.json` (the policy as the boards price it, with its digest) and `verification.json` (the mirror's envelope and ForShor's printed document), and one entry in `leaderboard/policy-submissions.json` (`schema: forshor.policy-archive/v1`; `policy_id` = head commit, `digest`, `mode`, `board`, `sha256` of the file, `summarySha256` (only with a summary), `source{repository, pr_number, github_login, run_id, created_at}`).
* It is a separate index from the table-generation archive's `leaderboard/results.json`: that index's writer (`table_generation_report.js`) drops entries that have no table results, so policy entries must not go in it. `publish_v2._archive_view` reads the union of the two for the accepted ids; the older flow is untouched.
* The source is not taken from the verification (an artifact of the untrusted job) alone: the publish job fetches `Policy.lean` at `--head-sha` from the GitHub contents API and refuses (stage `archive`) unless it equals the verification's `leanSource`.
* Idempotent: the same head and file is a no-op (a re-run after a failed publish adds nothing); the same head with a different file is refused. A push that loses a race is retried from the branch's new tip (4 attempts).
* The job needs `contents: write` on `submission-results` (the checkout must keep an `origin` it can push to; the token goes only into git's per-call environment, as `publish.refresh_archive` does). Branch protection on `submission-results` must allow that identity to push. Not exercised against GitHub: the tests push to a local bare repository, and the contents API is stubbed (a fork PR's head commit should resolve in the base repository, which is what the check relies on).
* The CLI's receipt links to the results page; it carries the archive path and commit in `publication-result.json` under `archive`.

Hosted integration is active. See `docs/POLICY_CI.md` for the workflow, runner/cache configuration, and November 9 hosted PP/PTP verification, archive, publication, rejection, and retry evidence.

