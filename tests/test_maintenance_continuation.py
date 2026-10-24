"""Real PowerShell checks of the reviewed post-stop continuation boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/cah-efficiency-upgrade-20261023"
DEPLOYMENT = EVIDENCE / "scripts/deploy-private-cah-maintenance.ps1"
PWSH = os.environ.get("CAH_PWSH") or shutil.which("pwsh")


@unittest.skipUnless(PWSH and os.name != "nt", "Needs real PowerShell with isolated POSIX TZ")
class MaintenanceContinuationTests(unittest.TestCase):
    def run_powershell(self, body):
        with tempfile.TemporaryDirectory(prefix="cah-maintenance-continuation-") as temporary:
            directory = Path(temporary)
            script = directory / "test.ps1"
            script.write_text("""$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:CAH_DEPLOYMENT_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Deployment source has parse errors' }
foreach ($name in @('ConvertTo-UtcInstant','Same-ProcessFact','Confirm-Continuation','Cutover-BusyChildren')) {
    $definition = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true))
    if ($definition.Count -ne 1) { throw 'Expected exactly one helper definition' }
    . ([ScriptBlock]::Create($definition[0].Extent.Text))
}
$original = Get-Content -LiteralPath $env:CAH_ORIGINAL_READBACK -Raw | ConvertFrom-Json -AsHashtable
$rawFrontier = Get-Content -LiteralPath $env:CAH_CONTINUATION_READBACK -Raw
$frontier = $rawFrontier | ConvertFrom-Json -AsHashtable
$originalHash = $frontier.original_readback_sha256
if ([TimeZoneInfo]::Local.GetUtcOffset([DateTime]::Now).TotalHours -ne 8) { throw 'Expected Asia/Shanghai' }
""" + body, encoding="utf-8")
            environment = {
                **os.environ, "TZ": "Asia/Shanghai", "CAH_DEPLOYMENT_SCRIPT": str(DEPLOYMENT),
                "CAH_ORIGINAL_READBACK": str(EVIDENCE / "private-host-reconciliation.json"),
                "CAH_CONTINUATION_READBACK": str(EVIDENCE / "private-cutover-frontier-39903582382-1.json"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_DATA_HOME": str(directory / "data"),
            }
            completed = subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                text=True, capture_output=True, env=environment, timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            return json.loads(completed.stdout)

    def test_real_stopped_frontier_accepted_and_contradictions_rejected(self):
        result = self.run_powershell("""
$console = Confirm-Continuation $frontier $original $originalHash
if ($console.pid -ne 7440) { throw 'The reviewed console was not selected' }
$mutations = @(
    { param($f) $f.errors = @('unresolved') },
    { param($f) $f.source.head = ('0' * 40) },
    { param($f) $f.samples[0].expected_identity[2].present = $true },
    { param($f) $f.samples[0].bridge_descendants += $f.samples[0].bridge_descendants[0] },
    { param($f) $f.samples[0].bridge_descendants[0].creation_date_utc = '2026-10-20T16:06:39.7675381Z' },
    { param($f) $f.state.frozen_sha256 = ('0' * 64) },
    { param($f) $f.samples[0].locks = @(@{ name = 'index.lock' }) },
    { param($f) $f.maintenance_result.source_updated = $true }
)
$rejected = 0
foreach ($mutate in $mutations) {
    $changed = $rawFrontier | ConvertFrom-Json -AsHashtable
    & $mutate $changed
    try { [void](Confirm-Continuation $changed $original $originalHash) } catch { $rejected++ }
}
try { [void](Confirm-Continuation $frontier $original ('0' * 64)) } catch { $rejected++ }
if ($rejected -ne 9) { throw 'A contradictory continuation proof was admitted' }
@{ approved_console = $console.pid; rejected_contradictions = $rejected } | ConvertTo-Json -Compress
""")
        self.assertEqual(result, {"approved_console": 7440, "rejected_contradictions": 9})

    def test_only_exact_reviewed_console_is_ignored_and_nested_work_stays_busy(self):
        result = self.run_powershell("""
$console = Confirm-Continuation $frontier $original $originalHash
function Native-Console {
    return [pscustomobject]@{ ProcessId = $console.pid; ParentProcessId = $console.parent_pid
        Name = $console.name; ExecutablePath = $console.executable_path
        CreationDate = (ConvertTo-UtcInstant $console.creation_date_utc).ToLocalTime() }
}
$native = Native-Console
if (@(Cutover-BusyChildren @($native) $console).Count) { throw 'Exact reviewed console blocked cutover' }
if (@(Cutover-BusyChildren @($native) $null).Count -ne 1) { throw 'Normal first deployment silently exempted a console' }
$mutations = @(
    { param($p) $p.ProcessId++ }, { param($p) $p.ParentProcessId++ },
    { param($p) $p.Name = 'git.exe' }, { param($p) $p.ExecutablePath = 'C:\\other\\conhost.exe' },
    { param($p) $p.CreationDate = $p.CreationDate.AddTicks(1) }
)
$rejected = 0
foreach ($mutate in $mutations) {
    $changed = Native-Console; & $mutate $changed
    if (@(Cutover-BusyChildren @($changed) $console).Count -eq 1) { $rejected++ }
}
if ($rejected -ne 5) { throw 'Console exemption weakened a full identity component' }
$gitChild = [pscustomobject]@{ ProcessId = 999; ParentProcessId = 7440; Name = 'git.exe'
    ExecutablePath = 'C:\\Git\\git.exe'; CreationDate = $native.CreationDate.AddSeconds(1) }
$busy = @(Cutover-BusyChildren @($native, $gitChild) $console)
if ($busy.Count -ne 1 -or $busy[0].ProcessId -ne 999) { throw 'Nested work under the console was ignored' }
@{ exact_console_ignored = $true; normal_path_keeps_console = $true
   rejected_identity_changes = $rejected; nested_git_stays_busy = $true } | ConvertTo-Json -Compress
""")
        self.assertEqual(result["rejected_identity_changes"], 5)
        self.assertTrue(result["exact_console_ignored"])
        self.assertTrue(result["normal_path_keeps_console"])
        self.assertTrue(result["nested_git_stays_busy"])


if __name__ == "__main__":
    unittest.main()
