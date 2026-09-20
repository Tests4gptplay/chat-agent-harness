# Storage and retention

Keep source, evidence, cache and scratch separate. Canonical Git records tasks and compact results; large workload intermediates belong in explicitly configured workspaces.

Source and accepted deliverables are not disposable cache. Preserve active work, its checkpoint and the evidence needed to understand failures. Do not use this policy as authority to delete arbitrary user files. Conversation cleanup is governed by the exact registered lane, not by a broad browser/account cleanup.

Machine paths and credentials belong in local configuration, not public evidence. Version-control only the artifacts the task requires. Keep temporary reproduction output out of Git. The public distribution's `state/`, `requests/`, `results/` and `cl/` directories are clean templates, not a copy of any live installation.
