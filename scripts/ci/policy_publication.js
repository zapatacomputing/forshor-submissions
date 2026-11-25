'use strict';
// Trusted main only: choose attribution from GitHub and bind evidence to the exact Lean file.
const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const REPOSITORY = 'zapatacomputing/forshor-submissions';
const SOURCE = 'TableGeneration/Submission/Policy.lean';
// The optional LaTeX summary: text for people, never compiled or run. It is bound to the exact file at the head commit exactly as the policy is.
const SUMMARY = 'TableGeneration/Submission/summary.tex';
const INDEX = 'leaderboard/policy-submissions.json';
const digest = text => createHash('sha256').update(text).digest('hex');

const strictUtf8 = new TextDecoder('utf-8', { fatal: true });

const MEASURED_SCHEMA = 'forshor.policy-submission-semiclassical/v1';

// A compiled per-family QFT design rides only on the semiclassical (measured) document; intake's check_full_run_binding
// is the real gate for what it may contain. Anywhere else the key means a coherent export carrying a design.
function requireOrdinaryExport(verification) {
  const printed = verification.printed ?? {};
  if (Object.hasOwn(printed, 'submitted_full_run')
      && !(printed.schema === MEASURED_SCHEMA && printed.submitted_readout === 'semiclassical'))
    throw new Error('Compiled submitted_full_run is accepted only on the semiclassical readout document');
}

async function content(github, context, path, ref, { strict = false } = {}) {
  const { data } = await github.rest.repos.getContent({ ...context.repo, path, ref });
  if (Array.isArray(data) || (data.type && data.type !== 'file') || data.encoding !== 'base64' || typeof data.content !== 'string')
    throw new Error('Expected a versioned source file');
  const bytes = Buffer.from(data.content, 'base64');
  return strict ? strictUtf8.decode(bytes) : bytes.toString('utf8');
}

/** The summary at a ref, or null when the ref has none (HTTP 404, the only answer that means "no summary": any other failure propagates). */
async function summaryAt(github, context, path, ref) {
  try {
    return await content(github, context, path, ref, { strict: true });
  } catch (error) {
    if (error?.status === 404) return null;
    throw error;
  }
}

async function accepted({ github, context, identity, readJson }) {
  const index = await readJson(github, context, INDEX);
  if (!index) return null;
  if (index.schema !== 'forshor.policy-archive/v1' || !Array.isArray(index.policies)) throw new Error('Invalid policy archive');
  const entry = index.policies.find(p => p.policy_id === identity.headSha);
  if (!entry) return null;
  const base = `results/policy-submissions/${identity.headSha}`;
  const verification = await readJson(github, context, `${base}/verification.json`);
  const receipt = verification?.ciVerification;
  if (!receipt || Object.entries(identity).some(([key, value]) => receipt[key] !== value)) return null;
  if (entry.path !== base || entry.source?.repository !== REPOSITORY
      || String(entry.source.run_id) !== receipt.runId || Number(entry.source.pr_number) !== receipt.pullRequest
      || !/^[1-9][0-9]*$/.test(receipt.runId) || !Number.isSafeInteger(receipt.runAttempt) || receipt.runAttempt < 1)
    throw new Error('Invalid archived policy verification identity');
  requireOrdinaryExport(verification);
  return { receipt, verification, base };
}

async function prepare({ github, context, core, run, pr, identity, result, recovered }) {
  const { data: details } = await github.rest.pulls.get({ ...context.repo, pull_number: pr.number });
  const login = details.user.login;
  const files = await github.paginate(github.rest.pulls.listFiles, { ...context.repo, pull_number: pr.number });
  // Exactly the policy, or the policy and its optional summary.tex: nothing else, no rename, no copy, and a summary alone is not a submission.
  const names = files.map(f => f.filename);
  if (files.length < 1 || files.length > 2 || new Set(names).size !== files.length || !names.includes(SOURCE)
      || names.some(name => name !== SOURCE && name !== SUMMARY) || files.some(f => f.previous_filename))
    throw new Error('A policy submission may change only Policy.lean and the optional summary.tex');
  if (files.some(f => f.filename === SUMMARY && f.status === 'removed'))
    throw new Error('summary.tex may be added or edited, not deleted');
  let verification;
  if (recovered) {
    verification = { ...recovered.verification,
      leanSource: await content(github, context, `${recovered.base}/Policy.lean`, 'submission-results') };
    if (recovered.verification.summarySha256 !== undefined)      // archived without its text (the text is stored once, as summary.tex)
      verification.summarySource = await content(github, context, `${recovered.base}/summary.tex`, 'submission-results', { strict: true });
  } else {
    if (result.kind !== 'policy' || result.challenge !== 'forshor-policy') throw new Error('Not a policy verification result');
    verification = JSON.parse(fs.readFileSync('artifacts/verification.json', 'utf8'));
    if (result.policy?.digest !== verification.digest) throw new Error('Policy verification digest changed');
  }
  const source = await content(github, context, SOURCE, pr.headSha);
  if (verification.schema !== 'forshor.policy-verification/v1' || verification.login !== login
      || verification.leanSource !== source || verification.leanSha256 !== digest(source))
    throw new Error('Policy evidence does not match the GitHub author and exact submitted source');
  // Apply even to archived/reused evidence. Read GitHub's exact source as data;
  // never run the policy or let an artifact select the checker executable.
  execFileSync('python3', [path.join(__dirname, 'policy_source.py'), '--stdin'], {
    input: source, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'],
  });
  requireOrdinaryExport(verification);
  // The summary is data: it must be the very file at the head commit, in both directions (none on either side means none on both), and it never changes the policy.
  const summaryHead = await summaryAt(github, context, SUMMARY, pr.headSha);
  const carriesSummary = ['summarySource', 'summarySha256', 'summaryBytes'].filter(key => verification[key] !== undefined);
  if (summaryHead === null ? carriesSummary.length !== 0
      : carriesSummary.length !== 3 || verification.summarySource !== summaryHead || verification.summarySha256 !== digest(summaryHead)
        || verification.summaryBytes !== Buffer.byteLength(summaryHead, 'utf8'))
    throw new Error('Summary evidence does not match the exact summary.tex submitted at the head commit');
  const receipt = recovered?.receipt ?? { ...identity, runId: String(run.id), runAttempt: run.run_attempt, pullRequest: pr.number };
  if (receipt.pullRequest !== pr.number) throw new Error('Archived verification belongs to another PR');
  verification.ciVerification = receipt;
  fs.mkdirSync('artifacts', { recursive: true });
  fs.writeFileSync('artifacts/verification.json', JSON.stringify(verification));
  const evaluationKey = digest(`${pr.headSha}:${receipt.runId}:${pr.number}`);
  fs.writeFileSync('evaluation-metadata.json', JSON.stringify({
    ...(details.user.id === 358585423 && ['baseline-gpt', 'baseline-claude', 'baseline-gpt-6.2-sol', 'baseline-claude-opus-5-5', 'baseline-gpt-6.2-sol-medium', 'baseline-claude-opus-5-5-medium'].includes(details.title)
      && summaryHead === `Organizer baseline: ${details.title}\n`
      ? { organizerBaseline: details.title } : {}),
    kind: 'policy', repository: REPOSITORY, headSha: pr.headSha, runId: receipt.runId,
    githubLogin: login, pullRequestUrl: `https://github.com/${REPOSITORY}/pull/${pr.number}`,
    pullRequest: pr.number, submittedAt: details.created_at, evaluationKey, policyDigest: verification.digest,
  }));
  core.setOutput('kind', 'policy');
  await core.summary.addHeading(recovered ? 'Reusing archived policy verification' : 'Policy verified; ready to publish')
    .addRaw('The trusted publishing step revalidates this evidence, archives the exact Lean file, and updates the v2 boards.\n').write();
}

module.exports = { accepted, prepare };
