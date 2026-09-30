// sbx: every coding agent Divernote starts runs inside a Docker Sandboxes VM,
// one per session, that sees only the issue folder. See README.md.

import { spawn } from 'node:child_process';
import { basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNNER = fileURLToPath(new URL('./bin/sbx-run', import.meta.url));

// Wrappers that stay on the Mac and start the agent themselves, like
// divernote-claude-select's runner: the sandbox goes around the agent they start.
const HOST_WRAPPERS = new Set(['claude-select-run']);

export function sandboxed(launch) {
  const spec = ['--agent', String(launch.agent ?? ''), '--cwd', String(launch.cwd ?? ''), '--session', String(launch.sessionId ?? ''), '--'];
  if (HOST_WRAPPERS.has(basename(launch.command)) && launch.args.length > 0) {
    return { ...launch, args: [RUNNER, ...spec, ...launch.args] };
  }
  return { ...launch, command: RUNNER, args: [...spec, launch.command, ...launch.args] };
}

// Tailnet URLs of launches that died with the worker are removed when it starts again.
function cleanUp() {
  try {
    spawn(RUNNER, ['--clean-up'], { detached: true, stdio: 'ignore' }).on('error', () => {}).unref();
  } catch {}
}

export default function sbx(divernote) {
  cleanUp();
  divernote.on('agent-launch', launch => sandboxed(launch));
}
