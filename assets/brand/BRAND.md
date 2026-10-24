# CAH brand

- **Name:** Chat Agent Harness (CAH)
- **Slogan / DD:** **Disposable conversations. Durable memory.**
- **Tagline:** Turn chat sessions into schedulable agent workers.
- **Positioning:** API-optional agent runtime for AI systems that may expose little more than a normal chat interface.

## DD: Disposable conversations. Durable memory.

DD states the core CAH design principle.

Chat conversations are temporary reasoning contexts. A Worker conversation may be replaced, a Planner may hand over to a successor, and browser/runtime implementations may change without becoming the long-term memory of the work.

Durable memory lives outside those replaceable conversations. In the current CAH architecture, Git is the long-term record for engineering memory and authoritative continuity: decisions, task state, checkpoints and handoffs, evidence, results, and other information that must survive conversation replacement or context loss.

The Harness exists to make that separation practical: conversations can be disposable because the work's durable memory is not.

Git is the current durable coordination/evidence substrate, not a requirement that every AI endpoint directly access Git. Product branding does not change task, repository or protocol identities.

## Current assets

- `cah-mark.png`: current text-free README mark, selected by the user.
- `icons/cah-icon-*.png`: current browser icons; existing artwork, renamed without altering its pixels.
- `icons/cah-start-launcher.png`: Windows launcher artwork.
- `icons/cah-start-launcher-hq.ico`: locally generated Windows multi-size icon, not committed.

The old banner directory has been retired. Superseded artwork remains available in Git history; do not restore it or generate a new logo merely to change a filename.

Public source: `Tests4gptplay/chat-agent-harness`. Internal `git-agent-harness` and `GAH_*` identifiers remain protocol-compatible.

CAH is an independent project; use of third-party services and tools does not imply affiliation or endorsement.
