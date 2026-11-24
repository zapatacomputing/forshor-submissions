# Evidence archive for epoch 494185cf666f9752

Produced 2026-11-07 and 2026-11-08 by `python -m export_v2 run-mirror <dir> --tree <tree> --evidence-dir <dir> --only all --only-digest <prefix>...` from the exporter branch
`export/v2-seed-m2` (`64292da`), against the interim STO fork's tree, built at Lean 4.36.0-rc1,
ForShor `21e129a`, STO base `361e81e`. Two trees (APFS clones of one build) ran at once, because the PhaseTripleProduct harness writes a generated module into its tree.

| runs | stage limit |
| --- | --- |
| the 13 policies other than `c733baf3` and `48063efa` | 21,600 s (6 h); `92d5b75d` was re-run with `TMPDIR` outside the system temp area after its first attempt lost a file there |
| `48063efa` (then `c733baf3`) | 43,200 s (12 h) |

Files: `<digest16>.policy.json` (the policy the harness hashed), `<digest16>.record.json` (the harness record), `<digest16>.evidence.json` (the envelope the exporter reads: `tree` pins, the harness
name, the record). Logs are not archived. `c733baf3` has no files here: its only record was a false rejection from a harness bug that was fixed (see the exporter README).
Only `accepted` envelopes count as evidence; `48063efa` is a timeout and yields none.
