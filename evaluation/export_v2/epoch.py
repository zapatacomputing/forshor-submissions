"""Build an epoch: the model, the planner rows with their evidence, the reference curves and every panel, as one
`forshor.epoch-input/v2` document. Ranks, boards and the snapshot id are derived from it downstream, not here.

Planner rows are the policies the epoch's planner builds from the table history ("STO frontier after <submission>"), at each
delta, canonicalized as the seed records are (consecutive same-table bands merged; prices unchanged). Many rows share one
policy, so the harness needs to run on far fewer policies than there are rows. A row with no valid policy-level evidence is
still listed, unranked."""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import engine, mapping, pricing
from .canon import epoch_of, policy_digest, sha256_hex
from .evidence import EvidenceStore

REPO = Path(__file__).resolve().parents[2]
FILE_KEY = {"1e-2": "1e-02", "3e-4": "3e-04", "1e-4": "1e-04", "1e-6": "1e-06"}
MODES = {"pp": "PhaseProduct", "ptp": "PhaseTripleProduct"}
#: ShorTableOptimization commit the interim fork is based on (upstream main, 2026-11-03).
STO_COMMIT = "361e81e25965506423d169530a7decb5b27973d4"
ARCHIVE_PR = "https://github.com/VerifiedQC/ShorTableOptimization/pull/{}"


#: The epoch model's `negation` field names ForShor's NegationMethod, not the engine's `ConventionSpec.negate` implementation. Mapping:
#:   epoch `incrementer`  = ForShor `oneQubit` = engine `gidney_circuit` (the DEFAULT): complement plus Gidney's one-clean-ancilla increment,
#:                          charged as ONE clean qubit with the Lean counts (Toffoli 12w-28 / 10w-18, CNOT 17w-22 / 17w-5, X 5w-3 / 5w+1);
#:   epoch `scratch`      = ForShor `scratch` = engine `legacy` (a w-bit constant-one register plus a Cuccaro adder).
#: The engine's own `incrementer` (a flat 32 w Toffoli) is the SUPERSEDED first draft of PR #79 and has no epoch name: an engine run priced
#: with it cannot start an epoch. The model field stays `incrementer` (no contract change); only this table and the README explain it.
NEGATION_OF = {"gidney_circuit": "incrementer", "legacy": "scratch"}


#: Board rows whose Q is, or equals, a quantity Lean's kernel has checked. A row listed here gets `leanPeak` and `build_epoch_input` refuses to
#: export it unless the engine's Q equals the number, so a label never claims Lean evidence the number does not have.
#:  "ptp": row id -> the kernel-checked qubit number at n = 2048, exact widths, ForShor `oneQubit`: the theorems
#:         `Peak.Board.Row<id>.peak_oneQubit_exact` (PhaseTripleProduct/Proofs/Peak/BoardNumerals/, in ForShor main at the pin;
#:         both negation methods give the same number: no table negates).
#:  "pp":  (row id, the policy's 2048 ladder) -> the full order-finding run's Q, the Lean module and whether ForShor's CI builds it. From shard S7: the
#:         theorems `Shor.QubitCount.PolicyNumeralsBoard.<module>.balanced_exact_oneQubit` (QubitCount/Proofs/PolicyNumeralsBoard/<module>.lean) say the
#:         balanced peak adds N extra qubits; Q = 4095 + 8795 + N (`board_q_agrees`). They are on ForShor branch swap003 (commit b6fab89, based on the pin), NOT
#:         in ForShor main yet. Row 6f7d25984ff8 re-optimises per delta, so it has two policies (K8 at 1e-2 and 3e-4, K6 at 1e-4 and 1e-6), told apart by the ladder.
#:         `ci` is False for the four modules that need 13 to 15 GB (library PolicyNumeralsBoardExactHeavy): kernel-checked locally, not built in CI.
LEAN_PEAK: Dict[str, Dict[Any, Any]] = {
    "ptp": {"82eaf805f71c": 6405, "6f7d25984ff8": 7095, "85fbe1296fbd": 6261,
            "265a0f53acbc": 6342, "2ed7dc90d130": 6573, "c733baf3cd08": 6828},
    "pp": {
        ("6f036362302e", "2048:2 -> 1026:2 -> 515:2 -> 259:2 -> 131:2 -> 67:2 -> 35:leaf"): {"q": 13236, "module": "Row6f036362302eExact", "n": 346, "ci": True},
        ("ea01254c45f8", "2048:3 -> 687:3 -> 232:3 -> 81:3 -> 30:leaf"): {"q": 13284, "module": "Rowea01254c45f8Exact", "n": 394, "ci": True},
        ("7df40770c0b6", "2048:4 -> 517:4 -> 134:4 -> 39:leaf"): {"q": 13310, "module": "Row7df40770c0b6Exact", "n": 420, "ci": True},
        ("a3972b965049", "2048:5 -> 421:5 -> 94:leaf"): {"q": 13404, "module": "Rowa3972b965049Exact", "n": 514, "ci": True},
        ("6f7d25984ff8", "2048:6 -> 354:4 -> 94:3 -> 35:leaf"): {"q": 13406, "module": "Row6f7d25984ff8K6Exact", "n": 516, "ci": True},
        ("c7f81cb40081", "2048:6 -> 354:6 -> 71:leaf"): {"q": 13496, "module": "Rowc7f81cb40081Exact", "n": 606, "ci": False},
        ("6f7d25984ff8", "2048:8 -> 279:4 -> 76:3 -> 29:leaf"): {"q": 13676, "module": "Row6f7d25984ff8K8Exact", "n": 786, "ci": False},
        ("f4aff955f16b", "2048:8 -> 279:3 -> 96:3 -> 35:leaf"): {"q": 13660, "module": "Rowf4aff955f16bExact", "n": 770, "ci": False},
        ("f0f15fefe959", "2048:8 -> 279:3 -> 96:3 -> 35:leaf"): {"q": 13660, "module": "Rowf0f15fefe959Exact", "n": 770, "ci": False},
    },
}


def negation_label(engine_impl: str) -> str:
    """The epoch model's `negation` value for the engine's negation convention (see `NEGATION_OF`)."""
    if engine_impl not in NEGATION_OF:
        raise ValueError(f"the engine's negation {engine_impl!r} has no epoch name (known: {sorted(NEGATION_OF)})")
    return NEGATION_OF[engine_impl]


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def forshor_pin(repo: Path = REPO) -> str:
    for package in json.loads((repo / "lake-manifest.json").read_text())["packages"]:
        if package["name"] in ("Fast_multiplication", "ForShor"):
            return package["rev"]
    raise RuntimeError("lake-manifest.json has no ForShor package")


def model_of(*, sto_commit: str = STO_COMMIT, engine_revision: Optional[str] = None) -> Dict[str, Any]:
    """The epoch's model identity. `engineRevision` is the last commit that changed `evaluation/engine`: stable across
    unrelated commits, so only a change that can move a price starts a new epoch. `dataFingerprint` is the engine's own
    fingerprint of its data and every source a price depends on (including the full-run pricer)."""
    eng = engine.load()
    return {
        "id": eng.SITE_MODEL_VERSION,
        "dataFingerprint": eng.full_frontier.fingerprint(),
        "engineRevision": engine_revision or _git("log", "-1", "--format=%H", "--", "evaluation/engine"),
        "qualtran": {"version": eng.tcost.qualtran_provenance()["version"], "source": "vendored"},
        "forshor": {"pin": forshor_pin()},
        "sto": {"commit": sto_commit},
        "lean": (REPO / "lean-toolchain").read_text().strip(),
        "widthRule": "exact", "negation": negation_label(eng.fullalg.HEADLINE.negate), "n": pricing.N,
        "rotation": {"synthesis": "Ross-Selinger measured mean, 3.075*log2(1/eps) + 0.86", "fraction": 0.5},
    }


def planner_rows(log: Callable[[str], None] = lambda s: None) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """(board, delta) -> the frontier rows with the canonical planner policy behind each."""
    eng = engine.load()
    subs = eng.library.submissions()
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for delta_key, file_key in FILE_KEY.items():
        delta = mapping.DELTA_VALUES[delta_key]
        for board, mode in MODES.items():
            cache = json.loads((eng.engine_dir / "cache" / f"frontier_{board.upper()}_{file_key}.json").read_text())
            rows = []
            for r in cache["leaderboard"]["rows"]:
                i = next(i for i, s in enumerate(subs, start=1) if s.id == r["id"])
                tabs = [T for T in eng.library.sto_tables(upto=i) if T.mode == mode]
                P, _ = eng.planner.optimal_policy(tabs, mode, pricing.N, delta)
                Q = eng.seed_policies.canonical(P)
                rows.append({"id": r["id"], "createdAt": r["created_at"], "pr": r.get("pr"), "policy": Q, "number": i,
                             "digest": policy_digest(Q.to_json())})
            out[(board, delta_key)] = rows
            log(f"planner rows {board} {delta_key}: {len(rows)}")
    return out


# Deliberately weaker policies, so the boards and the T-against-qubits plot have structure beyond the planner's frontier: for each
# board family, the best policy that uses a single arity k at every level. They are valid policies priced by the same engine; they are
# not claimed to be good, and they have no STO mirror evidence. Generated once at the default delta and repriced at every delta, so a
# baseline is the same policy on every delta. Their creation time is a fixed constant (the day they were generated), never "now", so the
# export stays reproducible.
BASELINE_ARITIES = (2, 3, 4, 5, 6)
BASELINE_CREATED_AT = "2026-11-06T00:00:00Z"


def baseline_policies(log: Callable[[str], None] = lambda s: None) -> List[Dict[str, Any]]:
    """The single-arity baseline policies: PhaseProduct first (they appear on the PhaseProduct and full boards), then
    PhaseTripleProduct, numbered after the library's last submission. A policy with no band is skipped: it is not a policy."""
    eng = engine.load()
    subs = eng.library.submissions()
    tabs = eng.library.sto_tables(upto=len(subs))
    out: List[Dict[str, Any]] = []
    for board, mode in MODES.items():
        for k in BASELINE_ARITIES:
            chosen = [t for t in tabs if t.mode == mode and t.k == k]
            if not chosen:
                continue
            P, _ = eng.planner.optimal_policy(chosen, mode, pricing.N, mapping.DELTA_VALUES["1e-2"], name=f"baseline-k{k}")
            Q = eng.seed_policies.canonical(P)
            if not Q.bands:
                # The planner finds no band at this arity (PhaseTripleProduct at k = 2 cannot shrink the width): the policy is the bare
                # base case. The harness refuses it ("bands must be a nonempty list") and it is not a submittable policy, so it is not listed.
                log(f"baseline {board} k={k}: no band, skipped")
                continue
            digest = policy_digest(Q.to_json())
            if any(b["digest"] == digest for b in out):
                continue
            out.append({"id": digest[:12], "createdAt": BASELINE_CREATED_AT, "pr": None, "policy": Q, "digest": digest,
                        "number": len(subs) + len(out) + 1, "board": board, "baseline": True})
            log(f"baseline {board} k={k}: {digest[:12]} forshor_sub{len(subs) + len(out)}")
    return out


# The leaderboard's starting rows when the seed starts only from the Kahanamoku-Meyer and Yao tables (`--kmy-only`, 2026-11-10 decision): for each board
# family the planner's optimum over exactly the tables in the “Parallel sequences” appendix of Kahanamoku-Meyer and Yao (2024) (`library.milestone_tables("kmy", mode)`: PhaseProduct k = 3; PhaseTripleProduct k = 3 and 4),
# generated once at the default delta and repriced at every delta like the baselines, so a row is the same policy on every delta. Their creation
# time is a fixed constant (not "now", so the export stays reproducible; on or before any seed time, as the contract requires of a row's date).
STARTING_CREATED_AT = "2026-11-10T00:00:00Z"


def starting_policies(log: Callable[[str], None] = lambda s: None) -> List[Dict[str, Any]]:
    """The starting policies (the planner's optimum over the Kahanamoku-Meyer and Yao tables, not a policy of theirs), PhaseProduct first (it appears on the PhaseProduct and full boards), then PhaseTripleProduct, named forshor_sub1, forshor_sub2."""
    eng = engine.load()
    out: List[Dict[str, Any]] = []
    for board, mode in MODES.items():
        tabs = eng.library.milestone_tables("kmy", mode)
        if not tabs:
            continue
        P, _ = eng.planner.optimal_policy(tabs, mode, pricing.N, mapping.DELTA_VALUES["1e-2"], name=f"starting-{board}")
        Q = eng.seed_policies.canonical(P)
        if not Q.bands:
            log(f"starting {board}: no band, skipped")
            continue
        digest = policy_digest(Q.to_json())
        out.append({"id": digest[:12], "createdAt": STARTING_CREATED_AT, "pr": None, "policy": Q, "digest": digest,
                    "number": len(out) + 1, "board": board, "starting": True})
        log(f"starting {board}: {digest[:12]} forshor_sub{len(out)} " + " -> ".join(f"{b.threshold}:{b.table.k}" for b in Q.bands))
    return out


def starting_listed(starts: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """What is listed in a seed that starts only from the Kahanamoku-Meyer and Yao tables: each board family's starting policy, at every delta."""
    return {(b["board"], delta): [b] for b in starts for delta in FILE_KEY}


# There is no guard on tables that negate any more. Until `engine/negation-real` the PhaseProduct and PhaseTripleProduct boards (tcost) priced a negation
# as Qualtran's `Negate` while the full run charged ForShor's `oneQubit` circuit, so a policy with a negating table (every KMY table negates) would have
# had boards that disagreed, and it was refused. Now tcost prices a negation with the same counts and the same one clean qubit as the full run.


def first_per_policy(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each distinct policy once, as the earliest row that has it. The planner's pick often does not change when a later submission
    arrives, so the frontier repeats one policy under many submissions (at 1e-2, sixteen PhaseProduct rows were the same policy with
    the same T and qubits); listing them all would be duplicates, not more policies, and would make one submission number mean many rows."""
    first: Dict[str, Dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["createdAt"], r["id"])):
        first.setdefault(r["digest"], r)
    return list(first.values())


def list_rows(rows_in: Dict[Tuple[str, str], List[Dict[str, Any]]], baselines: List[Dict[str, Any]]):
    """What is listed, per (board, delta): every DISTINCT planner policy on the frontier (D9: checked or not, ranked together), each as
    the earliest submission that has it, and the baseline policies of that board family, at every delta."""
    deltas = sorted({d for (_b, d) in rows_in})
    listed: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for (board, delta), board_rows in rows_in.items():
        listed[(board, delta)] = first_per_policy(board_rows) + [b for b in baselines if b["board"] == board]
    for b in baselines:   # a baseline on a board that has no frontier rows at some delta is still listed there
        for d in deltas:
            if (b["board"], d) not in listed:
                listed[(b["board"], d)] = [x for x in baselines if x["board"] == b["board"]]
    return listed


def build_epoch_input(*, store: EvidenceStore, publisher_commit: str, archive_commit: Optional[str] = None,
                      updated_at: Optional[str] = None, model: Optional[Dict[str, Any]] = None,
                      log: Callable[[str], None] = lambda s: None, kmy_only: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The epoch input and the evidence report (which policies have valid evidence, and why the others do not).
    `kmy_only`: the seed that starts only from the Kahanamoku-Meyer and Yao tables (see `starting_policies`): no planner frontier, no baselines, no reference curves, no benchmark rows; the only listed
    policies are the starting policies."""
    eng = engine.load()
    model = model or model_of()
    epoch = epoch_of(model)
    rows_in = {} if kmy_only else planner_rows(log)
    views: Dict[Tuple[str, str, str], Dict[str, Any]] = {}   # (board, delta, policy digest) -> priced view
    policies: Dict[str, Dict[str, Any]] = {}                 # digest -> {mode, policy, uses}
    boards_of = lambda board: ("pp", "full") if board == "pp" else ("ptp",)

    def view_of(b: str, delta: str, digest: str) -> Dict[str, Any]:
        if (b, delta, digest) not in views:
            log(f"pricing {b} {delta} {digest[:12]}")
            views[(b, delta, digest)] = pricing.price_views(policies[digest]["policy"], f"Planner policy {digest[:12]}", delta, [b])[b]
        return views[(b, delta, digest)]

    for (board, delta), rows in rows_in.items():
        for r in rows:
            entry = policies.setdefault(r["digest"], {"mode": MODES[board], "board": board, "policy": r["policy"], "uses": []})
            entry["uses"].append({"board": board, "delta": delta, "rowId": r["id"]})
    baselines = [] if kmy_only else baseline_policies(log)
    starts = starting_policies(log) if kmy_only else []
    for b in baselines + starts:
        entry = policies.setdefault(b["digest"], {"mode": MODES[b["board"]], "board": b["board"], "policy": b["policy"], "uses": []})
        entry["uses"].append({"board": b["board"], "delta": "1e-2", "rowId": b["id"]})

    # Evidence is about the policy, not about a delta, so it is looked up once per distinct policy (one pricing to get its file).
    evidence: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
    for digest, entry in policies.items():
        first = entry["uses"][0]
        b0 = boards_of(first["board"])[0]
        evidence[digest] = store.stomirror(view_of(b0, first["delta"], digest)["policy_json"], model, "ptp" if entry["mode"] == MODES["ptp"] else "pp")

    listed = starting_listed(starts) if kmy_only else list_rows(rows_in, baselines)

    panels: Dict[str, Any] = {}
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (board, delta), board_rows in listed.items():
        for r in board_rows:
            for b in boards_of(board):
                view = copy.deepcopy(view_of(b, delta, r["digest"]))
                title = (f"Kahanamoku-Meyer and Yao (2024) tables, policy {r['digest'][:12]}" if r.get("starting")
                         else f"Baseline policy {r['digest'][:12]}" if r.get("baseline") else f"STO frontier after {r['id']}") + (
                    f", {MODES[board]}, n = 2048, delta = {mapping.DELTA_VALUES[delta]:g}") + (", full order finding" if b == "full" else "")
                view["title"] = view["policy_json"]["name"] = title
                verification = {"stoMirror": evidence[r["digest"]][0], "forshorPolicy": None}   # None for a planner policy the STO mirror has not checked (D9)
                detail = mapping.panel_path(epoch, r["id"], b, delta)
                # the number the kernel checked is compared with the Q the panel will show: the full run's live peak, or the PTP panel's Q
                lean_peak: Any = False
                if b == "ptp":
                    lean_q = LEAN_PEAK["ptp"].get(r["id"])
                    lean_peak = lean_q is not None
                elif b == "full":
                    ev = LEAN_PEAK["pp"].get((r["id"], view["ladder_str"]))
                    lean_q = ev["q"] if ev else None
                    lean_peak = {k: ev[k] for k in ("module", "n", "ci")} if ev else False
                else:
                    lean_q = None
                shown = view["full"]["qubits_peak"] if b == "full" else view["qubits"]
                if lean_q is not None and shown != lean_q:
                    raise ValueError(f"row {r['id']} on {b}: Q {shown} is not the kernel-checked {lean_q}; the label would be false")
                if b == "full" and lean_q is not None and 4095 + 8795 + lean_peak["n"] != lean_q:
                    raise ValueError(f"row {r['id']}: 4095 + 8795 + {lean_peak['n']} is not {lean_q}")
                panel = mapping.panel_of(view, epoch=epoch, row_id=r["id"], board=b, delta=delta, verification=verification, lean_peak=lean_peak)
                if detail in panels:
                    raise ValueError(f"two listed rows share the id {r['id']} on {b} at {delta}")
                panels[detail] = panel
                rows.setdefault(f"{b}/{delta}", []).append(mapping.row_of(
                    panel, origin="planner", name=f"forshor_sub{r['number']}", created_at=r["createdAt"], pull_request_url=ARCHIVE_PR.format(r["pr"]) if r["pr"] else None,
                    submission_id=None, detail=detail))

    charts: Dict[str, Any] = {}
    benchmarks: Dict[str, Any] = {}
    for delta, file_key in FILE_KEY.items():
        for b in mapping.BOARDS:
            name = "fullalg" if b == "full" else f"frontier_{b.upper()}"
            if kmy_only:
                series = {}     # no reference curves at all (the page draws none), so nothing is read from the engine's caches
            else:
                series = {m: v for m, v in json.loads((eng.engine_dir / "cache" / f"{name}_{file_key}.json").read_text())["milestones"].items()}
                series = {m: v["points"] for m, v in series.items()}
            charts[mapping.chart_path(epoch, b, delta)] = mapping.chart_of(series, epoch=epoch, board=b, delta=delta)
            benchmarks[f"{b}/{delta}"] = [] if kmy_only else mapping.benchmarks_of(series)

    updated_at = updated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {"schema": "forshor.epoch-input/v2", "model": model, "rows": rows, "benchmarks": benchmarks, "submissions": [],
                "charts": charts, "panels": panels, "updatedAt": updated_at,
                "archiveCommit": archive_commit or _git("rev-parse", "HEAD"), "publisherCommit": publisher_commit}
    document["sourceDigest"] = sha256_hex(document)
    report = evidence_report(policies, evidence)
    report["rowsBaseline"] = sum(1 for v in listed.values() for r in v if r.get("baseline"))
    report["rowsStarting"] = sum(1 for v in listed.values() for r in v if r.get("starting"))
    report["rowsListed"] = sum(len(v) for v in listed.values())   # baseline policy x delta rows
    return document, report


def evidence_report(policies: Dict[str, Dict[str, Any]], evidence: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]]) -> Dict[str, Any]:
    """Which planner policies have valid policy-level evidence for this epoch, and why not for the rest (open item O1b). Policies without it are still listed (D9) and marked unchecked on the page."""
    items = []
    for digest, entry in sorted(policies.items(), key=lambda kv: (kv[1]["mode"], kv[0])):
        mirror, why = evidence[digest]
        items.append({"digest": digest, "mode": entry["mode"], "rows": len(entry["uses"]), "uses": entry["uses"],
                      "ladder": " -> ".join(f"{b.threshold}:{b.table.k}" for b in entry["policy"].bands),
                      "evidence": "valid" if mirror else "missing", "reason": why})
    rows = sum(i["rows"] for i in items)
    return {"policies": items, "distinctPolicies": len(items), "rowsTotal": rows,
            "rowsWithEvidence": sum(i["rows"] for i in items if i["evidence"] == "valid"),
            "rowsUnchecked": sum(i["rows"] for i in items if i["evidence"] != "valid"),   # listed, marked unchecked (D9)
            "policiesWithoutEvidence": [i["digest"] for i in items if i["evidence"] != "valid"]}
