"""Exercise only parsed pure deployment helpers in a real non-UTC PowerShell."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "evidence/cah-efficiency-upgrade-20261023/scripts/deploy-private-cah-maintenance.ps1"
PWSH = os.environ.get("CAH_PWSH") or shutil.which("pwsh")


@unittest.skipUnless(PWSH and os.name != "nt", "Needs real PowerShell on POSIX to set TZ without changing the host zone")
class MaintenanceDateTests(unittest.TestCase):
    def run_powershell(self, body):
        with tempfile.TemporaryDirectory(prefix="cah-maintenance-dates-") as temporary:
            directory = Path(temporary)
            script = directory / "test.ps1"
            script.write_text(
                """$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($env:CAH_DEPLOYMENT_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Deployment source has parse errors' }
foreach ($name in @('ConvertTo-UtcInstant','Assert-Process')) {
    $definition = @($ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true))
    if ($definition.Count -ne 1) { throw 'Expected one pure helper definition' }
    . ([ScriptBlock]::Create($definition[0].Extent.Text))
}
if ([TimeZoneInfo]::Local.GetUtcOffset([DateTime]::Now).TotalHours -ne 8) {
    throw 'Regression must run in the real Asia/Shanghai time zone'
}
""" + body,
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "TZ": "Asia/Shanghai",
                "CAH_DEPLOYMENT_SCRIPT": str(DEPLOYMENT),
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

    def test_typed_json_and_explicit_offsets_preserve_original_age_and_precision(self):
        result = self.run_powershell("""
$readback = '{"at":"2026-10-23T04:13:46.2804257Z"}' | ConvertFrom-Json -AsHashtable
$expected = [DateTimeOffset]::Parse('2026-10-23T04:13:46.2804257Z').UtcDateTime
$now = [DateTimeOffset]::Parse('2026-10-23T04:50:29.2669910Z').UtcDateTime
$values = @($readback.at, $expected, $expected.ToLocalTime(),
    [DateTimeOffset]::Parse('2026-10-23T12:13:46.2804257+08:00'),
    '2026-10-23T04:13:46.2804257Z', '2026-10-23T12:13:46.2804257+08:00')
foreach ($value in $values) {
    $utc = ConvertTo-UtcInstant $value
    if ($utc.Kind -ne [DateTimeKind]::Utc -or $utc.Ticks -ne $expected.Ticks) {
        throw 'Conversion changed the instant, zone kind or subsecond ticks'
    }
}
$rejected = 0
foreach ($value in @($null, 123, '2026-10-23T04:13:46.2804257',
                    [DateTime]::SpecifyKind($expected, [DateTimeKind]::Unspecified))) {
    try { [void](ConvertTo-UtcInstant $value) } catch { $rejected++ }
}
if ($rejected -ne 4) { throw 'Missing/ambiguous time zones must fail closed' }
$age = ($now - (ConvertTo-UtcInstant $readback.at)).TotalSeconds
$oldAge = ($now - [DateTime]::Parse($readback.at).ToUniversalTime()).TotalSeconds
$atLimit = (($expected.AddSeconds(3600)) - (ConvertTo-UtcInstant $readback.at)).TotalSeconds
$overLimit = (($expected.AddSeconds(3600).AddTicks(1)) - (ConvertTo-UtcInstant $readback.at)).TotalSeconds
@{ timezone = [TimeZoneInfo]::Local.Id; json_type = $readback.at.GetType().FullName
   age = $age; old_age = $oldAge; at_limit = $atLimit; over_limit = $overLimit
   equivalent_inputs = $values.Count; rejected_inputs = $rejected } | ConvertTo-Json -Compress
""")
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual(result["json_type"], "System.DateTime")
        self.assertAlmostEqual(result["age"], 2202.9865653, places=6)
        self.assertGreater(result["old_age"], 3600)
        self.assertEqual(result["at_limit"], 3600)
        self.assertGreater(result["over_limit"], 3600)
        self.assertEqual(result["equivalent_inputs"], 6)
        self.assertEqual(result["rejected_inputs"], 4)

    def test_process_identity_and_new_child_dates_compare_instants(self):
        result = self.run_powershell("""
$review = '{"creation_date_utc":"2026-10-20T16:06:39.3410720Z"}' | ConvertFrom-Json -AsHashtable
$expectedUtc = ConvertTo-UtcInstant $review.creation_date_utc
$script:actual = [PSCustomObject]@{
    CreationDate = $expectedUtc.ToLocalTime(); ExecutablePath = 'C:\\Python\\python.exe'
    CommandLine = 'python.exe -m exact-owned-process'; ParentProcessId = 31052
}
function Process-ById([int]$ProcessId) { return $script:actual }
$expected = @{ role = 'Bridge'; processId = 13368; parentProcessId = 31052
    executablePath = $script:actual.ExecutablePath; commandLine = $script:actual.CommandLine
    creationDateUtc = $review.creation_date_utc }
foreach ($value in @($review.creation_date_utc, '2026-10-20T16:06:39.3410720Z',
                    [DateTimeOffset]::Parse('2026-10-21T00:06:39.3410720+08:00'))) {
    $expected.creationDateUtc = $value
    [void](Assert-Process $expected)
}
$script:actual.CreationDate = $script:actual.CreationDate.AddTicks(1)
$rejectedReuse = $false
try { [void](Assert-Process $expected) } catch { $rejectedReuse = $true }
if (-not $rejectedReuse) { throw 'A reused PID with a different creation instant passed identity checks' }
$script:actual.CreationDate = $expectedUtc.ToLocalTime()
$script:actual.ParentProcessId++
$rejectedParent = $false
try { [void](Assert-Process $expected) } catch { $rejectedParent = $true }
if (-not $rejectedParent) { throw 'Parent ownership check was weakened' }
$readback = '{"at":"2026-10-23T04:13:46.2804257Z"}' | ConvertFrom-Json -AsHashtable
$before = (ConvertTo-UtcInstant $readback.at).AddTicks(-1).ToLocalTime()
$after = (ConvertTo-UtcInstant $readback.at).AddTicks(1).ToLocalTime()
if ((ConvertTo-UtcInstant $before) -ge (ConvertTo-UtcInstant $readback.at)) { throw 'Old child incorrectly appears fresh' }
if ((ConvertTo-UtcInstant $after) -lt (ConvertTo-UtcInstant $readback.at)) { throw 'New child incorrectly appears old' }
@{ typed_identity = $true; string_identity = $true; offset_identity = $true
   rejected_reused_pid = $rejectedReuse; rejected_parent = $rejectedParent
   child_freshness = $true } | ConvertTo-Json -Compress
""")
        self.assertTrue(all(result.values()))


if __name__ == "__main__":
    unittest.main()
