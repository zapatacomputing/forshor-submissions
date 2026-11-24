'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { citation } = require('./ci/submission_citation.js');

const DIGEST = 'f11abb65fbc7d0af6eab13ea66f58bde75db0db5757136b3d936f1c7d6af1144';
const HEAD = '6a8dcbbdbfcde9510b7d2404d90b734885d4314e';
const SNAPSHOT = '4c05495cf00425acbc1d48137ebf13ba9cca92a4a9b9d7b6db8ab88a133bf286';
const input = (over = {}) => ({
  state: { pr: { number: 68, headSha: HEAD } },
  metadata: { kind: 'policy', policyDigest: DIGEST, epoch: 'ab1f8887b1b50e56', githubLogin: 'courtneyjzapata', submittedAt: '2026-11-12T13:55:00Z' },
  result: { snapshotId: SNAPSHOT, updatedAt: '2026-11-12T14:30:42.248Z', name: 'courtneyjzapata_sub1', policy: { mode: 'PhaseProduct' } },
  ...over,
});

test('an accepted policy gets its digest, commit, snapshot, a plain citation and a BibTeX entry', () => {
  const text = citation(input());
  assert.match(text, /^### Cite this submission\n/);
  assert.ok(text.includes(`Policy digest (sha256): \`${DIGEST}\``));
  assert.ok(text.includes(`Submission commit: \`${HEAD}\``));
  assert.ok(text.includes(`Published in snapshot: \`${SNAPSHOT}\``));
  // The publisher's own test pins this same literal, and its Details dialog renders it: the two must agree, so change them together.
  assert.ok(text.includes(`courtneyjzapata (2026). ForShor challenge: PhaseProduct policy courtneyjzapata_sub1, pull request #68. policy digest sha256:${DIGEST}; submission commit ${HEAD}. https://github.com/zapatacomputing/forshor-submissions/pull/68\n`));
  assert.ok(!/; epoch |; snapshot /.test(text), 'what changes with a publication is not in the citation');
  assert.match(text, /```bibtex\n@misc\{forshor_f11abb65fbc7,\n  author = \{courtneyjzapata\},\n  title = \{ForShor challenge: PhaseProduct policy courtneyjzapata\\_sub1\},\n  year = \{2026\},/);
  assert.match(text, /DOI to be assigned at deposit after the challenge closes\.\}\n\}\n```$/);
  assert.match(text, /a DOI will be assigned when accepted submissions are deposited on Zenodo after the challenge closes/);
});

test('the year is the year the pull request was made, not the year of the publication', () => {
  const later = citation(input({ result: { ...input().result, updatedAt: '2027-01-01T00:10:00Z' } }));
  assert.match(later, /courtneyjzapata \(2026\)\. ForShor challenge/);
  assert.match(later, /year = \{2026\},/);
  const without = citation(input({ metadata: { ...input().metadata, submittedAt: undefined } }));
  assert.match(without, /courtneyjzapata \(2026\)\./, 'falls back to the publication year');
});

test('the citation is the same text every time, and it mentions nobody', () => {
  assert.equal(citation(input()), citation(input()));
  assert.ok(!/@(?!misc\{)[A-Za-z]/.test(citation(input())), 'only the BibTeX entry type carries an @');
});

test('without a snapshot or a row name the section still cites the digest and the commit', () => {
  const text = citation(input({ result: { policy: { mode: 'PhaseTripleProduct' } } }));
  assert.ok(text.includes(`Policy digest (sha256): \`${DIGEST}\``) && text.includes(`Submission commit: \`${HEAD}\``));
  assert.doesNotMatch(text, /Published in snapshot|; snapshot /, 'no snapshot, none named');
  assert.match(text, /ForShor challenge: PhaseTripleProduct policy, pull request #68/);
});

test('nothing a contributor could shape gets through unless it matches its pattern', () => {
  for (const [what, over] of [
    ['a login with a newline and a mention', { metadata: { ...input().metadata, githubLogin: 'x\n@victim' } }],
    ['a bot login', { metadata: { ...input().metadata, githubLogin: 'dependabot[bot]' } }],
    ['a short digest', { metadata: { ...input().metadata, policyDigest: DIGEST.slice(1) } }],
    ['an upper-case digest', { metadata: { ...input().metadata, policyDigest: DIGEST.toUpperCase() } }],
    ['a bad commit', { state: { pr: { number: 68, headSha: 'abc' } } }],
    ['no pull number', { state: { pr: { number: 0, headSha: HEAD } } }],
    ['a table, not a policy', { metadata: { ...input().metadata, kind: 'table' } }],
  ]) assert.equal(citation(input(over)), '', what);
  const text = citation(input({ result: { snapshotId: 'not-a-hash', name: 'evil_sub1\n[x](http://bad)', updatedAt: 'x', policy: { mode: '<b>x</b>' } } }));
  assert.ok(!text.includes('http://bad') && !text.includes('<b>') && !text.includes('not-a-hash'), 'a malformed name, mode or snapshot is left out, not escaped in');
  assert.match(text, /ForShor challenge: policy, pull request #68/);
});
