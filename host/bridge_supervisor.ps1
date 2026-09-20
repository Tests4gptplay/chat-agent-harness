param(
  [string]$TargetRepo = (Split-Path -Parent $PSScriptRoot),
  [string]$BridgeRoot = (Join-Path $env:LOCALAPPDATA 'CAH'),
  [int]$Port = 8765,
  [string]$Branch = 'main'
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
    last_error = if ($LastError) { $LastError } else { $null }
    updated_at = (Get-Date).ToUniversalTime().ToString('o')
  } | ConvertTo-Json -Depth 4 | Set-Content -Path $supervisorState -Encoding utf8
}

$python = (& py -c "import sys; print(sys.executable)").Trim()
$server = Join-Path $TargetRepo 'local_bridge\server.py'
Write-SupervisorState -State 'STARTING'

while ($true) {
  try {
    if (-not (Test-Path $server)) {
      Write-SupervisorState -State 'WAITING_FOR_SERVER' -LastError "server missing: $server"
      Start-Sleep -Seconds 3
      continue
    }

    $p = Start-Process -FilePath $python -ArgumentList @(
      $server,
      '--host', '127.0.0.1',
      '--port', "$Port",
      '--root', $BridgeRoot,
      '--repo-root', $TargetRepo,
      '--git-branch', $Branch
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    Write-SupervisorState -State 'RUNNING' -ChildPid $p.Id
    Wait-Process -Id $p.Id
    $exitCode = $null
    try { $exitCode = $p.ExitCode } catch {}
    Write-SupervisorState -State 'RESTARTING' -LastError "bridge exited code=$exitCode"
  } catch {
    Write-SupervisorState -State 'RESTARTING' -LastError ([string]$_.Exception.Message)
  }
  Start-Sleep -Seconds 2
}
