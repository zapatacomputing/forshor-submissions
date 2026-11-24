# Policy submission CI

The existing verification and trusted publication jobs serve both submission formats. `PolicySubmission` and `PTPPolicySubmission` select the complete-policy route; other files retain the older table-generator route.

Verification uses trusted base-branch code and full Git history. It builds the public ForShor pin and the in-repository STO targets in `evaluation/policy-trees.json`, then calls the existing verifier with `FORSHOR_TREE`, `STO_TREE`, `ENGINE_PYTHON`, and the GitHub PR author's login. Only the submitted Lean file reaches ForShor's disposable verification tree. No Google publication identity is available in this job.

## Environments and limits

A policy may contain at most **256 bands**, the limit the publisher and the frontend share; `MAX_POLICY_BANDS` in `evaluation/intake_v2.py` is the copy this repository enforces. Local `./forshor check` and hosted verification reject larger policies before the mirror runs (for Lean input, after ForShor extracts the policy). Trusted publication enforces the same limit before resource estimation. The submitted Lean file remains limited to 1,000,000 bytes; memory, runtime and publication payload limits still apply independently.

`Warm policy verification environments` builds on trusted `main`, then saves separate ForShor, STO, and Python caches. PR jobs restore only; they never save these caches. The v2 Python environment is separate from the legacy estimator, and uses `evaluation/engine/requirements.txt` with vendored Qualtran. The publisher checkout must include full history so `engineRevision` is the actual last engine change, rather than the shallow checkout's tip.

`POLICY_RUNNER` may name a provisioned Linux runner label. The default is the existing `ubuntu-24.04` private runner. This has 8 GB physical RAM; on disposable GitHub-hosted Linux machines we reclaim unused SDK space, add 8 GiB swap, and use `LEAN_NUM_THREADS=1`. **This does not satisfy a 16 GB physical RAM guarantee.** Hosted measurements must determine whether it is sufficient for the tested policies; larger policies may need a larger runner. Never perform that machine cleanup on a custom runner.

Bootstrap explicitly fetches the compiled `Mathlib` root. The cache tool does not traverse downstream ForShor/STO roots; requesting those roots silently omits the Mathlib artifacts and causes a source build. It suppresses the automatic whole-library fetch during Lake dependency updates. The two trusted trees share dependency directories only when their manifests pin the same Git source and revision. Relative links preserve this layout in the separate caches. On Linux, disposable verification trees copy project sources and their own build outputs while sharing the trusted job's dependency directory; they do not copy Mathlib again. Those trees and PR jobs never save dependency caches. Bootstrap stops its own build process if free disk falls below 3 GiB, retaining stage timings and available disk space before the runner's log writer fails.

The verification job is bounded to 60 minutes in total, including setup, ForShor, and the STO mirror. The mirror also has a 10-minute overall limit. `POLICY_STAGE_TIMEOUT=900` bounds individual STO replay stages; it is not a separate ForShor deadline. The outer job limit covers the ForShor harness as well. The trusted Qualtran/archive/publication job is bounded to 40 minutes. A resource timeout or exhausted memory is an infrastructure limit, not a mathematical verdict. Timings are retained in the `policy-bootstrap` artifact. The large disposable Lean tree is excluded from result artifacts; `verification.json`, identity, outcomes, and logs are retained.

The shared policy intake gives each mirror replay stage a 12 GiB process-tree memory cap, including checks run locally and any replay during publication. The hosted public-repository Linux runner has 16 GB RAM; the cap leaves headroom for the rest of the job. Replay diagnostics include peak usage, platform, architecture, accounting method and process count at the peak. Linux sums resident and swapped memory; macOS uses `top`'s resident-plus-compressed measure, so cross-platform peaks are not directly equivalent. A policy stopped at the cap has not received a mathematical rejection. Investigate another cap hit before raising it further.

Guard stops carry an explicit `memory` or `timeout` stage through the mirror and verifier result,
including PhaseTripleProduct compilation stops whose compiler output may be truncated.
The PR comment and CLI distinguish these from proof failures. The memory message says
**We're working towards supporting submissions that need more memory.** See [CLI.md](CLI.md)
for queue status and notification behavior.

The verifier clears earlier intake results and accepts success only when this run exits successfully
and writes a new verification receipt. On an invalid policy, `./forshor check` shows the failed
check and its reason; the GitHub run summary and PR comment use the same verifier result. If intake
could not run or wrote no usable result, that is an incomplete verification, not a failed proof.

## Source restrictions before compilation

The trusted `scripts/ci/policy_source.py` filter runs before dependency setup or
verification reuse. Hosted jobs fetch a separate, sparse `controls` checkout
from upstream main because older PR events may retain an earlier scientific
verifier revision without this filter. Local `./forshor check` runs it from the fetched upstream
checker. It applies the existing site-harness restrictions missing from the
upstream shell gate: no build-time commands, effects, metaprogramming, unproved
axioms, native proof shortcuts, compiler overrides, or extra imports. It uses
the existing hardened comment lexer. All 14 archived policy files and shipped
starter Lean templates passed the filter.

A source refusal updates the trusted preflight result, so the PR receives a
rejection with its reason and no Lean compilation or Google publication occurs.
The optional summary remains text and is not passed to this filter.

The trusted publisher applies the same filter to the exact file read from
GitHub, including archived/reused verification. This operational guard is
always applied around the mathematical proof cache; it does not change the
Lean theorems, estimator/model pins, epoch, or the archived proof identity.

## Workflow hardening

Every third-party action in `.github/workflows/` and `.github/actions/` is pinned by full commit SHA; `scripts/ci/test_workflow_pins.py` fails on any other `uses:` (local `./` actions excepted) and lists the few tags still awaiting a resolved SHA. `scripts/ci/test_workflow_security.py` holds the boundaries above in place: no `pull_request_target`, no credentials or write scope in a workflow a pull request starts, the verifier checked out at the base commit, every checkout without a persisted token, and the publisher and notifier running `main` code with the downloaded artifact as data.

There is deliberately no separate changed-files allowlist step in the verify workflow. The path filter only decides what triggers a run; the admitted paths are enforced by the verifier (`scripts/policy_verifier.py`) and again by the trusted publisher against GitHub's own file list (`scripts/ci/table_generation_report.js`), so a pull request that edits a workflow or anything else beside the submission surface is refused before anything is published.

## Trusted publication

After GitHub confirms the exact source run and PR files, the publisher checks the artifact's source/run metadata and compares the verification's Lean bytes and author with GitHub. For a policy it calls `intake_v2.py --publish-from` using the isolated v2 engine and short-lived Google identity. That call revalidates evidence, archives the policy on `submission-results`, calculates resource values, and publishes into the existing v2 epoch.

The archive carries a trusted verification receipt. Publisher retries may recover evidence from it if the original Actions artifact has expired. Evaluation identity stays bound to the original accepted verification run. Completed evaluations skip the engine and archive steps. Both PR checks remain attached to the source head, and successful publication closes the PR without merging it. Policy comments link to the v2 immutable snapshot and `results/policy-submissions/<head>` archive.

The policy archive is separate from the older `leaderboard/results.json` index. Model changes or any engine change require the epoch migration procedure; ordinary CI wiring must not alter scientific sources or the active model.

## The optional summary (`TableGeneration/Submission/summary.tex`)

A policy submission may carry a LaTeX summary of what the participant did, in one extra file: `TableGeneration/Submission/summary.tex`. It is text for people to read.

* **Admitted paths.** A pull request may change `Policy.lean` and, optionally, exactly `summary.tex`. A pull request that changes only `summary.tex` is refused ("a submission needs a policy"). Every other extra path stays refused: `notes.tex`, anything under `Policy/` other than `.lean` helpers (which a policy submission does not take either), `README`, workflows. The same rule runs in the verify job (`scripts/policy_verifier.py`), in the trusted publish job (`scripts/ci/table_generation_report.js`, `scripts/ci/policy_publication.js`) and in `./forshor` (`forshor`), and the notifier words it in plain language.
* **File rules** (one definition, `evaluation/summary_tex.py`; a violation is a rejection of the file with a message naming the rule, not a verdict on the policy): a regular file (no symlink, no directory); not empty; at most 100 KiB (102,400 bytes: about twenty-five dense pages, ten times anything worth reading, a tenth of the policy file's own limit); valid UTF-8; no control characters except tab, line feed and carriage return (so no NUL).
* **Never executed.** **No workflow or script compiles, renders, includes or runs the LaTeX.** TeX can run shell commands (`\write18`) and read files (`\input{/etc/passwd}`), so the file is only ever read as bytes, stored verbatim and shown as text. It cannot change verification: the policy's digest is computed from the policy alone, and the summary is carried beside it.
* **Flow.** The verify job reads the file from the submission checkout and records `summarySource`, `summarySha256` and `summaryBytes` in `verification.json`. The trusted publish job takes the identity (pull request, head commit, author) from the GitHub API, fetches `summary.tex` at the head commit through the contents API exactly as it does `Policy.lean`, and refuses unless the verification carries the same text, in both directions (a summary on one side only is refused). `evaluation/archive_policy.py` then writes it verbatim to `results/policy-submissions/<head>/summary.tex` on `submission-results`, records `summarySha256` in the index entry, and a re-run is a no-op. The v2 publisher contract and the site are unchanged; a link from the policy dialog to the archived summary is a follow-up.
