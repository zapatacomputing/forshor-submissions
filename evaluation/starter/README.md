# Starter pack: source and build

`pack/` holds the hand-written files of the participant starter pack (README, skill, format document, `starter.py`, ForShor's shipped template, the two ForShor printed documents, Lean-check guide).
`build_starter_pack.py` assembles the pack from them and from `evaluation/engine`, and writes the rest from the pack's own engine: the starting policies (built from the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024)), the templates, the submission files, the board
snapshot and every example's pinned output. It then writes `MANIFEST.json` and the zip.

**The pack starts from the Kahanamoku-Meyer and Yao (2024) tables and nothing else.** Its `engine/data/` keeps only `kmy_tables/` (PhaseProduct k = 3, PhaseTripleProduct k = 3 and 4); the archive tables, the DP
library, the seeded planner policies, STO's reference policies, `BestKnown` and every pinned run are left out, an empty `personal_tables/tables.json` stands in for the loader that expects one, and archive
identifiers that survive in the engine's *source* (docstrings, comments) are replaced. The repository's own `evaluation/engine/` is untouched, so **the repository itself still contains the archive**: that is not
hidden by the pack, and it matters when the repository becomes public.

```sh
python evaluation/starter/build_starter_pack.py [--seed <epoch-input.json>] --out <dir> --python <python with the pack's requirements>
python evaluation/starter/test_pack_docs.py <dir>/forshor-challenge-starter                  # no stale references in what a participant reads
python evaluation/starter/test_pack_kmy_only.py <dir>/forshor-challenge-starter.zip --python <python>
                                                                                              # nothing of the archive in the pack; the unzipped pack's own commands work
python evaluation/starter/test_pack_point_order_alias.py <dir>/forshor-challenge-starter --python <python>
                                                                                               # `pointOrder` (the site's spelling) and `point_order` price identically
python evaluation/starter/test_pack_validation.py <dir>/forshor-challenge-starter --python <python>
                                                                                               # defaults, allowed pricing corners and clean submission/format refusals
python evaluation/starter/test_pack_multi_table_same_k.py <dir>/forshor-challenge-starter --python <python>
                                                                                               # PR #132's 11-table policy prices to its published numbers; a broken k = 7 table is refused
python evaluation/starter/test_pack_matches_seed.py <dir>/forshor-challenge-starter.zip <epoch-input.json> --python <python>
                                                                                              # the unzipped pack prices every policy of a results seed to the boards' T and Q
```

Without `--seed` the snapshot rows are the starting policies priced by the pack's own engine. Pass `--seed` only with a seed that lists just the starting policies (the exporter's output with `--kmy-only`); a seed that still lists
the planner's policies would put their scores in `examples/board_snapshot.json`.

The pack changes whenever `evaluation/engine` does (its pinned outputs and `MANIFEST.json` go with it), so rebuild it after any engine change and after each re-seed.
Nothing under `evaluation/engine/` is touched by the pack. The site serves the result: copy `forshor-challenge-starter.zip` and `forshor-skill.md` (as `SKILL.md`) into the site's own starter directory and update its `starter-pack.json`.
