param(
    [string]$RepoRoot = $PSScriptRoot,
    [switch]$NoBrowser,
    [string]$LocalWorkerLanes = 'lane-00,lane-01'
)

$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $RepoRoot).Path
& py (Join-Path $Repo 'installation\preflight.py')
if ($LASTEXITCODE -ne 0) { throw 'CAH deployment is not configured' }
$BridgeRoot = '__CAH_BRIDGE_ROOT__'
$RunnerRoots = @(
    @{ Name = 'Managed'; Root = '__CAH_MANAGED_RUNNER_ROOT__' },
    @{ Name = 'Shot'; Root = '__CAH_SHOT_RUNNER_ROOT__' }
)
$Port = 8765
$BrowserRoot = '__CAH_BROWSER_ROOT__'

function Read-Bridge {
    try { return Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2 }
    catch { return $null }
}

function Ensure-Runner {
    param(
        [string]$Name,
        [string]$Root
    )

    $expectedListener = Join-Path $Root 'bin\Runner.Listener.exe'
    $listeners = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" | Where-Object { $_.ExecutablePath -eq $expectedListener })
    if ($listeners.Count -eq 0) {
        $runCmd = Join-Path $Root 'run.cmd'
        if (-not (Test-Path $runCmd)) { throw "$Name Runner missing: $Root" }
        Start-Process cmd.exe -ArgumentList '/d /c run.cmd' -WorkingDirectory $Root -WindowStyle Minimized | Out-Null
        $deadline = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            $listeners = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" | Where-Object { $_.ExecutablePath -eq $expectedListener })
        } while ($listeners.Count -eq 0 -and (Get-Date) -lt $deadline)
    }
    if ($listeners.Count -ne 1) {
        throw "$Name Runner is not the expected single instance at $expectedListener."
    }
    Write-Host ('      ' + $Name + ' Runner: ' + $expectedListener)
}

function Read-Cdp {
    try { return Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 2 }
    catch { return $null }
}

function Get-NativeHost {
    # Match the actual Python host, not its py.exe or PowerShell launcher.
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -match '(?:^|\s)-m\s+playwright_host(?:\s|$)' -and
        $_.CommandLine -match ('--runtime-root\s+"?' + [regex]::Escape($BrowserRoot) + '"?(?:\s|$)')
    })
}

function Ensure-NativeHost {
    $hosts = @(Get-NativeHost)
    if ($hosts.Count -gt 1) { throw 'Multiple native Playwright hosts own the CAH browser runtime.' }
    if ($hosts.Count -eq 1) {
        if (-not (Read-Cdp)) { throw 'Native host exists but CDP 9222 is unavailable; existing processes were not replaced.' }
        Write-Host ('      Native Playwright host reused: PID ' + $hosts[0].ProcessId)
        return
    }
    if ($NoBrowser -and -not (Read-Cdp)) { throw '-NoBrowser requires the existing CAH CDP browser on port 9222.' }
    $launcher = Join-Path $Repo 'host\start_playwright_host.ps1'
    if (-not (Test-Path -LiteralPath $launcher)) { throw "Native Playwright launcher missing: $launcher" }
    New-Item -ItemType Directory -Force -Path $BrowserRoot | Out-Null
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '" -RepoRoot "' + $Repo + '" -RuntimeRoot "' + $BrowserRoot + '"'
    $hadTracking = Test-Path Env:RUNNER_TRACKING_ID
    $tracking = $env:RUNNER_TRACKING_ID
    $env:RUNNER_TRACKING_ID = ''
    try {
        $child = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $Repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $BrowserRoot 'host.stdout.log') -RedirectStandardError (Join-Path $BrowserRoot 'host.stderr.log')
    } finally {
        if ($hadTracking) { $env:RUNNER_TRACKING_ID = $tracking }
        else { Remove-Item Env:RUNNER_TRACKING_ID -ErrorAction SilentlyContinue }
    }
    $deadline = (Get-Date).AddSeconds(25)
    do {
        Start-Sleep -Milliseconds 500
        $child.Refresh()
        if ($child.HasExited) { throw "Native Playwright launcher exited: $($child.ExitCode). See $BrowserRoot\host.stderr.log" }
        $hosts = @(Get-NativeHost)
        $cdp = Read-Cdp
    } while (($hosts.Count -ne 1 -or -not $cdp) -and (Get-Date) -lt $deadline)
    if ($hosts.Count -ne 1 -or -not $cdp) { throw "Native host/CDP did not become available. See $BrowserRoot\host.stderr.log" }
    Write-Host ('      Native Playwright host: PID ' + $hosts[0].ProcessId + '; CDP 9222 available.')
}

try {
    Write-Host 'CAH One-Click Start' -ForegroundColor Green
    if (-not (Test-Path (Join-Path $Repo '.git'))) { throw "Repository missing: $Repo" }
    Write-Host '[1/4] Updating the existing Git checkout...'
    & git -C $Repo fetch --quiet --no-tags origin main
    if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
    & git -C $Repo merge --ff-only FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Fast-forward update failed; local changes were not discarded.' }

    Write-Host '[2/4] Checking the CAH bridge...'

    function Stop-Stale-CahBridge {
        param(
            [int]$Port,
            [string]$ExpectedRoot
        )

        $staleHealth = Read-Bridge
        if (-not $staleHealth) {
            return
        }

        if (
            $staleHealth.ok -and
            $staleHealth.service -eq 'gah-local-wake-bridge' -and
            $staleHealth.root -eq $ExpectedRoot
        ) {
            return
        }

        if ($staleHealth.service -ne 'gah-local-wake-bridge') {
            throw "Port $Port is occupied by a non-CAH service. Refusing to terminate it."
        }

        Write-Host (
            '      Replacing stale CAH bridge: ' +
            $staleHealth.root +
            ' -> ' +
            $ExpectedRoot
        ) -ForegroundColor Yellow

        $supervisors = @(
            Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
            Where-Object {
                $_.CommandLine -match 'bridge_supervisor\.ps1' -and
                $_.CommandLine -match ('-Port\s+"?' + $Port + '"?(?:\s|$)')
            }
        )

        foreach ($p in $supervisors) {
            Write-Host ('      Stopping stale bridge supervisor PID ' + $p.ProcessId)
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }

        Start-Sleep -Milliseconds 300

        $listeners = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        )

        foreach ($listener in $listeners) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
            if (
                $owner -and
                $owner.Name -eq 'python.exe' -and
                $owner.CommandLine -match 'local_bridge[\\/]+server\.py'
            ) {
                Write-Host ('      Stopping stale bridge PID ' + $owner.ProcessId)
                Stop-Process -Id $owner.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }

        $deadline = (Get-Date).AddSeconds(5)
        do {
            Start-Sleep -Milliseconds 200
            $occupied = @(
                Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            ).Count -gt 0
        } while ($occupied -and (Get-Date) -lt $deadline)

        if ($occupied) {
            throw "Stale CAH bridge on port $Port could not be stopped."
        }
    }

    $health = Read-Bridge

    if (
        $health -and
        $health.service -eq 'gah-local-wake-bridge' -and
        $health.root -ne $BridgeRoot
    ) {
        throw 'Port 8765 belongs to a different CAH bridge root. No process was stopped. Resolve instance ownership explicitly before launch.'
    }

    if (-not $health) {
        $supervisor = Join-Path $Repo 'host\bridge_supervisor.ps1'
        if (-not (Test-Path $supervisor)) { throw "Bridge supervisor missing: $supervisor" }
        $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $supervisor + '" -TargetRepo "' + $Repo + '" -BridgeRoot "' + $BridgeRoot + '" -Port ' + $Port + ' -Branch main -LocalWorkerLanes "' + $LocalWorkerLanes + '"'
        # When Start_CAH is invoked from a self-hosted Actions job, child processes
        # inherit RUNNER_TRACKING_ID and GitHub's orphan cleanup will kill them at
        # job end. Bridge is persistent host infrastructure, so detach it using
        # the same no-tracking discipline as the native Playwright Host below.
        $hadTracking = Test-Path Env:RUNNER_TRACKING_ID
        $tracking = $env:RUNNER_TRACKING_ID
        $env:RUNNER_TRACKING_ID = ''
        try {
            Start-Process powershell.exe -ArgumentList $arguments -WindowStyle Hidden | Out-Null
        } finally {
            if ($hadTracking) { $env:RUNNER_TRACKING_ID = $tracking }
            else { Remove-Item Env:RUNNER_TRACKING_ID -ErrorAction SilentlyContinue }
        }
        $deadline = (Get-Date).AddSeconds(20)
        do { Start-Sleep -Milliseconds 500; $health = Read-Bridge } while (-not $health -and (Get-Date) -lt $deadline)
    }

    if (
        -not $health -or
        -not $health.ok -or
        $health.service -ne 'gah-local-wake-bridge' -or
        $health.root -ne $BridgeRoot
    ) {
        $observedRoot = if ($health) { $health.root } else { '<unavailable>' }
        throw "Expected CAH bridge on port $Port with root $BridgeRoot. Observed root: $observedRoot"
    }
    Write-Host ('      Bridge: ' + $health.root)

    Write-Host '[3/4] Checking the CAH self-hosted runners...'
    foreach ($runner in $RunnerRoots) {
        Ensure-Runner -Name $runner.Name -Root $runner.Root
    }

    Write-Host '[4/4] Checking the native Playwright host and CAH CDP browser...'
    Ensure-NativeHost
    # Startup is not a workload acceptance test and must not wait for an old demo.
    Write-Host 'CAH startup complete. Bridge, runners, native Playwright host and CDP are available.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('CAH startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
