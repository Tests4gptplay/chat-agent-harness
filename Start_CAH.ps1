param([string]$RepoRoot = $PSScriptRoot, [switch]$NoBrowser, [switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$log = Join-Path $Repo 'Start_CAH.log'
Start-Transcript -Path $log -Append | Out-Null
try {
    $configPath = Join-Path $Repo 'cah.local.json'
    if (-not (Test-Path $configPath)) { throw 'Run tools/configure_install.py once. See docs/INSTALL_WINDOWS.md.' }
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $BridgeRoot = [string]$cfg.bridge_root
    $RunnerRoot = [string]$cfg.runner_root
    $env:GAH_LOCAL_ROOT = $BridgeRoot
    $env:GAH_WORK_ROOT = [string]$cfg.work_root
    Write-Host 'CAH One-Click Start'
    & git -C $Repo fetch --quiet --no-tags origin main
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
    & git -C $Repo merge --ff-only FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Fast-forward failed; local changes were not discarded.' }
    $health = $null
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch {}
    if (-not $health) {
        $supervisor = Join-Path $Repo 'host\bridge_supervisor.ps1'
        $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $supervisor + '" -TargetRepo "' + $Repo + '" -BridgeRoot "' + $BridgeRoot + '" -Port 8765 -Branch main'
        Start-Process powershell.exe -ArgumentList $arguments -WindowStyle Hidden | Out-Null
        for ($i=0; $i -lt 40 -and -not $health; $i++) {
            Start-Sleep -Milliseconds 500
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2 } catch {}
        }
    }
    if (-not $health.ok -or $health.root -ne $BridgeRoot) { throw 'Expected bridge is not available on port 8765.' }
    $expected = Join-Path $RunnerRoot 'bin\Runner.Listener.exe'
    $listeners = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" | Where-Object { $_.ExecutablePath -eq $expected })
    if ($listeners.Count -eq 0) {
        if (-not (Test-Path (Join-Path $RunnerRoot 'run.cmd'))) { throw 'Configured runner directory has no run.cmd.' }
        Start-Process cmd.exe -ArgumentList '/d /c run.cmd' -WorkingDirectory $RunnerRoot -WindowStyle Minimized | Out-Null
    }
    if (-not $NoBrowser -and @(Get-Process chrome -ErrorAction SilentlyContinue).Count -eq 0) {
        $chrome = @((Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),(Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $chrome) { throw 'Chrome not found. Open your configured browser manually.' }
        Start-Process $chrome -ArgumentList @($cfg.projects) | Out-Null
    }
    Write-Host 'CAH startup complete. Verify the extension topology before your first task.' -ForegroundColor Green
    Stop-Transcript | Out-Null
    exit 0
} catch {
    Write-Host ('CAH startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
