import unittest
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import shutil

from policy_source import FULL_RUN_IMPORT, validate


SAFE = '''import ForShor.ShorVerification.Framework.Policy
namespace PolicySubmission
def points := #[1, 2, 3]
end PolicySubmission
'''
FULL_RUN = SAFE.replace('def points', 'def submittedFullRunDesign : Nat := 0\ndef points')


class PolicySource(unittest.TestCase):
    def test_normal_policy_and_nested_documentation_are_data(self):
        validate(SAFE)
        validate('/- #eval IO.println "not code" /- axiom -/ -/\n' + SAFE)
        validate(SAFE.replace('PolicySubmission', 'PTPPolicySubmission'))
        validate('/- submittedFullRunDesign /- nested -/ -/\n-- submittedFullRunDesign\n' + SAFE)
        validate(SAFE.replace('points', 'submittedFullRunDesignExample'))

    def test_full_run_declaration_and_import_are_dedicated_only(self):
        with self.assertRaisesRegex(ValueError, 'submittedFullRunDesign'):
            validate(FULL_RUN)
        with self.assertRaisesRegex(ValueError, 'approved template modules'):
            validate(f'import {FULL_RUN_IMPORT}\n' + SAFE)
        validate(f'import {FULL_RUN_IMPORT}\n' + FULL_RUN, qualify_full_run=True)
        for source in (f'import {FULL_RUN_IMPORT}.Other\n' + FULL_RUN,
                       f'import {FULL_RUN_IMPORT}\n' + FULL_RUN + '#eval 1\n'):
            with self.assertRaises(ValueError):
                validate(source, qualify_full_run=True)

    def test_full_run_cli_flag_cannot_mark_ordinary_preflight_artifacts(self):
        command = [sys.executable, str(Path(__file__).with_name('policy_source.py')), '--stdin', '--qualify-full-run']
        source = f'import {FULL_RUN_IMPORT}\n' + FULL_RUN
        self.assertEqual(subprocess.run(command, input=source, text=True, capture_output=True).returncode, 0)
        result = subprocess.run([*command, '--artifacts', '.'], input=source, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('cannot be used for ordinary preflight artifacts', result.stderr)

    def test_build_time_execution_and_trust_overrides_are_refused(self):
        payloads = ['#eval IO.println "canary"', 'run_cmd pure ()',
                    'axiom unproved : False', 'unsafe def f := 1',
                    'def p := by native_decide', '@[implemented_by other] def p := 1',
                    'attribute [csimp] something', 'macro "x" : command => pure default',
                    'def p := by run_tac pure ()', 'set_option debug.skipKernelTC true',
                    'attribute [command_elab x] f', 'initialize x := pure ()']
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate(SAFE + payload + '\n')

    def test_comments_strings_and_escaped_names_cannot_hide_executable_tokens(self):
        for camouflage in ['def a := "/-"\n#eval IO.println "canary"\ndef b := "-/"',
                           'def a := \'"\'\n#eval IO.println "canary"',
                           'def a := r#"/-"#\n#eval IO.println "canary"',
                           'def «x/-» := 1\n#eval IO.println "canary"',
                           '@[\nextern "canary"\n] def x := 1']:
            with self.subTest(camouflage=camouflage), self.assertRaises(ValueError):
                validate(SAFE + camouflage)

    def test_extra_imports_and_continuation_lines_are_refused(self):
        for prefix in ['import Lean\n',
                       'import ForShor.OtherModule\n',
                       'import ForShor.ShorVerification.Framework.Policy ForShor.OtherModule\n',
                       'import ForShor.ShorVerification.Framework.Policy\nForShor.OtherModule\n',
                       'import\nForShor.ShorVerification.Framework.Policy\n']:
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                validate(prefix + SAFE)

    def test_ci_refusal_retains_identity_and_reports_a_rejection_without_running_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Policy.lean'
            canary = root / 'must-not-exist'
            source.write_text(SAFE + f'\n#eval IO.FS.writeFile "{canary}" "executed"\n')
            report = root / 'table-generation-submission-result.json'
            report.write_text(json.dumps({'kind': 'policy', 'phase': 'preflight', 'status': 'success',
                                          'metadata': {'head_sha': 'a' * 40}, 'checks': []}))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('policy_source.py')), str(source),
                                     '--artifacts', str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(canary.exists())
            data = json.loads(report.read_text())
            self.assertEqual(data['metadata']['head_sha'], 'a' * 40)
            self.assertEqual(data['status'], 'failure')
            self.assertEqual(data['checks'][0]['name'], 'policy-source')

    def test_current_sparse_controls_work_with_an_older_scientific_verifier(self):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            controls = workspace / 'controls'
            for relative in ['scripts/ci/policy_source.py', 'scripts/verifier.py', 'scripts/leaderboard.py', 'leaderboard/config.json']:
                target = controls / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / relative, target)
            old_verifier = workspace / 'verifier'
            old_verifier.mkdir()
            (old_verifier / 'old-scientific-input').write_text('pinned and unchanged')
            submission = workspace / 'submission'
            submission.mkdir()
            source = submission / 'Policy.lean'
            source.write_text(SAFE + '\n#eval IO.println "canary"\n')
            artifacts = submission / 'artifacts'
            artifacts.mkdir()
            report = artifacts / 'table-generation-submission-result.json'
            report.write_text(json.dumps({'kind': 'policy', 'phase': 'preflight', 'status': 'success', 'checks': []}))
            result = subprocess.run([sys.executable, 'scripts/ci/policy_source.py', '../submission/Policy.lean',
                                     '--artifacts', '../submission/artifacts'], cwd=controls, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('build-time commands', result.stderr)
            self.assertEqual(json.loads(report.read_text())['checks'][0]['name'], 'policy-source')
            self.assertEqual((old_verifier / 'old-scientific-input').read_text(), 'pinned and unchanged')


PRIM = 'def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger'
FULL = 'def submittedFullRunDesign : Shor.FullRunDesign := .reference'
DESIGN = 'def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }'
FULL_IMPORT = f'import {FULL_RUN_IMPORT}\n'
POLICY_IMPORT = 'import ForShor.ShorVerification.Framework.Policy\n'


def source(*declarations, namespace='PolicySubmission', imports=''):
    """A header, the named namespace, the declarations at column 0, then a normal definition (a new command)."""
    body = ''.join(d + '\n' for d in declarations)
    return f'{POLICY_IMPORT}{imports}namespace {namespace}\n{body}def points := #[1, 2, 3]\nend {namespace}\n'


class ExplicitDefaults(unittest.TestCase):
    """Owner ruling 2026-11-24: Ross-Selinger, the reference full run and coherent readout are the defaults, and an
    explicit default token is identical to omission. Only those tokens: the grammar is not widened."""

    FULL_REFUSAL = 'submittedFullRunDesign requires dedicated nonpublishing full-run qualification'
    IMPORT_REFUSAL = 'imports must be the approved template modules'

    def test_explicit_reference_full_run_is_accepted_like_omission(self):
        # Fails if check_no_full_run_selector / the FULL_RUN_IMPORT allowance in validate refuse the default again.
        validate(source(FULL, imports=FULL_IMPORT))
        validate(source(FULL.replace('Shor.FullRunDesign', '_root_.Shor.FullRunDesign')
                        .replace(':= .reference', ':= Shor.FullRunDesign.reference'), imports=FULL_IMPORT))
        validate(source(FULL, DESIGN, PRIM, imports=FULL_IMPORT))

    def test_the_full_run_import_is_allowed_only_with_the_explicit_default(self):
        # Fails if the import is allowed unconditionally (an import with no declaration stays refused, as before).
        with self.assertRaisesRegex(ValueError, self.IMPORT_REFUSAL):
            validate(source(imports=FULL_IMPORT))
        with self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):   # a non-default declaration is refused first, by the old message
            validate(source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT))

    def test_non_default_and_unknown_full_run_tokens_are_refused_with_the_same_message(self):
        # Fails if the default recogniser accepts a token that is not exactly `.reference`.
        for value in ('.quotientExact', '.quotientCatalytic', '.splitStep4Kicks', '.Reference', '.referenceX', '.reference_',
                      '.referenc', 'Shor.FullRunDesign.quotientExact', '.reference |> id', 'default', '0'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
                validate(source(f'def submittedFullRunDesign : Shor.FullRunDesign := {value}', imports=FULL_IMPORT))

    def test_the_explicit_full_run_default_is_exactly_one_canonical_declaration(self):
        for label, text in {
                'wrong type': source(FULL.replace('Shor.FullRunDesign', 'Nat'), imports=FULL_IMPORT),
                'no type': source('def submittedFullRunDesign := Shor.FullRunDesign.reference', imports=FULL_IMPORT),
                'declared twice': source(FULL, FULL, imports=FULL_IMPORT),
                'decoy namespace': source(FULL, namespace='Decoy', imports=FULL_IMPORT),
                'nested namespace': source(FULL, namespace='PolicySubmission.Inner', imports=FULL_IMPORT),
                'ptp board': source(FULL, namespace='PTPPolicySubmission', imports=FULL_IMPORT),
                'private': source('private ' + FULL, imports=FULL_IMPORT),
                'protected': source('protected ' + FULL, imports=FULL_IMPORT),
                'abbrev': source(FULL.replace('def ', 'abbrev '), imports=FULL_IMPORT),
                'indented': source('  ' + FULL, imports=FULL_IMPORT),
                'attribute line before': source('@[reducible]', FULL, imports=FULL_IMPORT),
                'modifier line before': source('private', FULL, imports=FULL_IMPORT),
                'continued on the next line': source(FULL, '  |> fun _ => Shor.FullRunDesign.quotientExact', imports=FULL_IMPORT),
                'value on the next line': source(FULL.replace(':= .reference', ':=\n  .reference'), imports=FULL_IMPORT),
                'used elsewhere': source(FULL, 'def alias := submittedFullRunDesign', imports=FULL_IMPORT),
                'second use inside the policy': source(FULL, 'theorem t : submittedFullRunDesign = .reference := rfl', imports=FULL_IMPORT),
        }.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
                validate(text)

    def test_the_ci_preflight_command_passes_the_explicit_default_and_refuses_the_rest(self):
        # The workflow runs `policy_source.py <file> --artifacts ...` without --qualify-full-run.
        command = [sys.executable, str(Path(__file__).with_name('policy_source.py')), '--stdin']
        good = subprocess.run(command, input=source(FULL, imports=FULL_IMPORT), text=True, capture_output=True)
        self.assertEqual((good.returncode, good.stderr), (0, ''))
        bad = subprocess.run(command, input=source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT),
                             text=True, capture_output=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn(self.FULL_REFUSAL, bad.stderr)

    def test_comments_do_not_make_a_declaration_or_hide_a_second_one(self):
        validate(source(FULL, '-- ' + FULL, '/- ' + FULL + ' -/', imports=FULL_IMPORT))
        with self.assertRaisesRegex(ValueError, self.FULL_REFUSAL):
            validate(source(FULL, '/- note -/ ' + FULL, imports=FULL_IMPORT))

    def test_the_dedicated_qualification_mode_is_unchanged(self):
        validate(source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT), qualify_full_run=True)
        validate(source(FULL, imports=FULL_IMPORT), qualify_full_run=True)

    def test_default_declarations_are_consumed_only_in_the_right_namespace_and_board(self):
        import policy_source
        for text, tokens in ((source(PRIM, DESIGN, FULL, imports=FULL_IMPORT), {'submittedPrimitive', 'submittedDesign', 'submittedFullRunDesign'}),
                             (source(PRIM, namespace='PTPPolicySubmission'), {'submittedPrimitive'}),
                             (source(DESIGN, namespace='PTPPolicySubmission'), {'submittedDesign'}),
                             (source(FULL, namespace='PTPPolicySubmission', imports=FULL_IMPORT), set()),
                             (source(PRIM, namespace='Decoy'), set())):
            with self.subTest(tokens=tokens):
                remaining, consumed = policy_source.default_selectors(text)
                self.assertEqual(set(consumed), tokens)
                # a consumed declaration disappears; the rest of the file (and its line numbering) does not move
                self.assertEqual(remaining.count('\n'), text.count('\n'))
                for token in consumed:
                    self.assertNotIn(token, remaining)
                self.assertIn('def points', remaining)
        where = source('def submittedDesign : Shor.SubmissionDesign where\n  readout := .coherent')
        self.assertEqual(set(policy_source.default_selectors(where)[1]), {'submittedDesign'})
        semi = source('def submittedDesign : Shor.SubmissionDesign where\n  readout := .semiclassical')
        self.assertEqual(policy_source.default_selectors(semi)[1], {})
        self.assertIn('submittedDesign', policy_source.default_selectors(semi)[0])


class RefusalDifferentiation(unittest.TestCase):
    """A source refusal must name WHICH cause refused it.

    Owner priority 2026-11-24: differentiating incorrect policies from correct ones is the
    priority. One message over several causes cannot tell a PARTICIPANT ERROR (rewrite the file)
    from an UNSUPPORTED-BUT-VALID request (use the dedicated nonpublishing route), and those need
    different participant actions. The four causes that used to collide are census collisions
    C5, C6, C7 and C8, enumerated as the class-level patterns below, one per cause, and each
    test drives the one it names.

    Matching is on anchored, class-level patterns naming the CAUSE, never on incidental wording.
    Nothing here changes what is accepted or refused: every source below was refused before too.
    """

    #: The anchors the rest of the repository matches on; a rewording must keep them intact.
    #: Consumers match them as PREFIXES -- test_source_gate_surface.py, and
    #: test_the_import_message_does_not_promise_a_rule_that_is_not_checked below for the second --
    #: so a rewording keeps every consumer working only if the leading sentence survives.
    FULL_REFUSAL = ExplicitDefaults.FULL_REFUSAL
    IMPORT_REFUSAL = ExplicitDefaults.IMPORT_REFUSAL

    #: Cause classes for the full-run selector (policy_source.py:check_no_full_run_selector).
    UNSUPPORTED = re.compile(r'selects the non-default full-run design')
    WRONG_FORM = re.compile(r'not in the shipped form')
    MENTION = re.compile(r'does not declare a selector')
    #: Cause classes for the import rule (policy_source.py:validate).
    IMPORT_AFTER_HEADER = re.compile(r'an `import` after the header')
    IMPORT_NO_MODULE = re.compile(r'an `import` with no module name')
    IMPORT_FULL_RUN = re.compile(r'only together with the explicit default')
    IMPORT_UNKNOWN = re.compile(r'is not an approved template module')
    #: Always present once a cause is named, so triage can go straight to the line.
    AT_LINE = re.compile(r'\bline (\d+)\b')

    def reason(self, text, **kwargs):
        with self.assertRaises(ValueError) as refused:
            validate(text, **kwargs)
        return str(refused.exception)

    def classes(self, reason):
        return {name for name in ('UNSUPPORTED', 'WRONG_FORM', 'MENTION') if getattr(self, name).search(reason)}

    # --- C5: one message for 3 cause classes and 20+ forms -----------------------------------

    def test_a_non_default_profile_is_sent_to_the_dedicated_harness_and_names_the_value(self):
        # Production change that would fail this: collapsing check_no_full_run_selector's cause
        # classification back into the single old message. A named profile is a VALID request on
        # the wrong route, so the participant must be sent to the dedicated nonpublishing harness
        # rather than told to rewrite a declaration that is already well formed.
        for value in ('.quotientExact', '.quotientCatalytic', '.splitStep4Kicks',
                      'Shor.FullRunDesign.quotientExact', '.referenceX', 'default', '0'):
            with self.subTest(value=value):
                reason = self.reason(source(f'def submittedFullRunDesign : Shor.FullRunDesign := {value}',
                                            imports=FULL_IMPORT))
                self.assertIn(self.FULL_REFUSAL, reason)
                self.assertEqual(self.classes(reason), {'UNSUPPORTED'}, reason)
                self.assertIn(value, reason)
                self.assertRegex(reason, self.AT_LINE)

    def test_the_default_written_the_wrong_way_is_told_to_rewrite_it_not_to_qualify_it(self):
        # Production change that would fail this: reporting the mis-written default as a non-default
        # selection (or as a bare mention). The value IS the default here, so the participant must
        # rewrite the declaration in the shipped form; sending them to the qualification harness
        # would waste a nonpublishing run on a policy the ordinary route already accepts.
        for label, text in {
            'private': source('private ' + FULL, imports=FULL_IMPORT),
            'protected': source('protected ' + FULL, imports=FULL_IMPORT),
            'abbrev': source(FULL.replace('def ', 'abbrev '), imports=FULL_IMPORT),
            'indented': source('  ' + FULL, imports=FULL_IMPORT),
            'declared twice': source(FULL, FULL, imports=FULL_IMPORT),
            'decoy namespace': source(FULL, namespace='Decoy', imports=FULL_IMPORT),
            'nested namespace': source(FULL, namespace='PolicySubmission.Inner', imports=FULL_IMPORT),
            'ptp board': source(FULL, namespace='PTPPolicySubmission', imports=FULL_IMPORT),
            'attribute line before': source('@[reducible]', FULL, imports=FULL_IMPORT),
            'modifier line before': source('private', FULL, imports=FULL_IMPORT),
            'wrong type': source(FULL.replace('Shor.FullRunDesign', 'Nat'), imports=FULL_IMPORT),
            'no type': source('def submittedFullRunDesign := Shor.FullRunDesign.reference', imports=FULL_IMPORT),
            'continued on the next line': source(FULL, '  |> fun _ => Shor.FullRunDesign.quotientExact',
                                                 imports=FULL_IMPORT),
            'value on the next line': source(FULL.replace(':= .reference', ':=\n  .reference'), imports=FULL_IMPORT),
        }.items():
            with self.subTest(label):
                reason = self.reason(text)
                self.assertIn(self.FULL_REFUSAL, reason)
                self.assertEqual(self.classes(reason), {'WRONG_FORM'}, reason)
                self.assertIn('def submittedFullRunDesign : Shor.FullRunDesign := .reference', reason)
                self.assertRegex(reason, self.AT_LINE)

    def test_a_bare_mention_is_told_to_delete_it(self):
        # Production change that would fail this: classifying a use of the name as a declaration.
        # Neither rewriting a declaration nor qualifying a full run is the right action here: the
        # participant has to remove the mention.
        for label, text in {
            'used elsewhere': source(FULL, 'def alias := submittedFullRunDesign', imports=FULL_IMPORT),
            'in a theorem statement': source(FULL, 'theorem t : submittedFullRunDesign = .reference := rfl',
                                             imports=FULL_IMPORT),
            'no declaration at all': source('def alias := submittedFullRunDesign'),
        }.items():
            with self.subTest(label):
                reason = self.reason(text)
                self.assertIn(self.FULL_REFUSAL, reason)
                self.assertEqual(self.classes(reason), {'MENTION'}, reason)
                self.assertRegex(reason, self.AT_LINE)

    def test_the_reported_line_is_the_line_the_participant_has_to_edit(self):
        # Production change that would fail this: dropping the line number, or computing it on the
        # raw source instead of the comment-stripped, default-stripped text (comments and a consumed
        # explicit default must not shift it). Line 1 is the Policy import, 2 the FullRunDesign
        # import, 3 the namespace, 4 the comment, 5 the offending declaration.
        text = ('import ForShor.ShorVerification.Framework.Policy\n' + FULL_IMPORT
                + 'namespace PolicySubmission\n'
                + '/- a comment\n   spanning lines -/\n'
                + 'def submittedFullRunDesign : Shor.FullRunDesign := .quotientExact\n'
                + 'def points := #[1, 2, 3]\nend PolicySubmission\n')
        self.assertEqual(self.AT_LINE.search(self.reason(text))[1], '6')

    def test_the_three_full_run_cause_classes_never_share_a_message(self):
        # Production change that would fail this: any rewording that makes two classes produce the
        # same string again. This asserts the absence of the collision itself (census C5).
        reasons = [self.reason(text) for text in (
            source(FULL.replace('.reference', '.quotientExact'), imports=FULL_IMPORT),
            source('private ' + FULL, imports=FULL_IMPORT),
            source('def alias := submittedFullRunDesign'))]
        self.assertEqual(len(set(reasons)), 3, reasons)

    # --- C7: one message for 4 import causes ---------------------------------------------------

    def test_each_import_cause_is_named_separately(self):
        # Production change that would fail this: collapsing validate's import branch back into one
        # message. The FullRunDesign import is an unsupported-but-valid request (it is accepted with
        # the explicit default, and by the dedicated harness); an unknown module, a missing module
        # name and a misplaced import are three different participant errors.
        for label, text, expected in (
            ('full-run module', source(imports=FULL_IMPORT), 'IMPORT_FULL_RUN'),
            ('unknown module', source(imports='import ForShor.OtherModule\n'), 'IMPORT_UNKNOWN'),
            ('two modules, one unknown',
             source(imports='import ForShor.ShorVerification.Framework.Policy ForShor.OtherModule\n'),
             'IMPORT_UNKNOWN'),
            ('no module name', 'import\n' + SAFE, 'IMPORT_NO_MODULE'),
            ('after the header', SAFE + 'import ForShor.ShorVerification.Framework.Policy\n', 'IMPORT_AFTER_HEADER'),
        ):
            with self.subTest(label):
                reason = self.reason(text)
                self.assertIn(self.IMPORT_REFUSAL, reason)
                self.assertRegex(reason, self.AT_LINE)
                named = {name for name in ('IMPORT_AFTER_HEADER', 'IMPORT_NO_MODULE', 'IMPORT_FULL_RUN',
                                           'IMPORT_UNKNOWN') if getattr(self, name).search(reason)}
                self.assertEqual(named, {expected}, reason)

    def test_the_unknown_module_is_named_in_the_refusal(self):
        # Production change that would fail this: dropping the module name from the import message.
        # Without it a participant with several imports cannot tell which one is unapproved.
        self.assertIn('ForShor.OtherModule', self.reason(source(imports='import ForShor.OtherModule\n')))

    # --- C6 / C8: hash commands, escaped identifiers, header ------------------------------------

    def test_hash_commands_and_escaped_identifiers_are_told_apart(self):
        # Production change that would fail this: putting the `#` check and the guillemet check back
        # under one message. They are different edits, and the hash branch must keep the
        # "build-time commands" wording that test_contributor_cli.py and the CI test assert.
        hashed = self.reason(SAFE + '#eval 1\n')
        escaped = self.reason(SAFE + 'def \u00abx\u00bb := 1\n')
        self.assertIn('build-time commands', hashed)
        self.assertNotIn('build-time commands', escaped)
        self.assertRegex(escaped, r'escaped identifier')
        self.assertNotEqual(hashed, escaped)
        for reason in (hashed, escaped):
            self.assertRegex(reason, self.AT_LINE)

    def test_the_header_refusal_names_the_line_it_refused(self):
        # Production change that would fail this: dropping the line number from the header message.
        # "unsupported import/header" alone does not say which line is not a header command.
        reason = self.reason('import ForShor.ShorVerification.Framework.Policy\nnot_a_header_command\n')
        self.assertIn('unsupported import/header', reason)
        self.assertEqual(self.AT_LINE.search(reason)[1], '2')

    # --- F5: the message promised a rule the gate does not enforce -----------------------------

    def test_several_approved_modules_on_one_import_line_are_still_accepted(self):
        # Production change that would fail this: ENFORCING "one import command per line" by adding a
        # len(modules) test to validate. Census finding F5: the gate has only ever tested membership,
        # so this source is accepted today, and refusing it would change the ACCEPTED SET -- out of
        # scope for a differentiation lane. The message was corrected instead; this test is what makes
        # the correction the honest half of that choice.
        validate('import ForShor.ShorVerification.Framework.Policy '
                 'ForShor.ShorVerification.Submission.Decide\n'
                 'namespace PolicySubmission\ndef points := #[1, 2, 3]\nend PolicySubmission\n')

    def test_the_import_message_does_not_promise_a_rule_that_is_not_checked(self):
        # Production change that would fail this: restoring "one import command per line" to
        # IMPORT_REFUSAL without also enforcing it. A participant reading that clause would rewrite a
        # file that was never refused for that reason, and would not learn which module was wrong.
        # Census finding F5.
        from policy_source import IMPORT_REFUSAL
        self.assertNotIn('one import command per line', IMPORT_REFUSAL)
        self.assertTrue(IMPORT_REFUSAL.startswith('Policy.lean imports must be the approved template modules'))

    def test_an_unapproved_module_beside_an_approved_one_still_names_the_unapproved_one(self):
        # Production change that would fail this: dropping the per-module report when several modules
        # share a line. This is the case the gate DOES check, and the one the corrected message
        # describes.
        reason = self.reason(source(imports='import ForShor.ShorVerification.Framework.Policy '
                                            'ForShor.OtherModule\n'))
        self.assertIn('ForShor.OtherModule', reason)
        self.assertNotIn('ForShor.ShorVerification.Framework.Policy,', reason)


MEASURED = 'def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }'


class DeclaredReadout(unittest.TestCase):
    """`declared_readout` is how the verifier job knows, BEFORE it builds, whether this submission needs
    `--qualify-readout`. Three answers and no fourth: coherent, semiclassical, or a refusal."""

    def readout(self, text):
        from policy_source import declared_readout
        return declared_readout(text)

    def refusal(self, text):
        from policy_source import declared_readout
        with self.assertRaises(ValueError) as caught:
            declared_readout(text)
        return str(caught.exception)

    def test_omission_and_the_explicit_default_are_the_same_answer(self):
        # Owner ruling 2026-11-24, commit a4a7609. Production change that would fail this: recognising the
        # declaration by its token instead of by `default_selectors`, which would send `{ readout := .coherent }`
        # down the measured build and change the ForShor build for a submission that asked for the default.
        self.assertEqual(self.readout(source()), 'coherent')
        self.assertEqual(self.readout(source(DESIGN)), 'coherent')
        self.assertEqual(self.readout(source(DESIGN, namespace='PTPPolicySubmission')), 'coherent')
        self.assertEqual(self.readout(source(DESIGN.replace(':= { readout := .coherent }', 'where\n  readout := .coherent'))),
                         'coherent')
        self.assertEqual(self.readout(source(DESIGN, PRIM)), 'coherent')

    def test_the_shipped_measured_declaration_is_the_measured_answer(self):
        # Production change that would fail this: dropping `_MEASURED_FORMS`, so the verifier never passes
        # `--qualify-readout` and the two-job flow can still not carry a measured result.
        self.assertEqual(self.readout(source(MEASURED)), 'semiclassical')
        self.assertEqual(self.readout(source(MEASURED.replace('Shor.SubmissionDesign', '_root_.Shor.SubmissionDesign'))),
                         'semiclassical')
        self.assertEqual(self.readout(source(MEASURED.replace(':= { readout := .semiclassical }', 'where\n  readout := .semiclassical'))),
                         'semiclassical')
        # PhaseTripleProduct is recognised here and refused by `intake_v2.policy_from_lean` with its own named
        # reason; this module does not invent a second spelling of that rule.
        self.assertEqual(self.readout(source(MEASURED, namespace='PTPPolicySubmission')), 'semiclassical')

    def test_the_measured_declaration_obeys_the_same_shipped_form_as_the_default(self):
        # Production change that would fail this: recognising the measured value with a loose regex instead of
        # `default_selectors`' walker. Each of these would then choose the measured build from a declaration Lean
        # does not read, or reads differently.
        for label, text in {
                'wrong type': source(MEASURED.replace('Shor.SubmissionDesign', 'Nat')),
                'no type': source('def submittedDesign := { readout := .semiclassical }'),
                'declared twice': source(MEASURED, MEASURED),
                'measured and default together': source(MEASURED, DESIGN),
                'decoy namespace': source(MEASURED, namespace='Decoy'),
                'nested namespace': source(MEASURED, namespace='PolicySubmission.Inner'),
                'private': source('private ' + MEASURED),
                'abbrev': source(MEASURED.replace('def ', 'abbrev ')),
                'indented': source('  ' + MEASURED),
                'attribute line before': source('@[reducible]', MEASURED),
                'continued on the next line': source(MEASURED, '  |> id'),
                'value on the next line': source(MEASURED.replace(':= { readout', ':=\n  { readout')),
                'used elsewhere': source(MEASURED, 'def alias := submittedDesign'),
        }.items():
            with self.subTest(label):
                self.assertIn('line', self.refusal(text))

    def test_an_unrecognised_readout_refuses_and_never_guesses_a_path(self):
        # Production change that would fail this: falling back to `coherent` for anything the recogniser does not
        # know. A build would then silently ignore a readout the participant wrote.
        for label, text in {'another value': source(MEASURED.replace('.semiclassical', '.teleported')),
                            'bare mention': source('def alias := submittedDesign'),
                            'a theorem about it': source(DESIGN, 'theorem t : submittedDesign = default := rfl')}.items():
            with self.subTest(label):
                self.assertIn('submittedDesign', self.refusal(text))

    def test_comments_neither_declare_a_readout_nor_hide_one(self):
        self.assertEqual(self.readout(source('-- ' + MEASURED, '/- ' + MEASURED + ' -/')), 'coherent')
        self.assertEqual(self.readout(source(MEASURED, '-- ' + DESIGN)), 'semiclassical')

    def test_reading_the_readout_does_not_decide_whether_the_source_is_accepted(self):
        # `validate` is the gate; `declared_readout` only routes. Production change that would fail this: putting a
        # readout rule inside `validate`, which would change the accepted set for every submission.
        validate(source(MEASURED))
        validate(source(MEASURED.replace('.semiclassical', '.teleported')))

    def test_the_pre_build_refusal_is_the_intakes_own_refusal_word_for_word(self):
        # The verifier job refuses an unrecognised design before the build and `intake_v2.refuse_unqualified_design`
        # refuses it during the build. Production change that would fail this: letting either message drift, so a
        # participant is told two different things about the same line depending on which half ran first.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'evaluation'))
        import intake_v2
        from policy_source import READOUT_REFUSAL
        text = source(MEASURED.replace('.semiclassical', '.teleported'))
        with self.assertRaises(intake_v2.Refused) as intake:
            intake_v2.refuse_unqualified_design(text)
        self.assertEqual(self.refusal(text), intake.exception.reason)
        self.assertTrue(intake.exception.reason.startswith(READOUT_REFUSAL))


if __name__ == '__main__':
    unittest.main()
