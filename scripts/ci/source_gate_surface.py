"""Invariance digest for the shared source-gate surface, and the audit of `qualify_full_run`.

The gate is `scripts/ci/policy_source.py` plus the three hosted entry points in `evaluation/intake_v2.py` that
re-apply it. Several lanes depend on that surface not moving, so a merge proves it unchanged by digesting the
surface's BEHAVIOUR over a fixed corpus rather than by inspecting a diff.

THIS HARNESS IS NEW AND ITS DIGEST IS A NEW BASELINE. It continues nothing.

`c1cbc44026ca298b7d7fc6df0c515a1c1c22b1f93019f73d5fd7be8a8191d7fe`, the "546 sources x 8 shared entry points"
value quoted in lane XN's commit message and re-quoted by lane Y4, is UNREPRODUCIBLE and no number here should be
reconciled against it. It was computed by a script that was never committed: `validate_qfr` and `c1cbc440` appear
in no committed ref in this repository, under that or any other name, and lane Z-wire reached the same conclusion
independently. Anything citing that digest as a verified invariant is citing a number with no artifact behind it.
`SURFACE_DIGEST` below is this file's own baseline, computed from the corpus this file generates, and it is
deliberately NOT presented as a successor to `c1cbc440...`; there is nothing to be a successor to.

The described defect in XN's script is still worth designing against, and is the reason `duplicate_columns` exists.
As described, its `validate_qfr` row read `verdict(ps.validate, text, )` against a `verdict(fn, *args)` helper -- a
trailing comma and no keyword argument -- so the row was a byte-copy of the plain `validate` row. That mechanism is
real and checkable against the gate as it stands: `validate`'s `qualify_full_run` is KEYWORD-ONLY, so no positional
helper could ever have reached it (`validate(source, True)` raises `TypeError`). Whether XN's script was written
that way cannot be confirmed, because the script is gone. What CAN be confirmed is that a row built that way is
dead, and that a digest cannot detect its own dead rows -- so this harness checks for them directly.

ENTRY POINTS: 9 rows, 8 DISTINCT BEHAVIOURS. `check_legacy_full_run_source` is a one-line delegation to
`check_no_full_run_selector` in `evaluation/intake_v2.py`, so the two rows agree on every source by construction.
Both are kept, because they are separately importable entry points that could drift apart, but the honest coverage
number is EIGHT distinct behaviours over NINE rows. `duplicate_columns` reports the pair; see it for why a pair is
not automatically a bug but always makes a raw row count misleading.

CORPUS SIZE IS NOT A TARGET. An earlier draft of this file picked 7 headers and 39 bodies so that
2 x 7 x 39 = 546 would match the size XN reported. That is the same error in a different place -- a number reached
by construction -- and it cost real coverage: the byte ceiling, the bare `#` command, the escaped-identifier
delimiter and the empty `import` were all absent, because adding them would have broken the product. The axes are
now set by the gate's branch list and the size is read off the result.

`refuse_unqualified_*` and `check_legacy_full_run_source` live in `evaluation/intake_v2.py`, so `SURFACE_DIGEST`
moves if that file's behaviour moves -- which is the point of an invariance digest, not a defect in it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE), str(HERE.parents[1] / 'evaluation')]

import policy_source as ps

POLICY_IMPORT = 'ForShor.ShorVerification.Framework.Policy'
BOARDS = ('PolicySubmission', 'PTPPolicySubmission')

#: The 9 rows. `validate` appears twice because its two `qualify_full_run` settings are two branches of the gate,
#: and the second one decided verdicts for a release before anything observed it. See the module docstring: these
#: 9 rows carry 8 distinct behaviours, and `duplicate_columns` is what keeps that statement honest.
ENTRY_POINTS = ('validate', 'validate_qfr', 'default_selectors', 'without_default_selectors',
                'check_no_full_run_selector', 'selector_cause', 'refuse_unqualified_design',
                'refuse_unqualified_primitive', 'check_legacy_full_run_source')

#: The rows that agree on every source by construction, so a row count is not a behaviour count.
KNOWN_DUPLICATE_COLUMNS = [('check_no_full_run_selector', 'check_legacy_full_run_source')]

#: This file's own baseline over its own corpus. Not comparable to any previously reported value.
SURFACE_DIGEST = '8ffe41913e67ababf9967699da4573ae5f19cff4529540207f1b1abd52130137'

HEADERS = {
    'policy': f'import {POLICY_IMPORT}\n',
    'policy+fullrun': f'import {POLICY_IMPORT}\nimport {ps.FULL_RUN_IMPORT}\n',
    'fullrun-only': f'import {ps.FULL_RUN_IMPORT}\n',
    'unapproved': f'import {POLICY_IMPORT}\nimport ForShor.OtherModule\n',
    'two-on-a-line': f'import {POLICY_IMPORT} {ps.FULL_RUN_IMPORT}\n',
    'no-import': '',
    'misplaced': f'import {POLICY_IMPORT}\nnamespace Pre\nend Pre\nimport {ps.FULL_RUN_IMPORT}\n',
    'empty-import': 'import \n',
    #: First non-blank line is not one of `HEADER_END`'s template commands, so `validate` refuses before the
    #: import loop. `structure`, not `def`: `def` IS a header command, and `deriving`/`instance`/`class` are
    #: caught earlier by `BANNED`, so `structure` is one of the few tokens that actually reaches this branch.
    #: Without this header the "unsupported import/header" refusal is unreachable from the corpus.
    'no-header': 'structure Preamble where\n  x : Nat\n',
}

#: Sources that do not factor over the three axes, so they are named rather than crossed.
EDGE_SOURCES = {
    'oversized': 'import ' + POLICY_IMPORT + '\n-- ' + 'x' * (ps.MAX_BYTES + 1) + '\n',
}


def _selector_forms(selector, type_name, default, non_default, alternate):
    """The 12 shapes the gate must tell apart for one selector: the shipped form, the spellings Lean also reads,
    and the near-misses the default recogniser must NOT consume (count, continuation, modifier, attribute,
    indentation, missing type, bare mention).

    `alternate` is the selector's OTHER recognised spelling of the same default, and has to be given rather than
    derived: `submittedDesign`'s default is a record literal, so deriving `Shor.<type>.<ctor>` from it produces the
    shipped form again and the corpus quietly loses 14 rows to duplicates -- the same defect, one layer down."""
    return [
        ('default-same-line', f'def {selector} : Shor.{type_name} := {default}'),
        ('default-root', f'def {selector} : _root_.Shor.{type_name} := {default}'),
        ('default-alternate', alternate),
        ('default-twice', f'def {selector} : Shor.{type_name} := {default}\ndef {selector} : Shor.{type_name} := {default}'),
        ('default-continued', f'def {selector} : Shor.{type_name} := {default}\n  |> id'),
        ('default-private', f'private def {selector} : Shor.{type_name} := {default}'),
        ('default-attr', f'@[simp]\ndef {selector} : Shor.{type_name} := {default}'),
        ('default-indented', f'  def {selector} : Shor.{type_name} := {default}'),
        ('non-default', f'def {selector} : Shor.{type_name} := {non_default}'),
        ('untyped', f'def {selector} := {default}'),
        ('bare-mention', f'def other := {selector}'),
        ('default+mention', f'def {selector} : Shor.{type_name} := {default}\ndef other := {selector}'),
    ]


BODIES = (
    [(f'design/{n}', b) for n, b in _selector_forms(
        'submittedDesign', 'SubmissionDesign', '{ readout := .coherent }', '{ readout := .semiclassical }',
        'def submittedDesign : Shor.SubmissionDesign where\n  readout := .coherent')]
    + [(f'primitive/{n}', b) for n, b in _selector_forms(
        'submittedPrimitive', 'PrimitiveSynthesis', '.rossSelinger', '.catalyticB2',
        'def submittedPrimitive : Shor.PrimitiveSynthesis := Shor.PrimitiveSynthesis.rossSelinger')]
    + [(f'fullrun/{n}', b) for n, b in _selector_forms(
        'submittedFullRunDesign', 'FullRunDesign', '.reference', '.quotientExact',
        'def submittedFullRunDesign : Shor.FullRunDesign := Shor.FullRunDesign.reference')]
    + [('plain/data', 'def points := #[1, 2, 3]'),
       ('plain/empty', ''),
       ('plain/banned', 'def p := by native_decide'),
       ('plain/hash', '#eval 1'),
       ('plain/escaped', 'def «x» := 1')]
)


def sources():
    """The corpus: (name, source), deterministic order, the digest depends on it.

    The size is whatever the three axes and the edge list produce -- it is NOT a target. An earlier draft of this
    file chose its header and body counts so the product would be 546, the size lane XN reported, and that cost
    four real branches of `validate`: the byte ceiling, the bare `#` command, the escaped-identifier delimiter and
    the empty `import`. The axes are now set by the gate's branch list and the count is read off the result."""
    for board in BOARDS:
        for header_name, header in HEADERS.items():
            for body_name, body in BODIES:
                yield (f'{board}|{header_name}|{body_name}',
                       f'{header}namespace {board}\n{body}\nend {board}\n')
    for name, source in EDGE_SOURCES.items():
        yield f'edge|{name}', source


def verdict(call, *args, **kwargs):
    """What one entry point did: `('ok', repr)` or `(exception type, message)`.

    `**kwargs` is the fix. The scratch predecessor was `verdict(call, *args)`, so the one row that needed a keyword
    argument -- `validate(..., qualify_full_run=True)`, whose parameter is keyword-ONLY -- silently became a second
    copy of the row above it. A helper that cannot forward a keyword must not be fed one by a trailing comma."""
    try:
        return ('ok', repr(call(*args, **kwargs)))
    except Exception as error:                      # the gate's refusals are the observable, so record them all
        return (type(error).__name__, str(error))


def _selector_causes(text):
    """`selector_cause` for every selector still present in the residual. Participant-facing refusal text: it is the
    only part of the surface that tells a person what to DO, and the scratch harness never once called it."""
    residual = ps.without_default_selectors(text)
    out = {}
    for selector in ('submittedFullRunDesign', 'submittedPrimitive', 'submittedDesign'):
        if ps._SELECTOR_TOKEN[selector].search(residual):
            out[selector] = verdict(ps.selector_cause, residual, selector)
        else:
            out[selector] = ('absent', '')
    return out


def observe(source):
    """Every entry point's verdict for one source, keyed by `ENTRY_POINTS`."""
    import intake_v2
    text = ps.strip_lean_comments(source)
    return {
        'validate': verdict(ps.validate, source),
        'validate_qfr': verdict(ps.validate, source, qualify_full_run=True),
        'default_selectors': verdict(ps.default_selectors, text),
        'without_default_selectors': verdict(ps.without_default_selectors, text),
        'check_no_full_run_selector': verdict(ps.check_no_full_run_selector, text),
        'selector_cause': ('ok', json.dumps(_selector_causes(text), sort_keys=True)),
        'refuse_unqualified_design': verdict(intake_v2.refuse_unqualified_design, source),
        'refuse_unqualified_primitive': verdict(intake_v2.refuse_unqualified_primitive, source),
        'check_legacy_full_run_source': verdict(intake_v2.check_legacy_full_run_source, source),
    }


def observations():
    """(name -> entry point -> verdict) over the whole corpus."""
    return {name: observe(source) for name, source in sources()}


def digest(entry_points=ENTRY_POINTS, observed=None):
    """The invariance digest over `entry_points`, defaulting to all 9 rows."""
    observed = observations() if observed is None else observed
    reduced = {name: {point: rows[point] for point in entry_points} for name, rows in observed.items()}
    return hashlib.sha256(json.dumps(reduced, sort_keys=True).encode()).hexdigest()


def duplicate_columns(observed=None):
    """Pairs of entry points whose verdicts are identical on EVERY source -- i.e. rows that add no information.

    This is the self-check for the defect that started the lane. A row wired up wrongly (a keyword argument eaten
    by a trailing comma, a copy-pasted call that was never re-pointed) does not fail loudly; it quietly becomes a
    duplicate of its neighbour and inflates the claimed coverage. A digest cannot notice that about itself, so the
    harness is required to notice it instead: any pair here means the surface covers fewer entry points than it
    advertises, and the count in the report is wrong.

    A pair being listed is not automatically a bug -- two entry points can agree because one delegates to the
    other -- but it always means the claimed number must be stated as distinct BEHAVIOURS, not as rows."""
    observed = observations() if observed is None else observed
    columns = {point: tuple(rows[point] for rows in observed.values()) for point in ENTRY_POINTS}
    seen, duplicates = {}, []
    for point, column in columns.items():
        key = hashlib.sha256(repr(column).encode()).hexdigest()
        if key in seen:
            duplicates.append((seen[key], point))
        else:
            seen[key] = point
    return duplicates


def audit_qualify_full_run(observed=None):
    """Which sources `qualify_full_run=True` decides differently, and how.

    The non-negotiable for the lane that widened this observation is that no refusal is lost and none is added, so
    the audit is stated as the two directions that would violate it:

      `accepts_more`  -- refused by the plain path, accepted under qualification. EXPECTED and the point of the
                         branch: qualifying a full run is a different question, and the dedicated harness never
                         publishes.
      `refuses_more`  -- accepted by the plain path, refused under qualification. Must be EMPTY. `validate` only
                         ever relaxes on this flag (it skips `check_no_full_run_selector` and widens
                         `ALLOWED_IMPORTS`), so a non-empty list means the branch is not monotone and is a defect.
      `cause_changed` -- refused by both, with a different message. Not a verdict change, but it is the
                         participant-facing text, so it is reported rather than hidden.
    """
    observed = observations() if observed is None else observed
    report = {'accepts_more': [], 'refuses_more': [], 'cause_changed': [], 'identical': []}
    for name, rows in observed.items():
        plain, qualified = rows['validate'], rows['validate_qfr']
        if plain == qualified:
            report['identical'].append(name)
        elif plain[0] != 'ok' and qualified[0] == 'ok':
            report['accepts_more'].append({'source': name, 'plain_refusal': plain[1]})
        elif plain[0] == 'ok' and qualified[0] != 'ok':
            report['refuses_more'].append({'source': name, 'qualified_refusal': qualified[1]})
        else:
            report['cause_changed'].append({'source': name, 'plain': plain[1], 'qualified': qualified[1]})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--audit', action='store_true', help='Report what `qualify_full_run=True` decides differently')
    args = parser.parse_args()
    observed = observations()
    if args.audit:
        report = audit_qualify_full_run(observed)
        print(json.dumps({k: (len(v) if k == 'identical' else v) for k, v in report.items()}, indent=2))
    duplicates = duplicate_columns(observed)
    print(f'sources              {len(observed)}')
    print(f'rows                 {len(ENTRY_POINTS)}')
    print(f'distinct behaviours  {len(ENTRY_POINTS) - len(duplicates)}')
    print(f'duplicate columns    {duplicates or "none"}')
    print(f'surface digest       {digest(ENTRY_POINTS, observed)}')


if __name__ == '__main__':
    main()
