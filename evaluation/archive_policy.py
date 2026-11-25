"""Archive an accepted policy submission to the `submission-results` branch.

The trusted publish job calls this (through `intake_v2.py --publish-from ... --archive CHECKOUT`) *before* it publishes: the publisher requires the archive's
accepted ids to include the new head commit, and `publish_v2._archive_view` reads them from this branch.

Layout, beside the table-generation archive and independent of it (the v1 index `leaderboard/results.json` is table-generation's: its writer drops any entry
without table results, so policy entries do not go there):

    leaderboard/policy-submissions.json          {"schema": "forshor.policy-archive/v1", "policies": [<entry>, ...]}   sorted by (created_at, policy_id)
    results/policy-submissions/<head>/Policy.lean     the submitted file, byte for byte
    results/policy-submissions/<head>/policy.json     the policy as the boards price it, with its digest
    results/policy-submissions/<head>/verification.json   the verification the publish job checked (mirror envelope and ForShor's printed document)
    results/policy-submissions/<head>/summary.tex     only when the pull request has one: the optional LaTeX summary, byte for byte (never compiled or run);
                                                      the index entry then records `summarySha256`

The summary is held to the same standard as the policy: it must equal the file at `head_sha` on GitHub (fetched through the contents API; no file there means no summary)
AND the verification must carry the same text and hash. A summary on one side only, or two that differ, is refused (stage "archive"), as is one that breaks the rules of
`summary_tex.py`.

`<head>` is the pull request's head commit, which is the submission id on the boards. Writing is idempotent: the same head with the same file is a no-op, and the
same head with a different file -- or the same file priced as a different RESULT -- is refused. The source is not taken from the (untrusted) verification alone:
it must equal the file at `head_sha` on GitHub.

WHICH RESULT AN ENTRY ARCHIVES
------------------------------
One policy can be priced as more than one result. Each way it can be is one axis, each axis is one index key, and each key is ABSENT for the result the engine
prices by default -- so every row already on the branch keeps exactly the keys it was written with and nothing is backfilled (`KIND_DEFAULT`, `entry_kind`):

    readout     "coherent" (the default, absent) or "semiclassical"   -- `export_v2.readout_overlay`
    primitive   absent (the default `rossSelinger`, which stamps nothing into the compiled document) or a key of
                `PRIMITIVE_T_SITE_KIND` -- `export_v2.primitive_overlay`
    design      absent (no QFT design: `reference` and the empty selection are not designs) or the per-family mapping
                that was priced -- `export_v2.qft_overlay`, at `mapping.DESIGN_DELTA` on the full board
                only, the other three deltas being the design-free panels of the same policy that they are

`intake_v2.publish_verification` passes all three at the archive call site. Before that it passed none, so a published MEASURED
submission was archived as a legacy -- i.e. COHERENT -- entry.

A PRIMITIVE QUALIFICATION IS NOT ARCHIVED, AND THAT IS THE DESIGN
-----------------------------------------------------------------
The `primitive` key above is for a PUBLISHED primitive-bearing submission. It is NOT for a primitive QUALIFICATION, which is
local-only by design and can never reach this module at all:

  * `intake_v2.publish_verification` refuses any receipt carrying `localPrimitiveQualification`, and any compiled document carrying `submitted_primitive`, and that
    refusal fires BEFORE `archive_write` is called (`test_the_primitive_refusal_fires_before_the_archive_is_written`);
  * `--qualify-primitive` requires `--no-publish`, while `archive_write` is wired on `--publish-from` alone, so the two flag sets do not intersect
    (`test_the_cli_refuses_to_publish_a_primitive_qualification`).

DO NOT ADD A LOCAL-ONLY ARCHIVE-WRITE PATH. The absence of one is not missing architecture; it is the route keeping the promise it has always made (the receipts
carry `localOnly: true` / `publishable: false`, and the published row's closed field set -- `SITE_ROW_FIELDS` in `export_v2/pytests/test_design_price_plumbing.py` --
names no primitive price, which the publisher's validators reject rather than ignore). This note exists because the opposite reading has had
to be corrected three times already in this release -- a default-off gate read as "no publication path", a closed enum read as "not expressible", a `staging_*`
filename read as "awaiting deployment". A conservative guard is not a gap. `primitive` and its `tKind` stay exactly as written: unused they cost nothing,
and they are correct the day a primitive-bearing submission is published.

The archive is the ONLY durable record of the design and the primitive. The ledger row cannot carry either: the published row's field set is CLOSED -- an
unrecognised key is rejected, not ignored -- and it names neither (the field set is transcribed into this
repository as `SITE_ROW_FIELDS` in `export_v2/pytests/test_design_price_plumbing.py`), and `mapping.row_of` copies no such key -- yet `mapping.ranked_price_of` now ranks the QFT
projection's own T and qubits, so a design-bearing row would carry the projection's numbers with nothing naming the design. The compiled selection itself is
archived verbatim inside `verification.json` (`printed.submitted_full_run` / `submitted_primitive`); the index key is what makes it findable, comparable and
visible to the variant rules below, which is what the `readout` key is for too.

WHAT AN ENTRY NEVER RECORDS
---------------------------
An entry says WHICH result was archived; it has never carried the number, and `check_entry` refuses one that does (`BARE_PRICE_KEYS`). That is not tidiness:
`adaptiveB3`'s T is an UNCONDITIONAL EXPECTED value, not a bound, and `catalyticB2`'s is deterministic. `primitive_overlay` makes the two structurally
uncollapsible -- no bare `T` field, `tKind` plus exactly one of `tDeterministic` / `tExpectedAdaptive` (`primitive_overlay.py:212-217`). A bare `T` on an index
entry would undo that here, in the one place the result is kept forever. So a primitive-bearing entry records `tKind` -- the overlay's own per-site axis, under
the overlay's own name (the per-site axis keeps the short key because it is the one that is structurally enforced and reaches
production) -- and no number at all. `write_policy` admits the three axes above and nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary_tex  # noqa: E402

BRANCH = "submission-results"
INDEX_PATH = "leaderboard/policy-submissions.json"
INDEX_SCHEMA = "forshor.policy-archive/v1"
DIR = "results/policy-submissions"
SOURCE_PATH = "TableGeneration/Submission/Policy.lean"
SUMMARY_PATH = summary_tex.SUMMARY_PATH
REPOSITORY = "zapatacomputing/forshor-submissions"
HEAD = re.compile(r"[0-9a-f]{40}")


class ArchiveRefused(Exception):
    pass


#: What to do when a policy with the same digest (mode and bands) is already archived. "same_participant": the same GitHub login (compared case-insensitively);
#: "other_participant": anyone else. "refuse" publishes no row and tells the author which earlier submission it repeats; "allow" publishes as usual and the repeat is
#: only identified (the `duplicates` of the archive result, logged by `intake_v2`). Change `other_participant` to "refuse" to stop others repeating a published policy.
DUPLICATE_RULES = {"same_participant": "refuse", "other_participant": "allow"}

#: The result axes an entry may record, and the value each one has for the result the engine prices by default. An
#: entry equal to this on every axis is a LEGACY entry and records none of the keys, so it is byte-identical to every
#: row already on `submission-results` (`test_the_coherent_default_is_byte_identical_to_what_the_archive_already_holds`).
KIND_DEFAULT = {"readout": "coherent", "primitive": None, "design": None}
READOUTS = ("coherent", "semiclassical")
#: The synthesis primitives `export_v2.primitive_overlay` prices standalone, and WHICH KIND OF NUMBER each one's T is:
#: catalyticB2 charges every phase site the worst-case full-width carry path, so its T is DETERMINISTIC; adaptiveB3
#: probes repeat-until-success against a pinned atlas, so its T is an UNCONDITIONAL EXPECTATION. The default
#: `rossSelinger` is recorded as the ABSENCE of the key, never as a value, because it stamps nothing into the compiled
#: document and is priced by the engine itself. Pinned against the overlay's own `PRIMITIVE_OF` and the `kind` its
#: `project` returns, so the two cannot drift.
PRIMITIVE_T_SITE_KIND = {"catalyticB2": "deterministic", "adaptiveB3": "expectedAdaptive"}
#: The boards a submission's rows occupy, by the board its policy is priced on: a PhaseProduct policy enters its own
#: standalone board and the full order-finding board, a PhaseTripleProduct policy only its own (AGENTS.md, "A
#: submission"). An index entry written before `board` was recorded falls back to the PhaseProduct pair, which is what
#: the rows already on the branch are.
BOARDS_OF = {"pp": ["pp", "full"], "ptp": ["ptp"]}
#: The board sets an entry may name, sorted. Legacy and first entries name their whole set; a variant names only what
#: it re-prices.
BOARD_SETS = (["full", "pp"], ["full"], ["pp"], ["ptp"])
#: WHERE each axis re-prices. A readout design and a QFT design re-price the full order-finding run -- both are read by
#: `mapping.ranked_price_of` out of the panel's `fullRun` block, and nothing else. A synthesis primitive re-prices the
#: STANDALONE board only: `primitive_overlay` prices `pp` and `ptp` and states that "the full order-finding board takes
#: no primitive", and its hook sits in `pricing.price_views`'s single-product branch. The boards an axis does not
#: re-price carry the same rows under either result, which is exactly why a variant surrenders them to the earlier entry.
AXIS_BOARD = {"readout": "full", "design": "full", "primitive": "standalone"}
#: Price fields an index entry must never carry. `T` / `rankedT` because `primitive_overlay` makes deterministic and
#: expected-adaptive T structurally uncollapsible and a bare field would undo it here; `ranked_upper_T` because an
#: interval endpoint is not a bound; `qubits` because the full run's count is the live-qubit PEAK and the static layout
#: total is several times larger (`mapping.peak_label` states the distinction the panel renders, and the full board's
#: number comes from `qubits_peak`), so an unlabelled count is ambiguous in its unit.
BARE_PRICE_KEYS = ("T", "t", "rankedT", "ranked_upper_T", "qubits")


class DuplicatePolicy(ArchiveRefused):
    """A policy this submission repeats, and a rule that refuses it. `duplicate_of` describes the earlier archived submission.

    `source` names which check refused, because the same message was raised from two of them (census C21): the readout
    variant planner, whose refusal the writer may catch as control flow, and the writer's own duplicate rules. An
    operator reading a refused run could not tell whether the repeat was found while planning the readout variant or
    while writing the entry, and those are different places to look."""

    def __init__(self, duplicate_of: Dict[str, Any], source: str = "the archive's duplicate rules"):
        who = "you" if duplicate_of["sameParticipant"] else "another participant"
        super().__init__(f"{who} already submitted this exact policy as pull request #{duplicate_of['pull']} (commit {duplicate_of['policyId'][:7]}, "
                         f"{str(duplicate_of['createdAt'])[:10]}); each policy can be published once"
                         + (" per participant" if duplicate_of["sameParticipant"] else "")
                         + f" (refused by {source})")
        self.duplicate_of = duplicate_of
        self.source = source


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def github_source(head_sha: str, token: str, request_json: Callable[..., Any], repository: str = REPOSITORY) -> str:
    """`Policy.lean` as it is at `head_sha` on GitHub (the contents API returns it base64-encoded)."""
    doc = request_json(f"https://api.github.com/repos/{repository}/contents/{SOURCE_PATH}?ref={head_sha}", token)
    if doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
        raise ArchiveRefused(f"GitHub returned no file content for {SOURCE_PATH} at {head_sha}")
    return base64.b64decode(doc["content"]).decode()


def github_summary(head_sha: str, token: str, request_json: Callable[..., Any], repository: str = REPOSITORY) -> Optional[str]:
    """`summary.tex` as it is at `head_sha` on GitHub, or None when the commit has no such file (HTTP 404: the only answer that means "no summary"; any other failure
    propagates, so an outage never turns into "no summary"). Strict UTF-8: a file that is not text is refused here, whatever the verification says."""
    try:
        doc = request_json(f"https://api.github.com/repos/{repository}/contents/{SUMMARY_PATH}?ref={head_sha}", token)
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise
    if isinstance(doc, list) or doc.get("type") not in (None, "file") or doc.get("encoding") != "base64" or not isinstance(doc.get("content"), str):
        raise ArchiveRefused(f"{summary_tex.NAME} is not a regular file at {head_sha}")
    data = base64.b64decode(doc["content"])
    why = summary_tex.problem(data)
    if why:
        # Census C20: summary_tex.problem's three messages are raised here and again from check_summary below, so each
        # route says which file it read. Here it is GitHub's copy at the head commit; there it is the carried copy.
        raise ArchiveRefused(f"{why} (the summary.tex at the pull request's head commit)")
    return data.decode("utf-8")


def read_index(checkout: Path) -> Dict[str, Any]:
    path = checkout / INDEX_PATH
    if not path.exists():
        return {"schema": INDEX_SCHEMA, "policies": []}
    index = json.loads(path.read_text())
    if index.get("schema") != INDEX_SCHEMA or not isinstance(index.get("policies"), list):
        raise ArchiveRefused(f"{INDEX_PATH} is not a {INDEX_SCHEMA} index")
    return index


def policy_ids(checkout: Path) -> List[str]:
    return [p["policy_id"] for p in read_index(checkout)["policies"]]


def find_duplicates(index: Dict[str, Any], *, digest: str, mode: str, login: str, head_sha: str,
                    published_ids: Optional[Collection[str]] = None) -> List[Dict[str, Any]]:
    """The archived submissions with this policy (same digest and mode), other than `head_sha` itself, oldest first. The archive is written before the publisher is
    called, so an entry can exist for a head that was never published; when `published_ids` (the publisher's own list of published heads) is given, only those count."""
    found = []
    for p in index["policies"]:
        if p["digest"] != digest or p["mode"] != mode or p["policy_id"] == head_sha:
            continue
        if published_ids is not None and p["policy_id"] not in published_ids:
            continue
        who = str(p["source"].get("github_login") or "")
        found.append({"policyId": p["policy_id"], "login": who, "pull": p["source"].get("pr_number"), "createdAt": p["source"].get("created_at"),
                      "sameParticipant": who.lower() == login.lower()})
    return sorted(found, key=lambda d: (d["createdAt"] or "", d["policyId"]))


def _design_shape(design: Optional[Dict[str, Any]]) -> bool:
    """Shape only: a non-empty mapping of QFT family -> that family's own choice. The VALUES are not re-checked here --
    `full_run_design` delegates them to `qft_overlay.parse_design`, so there is one validator in the repo and not two,
    and what the archive records is what that validator already accepted."""
    return design is None or (isinstance(design, dict) and bool(design)
                              and all(isinstance(k, str) and isinstance(v, dict) for k, v in design.items()))


def entry_kind(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Which result an archived entry records, on every axis the archive admits (`KIND_DEFAULT`). A legacy entry names
    none of them and means the coherent readout, the default `rossSelinger` primitive and no QFT design -- which is what
    every row already on the branch is -- so reading one never needs a backfill."""
    return {axis: entry.get(axis, default) for axis, default in KIND_DEFAULT.items()}


def entry_boards(entry: Dict[str, Any]) -> List[str]:
    """The boards an archived entry's rows occupy. Legacy entries name none and mean every board their own board's
    policy enters."""
    return entry.get("boards") or BOARDS_OF.get(entry.get("board"), ["pp", "full"])


def variant_boards(board: str, asked: Dict[str, Any], other: Dict[str, Any]) -> List[str]:
    """The boards of a `board` policy whose rows DIFFER between result kinds `asked` and `other`, in board order.

    Read off `AXIS_BOARD` rather than hard-coded, so each axis surrenders the right half: a readout or a QFT design
    re-prices the full run and leaves the standalone row alone (the readout variant rule), while a synthesis primitive re-prices
    the standalone row and leaves the full run alone -- the same rule with the two boards exchanged, because the full
    order-finding board takes no primitive. The boards NOT in this list carry the same rows under both kinds, so the
    earlier entry keeps them and the later one is a variant of it."""
    where = {AXIS_BOARD[axis] for axis in KIND_DEFAULT if asked.get(axis) != other.get(axis)}
    return [b for b in BOARDS_OF.get(board, ["pp", "full"]) if ("full" if b == "full" else "standalone") in where]


def check_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Refuse an index entry that flattens what it archives. Two ways it could:

    a bare price field, which would let a reader rank a number without its kind -- `primitive_overlay` refuses the
    same thing at the other end of the pipeline, and the archive is the copy that is kept forever; or a primitive recorded with a T kind that is not the one the overlay prices it
    with, which would say `adaptiveB3`'s expectation is a deterministic cost (or the reverse) in the one durable record
    of it."""
    bare = [k for k in BARE_PRICE_KEYS if k in entry]
    if bare:
        raise ArchiveRefused(f"an archive entry records which result was archived, never the number: remove {bare} "
                             "and record a field whose name says what kind of number it is")
    primitive = entry.get("primitive")
    if primitive is not None and entry.get("tKind") != PRIMITIVE_T_SITE_KIND.get(primitive):
        raise ArchiveRefused(f"{primitive} prices a {PRIMITIVE_T_SITE_KIND.get(primitive)!r} T, not a "
                             f"{entry.get('tKind')!r} one; a deterministic and an expected T are never the same claim")
    return entry


def plan_readout_variant(index: Dict[str, Any], *, digest: str, login: str, head_sha: str,
                         readout: str, published_ids: Optional[Collection[str]] = None,
                         mode: str = "PhaseProduct", primitive: Optional[str] = None,
                         design: Optional[Dict[str, Any]] = None, board: str = "pp") -> Optional[str]:
    """How this participant's own earlier archived copies of this policy bear on it. Return a prior primitive ID, if any.

    Raises `DuplicatePolicy` for a repeat with the same readout. `mode` is the
    board mode to compare on; readout designs exist for PhaseProduct only, but
    `write_policy` asks about every mode it archives, so the same refusal holds.

    Take only visible records into account. Legacy records omit both circuit
    and board metadata and mean coherent readout with a primitive result.
    Future opt-in entries can carry `readout` and `boards` without backfill.

    THAT OPT-IN HAS ARRIVED:
    `intake_v2.publish_verification` now passes the readout, the primitive and
    the design at the archive call site, so a published measured submission
    reaches this planner as the variant it is instead of as the default it is
    not. Nothing is backfilled: a legacy row is still read exactly as written.

    The name and the readout rule are unchanged. The planner now compares on the whole result kind
    (`KIND_DEFAULT`): a repeat is the same policy priced the SAME way on every axis, and differing on any axis -- the
    readout, the synthesis primitive or the QFT design -- is a variant of the earlier entry, which keeps the boards both
    results price alike. `board` is the submission's own board, which decides how many boards there are to divide.

    TWO SENSES OF "PRIMITIVE" MEET HERE and they are different things: the earlier entry a variant derives from,
    which is what this function RETURNS; and the synthesis primitive, which is now one of the axes it compares on
    and is the `primitive` PARAMETER. The return value is the former."""
    asked = {"readout": readout, "primitive": primitive, "design": design}
    if readout not in READOUTS:
        raise ArchiveRefused("unknown readout design")
    if primitive is not None and primitive not in PRIMITIVE_T_SITE_KIND:
        raise ArchiveRefused(f"unknown synthesis primitive {primitive!r}: the archive admits "
                             f"{sorted(PRIMITIVE_T_SITE_KIND)} and records the default rossSelinger as no key at all")
    if not _design_shape(design):
        raise ArchiveRefused("unknown QFT design: expected the per-family mapping that was priced")
    if asked != KIND_DEFAULT and board not in BOARDS_OF:
        raise ArchiveRefused(f"a readout, primitive or design result cannot be archived for the {board!r} board")
    if asked != KIND_DEFAULT and not variant_boards(board, asked, KIND_DEFAULT):
        # A readout design and a QFT design re-price the full order-finding run, which a PhaseTripleProduct policy does
        # not enter: `readout_overlay` and `qft_overlay` are PhaseProduct's, and intake refuses the pair from the other
        # end ("readout qualification only supports PhaseProduct"). Refused here whether or not an earlier entry exists,
        # so no such row is ever written -- claiming no board, or claiming its own.
        raise ArchiveRefused(f"this result re-prices no board of a {board!r} policy")
    records = {p["policy_id"]: p for p in index["policies"]}
    primitive_of = None
    for previous in find_duplicates(index, digest=digest, mode=mode, login=login, head_sha=head_sha,
                                    published_ids=published_ids):
        if not previous["sameParticipant"]:
            continue
        entry = records[previous["policyId"]]
        chosen = entry_kind(entry)
        boards = entry_boards(entry)
        if chosen["readout"] not in READOUTS or (chosen["primitive"] is not None and chosen["primitive"] not in PRIMITIVE_T_SITE_KIND) \
                or not _design_shape(chosen["design"]) or sorted(boards) not in BOARD_SETS:
            raise ArchiveRefused("unrecognized circuit or boards in published archive entry")
        if chosen == asked:
            raise DuplicatePolicy(previous, "the readout variant planner")
        kept = [b for b in BOARDS_OF.get(board, ["pp", "full"]) if b not in variant_boards(board, asked, chosen)]
        if any(b in boards for b in kept) and primitive_of is None:
            primitive_of = previous["policyId"]
    return primitive_of


def check_summary(v: Dict[str, Any], summary: Optional[str]) -> None:
    """`summary` is the file at the head commit (None: there is none). It must satisfy the rules, and the verification must carry exactly it, in both directions."""
    carried = v.get("summarySource")
    if summary is None and carried is None and "summarySha256" not in v:
        return
    if summary is None:
        raise ArchiveRefused("the verification carries a summary.tex that the pull request's head commit does not have")
    why = summary_tex.problem(summary.encode("utf-8"))
    if why:
        raise ArchiveRefused(f"{why} (cross-checking the verification against the head commit)")
    if carried is None:
        raise ArchiveRefused("the pull request's head commit has a summary.tex that the verification does not carry")
    if carried != summary or v.get("summarySha256") != sha256(summary):
        raise ArchiveRefused("the verification's summary.tex is not the file at the pull request's head commit")


def write_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str,
                 summary: Optional[str] = None, repository: str = REPOSITORY, duplicates: Optional[List[Dict[str, Any]]] = None,
                 published_ids: Optional[Collection[str]] = None, readout: str = "coherent",
                 primitive: Optional[str] = None, design: Optional[Dict[str, Any]] = None,
                 variant: Optional[Dict[str, Any]] = None) -> bool:
    """Write the files and the index entry into the checkout. Returns whether anything changed. Raises `ArchiveRefused` (`DuplicatePolicy` when `DUPLICATE_RULES`
    refuse a repeat). Repeats that the rules allow are appended to `duplicates`. `published_ids`: see `find_duplicates`.
    `variant`, when given, is filled with what the request must carry to match the entry (`primitiveFrom`, the earlier entry this one derives from, or None; and `boards`, the entry's boards), also when the entry was already archived:
    the archive and the published request must name the same boards.

    `readout` is this submission's readout design. Your own earlier copies of the policy go through `plan_readout_variant`: the same readout is still the repeat it has
    always been, the other readout is a variant of them and keeps their primitive, so it enters the full board alone. Only such a non-legacy entry records `readout` and
    `boards`; the default coherent entry is written exactly as it always was, and no archived row is backfilled.

    `primitive` is the declared synthesis primitive (`PRIMITIVE_T_SITE_KIND`; None is the default `rossSelinger`, which stamps nothing and is recorded as no key) and
    `design` the QFT design that was priced (None is no design). They are the same arrangement as `readout`, one axis each, and the planner now compares on all three:
    a repeat is the same policy priced the same way throughout. A primitive-bearing entry also records `tKind` -- the overlay's per-site axis, under its own name -- so the one durable record of an `adaptiveB3` price says it is an expectation; it records no number at all, and `check_entry` refuses an entry that would."""
    if not HEAD.fullmatch(head_sha):
        raise ArchiveRefused("the head commit is not a 40-hex sha")
    if not source.strip():
        raise ArchiveRefused("there is no Lean source to archive")
    if v.get("leanSha256") != sha256(source) or v.get("leanSource") != source:
        raise ArchiveRefused("the verification's Lean source is not the file at the pull request's head commit")
    check_summary(v, summary)
    entry = {"policy_id": head_sha, "digest": v["digest"], "mode": v["mode"], "board": v["board"], "sha256": sha256(source),
             "path": f"{DIR}/{head_sha}", "source": {"repository": repository, "pr_number": pull, "github_login": login, "run_id": str(run_id),
                                                      "created_at": v["createdAt"]}}
    if summary is not None:
        entry["summarySha256"] = sha256(summary)
    kind = {"readout": readout, "primitive": primitive, "design": design}
    index = read_index(checkout)
    old = next((p for p in index["policies"] if p["policy_id"] == head_sha), None)
    if old is not None:
        if old["sha256"] != entry["sha256"] or old["digest"] != entry["digest"] or old.get("summarySha256") != entry.get("summarySha256"):
            raise ArchiveRefused(f"{head_sha} is already archived with a different policy")
        # The same file priced as a different RESULT is a different submission, and the three comparisons above cannot see
        # it: the source, the digest and the summary are all unchanged by a readout, a primitive or a design. Said
        # separately from "a different policy" for the reason census C20/C21 gives -- an operator reading a refused run has
        # to know which of the two it was, and they are different places to look.
        if entry_kind(old) != kind:
            raise ArchiveRefused(f"{head_sha} is already archived as a different result ({entry_kind(old)}, not {kind})")
        if variant is not None:
            variant["boards"], variant["primitiveFrom"] = entry_boards(old), None
            if "pp" not in variant["boards"] and old["board"] in BOARDS_OF:     # a full-only entry: say which earlier entry it derives from, as when it was written
                try:
                    variant["primitiveFrom"] = plan_readout_variant(index, digest=v["digest"], mode=v["mode"], login=login, head_sha=head_sha, readout=readout,
                                                                    published_ids=published_ids, primitive=primitive, design=design, board=old["board"])
                except DuplicatePolicy:
                    pass
        return False
    repeats = find_duplicates(index, digest=v["digest"], mode=v["mode"], login=login, head_sha=head_sha,
                              published_ids=published_ids)
    primitive_from = None
    try:
        primitive_from = plan_readout_variant(index, digest=v["digest"], mode=v["mode"], login=login, head_sha=head_sha,
                                              readout=readout, published_ids=published_ids, primitive=primitive,
                                              design=design, board=entry["board"])
    except DuplicatePolicy:
        if DUPLICATE_RULES["same_participant"] == "refuse":     # the same readout: the repeat the rules have always refused
            raise
    else:
        repeats = [r for r in repeats if not r["sameParticipant"]]   # your own copies are all of the other readout, so this is a variant of them
    for repeat in repeats:
        if DUPLICATE_RULES["same_participant" if repeat["sameParticipant"] else "other_participant"] == "refuse":
            raise DuplicatePolicy(repeat, "the archive's duplicate rules, while writing the entry")
        if duplicates is not None:
            duplicates.append(repeat)
    if kind != KIND_DEFAULT or primitive_from is not None:
        if primitive_from is None:
            boards = BOARDS_OF[entry["board"]]      # your first copy of this policy is its own primitive, on every board it enters
        else:
            boards = variant_boards(entry["board"], kind, entry_kind(next(p for p in index["policies"] if p["policy_id"] == primitive_from)))
            if not boards:                          # e.g. a readout on PhaseTripleProduct: the axis re-prices a board this policy does not enter
                raise ArchiveRefused(f"this result re-prices no board of a {entry['board']!r} policy")
        entry["readout"] = readout
        entry["boards"] = boards
        if primitive is not None:
            entry["primitive"] = primitive
            entry["tKind"] = PRIMITIVE_T_SITE_KIND[primitive]
        if design is not None:
            entry["design"] = {family: design[family] for family in sorted(design)}
    check_entry(entry)
    if variant is not None:
        variant["boards"], variant["primitiveFrom"] = entry_boards(entry), primitive_from
    folder = checkout / DIR / head_sha
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Policy.lean").write_text(source)
    (folder / "policy.json").write_text(json.dumps({"digest": v["digest"], "mode": v["mode"], "board": v["board"], "policy": v["policy"]}, indent=1) + "\n")
    if summary is not None:
        (folder / "summary.tex").write_bytes(summary.encode("utf-8"))   # verbatim: bytes, so no newline is translated
    stored = {k: val for k, val in v.items() if k not in ("leanSource", "summarySource")}   # the sources are already stored as Policy.lean and summary.tex
    (folder / "verification.json").write_text(json.dumps(stored, indent=1) + "\n")
    index["policies"] = sorted(index["policies"] + [entry], key=lambda p: (p["source"].get("created_at") or "", p["policy_id"]))
    (checkout / INDEX_PATH).write_text(json.dumps(index, indent=1) + "\n")
    return True


def _env(token: Optional[str]) -> Dict[str, str]:
    """The token is only ever in the child's environment, never in argv or a remote URL (as `publish.refresh_archive` does)."""
    env = dict(os.environ)
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def _git(checkout: Path, *args: str, env: Optional[Dict[str, str]] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(checkout), *args], env=env, check=check, text=True, capture_output=True)


def archive_policy(checkout: Path, v: Dict[str, Any], *, login: str, pull: int, head_sha: str, run_id: str, source: str, summary: Optional[str] = None,
                   token: Optional[str] = None, remote: str = "origin", attempts: int = 4, repository: str = REPOSITORY,
                   published_ids: Optional[Collection[str]] = None, readout: str = "coherent",
                   primitive: Optional[str] = None, design: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add the policy to `BRANCH` and push, starting each attempt from the branch's current tip (a competing archive push is retried, not merged).
    `checkout` is a clone with `remote`; returns `{"path", "commit", "added", "duplicates", "primitiveFrom", "boards"}` (the earlier submissions of the same policy that the rules allowed; the earlier entry this one is a variant of, and the boards the entry occupies: the published request must carry the same).
    `readout`, `primitive` and `design` are passed to `write_policy`, which decides there what an earlier copy of the policy priced another way means."""
    env = _env(token)
    last = ""
    for _ in range(attempts):
        _git(checkout, "fetch", "--depth=1", remote, BRANCH, env=env)
        _git(checkout, "checkout", "--detach", "FETCH_HEAD")
        duplicates: List[Dict[str, Any]] = []
        variant: Dict[str, Any] = {}
        if not write_policy(checkout, v, login=login, pull=pull, head_sha=head_sha, run_id=run_id, source=source, summary=summary, repository=repository,
                            duplicates=duplicates, published_ids=published_ids, readout=readout, primitive=primitive, design=design, variant=variant):
            tip = _git(checkout, "rev-parse", "HEAD").stdout.strip()
            return {"path": f"{DIR}/{head_sha}", "commit": tip, "added": False, "duplicates": [], **variant}
        _git(checkout, "add", INDEX_PATH, f"{DIR}/{head_sha}")
        _git(checkout, "-c", "user.name=forshor-results", "-c", "user.email=forshor-results@users.noreply.github.com", "commit", "-q", "-m",
             f"Archive policy submission {head_sha[:12]} (PR #{pull}, {login})")
        pushed = _git(checkout, "push", remote, f"HEAD:refs/heads/{BRANCH}", env=env, check=False)
        if pushed.returncode == 0:
            return {"path": f"{DIR}/{head_sha}", "commit": _git(checkout, "rev-parse", "HEAD").stdout.strip(), "added": True,
                    "duplicates": duplicates, **variant}
        last = (pushed.stderr or "").strip().splitlines()[-1:] and (pushed.stderr or "").strip().splitlines()[-1] or "push failed"
    raise RuntimeError(f"could not push the archive after {attempts} attempts: {last[:300]}")
