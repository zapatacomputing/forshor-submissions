"""intake_v2: one submission from a policy JSON to the publisher, with the Lean harness and the publisher stubbed.
Needs the vendored engine and its Python environment: python -m pytest export_v2/pytests/test_intake_v2.py -q --runslow  (from evaluation/).
The evidence is a REAL archived envelope (the PhaseProduct baseline 6f036362, accepted at the epoch's pins), so a stubbed mirror run hands back
what the harness really produced for that exact policy."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest

EVAL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EVAL))
import intake_v2  # noqa: E402
from export_v2 import epoch  # noqa: E402
from publish_v2 import ModelMismatch, ReservedHandle  # noqa: E402

pytestmark = pytest.mark.slow
ARCHIVE = EVAL / "export_v2" / "evidence-archive" / "494185cf666f9752"
DIGEST = "6f036362302e1ecd"


@pytest.fixture(scope="module")
def model():
    return epoch.model_of()


@pytest.fixture(scope="module")
def policy_doc():
    return json.loads((ARCHIVE / f"{DIGEST}.policy.json").read_text())


@pytest.mark.parametrize("size", [77, 256, 257])
def test_band_limit_before_mirror_and_during_publication(monkeypatch, tmp_path, model, policy_doc, size):
    doc = copy.deepcopy(policy_doc)
    doc["bands"] = [dict(copy.deepcopy(doc["bands"][0]), threshold=2 * (size - i)) for i in range(size)]
    if size <= 256:
        assert len(intake_v2.normalise(doc)["bands"]) == size
        return
    def no_mirror(*a, **kw):
        pytest.fail("oversized policies must be rejected before the mirror runs")
    with pytest.raises(intake_v2.Refused, match="at most 256 bands; found 257"):
        intake_v2.verify_submission(doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=no_mirror)
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, []))
    v = {"schema": intake_v2.VERIFICATION_SCHEMA, "login": "tester", "model": model, "policy": doc}
    with pytest.raises(intake_v2.Refused, match="at most 256 bands; found 257"):
        intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "p")


@pytest.mark.parametrize("namespace", ["PolicySubmission", "PTPPolicySubmission"])
def test_lean_band_limit_before_conversion(monkeypatch, tmp_path, namespace):
    from export_v2 import engine
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **kw: {"bands": [{}] * 257})
    with pytest.raises(intake_v2.Refused, match="at most 256 bands; found 257"):
        intake_v2.policy_from_lean(engine.load(), f"namespace {namespace}\n", tmp_path, tmp_path)


def fake_publisher(model, calls, number=1):
    state = {"epoch": "e", "model": model, "baseGeneration": "1", "nextNumbers": {"tester": number}, "completedEvaluations": {}, "submissionIds": ["f" * 40],
             "publication": {"updatedAt": "2026-11-08T00:00:00Z", "snapshotId": "s" * 64}}   # the real /v2/state has no archiveCommit

    def request_json(url, token, payload=None):
        calls.append((url, payload))
        if url.endswith("/v2/state"):
            return state
        if url.endswith("/results/v2/dashboard.json"):
            return {"publication": {"archiveCommit": "a" * 40}}
        return {"snapshotId": "n" * 64, "published": True}
    return request_json


def archived_mirror(calls):
    def run(policy_json, board, tree, out_dir, **kw):
        assert kw.get("fast") is True, "the mirror is run with --fast (both harnesses skip their interpreted report stage)"
        calls.append(("mirror", board))
        out_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("policy", "record", "evidence"):
            shutil.copy(ARCHIVE / f"{DIGEST}.{ext}.json", out_dir / f"{DIGEST}.{ext}.json")
        return json.loads((ARCHIVE / f"{DIGEST}.evidence.json").read_text())
    return run


def test_a_policy_is_published_with_the_mirror_evidence_and_the_next_name(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls, number=3))
    result = intake_v2.run_intake(policy_doc, login="tester", pull=7, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                                  run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert result["published"] and result["policy"]["stoMirror"] and not result["policy"]["forshorPolicy"]
    assert (result["schema"], result["kind"], result["name"], result["boards"]) == ("forshor.publication/v2", "policy", "tester_sub3", ["full", "pp"]), "the record `forshor status` reads"
    sent = [p for u, p in calls if u.endswith("/v2/publish")]
    assert len(sent) == 1
    req = sent[0]
    assert req["schema"] == "forshor.publish/v2" and req["baseGeneration"] == "1" and req["model"] == model
    boards = sorted({(p["board"], p["delta"]) for p in req["priced"]})
    assert boards == sorted((b, d) for b in ("full", "pp") for d in ("1e-2", "3e-4", "1e-4", "1e-6")), "a PhaseProduct policy is priced on the PP and full boards at four deltas"
    assert {p["row"]["name"] for p in req["priced"]} == {"tester_sub3"}
    assert all(p["panel"]["verification"]["stoMirror"]["verdict"] == "accepted" for p in req["priced"])
    assert all(p["panel"]["verification"]["forshorPolicy"] is None for p in req["priced"])
    assert req["evaluation"]["githubLogin"] == "tester" and req["evaluation"]["pullRequestUrl"].endswith("/pull/7")
    assert "f" * 40 in req["archive"]["acceptedIds"] and req["archive"]["policyId"] == req["evaluation"]["headSha"]
    assert calls[0][0] == "http://x/v2/state", "the live state is read before any compute"


def test_in_production_the_accepted_ids_and_archive_commit_are_the_archive_branchs(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    seen = []
    def view():
        seen.append(1)
        return (["1" * 40, "2" * 40], "c" * 40)
    intake_v2.run_intake(policy_doc, login="tester", pull=9, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                         run_mirror_fn=archived_mirror(calls), archive_view=view, log=lambda s: None)
    req = [p for u, p in calls if u.endswith("/v2/publish")][0]
    assert req["archive"]["acceptedIds"] == ["1" * 40, "2" * 40] and req["evaluation"]["archiveCommit"] == "c" * 40
    assert len(seen) >= 2, "read before pricing and again by the recovery loop"


def test_the_archive_commit_is_read_from_the_published_dashboard_or_must_be_given(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    intake_v2.run_intake(policy_doc, login="tester", pull=2, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1",
                         run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert [p for u, p in calls if u.endswith("/v2/publish")][0]["evaluation"]["archiveCommit"] == "a" * 40
    fake = fake_publisher(model, calls)
    monkeypatch.setattr(intake_v2, "request_json", lambda u, t, p=None: (_ for _ in ()).throw(intake_v2.RetryPublication("down")) if "dashboard" in u else fake(u, t, p))
    with pytest.raises(RuntimeError, match="--archive-commit"):
        intake_v2.run_intake(policy_doc, login="tester", pull=3, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w2",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    intake_v2.run_intake(policy_doc, login="tester", pull=4, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w3",
                         run_mirror_fn=archived_mirror(calls), archive_commit="d" * 40, log=lambda s: None)


def test_the_reserved_handle_and_a_foreign_epoch_are_refused_before_the_lean_run(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    with pytest.raises(ReservedHandle):
        intake_v2.run_intake(policy_doc, login="forshor", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    other = copy.deepcopy(model)
    other["forshor"]["pin"] = "0" * 40
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(other, calls))
    with pytest.raises(ModelMismatch):
        intake_v2.run_intake(policy_doc, login="tester", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w2",
                             run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert not any(c[0] == "mirror" for c in calls), "no Lean run for either"


def test_a_policy_the_harness_does_not_accept_is_refused_with_its_stage_and_nothing_is_published(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))

    def rejecting(policy_json, board, tree, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        env = json.loads((ARCHIVE / f"{DIGEST}.evidence.json").read_text())
        env["record"] = {**env["record"], "verdict": "rejected", "rejection": {"stage": "threshold", "reason": "threshold too low", "detail": "band 1 ..."}}
        (out_dir / f"{DIGEST}.evidence.json").write_text(json.dumps(env))
        return env
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.run_intake(policy_doc, login="tester", pull=1, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=rejecting, log=lambda s: None)
    assert e.value.stage == "threshold" and "threshold too low" in e.value.reason
    assert not any(u.endswith("/v2/publish") for u, _ in calls if isinstance(u, str))


@pytest.mark.parametrize("stage", ["memory", "timeout"])
def test_inner_mirror_resource_stop_is_incomplete_and_leaves_no_receipt(tmp_path, monkeypatch, policy_doc, stage):
    from export_v2 import evidence
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(policy_doc))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    result = tmp_path / "result.json"
    monkeypatch.delenv("PUBLISHER_URL", raising=False)

    def stop(*args, **kwargs):
        env = archived_mirror([])(*args, **kwargs)
        env["record"].update(verdict="rejected", rejection={"stage": stage, "reason": "replay limit reached"})
        return env

    monkeypatch.setattr(evidence, "run_mirror", stop)
    assert intake_v2.main(["--policy", str(policy), "--no-publish", "--login", "tester", "--tree", str(tmp_path),
                           "--workdir", str(tmp_path / "work"), "--verification-out", str(receipt), "--out", str(result)]) == 2
    got = json.loads(result.read_text())
    assert got["failed"] and got["stage"] == stage and "not a verdict" in got["error"]
    assert "refused" not in got and not receipt.exists()


def test_the_outer_mirror_wall_clock_limit_is_incomplete_and_leaves_no_receipt(tmp_path, monkeypatch, policy_doc):
    # Production change that would fail this: removing intake_v2._run_mirror_or_incomplete's
    # `except subprocess.TimeoutExpired` wrapper around the run_mirror call. run_mirror re-raises
    # TimeoutExpired (export_v2/evidence.py:205-207), so without the wrapper it escapes
    # verify_submission and is caught by the CLI's `subprocess.SubprocessError` arm at the bottom
    # of main(), which reports {"failed": true, "error": "TimeoutExpired: ..."} with NO stage and
    # without Incomplete's "not a verdict" sentence -- a resource stop indistinguishable in class
    # from a KeyError bug. Census finding F2.
    from export_v2 import evidence
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(policy_doc))
    receipt, result = tmp_path / "receipt.json", tmp_path / "result.json"
    monkeypatch.delenv("PUBLISHER_URL", raising=False)

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["check_banded_policy.py"], timeout=kwargs["timeout"])

    monkeypatch.setattr(evidence, "run_mirror", timed_out)
    assert intake_v2.main(["--policy", str(policy), "--no-publish", "--login", "tester", "--tree", str(tmp_path),
                           "--workdir", str(tmp_path / "work"), "--mirror-timeout", "11",
                           "--verification-out", str(receipt), "--out", str(result)]) == 2
    got = json.loads(result.read_text())
    assert got["failed"] and got["stage"] == "timeout", got           # a stage, like every other resource stop
    assert "not a verdict" in got["error"], got                       # Incomplete's sentence, so it is not read as a verdict
    assert "11" in got["error"], got                                  # the limit it reached
    assert "TimeoutExpired" not in got["error"], got                  # not an unlabelled exception name
    assert "refused" not in got and not receipt.exists()              # never a refusal, never a success receipt


def test_the_outer_mirror_timeout_raises_incomplete_out_of_verify_submission(tmp_path, policy_doc):
    # Production change that would fail this: the same wrapper, checked at the library boundary
    # rather than through the CLI -- verify_submission itself must not leak subprocess.TimeoutExpired.
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["check_banded_policy.py"], timeout=kwargs["timeout"])

    with pytest.raises(intake_v2.Incomplete) as stop:
        intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w",
                                    run_mirror_fn=timed_out, mirror_timeout=5, log=lambda s: None)
    assert stop.value.stage == "timeout"
    assert not isinstance(stop.value, intake_v2.Refused), "a resource stop is never a refusal"


def test_an_overlapping_cli_attempt_cannot_replace_the_active_receipt_or_result(tmp_path, monkeypatch):
    source, second = tmp_path / "first.lean", tmp_path / "second.lean"
    source.write_bytes(b"namespace PolicySubmission\r\n-- first\r\n")
    second.write_text("namespace PolicySubmission\n-- other submission\n")
    receipt, result = tmp_path / "receipt.json", tmp_path / "result.json"
    entered, release = Event(), Event()
    monkeypatch.delenv("PUBLISHER_URL", raising=False)

    def verify(_doc, *, lean_policy, **kw):
        assert lean_policy.encode() == source.read_bytes()
        entered.set()
        assert release.wait(5)
        return {"schema": intake_v2.VERIFICATION_SCHEMA, "mode": "pair", "digest": "a" * 64, "policy": {"bands": []},
                "forshor": None, "printed": None, "leanSource": lean_policy}

    monkeypatch.setattr(intake_v2, "verify_submission", verify)
    common = ["--no-publish", "--login", "tester", "--tree", str(tmp_path), "--verification-out", str(receipt), "--out", str(result)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(intake_v2.main, [*common, "--lean-policy", str(source), "--workdir", str(tmp_path / "one")])
        try:
            assert entered.wait(5)
            assert intake_v2.main([*common, "--lean-policy", str(second), "--workdir", str(tmp_path / "two")]) == 2
            assert not receipt.exists() and not result.exists()
        finally:
            release.set()
        assert running.result(timeout=5) == 0
    assert json.loads(receipt.read_text())["leanSource"].encode() == source.read_bytes()
    assert json.loads(result.read_text())["verified"]


def test_busy_compile_and_verification_workdirs_fail_before_running(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("an attempt doesn't own the workdir")

    monkeypatch.setattr(intake_v2, "_forshor_build", unexpected)
    monkeypatch.setattr(intake_v2, "_verify_submission", unexpected)
    with intake_v2.exclusive_attempt(tmp_path / ".forshor-build.lock"):
        with pytest.raises(intake_v2.AttemptBusy):
            intake_v2.forshor_build(tmp_path, "Policy.lean", tmp_path)
    with intake_v2.exclusive_attempt(tmp_path / ".verification.lock"):
        with pytest.raises(intake_v2.AttemptBusy):
            intake_v2.verify_submission({}, login="tester", tree=tmp_path, workdir=tmp_path)


def test_extra_keys_and_a_declared_ladder_are_dropped_before_anything_runs(policy_doc):
    noisy = {**policy_doc, "declared_ladder": [[2048, 6]], "widths": "scan", "note": "x"}
    assert intake_v2.normalise(noisy) == intake_v2.normalise(policy_doc)
    assert set(intake_v2.normalise(noisy)) <= {"name", "mode", "bands"}
    with pytest.raises(intake_v2.Refused):
        intake_v2.normalise({"policies": [policy_doc, policy_doc]})
    with pytest.raises(intake_v2.Refused):
        intake_v2.normalise({"mode": "PhaseProduct", "bands": []})


def test_forshor_record_needs_the_printed_bands_to_be_the_submitted_bands_and_the_same_price(model):
    from export_v2 import engine
    eng = engine.load()
    pack = EVAL / "starter" / "pack" / "examples" / "forshor_dropin.policy.json"
    doc = json.loads(pack.read_text())
    P = intake_v2.policy_from_document(eng, doc)
    rec = intake_v2.forshor_record(eng, P, doc, model["forshor"]["pin"])
    assert rec == {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "forshor-policy-submission", "forshorCommit": model["forshor"]["pin"]}
    other = intake_v2.normalise(json.loads((ARCHIVE / f"{DIGEST}.policy.json").read_text()))
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.forshor_record(eng, eng.tables.Policy.from_json(other), doc, model["forshor"]["pin"])
    assert "not the submitted bands" in e.value.reason
    # Census C10: the same sentence is raised again when the PhaseTripleProduct publisher re-checks its bands, so it
    # must say which document and which moment. Production change that would fail this: dropping either parenthetical.
    assert e.value.reason.startswith("the bands ForShor printed are not the submitted bands")   # the anchor, still a prefix
    assert "PhaseProduct" in e.value.reason and "PhaseTripleProduct" not in e.value.reason, e.value.reason


def test_the_command_line_maps_outcomes_to_exit_codes(monkeypatch, tmp_path, policy_doc):
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps(policy_doc))
    base = ["--policy", str(pol), "--login", "tester", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x", "--workdir", str(tmp_path / "w")]
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: {"published": True})
    assert intake_v2.main(base) == 0
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: (_ for _ in ()).throw(intake_v2.Refused("threshold", "too low")))
    assert intake_v2.main(base) == 1
    monkeypatch.setattr(intake_v2, "run_intake", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the harness wrote no record")))
    assert intake_v2.main(base) == 2


def test_weighted_cost_is_computed_for_a_table_the_archive_has_no_value_for_and_reproduces_every_archived_value():
    from export_v2 import engine, mapping
    engine.load()
    from site_engine import library as lib
    known = [(T, s.weighted[(T.mode, T.k)]) for s in lib.submissions() for T in s.tables.values() if (T.mode, T.k) in s.weighted]
    assert len(known) >= 98
    wrong = [(T.digest[:10], mapping.weighted_cost_of(T.to_json()["operations"]), w) for T, w in known if mapping.weighted_cost_of(T.to_json()["operations"]) != w]
    assert not wrong, wrong[:5]
    assert mapping.weighted_cost_of([["addScaled", 0, 1, 1, 0], ["shiftL", 1, 1], ["phaseProduct", 0], ["phaseProduct", 1], ["negate", 2], ["phaseProduct", 2]]) == 3 + 5 * 2


# ---- a submission that is the Lean file itself (Policy.lean, ForShor's PolicyTemplate format)
DROPIN = EVAL / "starter" / "pack" / "examples" / "forshor_dropin.policy.json"   # a real document ForShor printed at 21e129a


def test_a_lean_policy_becomes_the_policy_ForShor_printed_for_it(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    doc = json.loads(DROPIN.read_text())
    seen = {}
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, workdir, timeout=7200, kind="pp": seen.update(text=text, tree=tree, kind=kind) or doc)
    policy_json, printed = intake_v2.policy_from_lean(eng, "namespace PolicySubmission\n-- the submitted file", tmp_path / "forshor", tmp_path / "w")
    assert seen["text"].endswith("-- the submitted file") and seen["kind"] == "pp", "the submitted bytes are what ForShor builds"
    assert printed is doc
    assert [(b["threshold"], b["k"]) for b in policy_json["bands"]] == [(int(b["threshold"]), int(b["k"])) for b in doc["bands"]]
    assert policy_json["mode"] == eng.tables.PAIR


def test_a_lean_policy_that_asks_for_something_the_boards_do_not_price_is_refused(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    base = json.loads(DROPIN.read_text())
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **k: dict(base, negation="scratch"))
    with pytest.raises(intake_v2.Refused, match="oneQubit only") as compiled:
        intake_v2.policy_from_lean(eng, "namespace PolicySubmission", tmp_path, tmp_path)
    # Census C4: policy_options.check_table_options raises the byte-identical sentence for the submitted table JSON.
    # Production change that would fail this: dropping the parenthetical that names the compiled Lean export.
    from policy_options import check_table_options
    with pytest.raises(ValueError) as table_json:
        check_table_options({"negation": "scratch"})
    assert compiled.value.reason.startswith(str(table_json.value)), (compiled.value.reason, str(table_json.value))
    assert compiled.value.reason != str(table_json.value), "the two routes shared one message (census C4)"
    assert "compiled Lean export" in compiled.value.reason, compiled.value.reason
    with pytest.raises(intake_v2.Refused, match="larger than"):
        intake_v2.policy_from_lean(eng, "x" * (intake_v2.MAX_LEAN_BYTES + 1), tmp_path, tmp_path)
    with pytest.raises(RuntimeError, match="--forshor-tree"):
        intake_v2.policy_from_lean(eng, "namespace PolicySubmission", None, tmp_path)


@pytest.mark.parametrize("bands,reason", [([], "at least one band"), ([{"k": 9, "threshold": 42}], "threshold 42 has k = 9")])
def test_unsupported_lean_bands_name_the_rule_before_table_conversion(monkeypatch, tmp_path, bands, reason):
    monkeypatch.setattr(intake_v2, "forshor_build", lambda *a, **k: {"bands": bands})
    with pytest.raises(intake_v2.Refused, match=reason) as err:
        intake_v2.policy_from_lean(None, "namespace PolicySubmission", tmp_path, tmp_path)
    assert err.value.stage == "format"


@pytest.mark.parametrize("step,reason", [("build", "Lean build"), ("exe", "circuit extraction")])
@pytest.mark.parametrize("timeout", [True, False], ids=["timeout", "proof-refusal"])
def test_forshor_timeout_is_incomplete_not_a_failed_proof(monkeypatch, tmp_path, step, reason, timeout):
    tree = tmp_path / "source"
    template = tree / "ForShor/ShorVerification/Submission/PolicyTemplate.lean"
    template.parent.mkdir(parents=True)
    template.write_text("-- test template\n")
    work = tmp_path / "work"
    work.mkdir()
    source, result = tmp_path / "Policy.lean", tmp_path / "result.json"
    source.write_text("namespace PolicySubmission\n")
    receipt = work / "verification.json"
    receipt.write_text(json.dumps({"schema": intake_v2.VERIFICATION_SCHEMA}))
    monkeypatch.delenv("PUBLISHER_URL", raising=False)
    real_run = intake_v2.subprocess.run

    def process(args, **kwargs):
        if args[:2] == ["lake", step]:
            if timeout:
                raise intake_v2.subprocess.TimeoutExpired(args, kwargs["timeout"])
            if step == "build":
                print("error: proof failed", file=kwargs["stdout"])
            return intake_v2.subprocess.CompletedProcess(args, 1, "", "proof failed")
        if args[0] in {"lake", "bash"}:
            return intake_v2.subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(intake_v2.subprocess, "run", process)
    code = intake_v2.main(["--no-publish", "--login", "tester", "--lean-policy", str(source), "--tree", str(tmp_path),
                           "--forshor-tree", str(tree), "--workdir", str(work), "--out", str(result)])
    got = json.loads(result.read_text())
    if timeout:
        assert code == 2 and got["failed"] and got["stage"] == "timeout" and "refused" not in got
        assert reason in got["error"] and "not a verdict" in got["error"]
    else:
        assert code == 1 and got["refused"] and got["stage"] == "forshor" and "failed" not in got
        assert "proof failed" in got["reason"]
    assert not receipt.exists()


def test_the_lexical_gate_refuses_before_anything_is_built(tmp_path):
    tree = tmp_path / "forshor"
    (tree / "ForShor" / "ShorVerification" / "Submission").mkdir(parents=True)
    (tree / "scripts").mkdir()
    (tree / "scripts" / "check_template_lexical.sh").write_text("#!/usr/bin/env bash\necho 'reaches compiled code' >&2\nexit 1\n")
    w = tmp_path / "w"
    w.mkdir()
    with pytest.raises(intake_v2.Refused) as e:
        intake_v2.forshor_build(tree, "unsafe def x := 1\n", w)
    assert e.value.stage == "lexical" and "reaches compiled code" in e.value.reason
    assert not (w / "forshor.log").exists(), "no build was started"


def test_a_lean_submission_is_published_with_the_ForShor_record_and_never_written_as_a_template(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, text, tree, workdir, timeout=7200: (policy_doc, {"printed": True}))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda eng, P, doc, pin: {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "c", "forshorCommit": pin})
    monkeypatch.setattr(intake_v2, "forshor_check", lambda *a, **k: pytest.fail("the JSON route's template writer must not run for a Lean submission"))
    result = intake_v2.run_intake(None, lean_policy="-- the file", login="tester", pull=11, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w",
                                  forshor_tree=tmp_path / "f", run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert result["policy"]["forshorPolicy"] is True and result["policy"]["stoMirror"] is True
    assert calls[0][0] == "http://x/v2/state", "the handle and epoch are checked before ForShor builds anything"


def test_the_command_line_takes_exactly_one_of_the_two_inputs(tmp_path):
    with pytest.raises(SystemExit):
        intake_v2.main(["--login", "u", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x"])
    f = tmp_path / "p"
    f.write_text("{}")
    with pytest.raises(SystemExit):
        intake_v2.main(["--policy", str(f), "--lean-policy", str(f), "--login", "u", "--pull", "1", "--tree", str(tmp_path), "--publisher-url", "http://x"])


# ---- a PhaseTripleProduct Lean submission (namespace PTPPolicySubmission, ForShor's PTPPolicyTemplate format)
PTP_PRINTED = EVAL / "export_v2" / "pytests" / "data" / "ptp_printed_k4.json"     # a real document ForShor's forshor_ptp_policy_submission printed
PTP_ARCHIVE = EVAL / "export_v2" / "evidence-archive" / "7a11b2b8b6d68718"
PTP_DIGEST = "265a0f53acbcdd4f"                                                    # the board's PhaseTripleProduct k = 4 baseline, accepted at the pins


def test_the_board_is_told_from_the_namespace_the_file_declares():
    assert intake_v2.lean_kind("import X\nnamespace PolicySubmission\n") == "pp"
    assert intake_v2.lean_kind("import X\nnamespace PTPPolicySubmission\n") == "ptp"
    for bad in ("def x := 1\n", "namespace PolicySubmission\nnamespace PTPPolicySubmission\n"):
        with pytest.raises(intake_v2.Refused, match="exactly one"):
            intake_v2.lean_kind(bad)


def test_a_ptp_lean_policy_becomes_the_policy_ForShor_printed_for_it(monkeypatch, tmp_path):
    from export_v2 import engine
    eng = engine.load()
    doc = json.loads(PTP_PRINTED.read_text())
    seen = {}
    monkeypatch.setattr(intake_v2, "forshor_build", lambda tree, text, workdir, timeout=7200, kind="pp": seen.update(kind=kind) or doc)
    policy_json, printed = intake_v2.policy_from_lean(eng, "namespace PTPPolicySubmission\n", tmp_path / "forshor", tmp_path / "w")
    assert seen["kind"] == "ptp" and printed is doc
    assert policy_json["mode"] == eng.tables.TRIPLE
    assert [(b["threshold"], b["k"]) for b in policy_json["bands"]] == [(int(b["threshold"]), int(b["k"])) for b in doc["bands"]]
    archived = json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.policy.json").read_text())
    from export_v2.canon import policy_digest
    assert policy_digest(policy_json) == policy_digest(archived), "the printed tables are the board's archived policy, digest for digest"


def test_a_ptp_lean_submission_is_published_on_the_ptp_board_without_a_ForShor_record(monkeypatch, tmp_path, model):
    calls = []
    archived = json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.policy.json").read_text())

    def mirror(policy_json, board, tree, out_dir, **kw):
        assert board == "ptp" and kw.get("fast") is True
        calls.append(("mirror", board))
        out_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("policy", "record", "evidence"):
            shutil.copy(PTP_ARCHIVE / f"{PTP_DIGEST}.{ext}.json", out_dir / f"{PTP_DIGEST}.{ext}.json")
        return json.loads((PTP_ARCHIVE / f"{PTP_DIGEST}.evidence.json").read_text())

    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, text, tree, workdir, timeout=7200: (archived, json.loads(PTP_PRINTED.read_text())))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda *a, **k: pytest.fail("a PhaseTripleProduct policy has no IR, so no ForShor record is made"))
    result = intake_v2.run_intake(None, lean_policy="namespace PTPPolicySubmission\n", login="tester", pull=12, url="http://x", token="t", tree=tmp_path,
                                  workdir=tmp_path / "w", forshor_tree=tmp_path / "f", run_mirror_fn=mirror, log=lambda s: None)
    assert result["policy"]["mode"] == "PhaseTripleProduct" and result["policy"]["forshorPolicy"] is False and result["policy"]["stoMirror"] is True
    req = [p for u, p in calls if isinstance(u, str) and u.endswith("/v2/publish")][0]
    assert {p["board"] for p in req["priced"]} == {"ptp"}


# ---- verify only (`./forshor check`, the untrusted half of CI) and publish from a verification (the trusted half)
def test_verify_only_needs_no_publisher_and_sends_nothing(monkeypatch, tmp_path, policy_doc):
    monkeypatch.setattr(intake_v2, "request_json", lambda *a, **k: pytest.fail("a verification must not touch the network"))
    calls = []
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror(calls), log=lambda s: None)
    assert v["schema"] == intake_v2.VERIFICATION_SCHEMA and v["board"] == "pp" and v["login"] == "tester"
    assert v["envelope"]["record"]["verdict"] == "accepted" and v["forshor"] is None and v["printed"] is None
    assert calls == [("mirror", "pp")]


def test_a_verification_published_by_another_job_gives_the_same_row_as_one_call(monkeypatch, tmp_path, model, policy_doc):
    calls_a, calls_b = [], []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls_a, number=3))
    one = intake_v2.run_intake(policy_doc, login="tester", pull=7, url="http://x", token="t", tree=tmp_path, workdir=tmp_path / "w1", head_sha="a" * 40, run_id="1",
                               run_mirror_fn=archived_mirror(calls_a), log=lambda s: None)
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w2", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    v = json.loads(json.dumps(v))                        # through a file, as an artifact travels
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls_b, number=3))
    two = intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w3", head_sha="a" * 40, run_id="1", log=lambda s: None)
    assert two["policy"] == one["policy"]
    sent = lambda calls: [p for u, p in calls if isinstance(u, str) and u.endswith("/v2/publish")][0]
    a, b = sent(calls_a), sent(calls_b)
    def diff(x, y, path=""):
        if isinstance(x, dict) and isinstance(y, dict):
            return [d for k in sorted(set(x) | set(y)) for d in diff(x.get(k), y.get(k), f"{path}/{k}")]
        if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            return [d for i, (u, w) in enumerate(zip(x, y)) for d in diff(u, w, f"{path}[{i}]")]
        return [] if x == y else [path]
    differing = {re.sub(r"\[\d+\]", "[]", d) for d in diff(a["priced"], b["priced"])}
    assert all(d.rsplit("/", 1)[-1] in ("createdAt", "submittedAt", "evaluatedAt") for d in differing), f"only the clock may differ: {sorted(differing)}"


def test_nothing_in_a_verification_is_trusted(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls, number=1))
    good = json.loads(json.dumps(intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)))
    pub = lambda v, login="tester", n=0: intake_v2.publish_verification(v, login=login, pull=9, url="http://x", token="t", workdir=tmp_path / f"p{n}", log=lambda s: None)
    with pytest.raises(intake_v2.Refused, match="another handle"):
        pub(good, login="someone-else", n=1)
    other = copy.deepcopy(good)
    other["policy"]["bands"][0]["threshold"] += 1                   # a different policy under the same digest
    with pytest.raises(intake_v2.Refused, match="digest"):
        pub(other, n=2)
    stale = copy.deepcopy(good)
    stale["model"] = dict(stale["model"], lean="leanprover/lean4:v0.0.0")
    with pytest.raises(ModelMismatch):
        pub(stale, n=3)
    forged = copy.deepcopy(good)
    forged["envelope"]["record"]["verdict"] = "rejected"
    older = tmp_path / "p4" / "evidence" / "prior.evidence.json"
    older.parent.mkdir(parents=True)
    older.write_text(json.dumps(good["envelope"]))
    with pytest.raises(intake_v2.Refused, match="mirror"):
        pub(forged, n=4)
    assert json.loads(older.read_text()) == good["envelope"]
    swapped = copy.deepcopy(good)
    swapped["envelope"]["policyDigest"] = "0" * 64                 # evidence for another policy
    with pytest.raises(intake_v2.Refused, match="mirror"):
        pub(swapped, n=5)
    assert not any(url.endswith("/v2/publish") for url, _ in calls)
    assert pub(good, n=6)["published"]


def test_the_command_line_modes_are_exclusive(tmp_path, capsys):
    f = tmp_path / "p"
    f.write_text("{}")
    base = ["--login", "u", "--tree", str(tmp_path)]
    with pytest.raises(SystemExit):                                  # publishing needs a publisher and a pull request
        intake_v2.main(["--policy", str(f), *base])
    with pytest.raises(SystemExit):                                  # --publish-from publishes
        intake_v2.main(["--publish-from", str(f), "--no-publish", "--login", "u", "--pull", "1", "--publisher-url", "http://x"])
    with pytest.raises(SystemExit):
        intake_v2.main(["--publish-from", str(f), "--lean-policy", str(f), "--login", "u", "--pull", "1", "--publisher-url", "http://x"])
    # verify only: no publisher, no pull request; the file is written where asked and the result says where
    monkeypatch_free = intake_v2.verify_submission
    assert callable(monkeypatch_free)


def test_a_lean_verification_carries_the_source_and_the_archive_is_written_before_the_publish(monkeypatch, tmp_path, model, policy_doc):
    text = "namespace PolicySubmission\n-- the file\n"
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, t, tree, workdir, timeout=7200: (policy_doc, {"printed": True}))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda eng, P, doc, pin: {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "c", "forshorCommit": pin})
    v = intake_v2.verify_submission(None, lean_policy=text, login="tester", tree=tmp_path, workdir=tmp_path / "w", forshor_tree=tmp_path / "f",
                                    run_mirror_fn=archived_mirror([]), log=lambda s: None)
    assert v["leanSource"] == text and len(v["leanSha256"]) == 64
    calls, order = [], []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    def write(verification, **kind):
        order.append(("archive", sum(1 for u, _ in calls if isinstance(u, str) and u.endswith("/v2/publish")), kind))
        return {"path": "results/policy-submissions/" + "a" * 40, "commit": "c" * 40, "added": True}
    result = intake_v2.publish_verification(json.loads(json.dumps(v)), login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40,
                                            run_id="1", archive_commit="a" * 40, archive_write=write, log=lambda s: None)
    # The third element is the rule 1: an ORDINARY coherent submission names every axis at its default, which
    # `write_policy` writes as the legacy entry it has always written.
    assert order == [("archive", 0, {"readout": "coherent", "primitive": None, "design": None})], \
        "the archive is written once, before anything is sent to the publisher, and told which result it is archiving"
    assert result["archive"]["commit"] == "c" * 40
    assert any(isinstance(u, str) and u.endswith("/v2/publish") for u, _ in calls)


def publish_with_archive(monkeypatch, tmp_path, model, policy_doc, archived):
    """One ordinary coherent publication whose archive step answers `archived`; returns the request the publisher got."""
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40, run_id="1",
                                   archive_commit="a" * 40, archive_write=lambda verification, **kind: archived, log=lambda s: None)
    return [p for u, p in calls if u.endswith("/v2/publish")][0]


def test_a_variant_the_archive_recorded_is_priced_on_the_full_board_alone(monkeypatch, tmp_path, model, policy_doc):
    """The archive entry for your own second copy of a policy claims only the full board and names the earlier entry
    (`boards: ["full"]`). The request must say the same: before, `publish_verification` dropped the archive's answer and
    priced pp + full with no `primitiveFrom`, so the published rows disagreed with the record kept forever."""
    request = publish_with_archive(monkeypatch, tmp_path, model, policy_doc,
                                   {"path": "p", "commit": "c" * 40, "added": True, "boards": ["full"], "primitiveFrom": "f" * 40})
    assert request["submission"]["boards"] == ["full"] and request["submission"]["primitiveFrom"] == "f" * 40
    assert {p["board"] for p in request["priced"]} == {"full"}
    assert "f" * 40 in request["archive"]["acceptedIds"]


def test_an_ordinary_submission_carries_no_variant_and_both_boards(monkeypatch, tmp_path, model, policy_doc):
    request = publish_with_archive(monkeypatch, tmp_path, model, policy_doc,
                                   {"path": "p", "commit": "c" * 40, "added": True, "boards": ["pp", "full"], "primitiveFrom": None})
    assert request["submission"]["boards"] == ["pp", "full"] and "primitiveFrom" not in request["submission"]
    assert {p["board"] for p in request["priced"]} == {"pp", "full"}


def test_an_archive_entry_and_a_request_that_disagree_about_boards_publish_nothing(monkeypatch, tmp_path, model, policy_doc):
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    with pytest.raises(intake_v2.Incomplete, match="boards"):
        intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40, run_id="1", archive_commit="a" * 40,
                                       archive_write=lambda verification, **kind: {"path": "p", "commit": "c" * 40, "added": True, "boards": ["full"], "primitiveFrom": None},
                                       log=lambda s: None)
    assert not any(isinstance(u, str) and u.endswith("/v2/publish") for u, _ in calls)


def test_your_own_other_readout_copy_is_published_as_the_variant_the_archive_wrote(monkeypatch, tmp_path, model, policy_doc):
    """End to end through the real archive planner: an earlier published MEASURED copy of this policy by the same login,
    then this ordinary coherent copy. The archive writes a full-only entry, and the request now matches it."""
    import archive_policy
    lean_world(monkeypatch, policy_doc)
    v = intake_v2.verify_submission(None, lean_policy=LEAN, login="tester", tree=tmp_path, workdir=tmp_path / "w", forshor_tree=tmp_path / "f",
                                    run_mirror_fn=archived_mirror([]), log=lambda s: None)
    checkout = tmp_path / "archive"
    (checkout / archive_policy.INDEX_PATH).parent.mkdir(parents=True)
    earlier = dict(v, leanSource=LEAN + "-- earlier\n")
    earlier["leanSha256"] = archive_policy.sha256(earlier["leanSource"])
    archive_policy.write_policy(checkout, earlier, login="tester", pull=3, head_sha="f" * 40, run_id="0", source=earlier["leanSource"], readout="semiclassical")
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))     # its published ids hold "f" * 40

    def write(verification, **kind):
        variant = {}
        archive_policy.write_policy(checkout, verification, login="tester", pull=7, head_sha="a" * 40, run_id="1", source=verification["leanSource"],
                                    published_ids={"f" * 40}, variant=variant, **kind)
        return {"path": "p", "commit": "c" * 40, "added": True, **variant}

    result = intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40, run_id="1",
                                            archive_commit="a" * 40, archive_write=write, log=lambda s: None)
    entry = next(p for p in archive_policy.read_index(checkout)["policies"] if p["policy_id"] == "a" * 40)
    assert entry["boards"] == ["full"]
    assert result["boards"] == entry["boards"], "the published rows occupy exactly the boards the archive claims"
    sent = [p for u, p in calls if u.endswith("/v2/publish")][0]
    assert sent["submission"]["primitiveFrom"] == "f" * 40


def test_a_refused_archive_stops_the_publication(monkeypatch, tmp_path, model, policy_doc):
    import archive_policy
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    def refuse(verification, **kind):
        raise archive_policy.ArchiveRefused("the verification's Lean source is not the file at the pull request's head commit")
    with pytest.raises(archive_policy.ArchiveRefused):
        intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40, run_id="1",
                                       archive_commit="a" * 40, archive_write=refuse, log=lambda s: None)
    assert not any(isinstance(u, str) and u.endswith("/v2/publish") for u, _ in calls)


def test_the_command_line_archives_with_the_head_sha_the_run_and_the_githubs_copy_of_the_file(monkeypatch, tmp_path):
    import archive_policy
    seen = {}
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setattr(archive_policy, "github_source", lambda head, token, req: seen.update(source=(head, token)) or "SRC")
    monkeypatch.setattr(archive_policy, "github_summary", lambda head, token, req: seen.update(summary=(head, token)) or "SUMMARY")
    monkeypatch.setattr(archive_policy, "archive_policy", lambda checkout, v, **kw: seen.update(archive=(checkout, kw)) or {"path": "p", "commit": "c" * 40, "added": True})
    monkeypatch.setattr(intake_v2, "request_json", lambda url, token, payload=None: seen.update(state_url=url) or {"submissionIds": ["f" * 40, "e" * 40]})
    def publish(v, **kw):
        # the rule: `publish_verification` names the result kind, and the closure carries it through to
        # `archive_policy`. A measured publication is what makes the readout axis non-default; this stands in for one.
        kw["archive_write"](v, readout="semiclassical", primitive=None, design={"data": {"backend": "catalytic", "target": "exact"}})
        return {"published": True}
    monkeypatch.setattr(intake_v2, "publish_verification", publish)
    verification = tmp_path / "verification.json"
    verification.write_text("{}")
    code = intake_v2.main(["--publish-from", str(verification), "--login", "tester", "--pull", "7", "--head-sha", "a" * 40, "--run-id", "5", "--publisher-url", "http://x",
                           "--archive", str(tmp_path / "arch")])
    assert code == 0
    assert seen["source"] == ("a" * 40, "gh-token") and seen["summary"] == ("a" * 40, "gh-token"), "the summary is read at the same head commit, with the same token"
    checkout, kw = seen["archive"]
    assert seen["state_url"] == "http://x/v2/state" and kw["published_ids"] == {"f" * 40, "e" * 40}, "a repeat only counts when the publisher has actually published the earlier head"
    assert checkout == tmp_path / "arch" and kw["source"] == "SRC" and kw["summary"] == "SUMMARY" and kw["head_sha"] == "a" * 40 and kw["run_id"] == "5" and kw["pull"] == 7 and kw["token"] == "gh-token"
    # Fails if the closure stops forwarding the result kind: before ruling 1 it passed none of the three, so
    # `archive_policy` fell back to its defaults and a measured publication was archived as a coherent one.
    assert (kw["readout"], kw["primitive"], kw["design"]) == ("semiclassical", None, {"data": {"backend": "catalytic", "target": "exact"}})
    monkeypatch.setattr(intake_v2, "publish_verification", lambda v, **kw: kw["archive_write"](v))
    assert intake_v2.main(["--publish-from", str(verification), "--login", "tester", "--pull", "7", "--publisher-url", "http://x", "--archive", str(tmp_path / "arch")]) == 1, \
        "archiving without a head sha and run id is refused"


REPEAT = {"policyId": "e" * 40, "login": "someone", "pull": 41, "createdAt": "2026-11-10T01:38:00Z", "sameParticipant": False}


def test_a_duplicate_policy_is_its_own_refusal_stage_with_the_earlier_submission(monkeypatch, tmp_path):
    import archive_policy
    def refuse(v, **kw):
        raise archive_policy.DuplicatePolicy({**REPEAT, "login": "tester", "sameParticipant": True})
    monkeypatch.setattr(intake_v2, "publish_verification", refuse)
    verification, out = tmp_path / "verification.json", tmp_path / "publication-result.json"
    verification.write_text("{}")
    code = intake_v2.main(["--publish-from", str(verification), "--login", "tester", "--pull", "7", "--head-sha", "a" * 40, "--run-id", "5", "--publisher-url", "http://x", "--out", str(out)])
    result = json.loads(out.read_text())
    assert code == 1 and result["refused"] is True and result["stage"] == "duplicate"
    assert result["duplicateOf"] == {**REPEAT, "login": "tester", "sameParticipant": True} and "already submitted" in result["reason"]


def test_a_repeat_by_another_participant_publishes_and_is_logged(monkeypatch, tmp_path, model, policy_doc):
    calls, lines = [], []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    v = intake_v2.verify_submission(policy_doc, login="tester", tree=tmp_path, workdir=tmp_path / "w", run_mirror_fn=archived_mirror([]), log=lambda s: None)
    write = lambda verification, **kind: {"path": "p", "commit": "c" * 40, "added": True, "duplicates": [REPEAT]}
    result = intake_v2.publish_verification(v, login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40, run_id="1",
                                            archive_commit="a" * 40, archive_write=write, log=lines.append)
    assert result["archive"]["duplicates"] == [REPEAT]
    logged = [x for x in lines if x.startswith("duplicate-policy:")]
    assert len(logged) == 1 and "eeeeeeeeeeee" in logged[0] and "someone" in logged[0] and "#41" in logged[0] and "other_participant=allow" in logged[0]
    assert any(isinstance(u, str) and u.endswith("/v2/publish") for u, _ in calls)


# ---- the optional summary.tex: data that rides along with a Lean submission and never changes what is verified
HOSTILE = ("\\documentclass{article}\n\\immediate\\write18{touch CANARY}\n\\input{/etc/passwd}\n@everyone <script>alert(1)</script>\n" + "x" * 60000 + "\r\n\tend \u00e9\n")
LEAN = "namespace PolicySubmission\n-- the file\n"


def lean_world(monkeypatch, policy_doc):
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda eng, t, tree, workdir, timeout=7200: (policy_doc, {"printed": True}))
    monkeypatch.setattr(intake_v2, "forshor_record", lambda eng, P, doc, pin: {"verified": True, "bandsAgree": True, "irPriceAgrees": True, "claim": "c", "forshorCommit": pin})


def verify_with(tmp_path, summary):
    return intake_v2.verify_submission(None, lean_policy=LEAN, summary=summary, login="tester", tree=tmp_path, workdir=tmp_path / "w", forshor_tree=tmp_path / "f",
                                       run_mirror_fn=archived_mirror([]), log=lambda s: None)


def test_the_summary_is_carried_as_data_and_cannot_change_the_policy_digest(monkeypatch, tmp_path, policy_doc):
    import hashlib
    lean_world(monkeypatch, policy_doc)
    without = verify_with(tmp_path, None)
    with_it = verify_with(tmp_path, HOSTILE)
    assert "summarySource" not in without and "summarySha256" not in without
    assert with_it["summarySource"] == HOSTILE and with_it["summarySha256"] == hashlib.sha256(HOSTILE.encode()).hexdigest() and with_it["summaryBytes"] == len(HOSTILE.encode())
    for key in ("digest", "policy", "printed", "envelope", "forshor", "mode", "board", "leanSha256"):
        assert with_it[key] == without[key], f"{key}: the summary changed what was verified"


def test_a_summary_that_breaks_a_rule_is_refused_before_anything_is_built(monkeypatch, tmp_path):
    monkeypatch.setattr(intake_v2, "policy_from_lean", lambda *a, **k: pytest.fail("ForShor must not run for a refused summary"))
    for bad, text in (("limit", "a" * 102401), ("UTF-8", "bad \udcff\n"), (r"U\+0000", "a\x00b"), ("empty", " \n")):
        with pytest.raises(intake_v2.Refused, match=bad) as e:
            verify_with(tmp_path, text)
        assert e.value.stage == "summary"
    with pytest.raises(intake_v2.Refused, match="never alone"):
        intake_v2.verify_submission({"mode": "pair"}, summary="x\n", login="tester", tree=tmp_path, workdir=tmp_path / "w", log=lambda s: None)


def test_the_publish_step_checks_a_carried_summary_again(monkeypatch, tmp_path, model, policy_doc):
    import archive_policy
    lean_world(monkeypatch, policy_doc)
    v = verify_with(tmp_path, HOSTILE)
    calls = []
    monkeypatch.setattr(intake_v2, "request_json", fake_publisher(model, calls))
    def publish(verification):
        return intake_v2.publish_verification(json.loads(json.dumps(verification)), login="tester", pull=7, url="http://x", token="t", workdir=tmp_path / "w2", head_sha="a" * 40,
                                              run_id="1", archive_commit="a" * 40, log=lambda s: None)
    assert publish(v)["policy"]["digest"] == v["digest"]
    for mutate in (lambda x: x.update(summarySha256="0" * 64), lambda x: x.update(summaryBytes=1), lambda x: x.pop("summaryBytes"), lambda x: x.pop("summarySha256"),
                   lambda x: x.update(summarySource="a\x00b", summarySha256=archive_policy.sha256("a\x00b"), summaryBytes=3), lambda x: x.update(summarySource=5)):
        forged = json.loads(json.dumps(v))
        mutate(forged)
        with pytest.raises(intake_v2.Refused) as e:
            publish(forged)
        assert e.value.stage == "summary"
    only_hash = json.loads(json.dumps(v))
    for key in ("summarySource", "summarySha256", "summaryBytes"):
        only_hash.pop(key)
    only_hash["leanSource"] = LEAN
    assert publish(only_hash)["policy"]["digest"] == v["digest"], "no summary at all is the ordinary submission"


def test_the_command_line_reads_the_summary_as_bytes_and_refuses_a_bad_one_before_any_work(monkeypatch, tmp_path, capsys):
    lean = tmp_path / "Policy.lean"
    lean.write_text(LEAN)
    summary = tmp_path / "summary.tex"
    summary.write_bytes(HOSTILE.encode())
    seen = {}
    monkeypatch.setattr(intake_v2, "verify_submission", lambda doc, **kw: seen.update(kw) or {
        "schema": intake_v2.VERIFICATION_SCHEMA, "digest": "d" * 16, "mode": "pair", "policy": {"bands": []},
        "forshor": None, "printed": None})
    base = ["--lean-policy", str(lean), "--no-publish", "--login", "u", "--tree", str(tmp_path), "--forshor-tree", str(tmp_path), "--workdir", str(tmp_path / "w"),
            "--verification-out", str(tmp_path / "v.json")]
    assert intake_v2.main([*base, "--summary", str(summary)]) == 0
    assert seen["summary"] == HOSTILE and "\r\n" in seen["summary"], "bytes in, bytes out: the carriage return survives"
    seen.clear()
    summary.write_bytes(b"a\x00b")
    assert intake_v2.main([*base, "--summary", str(summary), "--out", str(tmp_path / "r.json")]) == 1
    assert seen == {} and json.loads((tmp_path / "r.json").read_text())["stage"] == "summary"
    summary.unlink()
    summary.symlink_to(lean)
    assert intake_v2.main([*base, "--summary", str(summary), "--out", str(tmp_path / "r.json")]) == 1
    assert "symbolic link" in json.loads((tmp_path / "r.json").read_text())["reason"] and seen == {}
    with pytest.raises(SystemExit):                                             # a summary with a JSON policy, or with --publish-from, is a usage error
        intake_v2.main(["--policy", str(lean), "--summary", str(summary), "--login", "u", "--tree", str(tmp_path), "--no-publish"])
