import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import extension from '../index.mjs';

const RUNNER = fileURLToPath(new URL('../bin/sbx-run', import.meta.url));

function hook() {
  let handler;
  extension({ on: (name, fn) => { assert.equal(name, 'agent-launch'); handler = fn; } });
  return handler;
}

const launch = { agent: 'pi', purpose: 'issue', command: 'pi', args: ['--session-id', 'id', 'Fix it'], cwd: '/w', sessionId: 'id', env: { HOME: '/h' } };
const spec = ['--agent', 'pi', '--cwd', '/w', '--session', 'id', '--'];

test('any agent runs through sbx-run with the real command after --', () => {
  const result = hook()(launch);
  assert.equal(result.command, RUNNER);
  assert.deepEqual(result.args, [...spec, 'pi', '--session-id', 'id', 'Fix it']);
  assert.equal(result.env, launch.env);
  const codex = hook()({ ...launch, agent: 'codex', command: '/opt/bin/codex', args: [] });
  assert.deepEqual(codex.args, ['--agent', 'codex', '--cwd', '/w', '--session', 'id', '--', '/opt/bin/codex']);
});

test('a wrapper that stays on the Mac keeps running there; the agent it starts is sandboxed', () => {
  const wrapped = { ...launch, agent: 'claude', command: '/x/bin/claude-select-run', args: ['claude', '--resume', 'id'] };
  const result = hook()(wrapped);
  assert.equal(result.command, '/x/bin/claude-select-run');
  assert.deepEqual(result.args, [RUNNER, '--agent', 'claude', '--cwd', '/w', '--session', 'id', '--', 'claude', '--resume', 'id']);
});
