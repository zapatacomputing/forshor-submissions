"""Guard: no price-relevant source may change while the epoch stands still.

The epoch's model carries `dataFingerprint` (the engine's own fingerprint) and `engineRevision` (the last commit under
`evaluation/engine`). The docstring of `evaluation/export_v2/epoch.py` calls `dataFingerprint` "the engine's own
fingerprint of its data and every source a price depends on". It is not. `full_frontier.fingerprint()` hashes
`engine/data` (minus `forshor_submitted`), `tcost/*.py`, `forshor_qualtran/*.py`, `sto_currency.py`, `fullalg/**`,
`paper_model/**` and exactly three `site_engine` modules. It does NOT hash:

  * the rest of `site_engine` -- the PhaseProduct/PhaseTripleProduct board pricer itself (`frontier.point`,
    `frontier.live_peak_qubits`, `policy_view.view`, `library` (the planner's input set), `seed_policies.canonical`
    (which changes a price AND a row identity), `crosscheck`, `trust`, `precompute`, `__init__`);
  * the vendored Qualtran tree, which is where the gate counts come from. The model records only its version STRING
    (`epoch.py` `qualtran_provenance()["version"]`), and an in-place patch of a bloq does not change that string;
  * `engine/cache/*.json`, which the exporter reads RAW (`epoch.py` `planner_rows` and the chart loop) without
    checking the fingerprint each file carries;
  * `engine/data/forshor_submitted/**`, excluded from `data_fingerprint` on the grounds that no frontier reads it --
    true of the frontiers, not of the published panels, which read it through `policy_view._source`.

Today only `engineRevision` closes those holes, and only because its pathspec is the whole folder. This module does
not change that, and changes nothing the epoch is computed from: `scripts/ci/` is not an epoch input, the manifest is
data, and no byte under `evaluation/engine` or `evaluation/export_v2` is touched.

    python3 scripts/ci/price_surface.py              # check (CI); exit 1 on a finding
    python3 scripts/ci/price_surface.py --report      # the same, as JSON, always exit 0
    python3 scripts/ci/price_surface.py --update      # re-record the manifest (after a deliberate epoch move)

Three rules. The manifest is a checkpoint -- the group hashes AND the epoch inputs as they stood when it was
written -- never a derivation of the thing it guards, so none of this is circular.

  R1 EPOCH COUPLING. A group's content moved while EVERY epoch input stood still -> fail, naming the files.
     HONEST SCOPE: with `engineRevision` at its present whole-folder pathspec, any COMMITTED change under
     `evaluation/engine` moves `engineRevision` and therefore the epoch, so R1 cannot fire for the engine groups
     today. It is a TRIPWIRE, not a live check: it fires the moment anyone narrows `engineRevision` (that proposal
     was investigated and rejected), or moves a
     price-relevant file out of `evaluation/engine`, or lands a group change on a branch with no engine commit.
     The rules that do fire today are R2 and R3.

  R2 CACHE VALIDITY. Every committed cache must match the fingerprint it is keyed on, its schema and its model id --
     the check `precompute.load` and `full_frontier.load` run on read and `build_epoch_input` skips entirely. This
     has already failed in this repository's history: at `ec934ca` the four `fullalg_*.json` carried
     `40653c51983143fb` while that tree's `full_frontier.fingerprint()` was `9749eef3826bbf40`, and the next commit
     `0275418` exists only to fix it ("engine: rebuild the full-board caches for the new fingerprint"). Between
     those two commits an export would have published pre-change curves under a post-change epoch.

  R3 CACHE COVERAGE. This is the Qualtran hole stated exactly. The cache key is `data_fingerprint()` /
     `full_frontier.fingerprint()`, and NEITHER covers the vendored Qualtran tree or the uncovered `site_engine`
     modules. So a change there leaves every cache looking valid while its contents are wrong: `precompute.load`
     cheerfully reuses them, the boards publish pre-change numbers, and the only signal -- `engineRevision` -- says
     "the engine changed" while saying nothing about whether the derived data was regenerated. The rule: if a group
     marked `requiresCacheRebuild` moved, `engine.cache` must have moved too. This also already fired at `ec934ca`,
     which changed `site_engine/frontier.py` (the PhaseProduct board's Q rule) while `frontier_PP_1e-02.json` kept
     the fingerprint `8c9bed8d89909ada` of the previous rule.

Why the epoch inputs and not the epoch itself: computing `canon.epoch_of(epoch.model_of())` needs the pinned engine
environment (it imports the vendored Qualtran). CI's `scripts/ci` suite runs on the plain runner, so the check reads
the epoch's inputs instead -- every one of them is a file or a git fact. `dataFingerprint` and `engineRevision` are
the only two that move in practice; `qualtran.version`, `forshor.pin` and `lean` are read from their files, and the
remaining model fields (`id`, `widthRule`, `negation`, `n`, `rotation`, `sto.commit`) live in files this module
hashes as groups, so a change to one of them is reported rather than missed. `test_price_surface.py` pins the
mirrored fingerprints against the engine itself whenever the engine environment is importable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "price-surface.json"
SCHEMA = "forshor.price-surface/1"

#: Mirrors of the two engine fingerprints, so the guard can read an epoch input without importing the engine.
#: Each line cites the engine source it mirrors; `test_price_surface.py` asserts they agree with the engine when the
#: pinned environment is present, and the manifest pins their values either way.
ENGINE = "evaluation/engine"
_DATA_SKIP_TOP = "forshor_submitted"          # precompute.data_fingerprint skips this subtree
_FULL_SUFFIXES = (".py", ".json", ".jsonl")   # full_frontier.fingerprint's suffix filter
_FULL_SITE_ENGINE = ("paper_full.py", "full_frontier.py", "fullalg.py")

#: The price-relevant paths `dataFingerprint` does not cover. `enforce` groups fail the build; `advisory` groups are
#: reported only, because they change for legitimate reasons that are not price changes.
#:
#: NOT in this table, deliberately: everything `dataFingerprint` already hashes (`engine/data` minus
#: `forshor_submitted`, `tcost/**/*.py`, `forshor_qualtran/**/*.py`, `sto_currency.py`, `fullalg/**`,
#: `paper_model/**`, and `site_engine/{paper_full,full_frontier,fullalg}.py`). Those are guarded by the fingerprint
#: itself, which the check compares separately, so listing them here would double-count.
GROUPS = (
    {
        "id": "site_engine.uncovered",
        "enforce": True,
        "requiresCacheRebuild": True,
        "why": "The PP/PTP board pricer. `frontier.point` produces the published T, rotations, Toffoli, CNOT and "
               "`agree`; `frontier.live_peak_qubits` is the panel's headline Q (the number `build_epoch_input` "
               "compares with the kernel-checked LEAN_PEAK); `policy_view.view` assembles the panel; `library` "
               "decides which tables the planner may use; `seed_policies.canonical` rewrites every planner policy "
               "before it is priced and before its digest is taken; `crosscheck` and `trust` write published "
               "verification claims; `precompute` holds the definition of data_fingerprint itself; `__init__` binds "
               "the vendored Qualtran tree. `full_frontier.fingerprint` hashes only three modules of this package.",
        "paths": (f"{ENGINE}/site_engine",),
        # the three modules the fingerprint does cover are excluded so a change there is attributed to the
        # fingerprint, not to this group
        "exclude": tuple(f"{ENGINE}/site_engine/{f}" for f in _FULL_SITE_ENGINE),
    },
    {
        "id": "vendor.qualtran",
        "enforce": True,
        "requiresCacheRebuild": True,
        "why": "The gate counts themselves. `tcost` binds this tree (site_engine/__init__ sets TCOST_QUALTRAN_PATH; "
               "tcost/__init__ chooses it outside CI) and every Add, Subtract, And and ProductPhaseLeaf cost comes "
               "from it. The model records ONLY `qualtran.version`, read from this tree's own `_version.py`. An "
               "in-place patch of a bloq moves every published price and changes neither that string nor "
               "`dataFingerprint`. This is the single largest hole the guard closes.",
        "paths": (f"{ENGINE}/vendor",),
    },
    {
        "id": "engine.cache",
        "enforce": True,
        "why": "`build_epoch_input` reads these files raw: the planner-row loop takes "
               "`cache['leaderboard']['rows']` (every row id, createdAt and pr of every board and delta) and the "
               "chart loop takes `cache['milestones']` (every reference and benchmark curve) with a bare "
               "json.loads and no fingerprint check, although each file carries the fingerprint it was keyed on. "
               "See `cache_report` for the validation the exporter omits.",
        "paths": (f"{ENGINE}/cache",),
    },
    {
        "id": "engine.data.forshor_submitted",
        "enforce": True,
        "why": "Excluded from `data_fingerprint` with the comment 'nothing in a frontier reads them'. True of the "
               "frontiers. Not true of the published panels: `submitted.seed_forshor_verified()` feeds "
               "`policy_view._source`, which writes a table's published `status` and `checkedBy`, and "
               "`full_frontier._records` prices the policy-record board from it.",
        "paths": (f"{ENGINE}/data/{_DATA_SKIP_TOP}",),
    },
    {
        "id": "engine.environment",
        "enforce": True,
        "why": "`engine/requirements.txt` pins the Python environment the counts are produced in (the engine "
               "environment action and the policy verifier both install from it). A pin change can move a number "
               "and is hashed by neither fingerprint.",
        "paths": (f"{ENGINE}/requirements.txt",),
    },
    {
        "id": "tcost.noncode",
        "enforce": True,
        "why": "`data_fingerprint` hashes only `*.py` under `tcost/`, so its data files are uncovered: "
               "`exact/SOURCES.json` (the provenance of the exact-width functions) and `certification/{specs,"
               "results}/*.json` (the seam certificates). Enforced conservatively: the audit could not establish "
               "that `exact/SOURCES.json` is never read on the price path.",
        # named file by file rather than as `tcost` minus `*.py`: git pathspec globs do not cross a `/`, so an
        # exclusion written as `tcost/**/*.py` silently keeps the top-level modules, which ARE fingerprinted.
        "paths": (f"{ENGINE}/tcost/exact/SOURCES.json", f"{ENGINE}/tcost/certification"),
    },
    {
        "id": "export_v2.model",
        "enforce": False,
        "why": "ADVISORY. These files define the epoch and build the published panel, and are deliberately NOT an "
               "epoch input (`pricing._all_tables`: 'Kept here, outside evaluation/engine, so the fix does not move "
               "engineRevision (and so the epoch)'). They are listed so a reviewer can see when the computation "
               "moved, but they are not enforced: they change for many legitimate reasons, and failing on them "
               "would make the guard unusable. A change here that moves a PRICE is still unguarded; R3 in "
               "this module's docstring states that uncovered surface exactly.",
        "paths": ("evaluation/export_v2/epoch.py", "evaluation/export_v2/canon.py",
                  "evaluation/export_v2/pricing.py", "evaluation/export_v2/mapping.py"),
    },
)


def _git(*args, repo: Path = REPO) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


# ------------------------------------------------------------------ mirrors of the engine's own fingerprints
def data_fingerprint(repo: Path = REPO) -> str:
    """`site_engine.precompute.data_fingerprint()`, recomputed from the working tree without importing the engine."""
    engine, data = repo / ENGINE, repo / ENGINE / "data"
    h = hashlib.sha256()
    for p in sorted(data.rglob("*")):
        if p.is_file() and p.name != ".DS_Store" and p.relative_to(data).parts[0] != _DATA_SKIP_TOP:
            h.update(str(p.relative_to(data)).encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    for p in sorted((engine / "tcost").rglob("*.py")) + sorted((engine / "forshor_qualtran").rglob("*.py")) \
            + [engine / "sto_currency.py"]:
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:16]


def full_fingerprint(repo: Path = REPO) -> str:
    """`site_engine.full_frontier.fingerprint()` -- the model's `dataFingerprint` -- recomputed the same way."""
    engine = repo / ENGINE
    h = hashlib.sha256(data_fingerprint(repo).encode())
    for p in sorted((engine / "fullalg").rglob("*")) + sorted((engine / "paper_model").rglob("*")):
        if p.is_file() and p.suffix in _FULL_SUFFIXES and "__pycache__" not in p.parts:
            h.update(hashlib.sha256(p.read_bytes()).digest())
    for f in _FULL_SITE_ENGINE:
        h.update(hashlib.sha256((engine / "site_engine" / f).read_bytes()).digest())
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ the epoch's inputs, without the engine
def qualtran_version(repo: Path = REPO) -> str:
    """The vendored tree's own `__version__`, the string the model records (`tcost.qualtran_provenance`)."""
    text = (repo / ENGINE / "vendor" / "Qualtran-main" / "Qualtran-main" / "qualtran" / "_version.py").read_text()
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise RuntimeError("the vendored qualtran/_version.py declares no __version__")


def forshor_pin(repo: Path = REPO) -> str:
    """`epoch.forshor_pin()`: the ForShor revision `lake-manifest.json` pins."""
    for package in json.loads((repo / "lake-manifest.json").read_text())["packages"]:
        if package["name"] in ("Fast_multiplication", "ForShor"):
            return package["rev"]
    raise RuntimeError("lake-manifest.json has no ForShor package")


def epoch_inputs(repo: Path = REPO) -> dict:
    """Every epoch input readable without the pinned engine environment. If all of these stand still, the epoch
    stands still; the model's remaining fields live in files the groups above hash."""
    return {
        "dataFingerprint": full_fingerprint(repo),              # model.dataFingerprint
        "engineDataFingerprint": data_fingerprint(repo),        # its inner half; also the frontier caches' key
        "engineRevision": _git("log", "-1", "--format=%H", "--", ENGINE, repo=repo).strip(),
        "qualtranVersion": qualtran_version(repo),              # model.qualtran.version
        "forshorPin": forshor_pin(repo),                        # model.forshor.pin
        "leanToolchain": (repo / "lean-toolchain").read_text().strip(),   # model.lean
    }


# ------------------------------------------------------------------ group hashing
def _pathspec(group: dict) -> list:
    """The group's pathspec, in git's own notation. One notation, applied by git itself everywhere this module
    looks at a group -- the listing, the hash and the `git diff` that names the moved files -- so the definition
    cannot mean one thing to the hash and another to the explanation. Exclusions are exact paths only."""
    return [*group["paths"], *(f":(exclude){p}" for p in group.get("exclude", ()))]


def group_entries(group: dict, repo: Path = REPO) -> list:
    """The committed files of a group as `<blob sha> <path>`, oldest-first by path. HEAD-exact (`--with-tree=HEAD`),
    like the `git ls-tree` idiom `scripts/ci/verification_identity.py` uses for the verification fingerprint, so the
    hash is of what is COMMITTED: untracked bytecode and scratch files cannot perturb it."""
    out = _git("ls-files", "--with-tree=HEAD", "--format=%(objectname) %(path)", "--",
               *_pathspec(group), repo=repo)
    return sorted(line for line in out.splitlines() if line)


def group_hash(group: dict, repo: Path = REPO) -> dict:
    entries = group_entries(group, repo)
    if not entries:
        raise RuntimeError(f"group {group['id']!r} matches no committed file; its pathspec is wrong")
    return {"files": len(entries),
            "sha256": hashlib.sha256("\n".join(entries).encode()).hexdigest()}


def moved_files(group: dict, since: str, repo: Path = REPO) -> list:
    """Exactly which files of the group changed between the manifest's head and HEAD."""
    try:
        out = _git("diff", "--name-status", since, "HEAD", "--", *_pathspec(group), repo=repo)
    except subprocess.CalledProcessError:
        return []      # the manifest's head is not an ancestor of this checkout; the hash mismatch still stands
    return [line for line in out.splitlines() if line]


# ------------------------------------------------------------------ the caches the exporter reads unvalidated
def cache_report(repo: Path = REPO, *, want_data: str = None, want_full: str = None) -> dict:
    """Validate every committed cache against the fingerprint it is keyed on -- the check
    `site_engine.precompute.load` and `site_engine.full_frontier.load` perform on read and
    `export_v2.epoch.build_epoch_input` skips.

    `frontier_*.json` are keyed on `data_fingerprint()` with `CACHE_SCHEMA` 4 and carry their width rule;
    `fullalg_*.json` are keyed on `full_frontier.fingerprint()` with `FULL_SCHEMA` 7. Both keys are mirrored above,
    so this IS computable here -- the fixpoint that blocks adding the caches to the fingerprint does not block
    validating them against it.

    `want_data` / `want_full` override the mirrored fingerprints; `test_price_surface.py` uses them to prove the
    comparison is live rather than asserting a tautology."""
    cache = repo / ENGINE / "cache"
    want_data = want_data or data_fingerprint(repo)
    want_full = want_full or full_fingerprint(repo)
    model = _site_model_version(repo)
    rows, stale = [], []
    for p in sorted(cache.glob("*.json")):
        c = json.loads(p.read_text())
        full = p.name.startswith("fullalg_")
        want = want_full if full else want_data
        row = {"file": p.name, "keyedOn": "full_frontier.fingerprint" if full else "data_fingerprint",
               "fingerprint": c.get("fingerprint"), "expected": want, "schema": c.get("schema"),
               "expectedSchema": 7 if full else 4, "model": c.get("model"), "expectedModel": model}
        row["ok"] = (row["fingerprint"] == want and row["schema"] == row["expectedSchema"]
                     and row["model"] == model)
        if not full:
            row["widths"] = c.get("widths")
            row["ok"] = row["ok"] and row["widths"] in ("exact", "scan")
        rows.append(row)
        if not row["ok"]:
            stale.append(p.name)
    return {"caches": rows, "stale": stale, "dataFingerprint": want_data, "dataFingerprintFull": want_full}


def _site_model_version(repo: Path = REPO) -> str:
    """`SITE_MODEL_VERSION` from `engine/sto_currency.py` -- the model's `id` and each cache's `model` field."""
    for line in (repo / ENGINE / "sto_currency.py").read_text().splitlines():
        if line.startswith("SITE_MODEL_VERSION"):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise RuntimeError("engine/sto_currency.py declares no SITE_MODEL_VERSION")


# ------------------------------------------------------------------ manifest
def build(repo: Path = REPO) -> dict:
    return {
        "schema": SCHEMA,
        "note": "Data, not a computation. scripts/ci is not an epoch input; nothing here is read by "
                "evaluation/export_v2/epoch.py. See scripts/ci/price_surface.py for the rule.",
        "head": _git("rev-parse", "HEAD", repo=repo).strip(),
        "epochInputs": epoch_inputs(repo),
        "groups": {g["id"]: {"enforce": g["enforce"], "pathspec": list(_pathspec(g)), **group_hash(g, repo)}
                   for g in GROUPS},
    }


def load(path: Path = MANIFEST) -> dict:
    m = json.loads(path.read_text())
    if m.get("schema") != SCHEMA:
        raise RuntimeError(f"{path.name}: schema {m.get('schema')!r}, expected {SCHEMA!r}")
    return m


def require_history(repo: Path = REPO) -> None:
    """Refuse to judge on a truncated history.

    `engineRevision` is `git log -1 -- evaluation/engine`, so a shallow checkout cannot compute it. The value it
    returns instead differs from the manifest's, which reads as "an epoch input moved" -- and enforcement is
    conditioned on the epoch standing still, so the guard would quietly become advisory-only and never fire. A
    guard that cannot see is not a passing guard; it is no guard. `actions/checkout` defaults to depth 1, so this
    is the normal state of a CI job unless it asks for `fetch-depth: 0`.
    """
    shallow = _git("rev-parse", "--is-shallow-repository", repo=repo).strip()
    if shallow == "true":
        raise RuntimeError(
            "price surface: this is a shallow checkout, so `engineRevision` cannot be read and the guard would "
            "silently degrade to advisory-only. Check out with `fetch-depth: 0` (see "
            ".github/workflows/contributor-cli.yml).")


def check(manifest: dict, repo: Path = REPO) -> dict:
    """The guard. `findings` is non-empty exactly when a price-relevant path moved with every epoch input standing
    still -- that is, when a published number could have changed with no epoch to attribute it to."""
    require_history(repo)
    now_inputs = epoch_inputs(repo)
    was_inputs = manifest["epochInputs"]
    inputs_moved = {k: {"was": was_inputs.get(k), "now": v} for k, v in now_inputs.items() if was_inputs.get(k) != v}
    epoch_stood_still = not inputs_moved

    groups, findings, advisories, moved_ids = [], [], [], set()
    for g in GROUPS:
        was = manifest["groups"].get(g["id"])
        now = group_hash(g, repo)
        row = {"id": g["id"], "rule": "R1", "enforce": g["enforce"], "files": now["files"],
               "was": (was or {}).get("sha256"), "now": now["sha256"],
               "moved": was is None or was["sha256"] != now["sha256"]}
        if row["moved"]:
            moved_ids.add(g["id"])
            row["files_moved"] = moved_files(g, manifest["head"], repo)
            if was is not None and was.get("files") != now["files"]:
                row["fileCount"] = {"was": was["files"], "now": now["files"]}
            (findings if g["enforce"] and epoch_stood_still else advisories).append(row)
        groups.append(row)

    # R2: every committed cache matches the fingerprint it is keyed on.
    caches = cache_report(repo)
    if caches["stale"]:
        findings.append({"id": "cache.validity", "rule": "R2", "enforce": True, "moved": True,
                         "stale": caches["stale"],
                         "detail": "a committed cache does not match the fingerprint it is keyed on. "
                                   "`precompute.load` and `full_frontier.load` would reject it, but "
                                   "`build_epoch_input` reads these files raw, so its rows and curves would be "
                                   "published as if this engine had priced them. Rebuild: "
                                   "cd evaluation/engine && python -m site_engine.precompute --force"})

    # R3: a change the cache key cannot see must come with a cache rebuild.
    blind = sorted(i for i in moved_ids if i != "engine.cache"
                   and next(g.get("requiresCacheRebuild", False) for g in GROUPS if g["id"] == i))
    if blind and "engine.cache" not in moved_ids:
        findings.append({"id": "cache.coverage", "rule": "R3", "enforce": True, "moved": True,
                         "blind": blind,
                         "detail": "these moved, and NEITHER cache key (`data_fingerprint` for frontier_*.json, "
                                   "`full_frontier.fingerprint` for fullalg_*.json) hashes them, so every committed "
                                   "cache still validates while its contents came from the previous code. The "
                                   "caches were not rebuilt. Rebuild them in the same commit, or state in the PR "
                                   "why the change cannot move a number."})

    return {"ok": not findings, "epochStoodStill": epoch_stood_still, "epochInputs": now_inputs,
            "epochInputsMoved": inputs_moved, "groups": groups, "findings": findings, "advisories": advisories,
            "caches": caches, "manifestHead": manifest["head"],
            "head": _git("rev-parse", "HEAD", repo=repo).strip()}


# ------------------------------------------------------------------ CLI
def _explain(result: dict) -> str:
    lines = []
    for f in result["findings"]:
        if f["rule"] == "R2":
            lines.append(f"  [R2 cache validity] {', '.join(f['stale'])}")
            for row in result["caches"]["caches"]:
                if not row["ok"]:
                    lines.append(f"      {row['file']}: carries {row['fingerprint']}, this engine keys it on "
                                 f"{row['expected']} ({row['keyedOn']})")
            lines.append(f"      {f['detail']}")
            continue
        if f["rule"] == "R3":
            lines.append(f"  [R3 cache coverage] moved without a cache rebuild: {', '.join(f['blind'])}")
            lines.append(f"      {f['detail']}")
            for i in f["blind"]:
                lines.append(f"      {i}: {next(g['why'] for g in GROUPS if g['id'] == i)}")
            continue
        why = next(g["why"] for g in GROUPS if g["id"] == f["id"])
        lines.append(f"  [R1 epoch coupling] {f['id']}: {f['was']} -> {f['now']}  ({f['files']} committed files)")
        for entry in f.get("files_moved", [])[:40]:
            lines.append(f"      {entry}")
        if len(f.get("files_moved", [])) > 40:
            lines.append(f"      ... and {len(f['files_moved']) - 40} more")
        lines.append(f"      why this is price-relevant: {why}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true",
                    help="re-record the manifest at HEAD; only after a DELIBERATE epoch move, in the same commit")
    ap.add_argument("--report", action="store_true", help="print the full result as JSON and exit 0")
    a = ap.parse_args(argv)

    if a.update:
        MANIFEST.write_text(json.dumps(build(), indent=1, sort_keys=False) + "\n")
        print(f"recorded {MANIFEST.relative_to(REPO)} at {build()['head'][:9]}")
        return 0

    result = check(load())
    if a.report:
        print(json.dumps(result, indent=1))
        return 0

    inputs = result["epochInputs"]
    print(f"price surface at {result['head'][:9]} (manifest {result['manifestHead'][:9]})")
    print(f"  dataFingerprint {inputs['dataFingerprint']}  engineRevision {inputs['engineRevision'][:9]}  "
          f"qualtran {inputs['qualtranVersion']}")
    print(f"  caches: {len(result['caches']['caches'])} committed, "
          f"{len(result['caches']['stale'])} not matching their own fingerprint")
    for row in result["advisories"]:
        print(f"  moved, {'epoch moved too' if not result['epochStoodStill'] else 'advisory only'}: {row['id']}")
    if result["epochInputsMoved"]:
        for k, v in result["epochInputsMoved"].items():
            print(f"  epoch input moved: {k} {v['was']} -> {v['now']}")
        print("  -> the epoch has moved; re-record with --update in the same commit")
    if result["ok"]:
        print("OK: no price-relevant path moved while the epoch stood still")
        return 0
    print("\nFAIL: a price-relevant path moved and the epoch did not.\n"
          "Every epoch input is unchanged, so a published number can change with nothing to attribute it to.\n"
          "Either revert the change, or make it a deliberate epoch move (AGENTS.md rule 1: a planned migration\n"
          "with a pause on submissions) and re-record this manifest in the same commit.\n", file=sys.stderr)
    print(_explain(result), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
