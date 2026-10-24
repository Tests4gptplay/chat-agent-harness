param(
  [string]$TargetRepo = '__CAH_REPO_ROOT__',
  [string]$BridgeRoot = '__CAH_BRIDGE_ROOT__',
  [int]$Port = 8765,
  [string]$Branch = 'main',
  [string]$LocalWorkerLanes = 'lane-00,lane-01'
)

$ErrorActionPreference = 'Continue'
$runtimeDir = Join-Path $BridgeRoot 'runtime'
$logsDir = Join-Path $BridgeRoot 'logs'
New-Item -ItemType Directory -Force -Path $runtimeDir, $logsDir | Out-Null

$supervisorState = Join-Path $runtimeDir 'bridge-supervisor.json'
$stdout = Join-Path $logsDir 'bridge-supervised.stdout.log'
$stderr = Join-Path $logsDir 'bridge-supervised.stderr.log'

function Write-SupervisorState([string]$State, [int]$ChildPid = 0, [string]$LastError = '') {
  @{
    v = 1
    state = $State
    supervisor_pid = $PID
    child_pid = if ($ChildPid -gt 0) { $ChildPid } else { $null }
    target_repo = $TargetRepo
    bridge_root = $BridgeRoot
    port = $Port
    branch = $Branch
    local_worker_lanes = $LocalWorkerLanes
    last_error = if ($LastError) { $LastError } else { $null }
    updated_at = (Get-Date).ToUniversalTime().ToString('o')
  } | ConvertTo-Json -Depth 4 | Set-Content -Path $supervisorState -Encoding utf8
}

$python = (& py -c "import sys; print(sys.executable)").Trim()
$server = Join-Path $TargetRepo 'local_bridge\server.py'
Write-SupervisorState -State 'STARTING'

$p = $null
while ($true) {
  try {
    # Retain the owned process object across errors; never spawn beside a live child.
    if ($null -eq $p -or $p.HasExited) {
      if (-not (Test-Path $server)) {
        Write-SupervisorState -State 'WAITING_FOR_SERVER' -LastError "server missing: $server"
        Start-Sleep -Seconds 3
        continue
      }

      $env:CAH_LOCAL_WORKER_LANES = $LocalWorkerLanes
      $p = Start-Process -FilePath $python -ArgumentList @(
        ('"' + $server + '"'),
        '--host', '127.0.0.1',
        '--port', "$Port",
        '--root', ('"' + $BridgeRoot + '"'),
        '--repo-root', ('"' + $TargetRepo + '"'),
        '--git-branch', $Branch
      ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr -ErrorAction Stop

      Write-SupervisorState -State 'RUNNING' -ChildPid $p.Id
    }

    $failedProbes = 0
    # This probe does not inspect queues or Git. Process exit wakes the wait early.
    while (-not $p.WaitForExit(10000)) {
      try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($health.ok -eq $true) {
          $failedProbes = 0
          continue
        }
      } catch {
        # A transient HTTP failure alone must not interrupt a live Bridge.
      }
      $failedProbes++
      if ($failedProbes -lt 3) { continue }
      Write-SupervisorState -State 'RESTARTING' -ChildPid $p.Id -LastError 'Bridge liveness failed three consecutive probes'
      try {
        if (-not $p.HasExited) { $p.Kill() }
      } catch {
        Write-SupervisorState -State 'RUNNING' -ChildPid $p.Id -LastError ("Bridge stop failed: " + $_.Exception.Message)
      }
      # Wait for this exact child to exit before the outer loop can start another.
    }
    $exitCode = $null
    try { $exitCode = $p.ExitCode } catch {}
    Write-SupervisorState -State 'RESTARTING' -LastError "bridge exited code=$exitCode"
  } catch {
    Write-SupervisorState -State 'RESTARTING' -LastError ([string]$_.Exception.Message)
  }
  Start-Sleep -Seconds 2
}
