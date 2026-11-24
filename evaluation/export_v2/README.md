# export_v2: the engine's output as contract-v2 documents

This package is the Python half of "engine -> contract". It is **not wired into any workflow**. What the contract requires of
these documents is pinned by the tests in `pytests/`, which are the authority relied on here.

It produces ledger-level inputs only: the model, rows **without ranks**, benchmarks, reference curves, panels and evidence.
Ranks, boards, the dashboard and the snapshot id are all derived downstream by a single shared implementation on the
publisher side, so there is no second ranking in Python. `mapping.py` builds the same panels as the publisher's own fixture
builder from the same real engine views; `pytests/test_mapping.py` is the golden test that holds the two equal.

Run from `evaluation/` with the vendored engine's Python environment (`evaluation/engine/requirements.txt`, Python 3.12):

```sh
python -m export_v2 evidence-needed OUT_DIR                      # which planner policies lack valid evidence; writes each as a file
python -m export_v2 run-mirror OUT_DIR --tree PATH               # run that tree's harness on them (needs a BUILT Lean tree)
python -m export_v2 epoch-input OUT.json --publisher-commit SHA --evidence-dir OUT_DIR
python -m export_v2 request OUT.json --policy FILE --evidence-dir DIR --head-sha ... --pull N ...   # one submission
```

A maintainer with a publisher checkout can additionally validate an output document against the publisher's own validator;
that cross-language check is driven from this repository by `pytests/test_engine_backed.py` (see **Tests** below).

## Negation: what the epoch field `negation` means

The epoch model carries `negation: "incrementer"` (no contract change). That label names ForShor's `NegationMethod`, not the engine's
`ConventionSpec.negate` option, and the two vocabularies differ:

| Epoch `negation` | ForShor method | Engine `negate` | What a w-bit negation is charged |
| --- | --- | --- | --- |
| `incrementer` | `oneQubit` | `gidney_circuit` (the default) | complement plus Gidney's one-clean-ancilla increment: Toffoli 12w-28 (even w) / 10w-18 (odd w), CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1 (exact for w >= 7, upper bounds below); ONE clean qubit |
| `scratch` | `scratch` | `legacy` | a w-bit constant-one register plus a Cuccaro adder (Toffoli 2w-3, w+1 clean qubits) |
| none | none | `incrementer` | SUPERSEDED first draft of PR #79 (flat 32 w Toffoli, no CNOT). Selectable, never the default, and it cannot start an epoch (`epoch.negation_label` refuses it) |

At w = 8 / 9 / 2048 the first row gives X / CNOT / Toffoli of 37 / 114 / 68, 46 / 148 / 72 and 10237 / 34794 / 24548, and `scratch` at 2048 gives
6140 / 10233 / 4093 with 2049 clean qubits: ForShor's `negateChargeAt` examples (`Emit/Json/Common.lean`), asserted in
`engine/tests/test_swap001.py`. The mapping lives in `epoch.NEGATION_OF`. The ForShor pin is the merge commit of PR #80 (on top of PR #79's `eb00c5adc72832469a5f223fbec6c8b75745d2dd`), `21e129aeefc0fd1bbb8fa63402f5799af2490788`, written in
text until it exists.

The qubit text of the full board's peak (`mapping.peak_label`, `mapping.peak_rule`) is a function of the per-row flag `leanPeak` (default False:
"Lean has not evaluated it on this policy"). It is set per row from `epoch.LEAN_PEAK`, and `build_epoch_input` refuses to export a row whose Q differs from the kernel-checked number there.
PhaseTripleProduct: six board rows (ForShor main, `Peak.Board.Row<id>.peak_oneQubit_exact`: 6405, 7095, 6261, 6342, 6573, 6828); the flag replaces the
engine's "formula, not Lean-proved" qubit label and rule (`mapping.PTP_LEAN_LABEL`, `mapping.ptp_lean_rule`: Q equals ForShor's upper bound
`ptpPeakQubits`, a bound on an existential circuit). PhaseProduct: nine policies (shard S7: `Shor.QubitCount.PolicyNumeralsBoard.Row<id>Exact.balanced_exact_oneQubit`,
Q = 4095 + 8795 + N), keyed by (row id, ladder) because row 6f7d25984ff8 re-optimises per delta; the flag is a dict (`module`, `n`, `ci`) and the label/rule
say the theorems are in ForShor main at the pin and whether ForShor's CI builds the module (`ci`) or only a developer machine
checked it (13 to 15 GB; library `PolicyNumeralsBoardExactHeavy`). Only the qubit text changes.

## Evidence rules

Evidence is for one policy (its digest: mode and bands, not the display title). A harness record counts only if the harness
accepted it, at exact widths, at the model's width, for the same bands, on the exact policy file the envelope names (the record
carries that file's sha256). **Evidence is valid only for the toolchain it ran on, and an epoch fixes the toolchain**: the Lean
version, the STO base and the ForShor pin must equal the model's. Anything else is reported, never silently used. A planner policy without
valid evidence is **still exported** (decision D9, which superseded D8) with `stoMirror: null`, and the page marks it unchecked; a
*submission* without evidence is still refused. The evidence report counts them (`rowsUnchecked`, `policiesWithoutEvidence`). A policy
that does have evidence is exported at every delta, priced there, even where the planner picked another policy (`rowsAdded`): evidence is
about the policy, and only the rotation-synthesis price depends on delta.

## STO mirror evidence at ForShor 21e129a (2026-11-08)

All 15 distinct listed policies were run through the interim STO fork's harnesses (`check_banded_policy.py`, `check_ptp_policy.py`, `--widths exact`) at the epoch's pins: Lean
4.36.0-rc1, ForShor `21e129a` (the merge of PR #80, on top of PR #79), STO base `361e81e`. Those three pins are what makes the
evidence valid, and an epoch fixes them. The envelopes (policy, record, evidence; no logs) are archived in `evidence-archive/494185cf666f9752/` (the first 13, and the timed-out record of `48063efa`) and, for all 15 accepted, in `evidence-archive/7a11b2b8b6d68718/` (the current epoch).

| Policy | Ladder (threshold, k) | Result | Report stage |
| --- | --- | --- | --- |
| PhaseProduct baselines k = 2 to 6 (`6f036362`, `ea01254c`, `7df40770`, `a3972b96`, `c7f81cb4`) | one band | all **accepted** | 5 to 95 s |
| PhaseProduct planner `1bc92ac6` | 6, 4, 3 | **accepted** | 54 s |
| PhaseProduct planner `80132d41` | 8, 4, 3 | **accepted** | 8,768 s |
| PhaseProduct planner `ab119309` | 8, 3 | **accepted** | 8,574 s |
| PhaseProduct planner `92d5b75d` | 8, 3 | **accepted** (second attempt, see below) | 8,624 s |
| PhaseTripleProduct baselines k = 3, 4, 5 (`85fbe129`, `265a0f53`, `2ed7dc90`) | one band | all **accepted** | 7 s, 23 s, 773 s |
| PhaseTripleProduct planner `627bb6e3` | 4 | **accepted** | 11 s |
| PhaseTripleProduct planner `48063efa` | 8, 4 | first attempt: report stage killed at its 12 h limit (a resource limit, not a rejection). **Accepted** with `--fast` (2026-11-08, see below) | 43,201 s (default path); skipped with `--fast` |
| PhaseTripleProduct baseline k = 6 (`c733baf3`) | one band | default path had not finished after 6 h. **Accepted** with `--fast` (2026-11-08) | skipped with `--fast` |

All 15 policies have evidence, which covers all 88 board rows of the seed (13 by the default path, two by `--fast`). The earlier guess in the section below that the k = 8 policies would "likely repeat" a timeout was wrong
for PhaseProduct: all three were accepted once the stage limit was raised (6 h), taking about 2.4 h each in the report stage. What took 2.4 h at the older pins also took 2.4 h here, so
expect long checks for large first bands; the PhaseTripleProduct k = 8 policy did not finish in 12 h.

**`run-mirror --fast` (both boards).** Only the harness's report stage is slow: `lean --run` interprets the exact ladder, the census and the formula numbers (773 s at k = 5, over 12 h at a k = 8 band), while the kernel
certificate that follows takes 10 to 25 s even at k = 8 (it recomputes `ladderX` itself: `ladder_exact ... := by decide +kernel`). With `--fast` the fork's `check_ptp_policy.py` skips the report, takes the exact ladder from `ptp_exact_width`
(Python, milliseconds, an untrusted oracle) and lets the certificate prove it, so a wrong ladder or a failed threshold rule makes the certificate fail to build. Measured on the k = 5 baseline: 39 s against about 820 s, the same 19 audited
theorems with the same statements and axioms, the same `ptp_peak_qubits`. `48063efa` and `c733baf3` were accepted this way (about a minute each). What `--fast` does not produce: the report's census and formula numbers (diagnostics; the gate-level census
theorems are in the certificate) and the specific threshold-rejection reason (the certificate simply does not build). A false reject would mean a Python/Lean disagreement; the default path is the fallback. At the time of these runs the flag
existed only on the mirror's PhaseTripleProduct harness; the PhaseProduct harness had no such option and still took hours at k = 8.

**PhaseProduct `--fast` (added 2026-11-09).** The same flag now exists on `scripts/check_banded_policy.py` (exact mode only; default off). Why the report stage is slow: a `State` is a function `Fin k → Fin k → Int` and every operation wraps the previous state in another closure; Lean's interpreter never memoises them, so evaluating a table's `check` doubles in cost about every 10 operations (one k = 8, 181-operation table: 1 ms after 16 operations, 76 ms after 80, 3.3 s after 120, 30 s after 160, 83 s after 168), and the report evaluates it per band, again inside `P.check`, and again for the chain. The kernel shares sub-terms, so the certificate takes 15 to 25 s regardless. With `--fast` the exact ladder comes from `scripts/exact_width.py` (an untrusted Python transcription of `Tight.lean`) and the certificate re-proves it (`exact_ladder`, `exact_ladder_at_target`, `chain_contracts_tight`) with `accepted` (every band's table contract). Measured on a scratch copy of the epoch's tree: `80132d41` (bands 8, 4, 3; 2.4 h in the report stage) accepted in 2 min 14 s with the same ladder as its earlier evidence; `7df40770` full and fast runs agree on the verdict, the ladder and every shared theorem (same statements and axioms); a deliberately wrong Python ladder (517 -> 516) is rejected at the certificate (`(kernel) application type mismatch` on the `exact_ladder` theorems). What `--fast` does not produce: the scan ladder and the advisory `bands_contract` theorem, and the report's named-band early rejections (a failed threshold rule is still named, by the Python mirror, once the kernel has rejected it).

Findings from these runs (each one produced a false `rejected` that is not a verdict on the policy; treat any `rejected` whose detail is not a threshold, contract or timeout message as an
infrastructure failure and re-run it):

* `PTP.Census.olean does not exist`: `lake build TableGeneration` does not build the PhaseTripleProduct modules; build them by name before running the PhaseTripleProduct harness.
* `Insufficient number of fields for ⟨...⟩ constructor ... BandedPolicy.mk`: the mirror's `BandedPolicy` gained a `negation` field in the port to ForShor's negation method; a harness script that
  builds one with two fields fails at its report stage.
* `certificate does not build ... no such file or directory` in a temporary directory under `/var/folders`: a source file in the harness's temporary workdir disappeared during a 2.4 h run
  (not reproduced; the re-run with `TMPDIR` on a path outside the system temp area passed). Set `TMPDIR` for runs of this length.
* The harness's concurrency rule (do not start Lean while two or more are running) made a run wait for stale processes from earlier runs; the harness gives up waiting after an hour and starts anyway.

## Tests

```sh
python -m pytest export_v2/pytests -q                       # no engine needed (mapping, evidence rules, stub harness)
python -m pytest export_v2/pytests -q --runslow             # prices a real policy
```
`--runslow` additionally runs the cross-language validation in `pytests/test_engine_backed.py` when `FORSHOR_SITE_DIR` points at a
publisher checkout with `node_modules`, and skips it otherwise; it is optional and nothing in this repository depends on it.
The tests live in `pytests/` (no `__init__.py`) on purpose: CI's `python -m unittest discover -s evaluation` runs in the pinned
Qualtran 0.7.0 environment and must not import the vendored engine.

The harness runner (`run_mirror`) was run against a real built tree on 2026-11-05 for the policies in the table above; its tests use
a stub for speed, plus the real records as fixtures.

## The seed that starts from the Kahanamoku-Meyer and Yao tables (2026-11-10)

The leaderboard starts from the starting policies, built from the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024) and nothing else. `--kmy-only` (on `evidence-needed`, `run-mirror` and `epoch-input`)
replaces the planner frontier, the baselines and the reference curves by two policies, each the planner's optimum over exactly those tables at n = 2048, delta = 0.01 (`epoch.starting_policies`; a policy is a recursion with thresholds, and these are not Kahanamoku-Meyer and Yao's):
PhaseProduct k = 3 (`forshor_sub1`, also on the full board) and PhaseTripleProduct k = 4 (`forshor_sub2`). The same policy is repriced at every delta. Their creation time is the constant
`STARTING_CREATED_AT`. Benchmarks are empty and the chart series are empty (no cache is read). Every KMY table contains a negation: the exporter's old guard on negating tables is gone because
the PhaseProduct and PhaseTripleProduct boards now price a negation like the full run (`engine/negation-real`).

```sh
python -m export_v2 run-mirror OUT --kmy-only --fast --tree BUILT_TREE            # minutes; needs a tree with the --fast harness
python -m export_v2 epoch-input epoch-input.json --kmy-only --publisher-commit SHA --archive-commit SHA --updated-at ISO --evidence-dir OUT
```

Both policies were accepted by the mirror (envelopes in `evidence-archive/ab1f8887b1b50e56/`). The seed document is validated once more by the publisher before release.
