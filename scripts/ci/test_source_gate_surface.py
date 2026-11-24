"""The digest harness must cover what it claims, and `qualify_full_run=True` must only ever relax the gate.

The defect these tests exist to prevent: a row built as `verdict(call, *args)` fed a keyword argument through a
trailing comma, which silently becomes a duplicate of the row above it and reports coverage that does not exist.
"""
import inspect
import re
import unittest

import policy_source as ps
import source_gate_surface as sgs


CORPUS_SIZE = 739      # 2 boards x 9 headers x 41 bodies, + 1 edge source. Read off the axes, not chosen.


class Corpus(unittest.TestCase):
    def test_the_corpus_sources_are_all_distinct(self):
        names = [name for name, _ in sgs.sources()]
        texts = [text for _, text in sgs.sources()]
        self.assertEqual(len(names), CORPUS_SIZE)
        self.assertEqual(len(set(names)), CORPUS_SIZE, 'source names must be unique or the digest silently drops rows')
        self.assertEqual(len(set(texts)), CORPUS_SIZE, 'duplicate sources inflate the claimed corpus size')

    def test_every_refusal_branch_of_validate_is_reached_by_some_source(self):
        # The guard against sizing the corpus to a target again: each branch is named by a probe, and the corpus
        # must independently produce the same refusal. Fails if an axis is trimmed to make a count come out.
        probes = {
            'byte ceiling': 'import ' + sgs.POLICY_IMPORT + '\n-- ' + 'x' * (ps.MAX_BYTES + 1) + '\n',
            'build-time command': 'namespace PolicySubmission\n#eval 1\nend PolicySubmission\n',
            'escaped identifier': 'namespace PolicySubmission\ndef \u00abx\u00bb := 1\nend PolicySubmission\n',
            'banned token': 'namespace PolicySubmission\ndef p := by native_decide\nend PolicySubmission\n',
            # `structure`, not `def`: `def` is itself a template header command and is accepted.
            'unsupported header': 'structure Foo where\n  x : Nat\n',
            'empty import': 'import \nnamespace PolicySubmission\nend PolicySubmission\n',
            'unapproved module': 'import ForShor.OtherModule\nnamespace PolicySubmission\nend PolicySubmission\n',
            'full-run selector': 'namespace PolicySubmission\ndef other := submittedFullRunDesign\nend PolicySubmission\n',
        }
        anonymise = lambda message: re.sub(r'line \d+', 'line N', message)
        reached = {anonymise(sgs.verdict(ps.validate, text)[1]) for _, text in sgs.sources()}
        for label, probe in probes.items():
            with self.subTest(branch=label):
                expected = sgs.verdict(ps.validate, probe)
                self.assertEqual(expected[0], 'ValueError', f'{label} probe must refuse')
                self.assertIn(anonymise(expected[1]), reached, f'no corpus source reaches the {label} branch')

    def test_the_corpus_is_stable_across_calls(self):
        self.assertEqual(list(sgs.sources()), list(sgs.sources()))


class Coverage(unittest.TestCase):
    """The claims in the module docstring, each as an executable check."""

    def test_qualify_full_run_is_keyword_only_so_a_positional_helper_cannot_reach_it(self):
        # This is WHY the scratch row was dead: no trailing comma could ever have passed it.
        kind = inspect.signature(ps.validate).parameters['qualify_full_run'].kind
        self.assertIs(kind, inspect.Parameter.KEYWORD_ONLY)
        with self.assertRaises(TypeError):
            ps.validate('', True)

    def test_verdict_forwards_keyword_arguments(self):
        # Fails if `verdict` is narrowed back to `(call, *args)`.
        self.assertIn('kwargs', inspect.signature(sgs.verdict).parameters)
        source = f'import {ps.FULL_RUN_IMPORT}\nnamespace PolicySubmission\ndef submittedFullRunDesign : Shor.FullRunDesign := .quotientExact\nend PolicySubmission\n'
        self.assertEqual(sgs.verdict(ps.validate, source)[0], 'ValueError')
        self.assertEqual(sgs.verdict(ps.validate, source, qualify_full_run=True)[0], 'ok')

    def test_the_two_validate_rows_are_not_byte_copies(self):
        # The exact defect. Fails if `validate_qfr` stops passing the keyword.
        observed = sgs.observations()
        differing = [n for n, r in observed.items() if r['validate'] != r['validate_qfr']]
        self.assertTrue(differing, 'validate_qfr must observe a branch the plain row does not')
        self.assertEqual(len(differing), 345, 'the two rows differ on a known 345 of 739 sources')

    def test_selector_cause_is_actually_called_for_every_selector(self):
        # The second half of the defect: a documented row that was never invoked.
        causes = sgs._selector_causes(ps.strip_lean_comments(
            'namespace PolicySubmission\ndef other := submittedFullRunDesign\nend PolicySubmission\n'))
        self.assertEqual(causes['submittedFullRunDesign'][0], 'ok')
        self.assertIn('remove the mention', causes['submittedFullRunDesign'][1])
        self.assertEqual(causes['submittedPrimitive'], ('absent', ''))
        reached = {s for _, text in sgs.sources()
                   for s, v in sgs._selector_causes(ps.strip_lean_comments(text)).items() if v[0] == 'ok'}
        self.assertEqual(reached, {'submittedFullRunDesign', 'submittedPrimitive', 'submittedDesign'})

    def test_every_declared_entry_point_has_a_row(self):
        rows = sgs.observe('namespace PolicySubmission\nend PolicySubmission\n')
        self.assertEqual(tuple(rows), sgs.ENTRY_POINTS)
        self.assertEqual(len(sgs.ENTRY_POINTS), 9)
        self.assertEqual(len(set(sgs.ENTRY_POINTS)), 9)


class Digest(unittest.TestCase):
    def test_the_pinned_digest_holds(self):
        self.assertEqual(sgs.digest(sgs.ENTRY_POINTS), sgs.SURFACE_DIGEST)

    def test_no_row_is_a_byte_copy_of_another_beyond_the_known_delegation(self):
        # The harness's self-check, and the whole point of the lane: a row wired up wrongly becomes a duplicate of
        # its neighbour and inflates the claimed coverage silently. Fails the moment a NEW duplicate appears.
        self.assertEqual(sgs.duplicate_columns(), sgs.KNOWN_DUPLICATE_COLUMNS)

    def test_the_duplicate_check_would_catch_an_injected_copy(self):
        # Proves the check above can fire at all, rather than passing because the comparison is degenerate.
        observed = sgs.observations()
        for rows in observed.values():
            rows['validate_qfr'] = rows['validate']            # re-create the described defect exactly
        self.assertIn(('validate', 'validate_qfr'), sgs.duplicate_columns(observed))

    def test_the_claimed_behaviour_count_matches_the_rows_minus_the_duplicates(self):
        # The honest coverage number, asserted rather than asserted-about in prose.
        self.assertEqual(len(sgs.ENTRY_POINTS) - len(sgs.duplicate_columns()), 8)


class QualifyFullRun(unittest.TestCase):
    """What the previously-unverified branch actually does, over all 546 sources."""

    @classmethod
    def setUpClass(cls):
        cls.report = sgs.audit_qualify_full_run()

    def test_qualification_never_refuses_what_the_ordinary_path_accepts(self):
        # The non-negotiable: widening observation must not add a refusal. `validate` only relaxes on this flag.
        self.assertEqual(self.report['refuses_more'], [],
                         'qualify_full_run=True must be monotone: it may accept more, never refuse more')

    def test_the_monotonicity_check_would_catch_a_non_monotone_branch(self):
        # `refuses_more == []` is satisfied by an empty list, so prove the audit can populate it at all.
        # Without this, the assertion above cannot distinguish a sound branch from a blind one.
        injected = {'synthetic': {'validate': ('ok', 'None'), 'validate_qfr': ('ValueError', 'refused')}}
        report = sgs.audit_qualify_full_run(injected)
        self.assertEqual(len(report['refuses_more']), 1)
        self.assertEqual(report['refuses_more'][0]['source'], 'synthetic')
        self.assertEqual(report['accepts_more'], [])

    def test_qualification_accepts_only_full_run_selectors_and_the_full_run_import(self):
        # Every relaxation must be one of the gate's two documented ones, not an accident.
        for case in self.report['accepts_more']:
            self.assertRegex(case['plain_refusal'],
                             f"submittedFullRunDesign requires|imports {ps.FULL_RUN_IMPORT}|"
                             f"line 1 imports {ps.FULL_RUN_IMPORT}",
                             f"{case['source']} is accepted under qualification for an unrelated reason")

    def test_a_banned_token_is_refused_on_both_paths(self):
        # Qualification relaxes the full-run rules and nothing else.
        banned = [n for n, _ in sgs.sources() if n.endswith('plain/banned')]
        observed = sgs.observations()
        self.assertEqual(len(banned), 18)
        for name in banned:
            self.assertEqual(observed[name]['validate'][0], 'ValueError')
            self.assertEqual(observed[name]['validate_qfr'][0], 'ValueError')

    def test_the_audit_partitions_the_whole_corpus(self):
        self.assertEqual(sum(len(v) for v in self.report.values()), CORPUS_SIZE)
        self.assertEqual(len(self.report['accepts_more']), 261)
        self.assertEqual(len(self.report['cause_changed']), 84)
        self.assertEqual(len(self.report['identical']), 394)

    def test_the_cause_differs_only_where_a_header_refusal_pre_empts_the_selector_refusal(self):
        # Both paths REFUSE; only the stated cause differs. It is participant-facing text, so it is pinned rather
        # than left to drift. The mechanism is ordering: the plain path runs `check_no_full_run_selector` BEFORE
        # the header/import loop, so it blames the selector; under qualification that check is skipped and the
        # same source is blamed on the structural problem it also has. Every such source is refused either way.
        headers = {case['source'].split('|')[1] for case in self.report['cause_changed']}
        self.assertEqual(headers, {'misplaced', 'unapproved', 'empty-import', 'no-header'},
                         'only headers that are independently malformed may change the cause')
        for case in self.report['cause_changed']:
            self.assertIn(ps.FULL_RUN_REFUSAL, case['plain'])
            self.assertTrue(
                case['qualified'].startswith(ps.IMPORT_REFUSAL)
                or case['qualified'].startswith('Policy.lean has an unsupported import/header'),
                f"{case['source']} changed cause for something other than a header/import fault: {case['qualified']}")
            self.assertIn('fullrun/', case['source'], 'only full-run bodies can have their cause pre-empted')


if __name__ == '__main__':
    unittest.main()
