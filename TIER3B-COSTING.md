# Tier 3b costing on the publishing side — Step 4 adaptive and controlled Steps 1/5

Lane XL. Branch `prepush/XL-tier3b-costing`, cut from **`prepush/XF-integration-wave2` at `ada7fb4`**
(integration wave 2's document commit; the code head is `6b56806`).

Lane XC derived two Tier 3b costings in the research repository and admitted a receipt for each. They were
real but unreachable from `forshor-submissions`: the same gap the QFT axis had before lane WM. This lane ports
the costings, policy-generally, into `evaluation/export_v2` — the shape lane WT used for the Step 2 sampled
estimator, and for the same reason: `export_v2` is explicitly outside the fingerprint (`pricing.py:16-25`),
so adding a pricer there moves neither `engineRevision` nor `dataFingerprint`.

## What was ported, and from which commits

Source: `/Users/joshua/Desktop/Resource_Lab/repos/forshor-submissions-synthesis`, branch
`local/prepush-XC-tier3b`.

| New file | Ported from | XC commits |
|---|---|---|
| `evaluation/export_v2/step4_sampled_fallback.py` | `tools/staging_retained_step4_sampled.py` | `a12e431`, `789d1cc`, `c69e4d5`, `7b0a8ed` |
| `evaluation/export_v2/step4_sampled_contract.json` | `qualification/step4_sampled_contract.json` | `a12e431` |
| `evaluation/export_v2/controlled_sampled_fallback.py` | `src/synthback/backends/controlled_sampled_costing.py`, `tools/staging_controlled_fallback.py` | `d6c5b73`, `787d888`, `7b0a8ed`, `429d5eb` |
| `evaluation/export_v2/controlled_sampled_contract.json` | `qualification/controlled_sampled_contract.json` | `7b0a8ed` |
| `evaluation/export_v2/rotation_pool.py` | the partition rule of `src/synthback/backends/step2_fallback_ledger.py:147-210` | WX's rule, XF's measurement, XC's `require(...)` in `d6c5b73` |

**Correction to the brief's commit map.** The brief attributes the admitted controlled receipt to `d6c5b73` /
`787d888`. It is in **`429d5eb`** (`synthesis: admitted controlled Steps 1/5 adaptive B3 receipt`), and
`7b0a8ed` is *freeze the controlled Steps 1/5 contract and record the Step 4 non-vacuity run*. The artifacts
named in the brief all exist at the paths given; the ordering is the only thing that differs.

Both contracts are mirrored **byte-identically**, which is also what WT did:

```
24678baf9a050b2bb2f5f9b6eaf97f4ff1827d5b5ad089341f4ea6eeb141edef  step4_sampled_contract.json   (both repos)
0a4ee0c42d44cc9b6a312cb00f302d6920e571b9a82d440e96b61102bd8918dc  controlled_sampled_contract.json (both repos)
38f6f5786983f257aeeabd4f63fe3baa820453041865a2a5e5252f266c6587d9  step2_sampled_contract.json   (WT's, both repos)
```

### Policy-generality, the WT shape

Nothing in either module names a fixture or an instance. The census is an argument, the sample is a function
of the declared population and the public contract salt only, and the per-site certification worker is a
parameter, so the synthesis backend is a parameter of the costing and not a frozen operator bank. What stayed
in the research repository is everything that names an instance: the staging lanes' fixture binding, the board
reconciliation, the compiled comparator check, the shell swap and the reduced bound witness.

### One estimator, not three

`step4_sampled_fallback` delegates to `sampled_fallback.price_step2_budgeted` with the Step 4 contract, and
`controlled_sampled_fallback` reuses `Stratum`, `stratified_total` and `_runner`. That is XC's own structure —
`staging_retained_step4_sampled.py` imports `price_step2_budgeted` directly — and it keeps **one
Maurer-Pontil implementation in this repository, not three**. `sampled_fallback.load_contract` grew a
`schema=` parameter to allow it, which is the same one-line change XC made in `a12e431`.

## The pool-sharing answer

**Yes — Step 4 adaptive draws on the same pool, and so does the controlled lane.** Not by inference:
both research plans declare `error_pool` **`1/200`**, which *is* the engine's own pool at delta 1e-2
(rotation fraction 0.5 × 1e-2). Same number, same budget.

**Alone, neither is a second spender.** A B3 phase scope re-prices sites that are already inside the
`per_site × run_sites` term, at the *same* allowance. It moves sites between routes inside one term; it does
not add a term. `rotation_pool.check_partition` and `check_scope` make that a checked fact, and the ported
rule closes **exactly** on both of XC's real admitted plans (pinned interpreter):

```
step4   partition closes exactly: True | run_sites 3,547,297,844 | 3 QFT families
        step4 scope 2,027,025,000 sites, retained outside 1,520,272,844, second share: False
steps15 partition closes exactly: True | run_sites 1,520,272,844 | 2 QFT families
        step1 and step5 506,756,250 sites each; 1,013,512,500 together <= 1,520,272,844
```

**Together with a QFT design, or with each other, they double-spend.** The two partitions of the *identical*
0.005 pool disagree by a factor of 4.9 on the QFT term alone:

```
qft_overlay's rule (3 catalytic families, real engine, delta 1e-2)
  pool      = 0.005
  deltaArm  = 0.003688894226218041   (73.78% of the pool)

the reference-plan rule XC's Step 4 was priced against
  QFT ceilings = 0.00075   (15%)   gradient prep = 0.0005 (10%)   per-site = 0.00375 (75%)
```

Honouring both spends `0.00075 + 0.0005 + 0.00375 + 0.003689 = 0.008689` — **173.8% of a pool that has no
slack**, i.e. 73.8% free error. And two sampled phase scopes cannot be added either: each one's ledger
reserves *every other* site at the per-site allowance and carries the whole fixed QFT-plus-gradient term
(`check_rotation_ledger`'s `residual_direct` bucket), so two independently priced scopes double-count the
fixed term and price each other's sites.

**So the combinations are REFUSED**, by name, in `rotation_pool.refuse_combination`, and the two entry points
call it **before any worker runs** — the same call lane WY made for readout + design and lane XF made for a
QFT design + a Step 2 fallback. Steps 1 and 5 are the one admissible pair, because
`controlled_sampled_fallback` prices them in a *single* run against *one* partition: two scopes of one
claimant, not two claimants. `cost_policy_controlled` refuses to price one of them alone, so the
add-two-receipts route is closed at the entry point.

### A specificity gap closed on the way

`qft_overlay.OUT_OF_SCOPE` named only `("step2", "step4")`, while `full_run_design.PHASE_STEPS` — and
`Design.json:84` — name all four. `step1` and `step5` were still **fail-closed** (the unknown-key check caught
them), but they were refused as *unrecognised families* rather than as phase-product scopes needing their own
qualification. Now that they have one, `OUT_OF_SCOPE` is the full set and the two are pinned equal.

## Estimate vs bound

XC's artifacts self-label `"estimate": true, "circuit_bound": false`. That honesty is carried through
structurally, the way lane XA made deterministic-vs-expected-adaptive uncollapsible
(`primitive_overlay.py:215-217`): `sampled_fallback.t_fields` emits **`tKind` plus exactly one of
`tSampledEstimateUpper` / `tCircuitBound`, and never a bare `T`**, with `check_t_fields` refusing a bare `T`,
both fields at once, neither, or labels that contradict `tKind`. Every interval block and every controlled
per-step block goes through it, and `check_receipt` re-checks on the way out.

**Two axes, both carried.** XA's axis is about what the per-site T *is*; this one is about where the total
*came from*. A B3 site's T is an unconditional expectation (repeat-until-success), so a sampled B3 phase is an
**estimate of an expectation** — twice removed from a worst case. `tSiteKind` carries the first axis beside
the second, so neither step can be read away. Both lanes also publish a `no_bound_basis` string saying *why*
no bound exists (495,000 turns against a 4,096-turn recertification cap; ~1.0e9 words for the controlled
bank), so "estimate" is a statement with a reason rather than a disclaimer.

This matters concretely because `mapping.row_of:289` writes `"T": price["T"]` — the ledger row has a **bare
`T` field and no kind**. A Tier 3b number must not reach it, and it cannot: see the blockers below.

## Two rules the port had to apply rather than inherit

The research controlled lane implements the three honesty rules only in part, and the port fixes both gaps
rather than carrying them across. Each fix has its own red perturbation (below).

1. **Silent widening.** `controlled_sampled_costing.combine` sets `spread = max(range_T, observed span)` and
   only *flags* the excess as `range_exceeded`. That rests the ranked endpoint on a range nobody calibrated —
   what SAMPLED-RULINGS.md forbids and what the Step 2 and Step 4 paths refuse. The port raises
   `OutOfContractRefusal`; only the explicit local diagnostic prices it, flagged NON-RANKABLE.
   **XC's admitted numbers are unaffected**: the run observed spans of 30.06 and 23.89 T against a `range_T`
   of 74, so it was in contract. The margin is now pinned.
2. **No resource stop at all.** `price_controlled_step` has no wall-clock stop; `budget_seconds` was recorded
   and never checked. The port raises `ResourceStop` with `verdict == "INCOMPLETE"` — never a refusal, never a
   pass — and the raise is the only exit, so no success receipt can exist for a run that stopped that way.
   XC's run took 365 s + 363 s against its 1800 s budget, so again the admitted numbers stand.

## Faithfulness: the ported code reproduces XC's own endpoints

The ranked endpoint rule is `ceil(exact_T + point + max(clt, eb)) + 1`. Against the two runs recorded in
`results/validation/phase-fallback-exports/step4-nonvacuity.json`, **to the integer**:

```
frozen_contract_salt    -> 96,079,901,165   reported 96,079,901,165   MATCH
one_hex_digit_flipped   -> 96,017,908,442   reported 96,017,908,442   MATCH
```

and the admitted 120,000-of-495,000 run: 94,983,648,143 T over 2,027,025,000 sites = **46.8586 T/site**
against the 123.19262356285252 Ross-Selinger mean (61.96% lower per site), with empirical-Bernstein
7.5378e7 inside the contract's 8.5e7 gate. All pinned.

## Counts and gates

Measured with the **pinned interpreter**
`/Users/joshua/Desktop/Resource_Lab/projects/synthesis_backends/.venv/bin/python` (**Python 3.12.14 /
numpy 2.5.4**). Digests in this project are environment-sensitive — an off-pin env (3.11.15 / numpy 2.4.6)
gives `c773f502…` for the full panel — so every number below is from that interpreter and no other.

| Suite | XF baseline | This lane |
|---|---|---|
| `export_v2/pytests` (fast) | 448 passed, 152 skipped | **545 passed, 152 skipped** |
| `export_v2/pytests --runslow` | 599 passed, 1 skipped | **696 passed, 1 skipped** |
| `scripts/ci` | 100 passed + 96 subtests | **100 passed + 96 subtests** |
| `evaluation/test_archive_policy.py` | 36 passed | **36 passed** |

The `+97` reconciles exactly: **95** in the new `test_tier3b_sampled_costing.py`, plus **2** in
`test_qft_overlay.py::test_the_out_of_scope_selector_fields_are_refused_with_a_qualification_message`, which
is parametrised over `OUT_OF_SCOPE` and therefore grows from 2 cases to 4. No existing test was dropped or
weakened; `test_a_qft_design_and_a_step_2_fallback_cannot_be_asked_for_together` keeps every assertion it had
and gains the Tier 3b family (see below).

The baseline itself was re-measured in this worktree before any change: **448 passed, 152 skipped**, exactly
XF's figure.

### Hard gates, at the final head

```
epoch           : ab1f8887b1b50e56
dataFingerprint : 176ce4047f5957f1
engineRevision  : 677cc2e05ab178afff8dc632ff56058a8196a116

$ git diff 792464f -- evaluation/engine
(empty)

sha256(evaluation/export_v2/readout_overlay.py)
  = 53a7e7ba32f12fe7de423b6d19e3ebdc458fc82b99cdaa7e0eaadeb93a45e032
```

The default path is byte-identical, measured directly and not only asserted:

```
full          998ad142a47fae05466541a3a92e0851cb86bac91876ce409ec4a98b3e0b19e3
pp            c83bf7a532a639d1b16d2e1e9b86b26e2bef36e8db96137e2d259a72d527179f
whole request 6641a96743ab92aedea0f65d9b883fa745985dfc5931e9e4c4a22967ce86896d
```

It could hardly be otherwise: the three new modules have **no production caller**, which
`test_full_run_chain.py` enforces, so nothing on the default path can reach them.

Under AGENTS.md rule 5 the **verification identity moves**, because `evaluation/export_v2/` is an identity
input and this lane changes it. An already-verified head is re-verified rather than reusing its receipt. The
Lean-cache inputs are untouched (rule 2), `evaluation/engine/` is byte-identical (rule 1), and no frozen
record is edited (rule 3).

### Non-vacuity, by perturbation

Targeted `-k`, per the operating rule. Every rule this lane adds has a measured red:

| Perturbation | Red |
|---|---|
| restore the research lane's silent widening in `combine` | `test_an_observed_range_excess_is_refused_and_never_silently_widened[controlled]` |
| remove the controlled resource stop | `test_a_blown_budget_is_incomplete…[controlled]`, `test_a_resource_stop_emits_no_success_receipt[controlled]` |
| disable both `refuse_combination` clauses and the partition identity | **15 failed**, 75 passed |

## Overlap, reported rather than resolved

* **Lane XG (`intake_v2.py` primitive half) and lane XH (receipt/publish path)** — not touched. Nothing in
  this lane edits `evaluation/intake_v2.py` or any publish path. The one place a Tier 3b receipt would meet
  XH's work is the receipt-trust surface, and this lane deliberately stops short of it: the costings emit a
  receipt and nothing consumes it.
* **Lane XF's handoff-2 test** was extended, not replaced. Its substring clause (`"sampled_fallback" not in
  text` over every other `export_v2/*.py`) could not tell a **call** from a **mention**, and it fired on
  `qft_overlay`'s own docstring as soon as that docstring started saying which module prices each out-of-scope
  selector. It now **parses** imports with `ast`, including deferred ones, which is exact — naming a refusal is
  the opposite of composing with it. The clause is also now stronger in two directions: no production module
  may import *any* of the three sampled pricers, and no sampled pricer may import back into the chain
  (`qft_overlay`, `full_run_design`, `pricing`, `mapping`, `request`).
* **`rotation_pool.py` is the one module allowed to NAME the pool.** It is not a pricer: it imports nothing
  from the engine and derives no allowance, it only checks that the caller's own terms partition.
  `test_no_sampled_pricer_derives_its_own_allowance_from_the_engine_currency` pins that distinction, so a
  pricer that started computing `eps` itself — which is what would make it a second spender — goes red.

## What still blocks Tier 3b from publishing

Five things, in order of how hard they are to move. **None of them is a bug in XC's work**; the derivations
are sound and now reachable. What is missing is a publication route, and four of the five are deliberate.

1. **No production caller, by design.** `pricing.price_views` has no `step4=` or `controlled=` hook, and the
   receipts reach no panel and no row. This mirrors what integration wave 2 says about WT: *"Any production
   caller for WT's sampled Step 2 fallback"* is explicitly not in the release. Adding one is a decision, not a
   fix.
2. **The site contract has no field for an estimate.** `mapping.row_of:289` emits a bare `"T"`, and the row
   is a Zod `strictObject` whose `rowFields` name no kind; the panel's `fullRun` is a `strictObject` whose
   only optional price is `readout`. So a Tier 3b number has nowhere to go that says what it is — and putting
   it in `T` is exactly the harm `tKind` exists to prevent. This is the same wall lane XD hit for the design
   key, and it needs a site-side change, not a repository-side one.
3. **Both topologies are refused this release.** XC's Step 4 lane requires `modmul = split_compare` and the
   controlled lane requires `quotient_stream`; measured at this head, both refuse by name on WR's topology
   gate (`reference` is the only label that converts). Quotient and splitCompare customization is deferred by
   owner decision, so the *base design under which XC's numbers were derived* cannot currently be expressed on
   the publishing side at all.
4. **The configuration XC priced is a refused combination.** Both lanes require three (resp. two) approximate
   **catalytic** QFTs. A QFT design plus a sampled phase scope is now refused by name, on the 173.8%
   arithmetic above. Ranking XC's exact configuration publicly therefore needs a *single* partition rule
   covering both claimants — a derivation nobody has done — not a precedence.
5. **No certified B3 worker exists in this repository, and the verifier pins do not line up.** The worker is a
   parameter (that is what makes the costing policy-general), but `pygridsynth` and `synthback` are
   research-side only, so the pricers are callable here and have no backend to call. Separately, XC's receipts
   carry verifier pin **`d6ea09e1ac20df4ebb4257d70b5fc99078a032ed`**, which matches neither
   `primitive_overlay.VERIFIER_PIN` (`931bbf16…`) nor `readout_overlay.VERIFIER_PIN` / the operational pin in
   `evaluation/policy-trees.json` (`80d7b6b2…`). Their `parent_epoch` *is* the live `ab1f8887b1b50e56`, so the
   epoch lines up and only the verifier build does. **Escalated, not resolved**: picking a pin is an owner
   decision and it touches lane XH's surface.
