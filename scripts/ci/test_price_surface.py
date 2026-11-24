"""Tests for the price-surface guard. The point of most of them is NON-VACUITY: a guard that can never fail is
worse than no guard, because it reads like coverage. Each rule is driven to a failure here, and the structural tests
refuse a pathspec that silently matches nothing.

The manifest is perturbed in memory, never on disk: these tests do not write to the repository.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
import unittest.mock

import price_surface as ps

REPO = ps.REPO
#: Pinned by this release's verification. test_the_mirrored_dataFingerprint_equals_the_engines and
#: test_the_mirrored_engine_data_fingerprint_equals_the_engines re-derive these from the engine itself.
EPOCH_DATA_FINGERPRINT = "176ce4047f5957f1"
ENGINE_DATA_FINGERPRINT = "a098f72365d01ee5"
ENGINE_REVISION = "677cc2e05ab178afff8dc632ff56058a8196a116"


def manifest():
    return ps.load()


def perturb(groups=(), inputs=()):
    """The live manifest with some group hashes and/or epoch inputs replaced, i.e. 'these moved since then'."""
    m = copy.deepcopy(manifest())
    for g in groups:
        m["groups"][g]["sha256"] = "0" * 64
    for i in inputs:
        m["epochInputs"][i] = "stale-" + i
    return m


class LiveTree(unittest.TestCase):
    def test_the_committed_tree_passes_every_rule(self):
        result = ps.check(manifest())
        self.assertEqual(result["findings"], [], "the committed tree must satisfy the guard")
        self.assertTrue(result["ok"])

    def test_the_manifest_records_the_epoch_inputs_this_release_pinned(self):
        inputs = manifest()["epochInputs"]
        self.assertEqual(inputs["dataFingerprint"], EPOCH_DATA_FINGERPRINT)
        self.assertEqual(inputs["engineDataFingerprint"], ENGINE_DATA_FINGERPRINT)
        self.assertEqual(inputs["engineRevision"], ENGINE_REVISION)

    def test_the_manifest_agrees_with_the_group_table(self):
        """A group added to GROUPS without re-recording the manifest would otherwise be unguarded."""
        m = manifest()
        self.assertEqual(sorted(m["groups"]), sorted(g["id"] for g in ps.GROUPS))
        for g in ps.GROUPS:
            self.assertEqual(m["groups"][g["id"]]["pathspec"], ps._pathspec(g), g["id"])
            self.assertEqual(m["groups"][g["id"]]["enforce"], g["enforce"], g["id"])


class NotVacuous(unittest.TestCase):
    def test_every_group_matches_at_least_one_committed_file(self):
        """A typo in a pathspec would make a group guard nothing while still reporting 'unchanged'."""
        for g in ps.GROUPS:
            with self.subTest(group=g["id"]):
                self.assertGreater(len(ps.group_entries(g)), 0)

    def test_the_group_file_counts_are_the_ones_the_audit_found(self):
        counts = {g["id"]: len(ps.group_entries(g)) for g in ps.GROUPS}
        self.assertEqual(counts["vendor.qualtran"], 789, "the vendored Qualtran tree")
        self.assertEqual(counts["engine.cache"], 16, "four deltas x (PP, PP_scan, PTP, fullalg)")
        self.assertEqual(counts["site_engine.uncovered"], 15, "18 modules minus the 3 the fingerprint covers")
        self.assertEqual(counts["engine.environment"], 1)

    def test_the_three_fingerprinted_site_engine_modules_are_excluded(self):
        """They are covered by dataFingerprint; including them would attribute a change to the wrong guard."""
        paths = [line.split(" ", 1)[1] for line in ps.group_entries(ps.GROUPS[0])]
        for name in ("paper_full.py", "full_frontier.py", "fullalg.py"):
            self.assertNotIn(f"evaluation/engine/site_engine/{name}", paths)
        self.assertIn("evaluation/engine/site_engine/frontier.py", paths)
        self.assertIn("evaluation/engine/site_engine/seed_policies.py", paths)

    def test_R1_fires_when_a_price_relevant_group_moves_and_the_epoch_stands_still(self):
        result = ps.check(perturb(groups=["site_engine.uncovered"]))
        self.assertFalse(result["ok"])
        self.assertTrue(result["epochStoodStill"])
        r1 = [f for f in result["findings"] if f["rule"] == "R1"]
        self.assertEqual([f["id"] for f in r1], ["site_engine.uncovered"])

    def test_R1_is_silent_when_an_epoch_input_moved_too(self):
        """A price change the epoch already records is attributable; the guard must not block it."""
        result = ps.check(perturb(groups=["site_engine.uncovered"], inputs=["engineRevision"]))
        self.assertFalse(result["epochStoodStill"])
        self.assertEqual([f for f in result["findings"] if f["rule"] == "R1"], [])
        self.assertIn("site_engine.uncovered", [a["id"] for a in result["advisories"]])

    def test_R2_fires_when_a_cache_does_not_match_the_key_it_is_keyed_on(self):
        """Driven by overriding the expected fingerprints, so this asserts the comparison, not a tautology."""
        report = ps.cache_report(want_data="dead" * 4, want_full="beef" * 4)
        self.assertEqual(len(report["stale"]), 16, "every committed cache must be judged, not skipped")
        self.assertTrue(all(not r["ok"] for r in report["caches"]))

    def test_R3_fires_when_qualtran_moves_without_a_cache_rebuild(self):
        """The hole this guard exists for: neither cache key hashes the vendored tree, so every cache still
        validates while its numbers came from the previous Qualtran."""
        result = ps.check(perturb(groups=["vendor.qualtran"]))
        self.assertFalse(result["ok"])
        r3 = [f for f in result["findings"] if f["rule"] == "R3"]
        self.assertEqual(len(r3), 1)
        self.assertEqual(r3[0]["blind"], ["vendor.qualtran"])

    def test_R3_is_independent_of_the_epoch(self):
        """Moving an epoch input does NOT excuse a missing cache rebuild: the epoch says the engine changed, it
        does not say the derived data was regenerated."""
        result = ps.check(perturb(groups=["vendor.qualtran"], inputs=["engineRevision"]))
        self.assertFalse(result["epochStoodStill"])
        self.assertEqual(len([f for f in result["findings"] if f["rule"] == "R3"]), 1)

    def test_R3_is_satisfied_when_the_caches_were_rebuilt_in_the_same_commit(self):
        result = ps.check(perturb(groups=["vendor.qualtran", "engine.cache"], inputs=["engineRevision"]))
        self.assertEqual([f for f in result["findings"] if f["rule"] == "R3"], [])

    def test_R3_does_not_demand_a_rebuild_for_a_group_the_cache_key_deliberately_ignores(self):
        """`data_fingerprint` skips `data/forshor_submitted` on purpose, so installing a record must not be read as
        a stale cache."""
        result = ps.check(perturb(groups=["engine.data.forshor_submitted"], inputs=["engineRevision"]))
        self.assertEqual([f for f in result["findings"] if f["rule"] == "R3"], [])

    def test_an_advisory_group_never_fails_the_build_on_its_own(self):
        result = ps.check(perturb(groups=["export_v2.model"]))
        self.assertTrue(result["ok"])
        self.assertIn("export_v2.model", [a["id"] for a in result["advisories"]])


class CacheCoherence(unittest.TestCase):
    def test_each_committed_cache_is_keyed_on_the_fingerprint_its_loader_checks(self):
        report = ps.cache_report()
        keyed = {r["file"]: r["keyedOn"] for r in report["caches"]}
        self.assertEqual(keyed["fullalg_1e-02.json"], "full_frontier.fingerprint")
        self.assertEqual(keyed["frontier_PP_1e-02.json"], "data_fingerprint")
        self.assertEqual(report["stale"], [])
        self.assertEqual(report["dataFingerprint"], ENGINE_DATA_FINGERPRINT)
        self.assertEqual(report["dataFingerprintFull"], EPOCH_DATA_FINGERPRINT)

    def test_the_exporter_still_reads_these_caches_without_validating_them(self):
        """If this ever stops being true, R2 has been fixed at the source and this guard can be relaxed. Until then
        the guard is the only thing that checks them."""
        source = (REPO / "evaluation/export_v2/epoch.py").read_text()
        self.assertIn('eng.engine_dir / "cache"', source)
        self.assertNotIn("full_frontier.load", source)
        self.assertNotIn("precompute.load", source)


class Additive(unittest.TestCase):
    """The release must change no computed price, no epoch and no model field."""

    def test_nothing_under_evaluation_reads_this_guard(self):
        for path in (REPO / "evaluation").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(errors="ignore")
            self.assertNotIn("price_surface", text, f"{path} reads the guard")
            self.assertNotIn("price-surface", text, f"{path} reads the guard's manifest")

    def test_the_model_is_built_from_the_engine_and_the_repo_pins_only(self):
        """`scripts/ci` must not appear anywhere in the epoch computation."""
        source = (REPO / "evaluation/export_v2/epoch.py").read_text()
        self.assertNotIn("scripts/ci", source)
        self.assertNotIn("scripts", source)

    def test_the_manifest_is_data(self):
        m = json.loads((Path(ps.MANIFEST)).read_text())
        self.assertEqual(m["schema"], ps.SCHEMA)
        self.assertIn("not an epoch input", m["note"])


class MirrorsTheEngine(unittest.TestCase):
    """The guard recomputes two engine fingerprints without importing the engine. If they ever drift, every rule
    silently weakens, so pin them against the engine itself whenever the pinned environment is importable."""

    def setUp(self):
        import sys
        sys.path.insert(0, str(REPO / "evaluation"))
        try:
            from export_v2 import engine
            self.eng = engine.load()
        except Exception as error:                      # pragma: no cover - the plain CI runner has no engine
            self.skipTest(f"the pinned engine environment is not importable here: {error}")

    def test_the_mirrored_dataFingerprint_equals_the_engines(self):
        self.assertEqual(ps.full_fingerprint(), self.eng.full_frontier.fingerprint())

    def test_the_mirrored_engine_data_fingerprint_equals_the_engines(self):
        self.assertEqual(ps.data_fingerprint(), self.eng.precompute.data_fingerprint())

    def test_the_epoch_inputs_equal_the_models_fields(self):
        from export_v2 import epoch
        model = epoch.model_of()
        inputs = ps.epoch_inputs()
        self.assertEqual(inputs["dataFingerprint"], model["dataFingerprint"])
        self.assertEqual(inputs["engineRevision"], model["engineRevision"])
        self.assertEqual(inputs["qualtranVersion"], model["qualtran"]["version"])
        self.assertEqual(inputs["forshorPin"], model["forshor"]["pin"])
        self.assertEqual(inputs["leanToolchain"], model["lean"])
        self.assertEqual(ps._site_model_version(), model["id"])


class ShallowCheckout(unittest.TestCase):
    """The guard must refuse a history it cannot read, rather than passing.

    `actions/checkout` defaults to depth 1. On that history `engineRevision` cannot be computed, the guard reads
    the epoch as moved, and because R1 only enforces while the epoch stands still it becomes advisory-only --
    silently, and in exactly the environment it exists to protect. This was live: the first CI run of this guard
    reported `epochStoodStill: False` with zero findings while the same commit enforced correctly on a full
    clone. It is the vacuity failure this file's docstring is about, so it gets a test of its own.
    """

    def test_a_shallow_checkout_is_refused_outright(self):
        with unittest.mock.patch.object(ps, "_git", return_value="true\n") as git:
            with self.assertRaises(RuntimeError) as caught:
                ps.require_history(REPO)
        git.assert_called_once()
        self.assertIn("shallow", str(caught.exception))
        self.assertIn("fetch-depth: 0", str(caught.exception), "the message must say how to fix it")

    def test_a_full_checkout_is_accepted(self):
        with unittest.mock.patch.object(ps, "_git", return_value="false\n"):
            ps.require_history(REPO)       # must not raise

    def test_check_itself_refuses_before_judging(self):
        """Not merely the helper: `check` must not return a verdict derived from unreadable history."""
        with unittest.mock.patch.object(ps, "require_history",
                                        side_effect=RuntimeError("shallow ... fetch-depth: 0")):
            with self.assertRaises(RuntimeError):
                ps.check(ps.load())

    def test_this_repository_has_the_history_the_guard_needs(self):
        """The guard is only meaningful where it runs; assert the condition rather than assume it."""
        ps.require_history(REPO)


if __name__ == "__main__":
    unittest.main()
