# CAH v1.0.5 — 隐私脱敏与重新部署规划 / Private re-deployment plan

IMPORTANT: This is a PUBLIC, UNCONFIGURED source distribution, not the maintainer's live operational CAH. 公开源码已脱敏：不可恢复或照抄维护者的真实本机路径、账户、Project ID、浏览器会话、Runner 身份、注册令牌、私人工作流、任务、记忆、证据、私人 Skill 或 Git 历史。

## 1. 先重新规划所有私有绑定 / Re-plan all private bindings

1. Choose a new/private operational GitHub repository and independently verify its visibility is PRIVATE from authoritative GitHub metadata. User must authorize repo writes and separately manage confidential credentials. Do not run managed tasks, personal data or real runner enrollment against the public source repository.
2. Inventory the target user's actual Windows host and permissions. Re-select paths for source checkout, Bridge runtime and logs, Playwright Host state, browser CDP Profile, Node tool installation, workspaces, deliverables, temporary files and each Runner. Keep all runtime state/profiles/secrets outside the source checkout and Git.
3. Authenticated user creates/selects dedicated ChatGPT Task Cell, at least one Sandbox lane and a specific Foreground conversation. Installer must observe actual unique Project keys and URLs in the authorized logged-in browser, not infer them from display names or historical screenshots. Never use the original maintainer's Project keys.
4. Configure three *independent* Runner identities/roots: primary Managed (self-hosted, Windows, X64); dedicated Short Task (cah-shot, without generic Managed labels); and a normally-off optional Cold/Burst Managed Runner (self-hosted, Windows, X64, backup). Re-plan COLD_RUNNER_ROOT and COLD_RUNNER_NAME for this user's repo; do not inherit any old Runner name, root or registration token. The Cold/Burst lifecycle must be activated explicitly when needed, not auto-started as a third always-on workload Runner.
5. This source distribution intentionally omits PRIVATE .github/workflows, toolbox jobs, user imports, live task scripts and personal Skills. Audit needed capabilities against AGENTS.md, docs/task-cell/HELPER.md, docs/TOOL_SYSTEM.md and installation/OMITTED_COMPONENTS.md. Provision and verify new task-appropriate, permission-scoped workflows in the user's PRIVATE repo only. Until real workflows are installed, report WORKFLOW_NOT_PROVISIONED; never claim the Helper/Worker end-to-end operational path is ready. Never route Managed workload through cah-shot.
6. Start new operational Task Cell state from the published IDLE templates, empty lane pools and empty Skill Registry. Do NOT migrate live tasks, cold records, memories, request ledgers, browser state, private video/audio, third-party property or private showcase assets. Approved legacy PUBLIC showcases/branding/sponsor QR are independent public assets, not an invitation to import private media.

## 2. 配置与防误启动 / Configure and fail closed

Review LICENSE, NOTICE, AGENTS.md, installation/README.md, CONFIGURATION_MAP.md, placeholders.json and OMITTED_COMPONENTS.md. Store config.example.json-derived settings in a private location OUTSIDE the public source checkout; never commit the filled config.

- First run: py installation/audit.py. Any privacy/inventory failure is a release problem, not permission to disable validation.
- Confirm the NEW operational repo is PRIVATE and each ChatGPT Project identity is owned/authorized. Then run: py installation/configure.py --config <PRIVATE_CONFIG_JSON> --output <NEW_PRIVATE_WORKTREE_PATH>. It creates a new configured copy; it does not clone, push, register, install or start services.
- In that private copy run: py installation/preflight.py. Confirm no placeholders remain in runtime files, configured.local.json is private, Git remote/main targets the authorized private repository, and the runtime/Runner/browser data dirs are outside tracked source.
- Complete real independent Windows checks: Python/Node/Playwright versions, CDP 9222 identity, Bridge 8765 identity, actual Runner names and labels, startup ownership and browser Project access. Provision necessary GitHub Actions workflow payloads. Test a harmless short task, managed Worker result, Planner continuation, Helper recovery and cleanup when authorized. Missing steps must be listed as unverified; offline Python tests do not establish real new-user installation readiness.

## 3. 已有私有实例升级 / Existing configured private installations

NEVER overlay v1.0.5 onto an ACTIVE private instance. Record current Task/Planner/Worker/Helper and generation/fence state, owned jobs, Runner occupancy, external source workspaces, artifacts, runtime profiles, credentials and private workflow bindings. Prepare backups and a separate per-instance compatibility/migration plan. Do not reset, hard-reset, force-push, delete ChatGPT conversations, move Runner roots or replace workflows until exact ownership, safety, rollback and live verification are established. Do not import the maintainer's private Git history into public source.

## 4. 出版和验收边界 / Release and acceptance criteria

Public packaging scan must reject real tokens, identity URLs, private Windows/home paths, browser sessions, active state, tasks, memories, requests, evidence, unapproved media and undisclosed third-party source. Allow only explicit reviewed public brand/sponsor/historical showcase exceptions. Test code and selected synthetic fixtures are not live user data. Verify input/source code behavior through recorded unit/syntax tests; check the actual installation mapping and fresh private deployment independently.

New original CAH source is AGPL-3.0-only as declared in LICENSE and NOTICE. Earlier MIT-distributed copies retain those earlier permissions; third-party assets and dependencies retain their respective licenses. A passing offline scan is NOT a security certification or a claim of third-party Windows/ChatGPT/Runner acceptance.
