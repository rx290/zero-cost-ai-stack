# Delegation rules

These are the rules I give Claude Code (in my global `CLAUDE.md`) for handing work to free models. Copy them and adjust.

## Gate 1: route

Before any bulk text or code generation, decide: delegate or keep.

**Delegate** to `fllm` or relay:
- summaries of files, logs and transcripts
- first drafts of docs, READMEs and posts
- translations and data reformatting
- boilerplate, small scripts and test scaffolds
- repetitive edits across many files

**Keep for Claude:**
- architecture and design decisions
- hard debugging
- CAD and geometry maths
- security-sensitive code
- final review of anything delegated
- anything that touches live accounts or gets posted publicly

Pick the tier by the job:

| Tier | Use it for |
|---|---|
| `fast` | short rewrites, classification, commit and PR text |
| `code` | boilerplate, tests, small scripts, repetitive edits |
| `smart` | prose drafts and summaries that need judgment |
| `long` | big inputs: logs, transcripts, many files at once |

## Gate 2: data

Never send secrets, credentials, `.env` files, or employer or customer data to a free provider. fllm refuses obvious keys, but the judgment stays with the person (or agent) sending.

## Gate 3: verify

Delegated output goes to a file (`fllm -o out.md`) and gets checked before use:
- code must pass its tests or a real run
- prose gets read for facts and tone

Unreviewed output never goes straight into a repo.

## Gate 4: report

Say in one line what was delegated and which model served it. fllm prints the model on stderr after every call.

## When things fail

- A whole tier fails: run `fllm-stats`, try another tier, then do it in Claude.
- The router is down: restart it (`docker compose up -d` in the FreeLLMAPI folder).
- A provider is rate limited: respect the cooldown. Don't retry in a loop and don't create extra accounts to get around limits.
- Paid usage is a last resort, never the default.
