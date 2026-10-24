import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


class ColdBurstRunnerContractTests(unittest.TestCase):
    def test_startup_keeps_reporting_separate_from_idempotent_start(self):
        text = (ROOT / "Start_CAH.ps1").read_text(encoding="utf-8")
        self.assertIn("$ColdBurstRoot = '__CAH_SECOND_RUNNER_ROOT__'", text)
        self.assertIn("$ColdBurstName = '__CAH_SECOND_RUNNER_NAME__'", text)

        block = text.split("function Report-ColdBurstRunner", 1)[1].split("function Ensure-ColdBurstRunner", 1)[0]
        self.assertNotIn("Start-Process", block)
        self.assertNotIn("Stop-Process", block)
        state_block = text.split("function Get-ColdBurstRunnerState", 1)[1].split("function Report-ColdBurstRunner", 1)[0]
        for state in ("READY_OFF", "ACTIVE_IDLE", "ACTIVE_BUSY", "DIRTY", "MISSING"):
            self.assertIn(state, state_block)
        self.assertIn("    Ensure-ColdBurstRunner\n", text)

    def test_cold_workflow_has_dedicated_runner_and_concurrency(self):
        text = (ROOT / ".github" / "workflows" / "managed-host-script-cold.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("requests/managed-host-script-cold/*.json", text)
        self.assertIn("runs-on: [self-hosted, Windows, X64, backup]", text)
        self.assertIn("group: gah-managed-host-script-cold", text)
        self.assertIn("queue: max", text)

    def test_skill_sync_is_explicit_and_does_not_serialize_stage0(self):
        text = (ROOT / ".github/workflows/codex-skill-sync.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("group: codex-skill-sync", text)
        self.assertNotIn("group: stage0-single-thread", text)

    def test_role_contract_preserves_capacity_ownership(self):
        planner = (ROOT / "docs" / "task-cell" / "PLANNER.md").read_text(encoding="utf-8")
        worker = (ROOT / "docs" / "task-cell" / "WORKER.md").read_text(encoding="utf-8")
        helper = (ROOT / "docs" / "task-cell" / "HELPER.md").read_text(encoding="utf-8")

        self.assertIn("## COLD_BURST RUNNER", worker)
        self.assertIn("READY_OFF", planner)
        self.assertIn("ACTIVE_BUSY", helper)
        self.assertIn("cah-shot", helper)

    def test_obsolete_runner_control_protocol_is_absent(self):
        self.assertFalse((ROOT / "host" / "backup_runner.ps1").exists())
        active_contracts = (
            "AGENTS.md",
            "docs/task-cell/PLANNER.md",
            "docs/task-cell/WORKER.md",
            "docs/task-cell/HELPER.md",
            "installation/README.md",
            "local_bridge/planner_runtime.py",
        )
        combined = "\n".join((ROOT / rel).read_text(encoding="utf-8") for rel in active_contracts)
        self.assertNotIn("host/backup_runner.ps1", combined)
        self.assertNotIn("cah-cold-rescue", combined)


@unittest.skipUnless(SHELL, "PowerShell is needed for the isolated Runner startup contract")
class RunnerStartupTests(unittest.TestCase):
    def run_case(self, body):
        source = str(ROOT / "Start_CAH.ps1").replace("'", "''")
        # Load function definitions only. Top-level Git/browser/Runner startup
        # never executes; every process query and launch used below is synthetic.
        script = r"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('SOURCE',[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$ColdBurstRoot='test-cold-root'
$ColdBurstName='__CAH_SECOND_RUNNER_NAME__'
""".replace("SOURCE", source)
        command = base64.b64encode((script + body + "\nexit 0\n").encode("utf-16le")).decode()
        result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)

    def test_busy_second_runner_is_reused_without_start_or_stop(self):
        self.run_case(r"""
function Get-ColdBurstRunnerState { [pscustomobject]@{State='ACTIVE_BUSY';Name='__CAH_SECOND_RUNNER_NAME__';Root=$ColdBurstRoot;ListenerCount=1;WorkerCount=1} }
function Ensure-Runner { throw 'busy runner must not be restarted' }
function Start-Process { throw 'must not launch' }
function Stop-Process { throw 'must not stop' }
Ensure-ColdBurstRunner
""")

    def test_stopped_second_runner_starts_once_and_repeated_start_reuses_it(self):
        self.run_case(r"""
$script:starts=0
function Get-ColdBurstRunnerState {
 $state=if($script:starts){'ACTIVE_IDLE'}else{'READY_OFF'}
 [pscustomobject]@{State=$state;Name='__CAH_SECOND_RUNNER_NAME__';Root=$ColdBurstRoot;ListenerCount=$script:starts;WorkerCount=0}
}
function Ensure-Runner {
 param($Name,$Root)
 if($Name -ne 'Managed-02' -or $Root -ne $ColdBurstRoot){throw 'wrong runner target'}
 $script:starts++
}
Ensure-ColdBurstRunner
Ensure-ColdBurstRunner
if($script:starts -ne 1){throw 'second runner was not started exactly once'}
""")

    def test_missing_or_dirty_second_runner_never_launches(self):
        for state in ("MISSING", "DIRTY"):
            with self.subTest(state=state):
                self.run_case(r"""
function Get-ColdBurstRunnerState { [pscustomobject]@{State='STATE'} }
function Ensure-Runner { throw 'must not start an unverified instance' }
try { Ensure-ColdBurstRunner; throw 'expected failure missed' }
catch { if($_.Exception.Message -notlike '*not the configured*state=STATE*'){throw} }
""".replace("STATE", state))

    def test_listener_launch_detaches_and_restores_job_tracking(self):
        self.run_case(r"""
$script:starts=0
$root='test-primary-root'
$env:RUNNER_TRACKING_ID='synthetic-owned-job'
function Test-Path { param($Path) return $true }
function Start-Sleep { param($Milliseconds) }
function Get-CimInstance {
 if($script:starts){[pscustomobject]@{ExecutablePath=(Join-Path $root 'bin\Runner.Listener.exe')}}
}
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)
 if($env:RUNNER_TRACKING_ID){throw 'listener inherited Actions job ownership'}
 if($WorkingDirectory -ne $root -or $FilePath -ne 'cmd.exe'){throw 'wrong listener launch'}
 $script:starts++
}
Ensure-Runner -Name 'Managed' -Root $root
if($script:starts -ne 1 -or $env:RUNNER_TRACKING_ID -ne 'synthetic-owned-job'){throw 'tracking state was not restored'}
""")

    def test_actual_state_reader_rejects_foreign_config_and_orphan_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "run.cmd").write_text("@echo off\n")
            (root / ".runner").write_text(json.dumps({"agentName": "other-runner"}))
            quoted = str(root).replace("'", "''")
            self.run_case(r"""
$ColdBurstRoot='ROOT'
function Get-CimInstance {}
$state=Get-ColdBurstRunnerState
if($state.State -ne 'DIRTY'){throw 'foreign Runner configuration accepted'}
@{agentName='__CAH_SECOND_RUNNER_NAME__'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ColdBurstRoot '.runner')
function Get-CimInstance {
 param($ClassName,$Filter)
 if($Filter -like '*Runner.Worker.exe*'){[pscustomobject]@{ExecutablePath=(Join-Path $ColdBurstRoot 'bin\Runner.Worker.exe')}}
}
$state=Get-ColdBurstRunnerState
if($state.State -ne 'DIRTY'){throw 'orphan Worker was not rejected'}
""".replace("ROOT", quoted))


@unittest.skipUnless(SHELL, "PowerShell is needed for isolated managed-script execution")
class ManagedScriptBatchTests(unittest.TestCase):
    def run_request(self, cold, script_bytes, *, existing=None, extra_request=False):
        workflow = "managed-host-script-cold" if cold else "managed-host-script"
        content = (ROOT / ".github/workflows" / (workflow + ".yml")).read_text(encoding="utf-8")
        block = content.split("        run: |\n", 1)[1]
        code = "\n".join(line[10:] if line else "" for line in block.splitlines())
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            requests = work / "requests" / workflow
            requests.mkdir(parents=True)
            path = requests / "synthetic-batch.json"
            path.write_text(json.dumps({"v": 1, "request_id": "synthetic-batch", "task_id": "synthetic-child",
                                        "script_b64": base64.b64encode(script_bytes).decode()}))
            runtime = work / "runner-temp"
            runtime.mkdir()
            materialized = runtime / (("gah-managed-cold-" if cold else "gah-managed-") + "synthetic-batch.ps1")
            if existing is not None:
                materialized.write_bytes(existing)
            relative = path.relative_to(work).as_posix()
            stub = "function git { '" + relative + "'"
            if extra_request:
                stub += "; 'requests/" + workflow + "/other.json'"
            stub += " }\n"
            wrapper = work / "synthetic-workflow.ps1"
            wrapper.write_text(stub + code)
            env = os.environ.copy()
            env.update(RUNNER_TEMP=str(runtime), GITHUB_SHA="synthetic-commit", RUNNER_NAME="synthetic-runner")
            result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
                                    cwd=work, env=env, capture_output=True, text=True, timeout=30)
            retained = materialized.read_bytes() if materialized.exists() else None
            return result, retained

    def test_one_request_runs_continuous_steps_and_cleans_owned_script(self):
        for cold in (False, True):
            with self.subTest(cold=cold):
                result, retained = self.run_request(cold, b"$ErrorActionPreference='Stop'\nWrite-Output 'BATCH_STEP_A'\nWrite-Output 'BATCH_STEP_B'\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("BATCH_STEP_A", result.stdout)
                self.assertIn("BATCH_STEP_B", result.stdout)
                self.assertIsNone(retained)

    def test_failed_step_does_not_claim_completion_or_run_following_step(self):
        for cold in (False, True):
            with self.subTest(cold=cold):
                result, retained = self.run_request(cold, b"$ErrorActionPreference='Stop'\nthrow 'synthetic stage failed'\nWrite-Output 'UNREACHABLE_STEP'\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("synthetic stage failed", result.stderr)
                self.assertNotIn("UNREACHABLE_STEP", result.stdout)
                self.assertNotIn("_COMPLETE|", result.stdout)
                self.assertIsNone(retained)

    def test_distinct_existing_script_is_preserved_and_not_executed(self):
        for cold in (False, True):
            with self.subTest(cold=cold):
                previous = b"Write-Output 'STALE_SCRIPT_EXECUTED'\n"
                result, retained = self.run_request(cold, b"Write-Output 'NEW_SCRIPT_EXECUTED'\n", existing=previous)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Physical materialization mismatch", result.stderr)
                self.assertEqual(retained, previous)
                self.assertNotIn("SCRIPT_EXECUTED", result.stdout)

    def test_one_commit_cannot_smuggle_multiple_request_files(self):
        result, retained = self.run_request(False, b"Write-Output 'UNREACHABLE_STEP'\n", extra_request=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected exactly one", result.stderr)
        self.assertNotIn("UNREACHABLE_STEP", result.stdout)
        self.assertIsNone(retained)

    def test_invalid_utf8_and_parse_failure_never_execute(self):
        for script in (b"\xff", b"if ( {\nWrite-Output 'UNREACHABLE_STEP'\n"):
            with self.subTest(script=script):
                result, retained = self.run_request(True, script)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("UNREACHABLE_STEP", result.stdout)
                self.assertIsNone(retained)


if __name__ == "__main__":
    unittest.main()
