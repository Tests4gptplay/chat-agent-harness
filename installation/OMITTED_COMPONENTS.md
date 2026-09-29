# Intentional packaging omissions

This source distribution contains no supplied GitHub workflows, toolbox jobs, personal Skill procedures/import snapshots, private cases, screenshots, binary showcases, active tasks, conversation pools, result ledgers or private Git history. Core scheduler, Task Cell roles, executor interfaces, Skill/Tool schemas and browser providers remain.

Skill Registry starts empty (index counts zero). Personal examples are not relabeled as public examples. Unit tests create synthetic Skills in temporary directories. The former export test demanding a shipped active Skill is replaced with an explicit empty-registry packaging test under this request.

Workflow files are not silently assumed installed. Existing runtime references describe protocol/integration points. Before using a path, the installing AI must inventory the actual new private operational repository. A missing entry means WORKFLOW_NOT_PROVISIONED, not a successful dispatch.

Typical optional execution surfaces to provision, only when required: a bounded host-script runner, action submission/dispatch, semantic result reconciliation/finalization, capability probing, and Foreground direct commands. Read the corresponding executor/module CLI and current Task Contract; choose least permissions, explicit inputs, correct runner labels, timeouts, exact outputs and result verification. Never migrate a previous user's command payload or token. All browser provider source remains; browser.cli requires an explicitly configured normal/short host execution channel. No automatic workflow creation or external service is performed by this package.

Source privacy scan, syntax tests and unit tests cannot replace a fresh machine installation, real browser login, Runner registration, workflow acceptance or independently conducted third-party security review. This source publication does not claim those live or independent acceptance steps.
