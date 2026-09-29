import base64
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which('powershell.exe') or shutil.which('pwsh')


@unittest.skipUnless(SHELL, 'PowerShell is needed for the Windows launcher contract')
class NativeStartupTests(unittest.TestCase):
    def run_case(self, body):
        # Load only the real launcher functions through its AST; never run Git,
        # start browsers/runners, or modify the real runtime during these tests.
        source = str(ROOT / 'Start_CAH.ps1').replace("'", "''")
        script = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('SOURCE',[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$Repo=Split-Path -Parent 'SOURCE'
$BrowserRoot='__CAH_BROWSER_ROOT__'
$NoBrowser=$false; $script:starts=0
function New-Item { param($ItemType,$Path,[switch]$Force) }
function Start-Sleep { param($Milliseconds) }
""".replace('SOURCE', source)
        result = subprocess.run([SHELL, '-NoProfile', '-EncodedCommand', base64.b64encode((script+body+'\nexit 0\n').encode('utf-16le')).decode()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)

    def test_missing_host_starts_native_launcher_even_with_an_existing_browser(self):
        self.run_case(r'''
function Get-NativeHost { if($script:starts) { [pscustomobject]@{ProcessId=123} } }
function Read-Cdp { if($script:starts) { @{Browser='Chrome'} } }
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
 if($ArgumentList -notlike '*host\start_playwright_host.ps1*' -or $WorkingDirectory -ne $Repo -or $WindowStyle -ne 'Hidden') { throw 'wrong launcher binding' }
 $script:starts++; $p=[pscustomobject]@{HasExited=$false;ExitCode=0};$p|Add-Member ScriptMethod Refresh {};return $p
}
Ensure-NativeHost
if($script:starts -ne 1) { throw 'native launcher not started exactly once' }
''')

    def test_running_native_and_cdp_are_reused(self):
        self.run_case('''
function Get-NativeHost { [pscustomobject]@{ProcessId=123} }
function Read-Cdp { @{Browser='Chrome'} }
function Start-Process { throw 'duplicate launch' }
Ensure-NativeHost
''')

    def test_no_browser_does_not_claim_readiness_without_cdp(self):
        self.run_case('''
$NoBrowser=$true
function Get-NativeHost {}
function Read-Cdp {}
function Start-Process { throw 'must not launch' }
try { Ensure-NativeHost; throw 'expected failure was missed' } catch { if($_.Exception.Message -notlike '*-NoBrowser requires*') { throw } }
''')

    def test_existing_host_without_cdp_is_not_healthy(self):
        self.run_case('''
function Get-NativeHost { [pscustomobject]@{ProcessId=123} }
function Read-Cdp {}
try { Ensure-NativeHost; throw 'expected failure was missed' } catch { if($_.Exception.Message -notlike '*CDP 9222 is unavailable*') { throw } }
''')

    def test_process_match_excludes_unrelated_python_and_other_runtime(self):
        self.run_case(r'''
function Get-CimInstance {
 param($ClassName,$Filter)
 @(
 [pscustomobject]@{ProcessId=1;CommandLine='python.exe -m playwright_host --runtime-root __CAH_BROWSER_ROOT__'},
 [pscustomobject]@{ProcessId=2;CommandLine='python.exe -m playwright_host --runtime-root E:\Other'},
 [pscustomobject]@{ProcessId=3;CommandLine='python.exe unrelated.py'}
 )
}
$found=@(Get-NativeHost)
if($found.Count -ne 1 -or $found[0].ProcessId -ne 1) { throw 'process identity mismatch' }
''')


if __name__ == '__main__':
    unittest.main()
