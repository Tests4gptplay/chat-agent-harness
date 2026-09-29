# Downloads and distribution scope

[Case](README.md) · [Viewer client](viewer_client.py) · [Operation notes](AGENT_VIEWER.md) · [Evidence](evidence.json)

## Included here

- [viewer_client.py](viewer_client.py) — the exact public Python client for the Viewer control surface.
- [AGENT_VIEWER.md](AGENT_VIEWER.md) — corrected public operation notes.
- [evidence.json](evidence.json) — sanitized terminal evidence for the 2026-09-28 rerun.
- [Rerun record](records/2026-09-28-rerun-record.md) — curated timeline derived from the private working draft.

The public Git history also preserves the original native Viewer screenshots from the earlier public case. The four newer Phase-1 rerun captures are represented in evidence.json by exact byte sizes and SHA-256 hashes.

## Not included

The full UE-linked Viewer module is **not** published in this repository.

Unreal Engine is not bundled or mirrored.

The rerun's real game/MOD package inputs, private host configuration, raw runtime logs, private task state and account/browser bindings are not public artifacts.

The included Python client is not a replacement for the Viewer itself.

## License boundary

The CAH repository is MIT-licensed. That does not automatically grant a separate redistribution right for Unreal Engine code, engine-linked modules, third-party game content or other external dependencies.

Use Unreal Engine and any target content under their own applicable terms.

## Tested scope

The accepted Phase-1 rerun used a compatible classic PAK camera fixture with an installed UE 5.6.1 environment.

Phase 2 investigated real IoStore/package-store behavior, but Phase 2B was stopped before direct-preview acceptance. The public case therefore does not claim arbitrary game or IoStore compatibility.
