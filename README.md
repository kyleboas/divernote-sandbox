# divernote-sandbox

A Divernote extension, installed as `sbx`. Every coding agent Divernote starts, for an issue or
in a terminal you open on one, runs inside a
[Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) VM instead of
directly on the Mac.

## Install

Clone it into Divernote's extensions folder and restart the worker:

```sh
git clone git@github.com:kyleboas/divernote-sandbox.git \
  ~/.config/divernotes/extensions/sbx
launchctl kickstart -k gui/$(id -u)/com.divernote.pi-work
```

To turn it off, delete the folder and restart the worker.

## Requirements

- Docker Sandboxes `sbx`, signed in (`sbx login`), at `~/.local/bin/sbx` or
  on the usual paths.
- `gh` logged in to GitHub on the Mac.
- For Claude: Claude Code logged in on the Mac (claude-select accounts work).
- For Pi on a ChatGPT subscription: `sbx secret set openai` done once.

## What the sandbox sees

One sandbox per Divernote session, named `dn-<session>`:

- the issue folder, writable;
- the repository's Git data, writable, except `config`, `hooks` and the
  worktree links, which are read-only so nothing the agent does can make
  Git run code on the Mac;
- the agent's own conversation folder, so Divernote can read replies
  (Claude: `~/.claude/projects/<folder>`, Pi: `~/.pi/agent/sessions/<folder>`);
- files the launch names, such as Pi extensions, read-only.

Nothing else on the Mac. The network allows only the hosts in
`kit/spec.yaml`; add one there when an agent needs it.

Credentials stay on the Mac. The sandbox gets placeholders and the sbx proxy
swaps in the real value on the way out: Claude tokens come from the Keychain
(`bin/claude-token`), GitHub from `gh` (`bin/github-auth`), ChatGPT from
sbx's own OpenAI login.

## Lifecycle

- A new session's sandbox is ready in about 15 seconds; later turns reuse it.
  It starts from a saved image with the agents installed (`sbx template ls`
  shows `divernote-agents`). The image is built once, and again whenever
  `kit/` changes, which takes a few minutes. To update Claude Code or Pi,
  change their versions in `kit/spec.yaml`.
- It stops when no agent is using it and is removed once Divernote removes
  the issue folder.
- If what a launch needs to see changes, the sandbox is recreated. The work
  and the conversation live in the mounted folders, so nothing is lost.

## Other agents

`index.mjs` wraps every launch, whatever the agent. Claude and Pi also get
their conversation folder and login. For another agent, add its
folder and login in `bin/sbx-run` and install it in `kit/spec.yaml`.

## Tests

```sh
node --test test/index.test.mjs
```
