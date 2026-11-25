"""Cheap, non-executing source restrictions for complete PP/PTP policies.

Apply before compilation/reuse and again to GitHub's exact source before
publication. Lean and the pinned estimator still decide mathematical validity.
The lexer is the existing hardened verifier lexer; no submitted code is run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.verifier import strip_lean_comments

MAX_BYTES = 1_000_000
FULL_RUN_IMPORT = 'ForShor.ShorVerification.Framework.FullRunDesign'
ALLOWED_IMPORTS = {
    'ForShor.ShorVerification.Framework.Policy',
    'ForShor.ShorVerification.Framework.PrimitiveSynthesis',
    'ForShor.ShorVerification.Framework.ToomCookTable',
    'ForShor.ShorVerification.Implementation.PhaseTripleProduct.Statement',
    'ForShor.ShorVerification.Submission.Decide',
}
# Same additional restrictions as the existing site harness, including paths
# that the upstream lexical shell gate does not reject (notably #eval).
BANNED = re.compile(
    r"(?<![A-Za-z0-9_'])"
    r"(?:run_cmd|run_meta|run_elab|run_tac|runCmd|runTac|native_decide|ofReduceBool|trustCompiler|"
    r"sorry|sorryAx|admit|axiom|constant|opaque|partial|unsafe|implemented_by|extern|csimp|"
    r"builtin_initialize|initialize|macro_rules|macro|elab_rules|elab|syntax|notation|"
    r"attribute|instance|class|export|deriving|command_elab|term_elab|tactic_elab|"
    r"infixl|infixr|infix|prefix|postfix|declare_syntax_cat|set_option|Lean|IO|System|"
    r"unsafeBaseIO|dbgTrace|panic|prelude)"
    r"(?![A-Za-z0-9_'])"
)
HEADER_END = re.compile(r'^(?:namespace|open|section|end|def|abbrev|theorem|lemma|example|variable|universe)\b')
FULL_RUN_SELECTOR = re.compile(r"(?<![\w'])submittedFullRunDesign(?![\w'])")


# Explicit default selections. Ross-Selinger, the reference full run and coherent readout are
# the defaults, and an explicit default token is identical to omission (Lean's resolvers return the default when the
# declaration is absent, and attest the document unchanged when it is the default). Only these exact declarations are
# recognised: one `def` at column 0 directly inside the board's namespace, annotated with the repo's type, with the
# default value and nothing else on the statement. Recognition is lexical only (nothing is run) and Lean still
# decides validity; anything else that mentions a selector name is refused exactly as before.
_SHOR = r'(?:_root_\.)?Shor\.'
_BOARDS = ('PolicySubmission', 'PTPPolicySubmission')
_DEFAULT_FORMS = {   # name: (declaration after the name, boards where Lean reads it)
    'submittedPrimitive': (rf':[ \t]*{_SHOR}PrimitiveSynthesis[ \t]*:=[ \t]*(?:\.|{_SHOR}PrimitiveSynthesis\.)rossSelinger',
                           _BOARDS),
    # PTPPolicyCheck refuses any submittedFullRunDesign, `.reference` included, so only the PhaseProduct board has the default.
    'submittedFullRunDesign': (rf':[ \t]*{_SHOR}FullRunDesign[ \t]*:=[ \t]*(?:\.|{_SHOR}FullRunDesign\.)reference',
                               ('PolicySubmission',)),
    'submittedDesign': (rf':[ \t]*{_SHOR}SubmissionDesign[ \t]*(?::=[ \t]*\{{[ \t]*readout[ \t]*:=[ \t]*\.coherent[ \t]*\}}'
                        rf'|where[ \t]*\n[ \t]+readout[ \t]*:=[ \t]*\.coherent)', _BOARDS),
}
_DEFAULT_DECL = {name: re.compile(rf'def[ \t]+{name}[ \t]*{form}[ \t]*(?:\n|\Z)') for name, (form, _) in _DEFAULT_FORMS.items()}
# The MEASURED readout declaration, in the very same shipped form as the explicit default above and recognised by the
# very same walker: one `def` at column 0 directly inside the board's namespace, the repo's type, the value on the
# statement and nothing continuing it. This is the only non-default selection anything here recognises, because it is
# the only one with a route that publishes (`--qualify-readout`); every other selection stays refused exactly as it is.
_MEASURED_FORMS = {
    'submittedDesign': (rf':[ \t]*{_SHOR}SubmissionDesign[ \t]*(?::=[ \t]*\{{[ \t]*readout[ \t]*:=[ \t]*\.semiclassical[ \t]*\}}'
                        rf'|where[ \t]*\n[ \t]+readout[ \t]*:=[ \t]*\.semiclassical)', _BOARDS),
}
_MEASURED_DECL = {name: re.compile(rf'def[ \t]+{name}[ \t]*{form}[ \t]*(?:\n|\Z)') for name, (form, _) in _MEASURED_FORMS.items()}
_COMMAND_START = re.compile(r'(?:end|def|abbrev|theorem|lemma|example|namespace|open|section|variable|universe|structure|inductive)\b')
_MODIFIER_LINE = re.compile(r'(?:@\[|private\b|protected\b|noncomputable\b|unsafe\b|partial\b|nonrec\b|scoped\b|local\b)')
_NAMESPACE = re.compile(r'namespace[ \t]+([A-Za-z_][\w.]*)[ \t]*$')
_SECTION = re.compile(r'section\b')
_END = re.compile(r'end\b')


def default_selectors(text, *, forms=_DEFAULT_FORMS, declarations=_DEFAULT_DECL):
    """(`text` with each explicit default declaration blanked, {selector: board namespace}) for comment-stripped source.

    Line count is preserved. A declaration is consumed only once per selector, only at column 0 directly inside the
    board's namespace, and only when the next non-blank line starts a new command at column 0 (so no continuation
    line can change the value) and the previous one is not a modifier or attribute line. The rest of the file,
    including any second declaration or any other mention of the selector, is left for the refusal checks.

    `forms`/`declarations` select WHICH declarations are consumed, and default to the explicit defaults, so every
    existing caller is unchanged. `declared_readout` passes `_MEASURED_FORMS` to recognise the measured readout under
    exactly this discipline rather than a second, looser one."""
    lines = text.split('\n')
    consumed = {}
    scopes = []
    prefixes = ['']                                          # prefixes[-1] is the joined namespace; O(1) per line
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (m := _NAMESPACE.fullmatch(stripped)):
            scopes.append(m[1])
            prefixes.append(f'{prefixes[-1]}.{m[1]}' if prefixes[-1] else m[1])
        elif _SECTION.match(stripped):
            scopes.append('')
            prefixes.append(prefixes[-1])
        elif _END.match(stripped) and scopes:
            scopes.pop()
            prefixes.pop()
        namespace = prefixes[-1]
        found = re.match(r'def[ \t]+(submitted\w+)', line)       # column 0 only
        if not found or found[1] not in declarations or found[1] in consumed or namespace not in forms[found[1]][1]:
            continue
        rest = '\n'.join(lines[i:i + 2]) + '\n'
        match = declarations[found[1]].match(rest)
        if not match:
            continue
        span = match.group(0).count('\n')                      # lines the declaration occupies
        end = i + (span if span else 1)
        following = next((l for l in lines[end:] if l.strip()), None)
        previous = next((l.strip() for l in reversed(lines[:i]) if l.strip()), '')
        if (following is not None and not _COMMAND_START.match(following)) or _MODIFIER_LINE.match(previous):
            continue
        consumed[found[1]] = namespace
        for j in range(i, end):
            lines[j] = ''
    return '\n'.join(lines), consumed


def without_default_selectors(text):
    """Comment-stripped source with the explicit default selector declarations removed (see `default_selectors`)."""
    return default_selectors(text)[0]


# A refusal has to name WHICH cause refused, because the causes need different participant actions: a
# non-default profile is a valid request this route does not accept, a
# mis-written default only has to be rewritten, and a bare mention has to be deleted. The leading
# sentence of each message is the anchor the rest of the repository matches on and never changes; the
# cause follows it after a colon. Classification happens only AFTER the refusal is decided, so nothing
# here changes which sources are accepted or refused.
SHIPPED_FULL_RUN_FORM = 'def submittedFullRunDesign : Shor.FullRunDesign := .reference'
#: The leading sentence of every full-run source refusal. The cause follows it after a colon and states the rule
#: that failed, so the participant knows what to change.
FULL_RUN_REFUSAL = 'submittedFullRunDesign is not accepted as written'
#: The one readout declaration a full-run design is accepted beside, as a participant writes it.
MEASURED_READOUT_FORM = 'def submittedDesign : Shor.SubmissionDesign := { readout := .semiclassical }'
#: The message says what the gate checks -- `import A B` with both modules approved is accepted, because `validate`
#: only tests membership and never `len(modules)`: every module named on an `import` line, however many
#: there are, must be an approved template module, and every `import` must be in the header. The leading clause
#: ("imports must be the approved template modules") is the anchor other code matches on and is unchanged.
IMPORT_REFUSAL = 'Policy.lean imports must be the approved template modules, in the file\'s header'

#: Per selector: the shipped declaration, the noun for a non-default value, the noun for the default, the route that
#: does qualify it, and the pattern that recognises the default VALUE. `evaluation/intake_v2.py` classifies
#: `submittedDesign` and `submittedPrimitive` through `selector_cause` below, so these three rows are the one place
#: the source gate, the hosted intake and the publisher re-check agree about what each selector means.
_SELECTOR_CAUSES = {
    'submittedFullRunDesign': (SHIPPED_FULL_RUN_FORM, 'full-run design', 'full run',
                               'a full-run design is accepted only beside the semiclassical readout declaration '
                               f'`{MEASURED_READOUT_FORM}`, and this file does not declare it',
                               re.compile(rf"(?:\.|{_SHOR}FullRunDesign\.)reference(?![\w'])")),
    'submittedPrimitive': ('def submittedPrimitive : Shor.PrimitiveSynthesis := .rossSelinger', 'primitive synthesis',
                           'Ross-Selinger primitive synthesis',
                           'the standalone primitive qualification runs locally and does not publish, so a scored submission does not declare it',
                           re.compile(rf"(?:\.|{_SHOR}PrimitiveSynthesis\.)rossSelinger(?![\w'])")),
    'submittedDesign': ('def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }', 'submission design',
                        'coherent readout design',
                        'a submission design is accepted only as the coherent default '
                        '`def submittedDesign : Shor.SubmissionDesign := { readout := .coherent }` or the semiclassical '
                        f'readout `{MEASURED_READOUT_FORM}` (or the `where` form of either), as one public `def` at '
                        'column 0 directly inside the board\'s namespace, declared once',
                        re.compile(r"(?:\.|(?:Shor\.)?Readout\.)coherent(?![\w'])")),
}
_SELECTOR_TOKEN = {name: re.compile(rf"(?<![\w']){name}(?![\w'])") for name in _SELECTOR_CAUSES}
_SELECTOR_DECLARATION = {name: re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*|(?:private|protected|noncomputable|unsafe|partial|nonrec|scoped|local)[ \t]+)*"
    rf"(?:def|abbrev|instance|structure|inductive)[ \t]+{name}(?![\w'])(?P<rest>.*)$") for name in _SELECTOR_CAUSES}


def _shipped_form_rule(selector):
    """The clause that says what the shipped form is, naming the board namespace(s) Lean reads the selector in."""
    boards = _DEFAULT_FORMS[selector][1]
    where = (f'`namespace {boards[0]}`' if len(boards) == 1
             else ' or '.join(f'`namespace {board}`' for board in boards))
    return (f'one public `def` at column 0 directly inside {where}, with the repo type, the value on the same line, '
            'nothing continuing it on the next line, no attribute or modifier line before it, and declared exactly once')


def _declaration_span(lines, index):
    """`(first, end)` line indices of the declaration starting at `lines[index]`: it runs until the next non-blank
    line that starts a command at column 0 (or the end of the text)."""
    end = index + 1
    while end < len(lines) and not (lines[end].strip() and not lines[end][0].isspace()):
        end += 1
    return index, end


def _declared_value(lines, index):
    """The value of the declaration at `lines[index]`, continuation lines included: everything after its first `:=`
    (or `where`), whitespace-collapsed. Empty when the declaration states no value."""
    first, end = _declaration_span(lines, index)
    text = '\n'.join(lines[first:end])
    split = re.search(r":=|(?<![\w'])where(?![\w'])", text)
    return ' '.join(text[split.end():].split()) if split else ''


def selector_cause(residual, selector='submittedFullRunDesign'):
    """(line number, cause) for the first `selector` left after its explicit default declaration is consumed.

    Part of the intake contract: `evaluation/intake_v2.py` imports this to classify `submittedDesign` and
    `submittedPrimitive` with the same three classes this gate uses for `submittedFullRunDesign`, so the two cannot
    disagree about which cause refused. `residual` is comment-stripped, default-stripped source; both transformations
    preserve the line count, so the number is the participant's own line. Three classes, three participant actions:
    qualify elsewhere, rewrite, or delete. Nothing here decides whether a source is refused -- only why."""
    shipped, value_noun, default_noun, route, default_value = _SELECTOR_CAUSES[selector]
    found = _SELECTOR_TOKEN[selector].search(residual)
    number = residual.count('\n', 0, found.start()) + 1
    declaration = _SELECTOR_DECLARATION[selector].match(residual.split('\n')[number - 1])
    if declaration is None:
        return number, ('the name is used here but does not declare a selector, and an ordinary submission may not '
                        'mention it at all; remove the mention')
    # The value is the WHOLE declaration, not only its first line: a structure literal written on the lines after
    # `:=` (the natural way to write a design) was otherwise read as an empty value and told it "selects the default".
    value = _declared_value(residual.split('\n'), number - 1)
    if value and not default_value.search(value):
        return number, (f'this declaration selects the non-default {value_noun} `{value}`, and {route}')
    return number, (f'this declaration selects the default {default_noun}, but not in the shipped form '
                    f'`{shipped}` ({_shipped_form_rule(selector)}); rewrite it, or omit it, and the '
                    'ordinary route accepts the policy')


#: The two readout designs. `coherent` is the default and the only one an ordinary submission may declare;
#: `semiclassical` is the measured design that `evaluation/export_v2/readout_overlay.py` prices and that the intake
#: compiles only under `--qualify-readout`.
READOUTS = ('coherent', 'semiclassical')

#: The leading sentence of the refusal for an unrecognised `submittedDesign`. It is `evaluation/intake_v2.py`'s own
#: `refuse_unqualified_design` anchor: the verifier job decides the readout BEFORE the build and the intake decides it
#: again DURING the build, and the two must not tell a participant two different things about the same line.
READOUT_REFUSAL = 'submittedDesign is not accepted as written'
#: The cause for the semiclassical readout, declared correctly, on a check that does not price the readout.
READOUT_UNPRICED_CAUSE = 'this declaration selects the semiclassical readout, and this check was run without the readout'


def declared_readout(source):
    """Which readout design `source` declares: one of `READOUTS`. Raises `ValueError` for anything else.

    `scripts/policy_verifier.py` needs this answer BEFORE it builds, because the measured design is the one whose
    build takes `--qualify-readout` and an ordinary submission must take the build it takes today, unchanged. So the
    answer has to come from the source and nothing else; recognition is lexical only (nothing is run) and Lean still
    decides validity.

    Three answers, no fourth:

      * `coherent` -- the declaration is omitted, OR it is the explicit default `{ readout := .coherent }`, which
        is identical to omission. Both leave no `submittedDesign` in the residual.
      * `semiclassical` -- the measured declaration, in the one shipped form, consumed by the same walker under the
        same discipline (`default_selectors`).
      * `ValueError` -- anything else: another value, a mis-written default, a bare mention, a second declaration.
        The path is REFUSED rather than guessed, and the cause is `selector_cause`'s, so the pre-build refusal names
        the same line and the same participant action the intake's own refusal would have named.
    """
    text = strip_lean_comments(source)
    residual = without_default_selectors(text)
    if not _SELECTOR_TOKEN['submittedDesign'].search(residual):
        return 'coherent'                             # omitted, or the explicit default: the same build either way
    measured, consumed = default_selectors(text, forms=_MEASURED_FORMS, declarations=_MEASURED_DECL)
    if 'submittedDesign' in consumed and not _SELECTOR_TOKEN['submittedDesign'].search(measured):
        return 'semiclassical'
    number, cause = selector_cause(residual, 'submittedDesign')
    raise ValueError(f'{READOUT_REFUSAL}: line {number}, {cause}')


#: The one non-default full-run declaration this gate admits: a public `def` at column 0 directly inside
#: `namespace PolicySubmission`, typed with the repo's type, whose value (which may continue on the following
#: indented lines) Lean then type-checks and serializes. Any declaration the pinned
#: verifier will serialize is admitted, bound to the source, and ONLY beside the semiclassical readout declaration -- the
#: verifier itself emits a design only on the measured route (`--readout submitted`).
_DESIGN_DECL = re.compile(rf"def[ \t]+submittedFullRunDesign[ \t]*:[ \t]*{_SHOR}FullRunDesign[ \t]*(?::=|where)(?![\w'])")
#: Field and constructor names of the full-run record OUTSIDE the QFT backend axis, and the qualified profiles, each
#: of which selects a multiplier topology, a rotation backend or a phase-product step. The topology gate and the
#: rotation/phase-step refusals are unchanged, so a source naming one is refused BY NAME here, before anything is
#: built, exactly as every non-default declaration was before. `exponent` is refused too: under the semiclassical
#: readout the exponent QFT is replaced by measured feed-forward phases, so there is no exponent QFT to choose
#: (`evaluation/export_v2/readout_qft_overlay.py`).
_REFUSED_AXES = (
    ('multiplier|quotientStream|splitCompare', 'a multiplier topology'),
    ('quotient\\w*|split\\w*', 'a profile'),
    ('dataRotations|workRotations|scratchRotations|exponentRotations', 'a per-family rotation backend'),
    ('step1|step2|step4|step5', 'a phase-product step backend'),
    ('fallback|selectiveKick', 'a rotation or phase-step backend'),
    ('exponent', 'the `exponent` QFT family (the semiclassical readout replaces that QFT by measured phases)'),
)


#: The fields of `Shor.FullRunDesign` a full-run design may set: the three QFT families the reference topology has. Every other
#: field (`multiplier`, the `*Rotations`, `step*`, `exponent`) is outside the axis the verifier serializes.
_DESIGN_FIELDS = ('data', 'work', 'scratch')
_OPEN = {'(': ')', '[': ']', '{': '}', '⟨': '⟩'}
_CLOSE = frozenset(_OPEN.values())


def _design_literal_problem(value, where):
    """Why `value` (a design declaration's value, whitespace-collapsed) is not a plain structure literal that sets only the QFT
    families -- `{ data := .., work := .., scratch := .. }`, or `where` and the same fields -- or None when it is one.

    Reading the fields off the text is only sound when the text IS the literal: a name (`def d := { multiplier := .. }` and then
    `submittedFullRunDesign := d`), a call, or a `{ base with .. }` copy can carry fields this gate cannot see. A family's own
    value cannot (the three are typed `Option QftChoice`), so bracketed text is skipped and only the field names at the
    literal's own level are read. This refuses what the gate cannot read; Lean still decides validity."""
    text = value.strip()
    braced = text.startswith('{')
    if braced == where:
        return 'it is not a structure literal `{ data := .., work := .., scratch := .. }`' if not where else \
            'it is written with `where` but starts a brace literal'
    level, stack, own = (1 if braced else 0), [], []
    for index, char in enumerate(text):
        if char in _OPEN:
            if len(stack) == level:
                own.append(' ')                                # a nested group stands in for one token
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            if not stack or stack.pop() != char:
                return 'its brackets do not match'
            if braced and not stack and index != len(text) - 1:
                return 'it continues after the closing brace of the literal'
        elif len(stack) == level:
            own.append(char)
    if stack:
        return 'its brackets do not match'
    flat = ''.join(own)
    if re.search(r"(?<![\w'.])with(?![\w'.])", flat):
        return 'it copies another record with `with`, whose fields this gate cannot read'
    names = re.findall(r"(?<![\w'.])([A-Za-z_][\w'.]*)[ \t]*:=", flat)
    if (unknown := next((n for n in names if n not in _DESIGN_FIELDS), None)) is not None:
        return f'it sets the field `{unknown}`, which is not one of the QFT families `data`, `work`, `scratch`'
    if not names:
        return 'it sets no field'
    return None


def _measured_readout_declared(text):
    """True when `text` (comment-stripped) declares the semiclassical readout in its one shipped form."""
    measured, consumed = default_selectors(text, forms=_MEASURED_FORMS, declarations=_MEASURED_DECL)
    return 'submittedDesign' in consumed and not _SELECTOR_TOKEN['submittedDesign'].search(measured)


def check_full_run_selector(text):
    """The full-run selector gate, for comment-stripped source. Returns True when the source declares a full-run
    design that is accepted, False when it declares none (omitted, or the explicit default `.reference`).

    A design is accepted only when ALL hold, and is otherwise refused with `FULL_RUN_REFUSAL` and the rule that failed:
      * its value names only the per-family QFT choice on the reference topology (`_REFUSED_AXES` names the rest);
      * the semiclassical readout is declared in its shipped form (the verifier emits a design on no other build);
      * exactly one `def submittedFullRunDesign : Shor.FullRunDesign := ...` at column 0 directly inside
        `namespace PolicySubmission`, with no attribute or modifier line before it, and the name mentioned nowhere
        else (so the declaration Lean serializes is the one this gate read).
    Lean still decides validity (`FullRunDesign.qftOnly`) and the compiled body is converted fail-closed
    (`evaluation/export_v2/full_run_design.py`)."""
    residual = without_default_selectors(text)
    found = FULL_RUN_SELECTOR.search(residual)
    if not found:
        return False
    number, cause = selector_cause(residual, 'submittedFullRunDesign')
    lines = residual.split('\n')
    index = number - 1
    value = _declared_value(lines, index)
    if _SELECTOR_DECLARATION['submittedFullRunDesign'].match(lines[index]) is None or not value \
            or _SELECTOR_CAUSES['submittedFullRunDesign'][4].search(value):
        # A bare mention (remove it) or the default written another way (rewrite it): the cause already says which.
        raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, {cause}')
    for pattern, what in _REFUSED_AXES:
        if (hit := re.search(rf"(?<![\w'])(?:{pattern})(?![\w'])", value)):
            raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, this declaration selects the non-default full-run '
                             f'design `{value}`, which names {what} (`{hit[0]}`); a full-run design chooses only the '
                             'data, work and scratch QFT families (Ross-Selinger or catalytic, exact or approximate) on '
                             'the reference topology')
    if not _measured_readout_declared(text):
        raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, {cause}')
    first, end = _declaration_span(lines, index)
    where = re.search(r":=|(?<![\w'])where(?![\w'])", '\n'.join(lines[first:end]))[0] == 'where'
    if (problem := _design_literal_problem(value, where)) is not None:
        raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, this declaration cannot be read as a design: {problem}; a full-run '
                         'design is written as one structure literal that sets only the QFT families, '
                         '`{ data := some ⟨..⟩, work := some ⟨..⟩, scratch := some ⟨..⟩ }` (or the same fields after `where`)')
    namespace = []
    for line in lines[:index]:
        stripped = line.strip()
        if (m := _NAMESPACE.fullmatch(stripped)):
            namespace.append(m[1])
        elif _SECTION.match(stripped):
            namespace.append('')
        elif _END.match(stripped) and namespace:
            namespace.pop()
    previous = next((l.strip() for l in reversed(lines[:index]) if l.strip()), '')
    first, end = _declaration_span(lines, index)
    rest = residual[:sum(len(l) + 1 for l in lines[:first])] + '\n'.join(lines[end:])
    if (not _DESIGN_DECL.match(lines[index]) or '.'.join(p for p in namespace if p) != 'PolicySubmission'
            or _MODIFIER_LINE.match(previous) or FULL_RUN_SELECTOR.search(rest)):
        raise ValueError(f'{FULL_RUN_REFUSAL}: line {number}, a full-run design is accepted only as one public '
                         '`def submittedFullRunDesign : Shor.FullRunDesign := ...` at column 0 directly inside '
                         '`namespace PolicySubmission`, with no attribute or modifier line before it, declared exactly '
                         'once and mentioned nowhere else, and this declaration is not that form')
    return True


def declared_full_run(source):
    """Whether `source` declares a full-run design (True), declares none (False), or is refused (ValueError).
    The binding check compares this with the compiled export's `submitted_full_run` key."""
    return check_full_run_selector(strip_lean_comments(source))


def validate(source, *, qualify_full_run=False):
    if len(source.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'Policy.lean exceeds {MAX_BYTES} bytes')
    text = strip_lean_comments(source)
    # #[] array literals are data; every other hash command is outside the
    # policy surface. Escaped names must not hide namespace/command tokens.
    # The two are different edits, so they are reported as different causes.
    if (hashed := re.search(r'#(?!\[)', text)):
        raise ValueError(f'Policy.lean line {text.count(chr(10), 0, hashed.start()) + 1}: Policy.lean may not contain '
                         'build-time commands; only `#[...]` array literals may use `#`')
    if (escaped := re.search('[«»]', text)):
        raise ValueError(f'Policy.lean line {text.count(chr(10), 0, escaped.start()) + 1}: the escaped identifier '
                         f'delimiter {escaped[0]} is outside the policy source rules, because an escaped name can '
                         'hide a namespace or command token; use a plain identifier')
    if (interp := re.search(r'[A-Za-z_]!\s*"', text)):
        raise ValueError(f'Policy.lean line {text.count(chr(10), 0, interp.start()) + 1}: interpolated strings '
                         '(`s!"..."`, `f!"..."`, `m!"..."`) are outside the policy source rules, because code inside '
                         'the braces can hide what follows from the source gate')
    found = BANNED.search(text)
    if found:
        line = text.count('\n', 0, found.start()) + 1
        raise ValueError(f'Policy.lean line {line}: {found[0]} is outside the policy source rules')
    design_full_run = False if qualify_full_run else check_full_run_selector(text)
    default_full_run = design_full_run or 'submittedFullRunDesign' in default_selectors(text)[1]
    header = True
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if re.match(r'import\b', line):
            # Same four refusal conditions as before, in the same order, each now naming its own cause: an
            # unapproved module and a misplaced or empty import are participant errors, while the full-run
            # framework is a valid import only beside the design it is needed for.
            modules = line.split()[1:]
            if not header:
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} is an `import` after the header; every import '
                                 "belongs in the file's header, before the first command")
            if not modules:
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} is an `import` with no module name')
            allowed = ALLOWED_IMPORTS | ({FULL_RUN_IMPORT} if qualify_full_run or default_full_run else set())
            if (unapproved := [m for m in modules if m not in allowed]):
                if FULL_RUN_IMPORT in unapproved:
                    raise ValueError(f'{IMPORT_REFUSAL}: line {number} imports {FULL_RUN_IMPORT}, which an ordinary '
                                     f'submission may import only together with the explicit default '
                                     f'`{SHIPPED_FULL_RUN_FORM}`, or together with a reference-topology QFT design '
                                     'declared beside the semiclassical readout')
                raise ValueError(f'{IMPORT_REFUSAL}: line {number} imports {", ".join(unapproved)}, which '
                                 f'{"is" if len(unapproved) == 1 else "are"} not an approved template module')
        elif header:
            if not HEADER_END.match(line):
                raise ValueError(f'Policy.lean has an unsupported import/header; use the shipped template: line '
                                 f'{number} begins with {line.split()[0]!r}, which is not one of the template\'s '
                                 'header commands')
            header = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--stdin', action='store_true')
    parser.add_argument('--qualify-full-run', action='store_true', help='Use only in the local full-run qualification harness, which does not publish')
    parser.add_argument('--artifacts', type=Path, help='Update the trusted preflight report on a source-rule refusal')
    args = parser.parse_args()
    if args.stdin == (args.source is not None):
        parser.error('Provide one policy file or --stdin')
    if args.qualify_full_run and args.artifacts is not None:
        parser.error('--qualify-full-run cannot be used for ordinary preflight artifacts')
    try:
        validate(sys.stdin.read() if args.stdin else args.source.read_text(encoding='utf-8'),
                 qualify_full_run=args.qualify_full_run)
    except ValueError as error:
        if args.artifacts:
            report = args.artifacts / 'table-generation-submission-result.json'
            result = json.loads(report.read_text())
            if result.get('kind') != 'policy' or result.get('phase') != 'preflight':
                raise RuntimeError('Expected a trusted policy preflight result')
            result['status'] = 'failure'
            result.setdefault('checks', []).append({'name': 'policy-source', 'status': 'failure', 'details': str(error)})
            report.write_text(json.dumps(result, indent=2) + '\n')
            summary = args.artifacts / 'table-generation-summary.md'
            with summary.open('a') as output:
                output.write('\n## Policy source refused\n\n' + str(error) + '\n')
        parser.exit(1, f'{error}\n')
    except OSError as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
