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
$ColdBurstRoot = '__CAH_COLD_RUNNER_ROOT__'
$ColdBurstName = '__CAH_COLD_RUNNER_NAME__'
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

function Report-ColdBurstRunner {
    $runCmd = Join-Path $ColdBurstRoot 'run.cmd'
    $configPath = Join-Path $ColdBurstRoot '.runner'
    $listenerPath = Join-Path $ColdBurstRoot 'bin\Runner.Listener.exe'
    $workerPath = Join-Path $ColdBurstRoot 'bin\Runner.Worker.exe'

    $listeners = @(
        Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" |
            Where-Object { $_.ExecutablePath -eq $listenerPath }
    )
    $workers = @(
        Get-CimInstance Win32_Process -Filter "Name='Runner.Worker.exe'" |
            Where-Object { $_.ExecutablePath -eq $workerPath }
    )

    if (-not (Test-Path -LiteralPath $ColdBurstRoot) -or -not (Test-Path -LiteralPath $runCmd) -or -not (Test-Path -LiteralPath $configPath)) {
        $state = 'MISSING'
        $name = $ColdBurstName
    } else {
        try { $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json }
        catch { $config = $null }

        $name = if ($config) { [string]$config.agentName } else { '<invalid-config>' }
        if (
            $null -eq $config -or
            $name -ne $ColdBurstName -or
            $listeners.Count -gt 1 -or
            ($listeners.Count -eq 0 -and $workers.Count -gt 0)
        ) {
            $state = 'DIRTY'
        } elseif ($listeners.Count -eq 0) {
            $state = 'READY_OFF'
        } elseif ($workers.Count -gt 0) {
            $state = 'ACTIVE_BUSY'
        } else {
            $state = 'ACTIVE_IDLE'
        }
    }

    Write-Host (
        '      Cold/Burst Runner: state={0}; name={1}; root={2}; listener_count={3}; worker_count={4}' -f
        $state,$name,$ColdBurstRoot,$listeners.Count,$workers.Count
    )
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

function Get-CahBrowser {
    $profile = Join-Path $env:LOCALAPPDATA 'CAH\PlaywrightChrome'
    @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
        $_.CommandLine -match ('(?:^|\s)"?--user-data-dir=(?:"' + [regex]::Escape($profile) + '"|' + [regex]::Escape($profile) + ')(?:"?\s|$)') -and
        $_.CommandLine -match '(?:^|\s)--remote-debugging-port=9222(?:\s|$)' -and
        $_.CommandLine -notmatch '(?:^|\s)--type='
    })
}

function Assert-CahCdpOwner {
    $owners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 9222 } | Select-Object -ExpandProperty OwningProcess -Unique)
    $browsers = @(Get-CahBrowser)
    foreach ($owner in $owners) {
        if ($owner -notin @($browsers.ProcessId)) {
            throw "CDP 9222 belongs to an unverified process (PID $owner); no processes were replaced."
        }
    }
    if ($browsers.Count -gt 1) { throw 'Multiple CAH browser roots found; refusing ambiguous recovery.' }
}

function Assert-CahRecoveryIdle {
    $health = Read-Bridge
    if (-not $health -or -not $health.ok -or $health.service -ne 'gah-local-wake-bridge' -or
        $health.root -ne $BridgeRoot -or $null -eq $health.pending -or $null -eq $health.claimed -or
        $health.pending -ne 0 -or $health.claimed -ne 0) {
        throw 'CAH recovery requires a healthy idle bridge with no pending or claimed work.'
    }
    $workerPaths = @($RunnerRoots | ForEach-Object { Join-Path $_.Root 'bin\Runner.Worker.exe' })
    $workerPaths += Join-Path $ColdBurstRoot 'bin\Runner.Worker.exe'
    $workers = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Worker.exe'" | Where-Object {
        -not $_.ExecutablePath -or $_.ExecutablePath -in $workerPaths
    })
    if ($workers.Count) { throw 'CAH Runner work is active or ownership is unknown; browser recovery was not performed.' }
    $state = Get-Content -LiteralPath (Join-Path $Repo 'state\chatgpt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in @('active_task', 'active_action', 'active_dispatch_ref', 'paused_work')) {
        if ($null -eq $state.PSObject.Properties[$field] -or $state.$field) {
            throw "CAH state is active or unknown ($field); browser recovery was not performed."
        }
    }
    if ($state.control_request -and $state.control_request.status -notin @('DONE', 'CANCELLED')) {
        throw 'CAH control request is unresolved; browser recovery was not performed.'
    }
    $topology = Get-Content -LiteralPath (Join-Path $Repo 'state\lanes.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $topology.lanes) { throw 'CAH lane state is unknown.' }
    foreach ($lane in $topology.lanes) {
        if ($lane.status -ne 'IDLE' -or @($lane.task_pools.PSObject.Properties).Count) {
            throw 'CAH lane has retained work or is not idle; browser recovery was not performed.'
        }
    }
    $runtimePath = Join-Path $BrowserRoot 'state.json'
    if (Test-Path -LiteralPath $runtimePath) {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($lane in $runtime.lanes.PSObject.Properties) {
            if (@($lane.Value.task_pools.PSObject.Properties).Count) { throw 'CAH host retains Worker work; recovery stopped.' }
        }
        if (@($runtime.task_cell.planner_successors.PSObject.Properties).Count) { throw 'CAH host retains a Planner successor; recovery stopped.' }
        foreach ($role in $runtime.task_cell.roles.PSObject.Properties) {
            # A completed error Helper may remain bound until browser cleanup succeeds.
            $record = $role.Value
            $ref = [string]$record.active_output_ref
            if ($record.helper_source -ne 'harness_error' -or $ref -notmatch '^evidence/harness-errors/[A-Za-z0-9_-]+\.json$') {
                throw 'CAH host retains an unresolved role; recovery stopped.'
            }
            $result = Get-Content -LiteralPath (Join-Path $Repo $ref) -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($result.request_id -ne $record.active_request_id -or $result.turn_signal -ne 'done') {
                throw 'CAH Helper has not completed; recovery stopped.'
            }
        }
    }
}

function Stop-StaleCahBrowserRuntime {
    Assert-CahRecoveryIdle
    Assert-CahCdpOwner
    # Snapshot only exact roots plus known browser/Playwright children. Never taskkill /IM or /T.
    $roots = @((Get-NativeHost)) + @((Get-CahBrowser))
    $snapshot = @(Get-CimInstance Win32_Process)
    $targets = @($roots)
    for ($index = 0; $index -lt $targets.Count; $index++) {
        $parent = $targets[$index]
        foreach ($child in @($snapshot | Where-Object { $_.ParentProcessId -eq $parent.ProcessId -and $_.CreationDate -ge $parent.CreationDate })) {
            if ($child.ProcessId -in @($targets.ProcessId)) { continue }
            $browserChild = $parent.Name -eq 'chrome.exe' -and $child.Name -eq 'chrome.exe' -and $child.ExecutablePath -eq $parent.ExecutablePath
            $driverChild = $parent.Name -eq 'python.exe' -and $child.Name -eq 'node.exe' -and $child.ExecutablePath -match '[\\/]playwright[\\/]driver[\\/]node\.exe$'
            if (-not ($browserChild -or $driverChild)) {
                throw "Unknown child of CAH process (PID $($child.ProcessId)); no processes were replaced."
            }
            $targets += $child
        }
    }
    # Recheck immediately before the first stop; never infer idleness just from missing CDP.
    Assert-CahRecoveryIdle
    if (Read-Cdp) { throw 'CDP recovered during inspection; retry startup to reuse it.' }
    foreach ($target in $targets) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($target.ProcessId)"
        if (-not $current) { continue }
        if ($current.CreationDate -ne $target.CreationDate -or $current.CommandLine -ne $target.CommandLine -or $current.ExecutablePath -ne $target.ExecutablePath) {
            throw "Process identity changed for PID $($target.ProcessId); recovery stopped."
        }
        Write-Host ('      Stopping stale CAH process: ' + $target.Name + ' PID ' + $target.ProcessId) -ForegroundColor Yellow
        Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
        Wait-Process -Id $target.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        if (Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue) { throw "CAH process $($target.ProcessId) did not stop." }
    }
    Assert-CahCdpOwner
}

function Ensure-NativeHost {
    $mutex = New-Object System.Threading.Mutex($false, 'Local\CAH.NativeStartup.9222')
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw 'Another CAH native startup is in progress; retry after it completes.' }
        Ensure-NativeHostCore
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}


function Ensure-NativeHostCore {
    $hosts = @(Get-NativeHost)
    if ($hosts.Count -gt 1) { throw 'Multiple native Playwright hosts own the CAH browser runtime.' }
    Assert-CahCdpOwner
    $cdp = Read-Cdp
    if ($NoBrowser -and -not $cdp) { throw '-NoBrowser requires the existing CAH CDP browser on port 9222.' }
    if ($hosts.Count -eq 1 -and $cdp) {
        Write-Host ('      Native Playwright host reused: PID ' + $hosts[0].ProcessId)
        return
    }
    if (-not $cdp -and ($hosts.Count -or @(Get-CahBrowser).Count)) {
        Write-Host '      CDP 9222 is unavailable; checking idle CAH runtime for safe restart...' -ForegroundColor Yellow
        Stop-StaleCahBrowserRuntime
    }
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
    Assert-CahCdpOwner
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
        Stop-Stale-CahBridge -Port $Port -ExpectedRoot $BridgeRoot
        $health = $null
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
    # Cold/Burst is intentionally not part of $RunnerRoots: startup knows and
    # reports its state but never starts or stops it. Semantic roles decide when
    # the extra capacity is useful; exact operational changes remain incident work.
    Report-ColdBurstRunner

    Write-Host '[4/4] Checking the native Playwright host and CAH CDP browser...'
    Ensure-NativeHost
    # Startup is not a workload acceptance test and must not wait for an old demo.
    Write-Host 'CAH startup complete. Bridge, active runners, Cold/Burst status, native Playwright host and CDP are available.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('CAH startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
