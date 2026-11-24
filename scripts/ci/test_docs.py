"""The README, AGENTS.md and the docs describe this repository as it is: links resolve, named paths exist, nothing from the earlier route's README or the
maintainers' workstations leaks into the participant-facing files, and the rules that keep the live epoch intact are written down."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / n for n in ("README.md", "AGENTS.md", "PROVENANCE.md")] + sorted((ROOT / "docs").glob("*.md"))
PARTICIPANT_FACING = [ROOT / "README.md", ROOT / "AGENTS.md"]
FORBIDDEN = re.compile(r"ShorTableOptimization|sto_site|PLAN_v2|/Users/|/home/joshua|JMC_NOTES|VerifiedQC|ForShor's|the pinned public ForShor")
NOT_IN_THE_REPOSITORY = {"TableGeneration/Submission/summary.tex"}   # the optional file a participant adds
PATH_ROOTS = ("TableGeneration", "scripts", "evaluation", "docs", "leaderboard", "policies", ".github")

#: The lane working-records tree. No path under either prefix exists in this repository, so any
#: mention of one is a citation of a private coordinate rather than a reference to anything here.
#: The rules above read DOCUMENTS only, which is why this class of citation survived in code and in
#: test docstrings; the sweep below reads every tracked text file instead.
PRIVATE_PATHS = re.compile(r"ForShor_Integration/|readiness/pre-push/")
#: The private site repository's own name. The README rule above forbids it in the participant files;
#: this forbids it in code too.
PRIVATE_REPOSITORY = re.compile(r"forshor-site")
#: Files whose private strings are LOAD-BEARING, so removing them would disable a guard or break a
#: tool rather than close a leak. This file and evaluation/starter/test_pack_docs.py are the two
#: must-not-ship lists: these patterns are what they forbid, so scrubbing them disables the scrub.
#: scripts/backups/backup.py names both repositories because it genuinely backs both up -- a
#: functional reference, not a description of the private side's internals.
PRIVATE_PATHS_ALLOWED = {"scripts/ci/test_docs.py"}
PRIVATE_REPOSITORY_ALLOWED = {"scripts/ci/test_docs.py", "evaluation/starter/test_pack_docs.py",
                              "scripts/backups/backup.py"}
#: evaluation/engine/ is the epoch-locked vendored engine: no scrub may edit it, so it is not swept.
UNSWEPT_PREFIXES = ("evaluation/engine/",)
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".olean", ".ico", ".woff", ".woff2"}


def read(path):
    return path.read_text(encoding="utf-8")


class Docs(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        for doc in DOCS:
            for target in re.findall(r"\]\(([^)\s]+)\)", read(doc)):
                if re.match(r"(https?:|mailto:|#)", target):
                    continue
                self.assertTrue((doc.parent / target.split("#")[0]).exists(), f"{doc.relative_to(ROOT)} links to {target}")

    def test_paths_in_backticks_exist_in_the_participant_facing_files(self):
        for doc in PARTICIPANT_FACING:
            for path in re.findall(r"`((?:%s)/[A-Za-z0-9_./-]*[A-Za-z0-9_])/?`" % "|".join(PATH_ROOTS), read(doc)):
                if "<" in path or "NN" in path or path in NOT_IN_THE_REPOSITORY:
                    continue
                self.assertTrue((ROOT / path).exists(), f"{doc.name}: {path} exists")

    def test_the_participant_facing_files_name_no_workstation_no_old_route_and_no_library_repository(self):
        for doc in PARTICIPANT_FACING:
            m = FORBIDDEN.search(read(doc))
            self.assertIsNone(m, f"{doc.name} contains {m and m.group(0)!r}")

    def test_no_tracked_file_cites_a_private_coordinate(self):
        """Every tracked text file, not just the documents.

        The document rules above are why five scrub lanes left private citations in code and in test
        docstrings: they read README.md, AGENTS.md, PROVENANCE.md and docs/*.md and nothing else.

        NON-VACUITY FIRST. A sweep that reports nothing may be a sweep that cannot match, so both
        patterns are driven against a string known to contain them before any file is read, and the
        number of files actually read is asserted at the end. Without those two the test would pass
        just as happily with a broken regex or an empty file list.
        """
        self.assertRegex("x/ForShor_Integration/readiness/pre-push/NOTE.md", PRIVATE_PATHS)
        self.assertRegex("zapatacomputing/forshor-site", PRIVATE_REPOSITORY)
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=True).split("\0")
        read_count = 0
        for rel in filter(None, tracked):
            if rel.startswith(UNSWEPT_PREFIXES) or Path(rel).suffix in BINARY_SUFFIXES:
                continue
            try:
                text = (ROOT / rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
                continue
            read_count += 1
            if rel not in PRIVATE_PATHS_ALLOWED:
                hit = PRIVATE_PATHS.search(text)
                self.assertIsNone(hit, f"{rel} cites the lane working-records tree: {hit and hit.group(0)!r}")
            if rel not in PRIVATE_REPOSITORY_ALLOWED:
                hit = PRIVATE_REPOSITORY.search(text)
                self.assertIsNone(hit, f"{rel} names the private site repository: {hit and hit.group(0)!r}")
        self.assertGreater(read_count, 200, "the sweep read almost nothing, so it proves almost nothing")

    def test_readme_is_short_and_has_what_a_participant_needs(self):
        text = read(ROOT / "README.md")
        self.assertLessEqual(len(text.splitlines()), 150)
        for needle in ("./forshor setup", "./forshor check", "./forshor submit", "TableGeneration/Submission/Policy.lean", "namespace PolicySubmission",
                       "namespace PTPPolicySubmission", "summary.tex", "docs/OPERATIONS.md", "BENCHMARK.md", "leaderboard/", "https://forshor.ai", "PROVENANCE.md"):
            self.assertIn(needle, text)
        self.assertNotIn("Interim fork", text)
        self.assertNotRegex(text, r"linear\.app|MIS-\d+|QP-\d+|forshor-site")

    def test_agents_writes_down_the_rules_that_keep_the_live_epoch_and_the_caches_intact(self):
        text = read(ROOT / "AGENTS.md")
        for needle in ("evaluation/engine", "new epoch", "canon.epoch_of(epoch.model_of())", "evaluation/export_v2/evidence-archive", "submission-results",
                       "TableGeneration/", "evaluation/policy-trees.json", "scripts/leaderboard.py", "docs/POLICY_CI.md", "Policy.lean", "summary.tex"):
            self.assertIn(needle, text)

    def test_operations_has_the_queue_and_recovery_text_that_left_the_readme(self):
        text = read(ROOT / "docs/OPERATIONS.md")
        for needle in ("## Queues and recovery", "### Recover a failed publication", "gh run rerun PUBLISH_RUN_ID", "four active verification jobs"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
