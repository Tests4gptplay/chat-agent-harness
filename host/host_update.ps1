param(
  [string]$TargetRepo = (Split-Path -Parent $PSScriptRoot),
  [string]$Branch = 'main',
  [string]$ExpectedExtensionVersion = '1.0.4',
  [string]$BridgeRoot = (Join-Path $env:LOCALAPPDATA 'CAH'),
  [int]$BridgePort = 8765,
  [int]$ProbePort = 8766,
  [bool]$AllowChromeRestart = $false,
  [bool]$CreateDesktopShortcut = $false,
  [string]$EvidenceOut
)

$ErrorActionPreference = 'Stop'
$started = (Get-Date).ToUniversalTime().ToString('o')
$result = [ordered]@{
  v = 1
  kind = 'host_update'
  started_at = $started
  completed_at = $null
  overall = 'RUNNING'
  target_repo = $TargetRepo
  branch = $Branch
  head_before = $null
  head_after = $null
  repo_update = 'PENDING'
  extension_build = 'PENDING'
  extension_built_version = $null
  bridge_probe = 'PENDING'
  bridge_restart = 'PENDING'
  bridge_health = $null
  extension_reload = 'PENDING'
  extension_live_version = $null
  extension_reload_detail = $null
  desktop_shortcut = 'SKIPPED'
  desktop_shortcut_path = $null
  cdp_ports = @()
  errors = @()
}

function Save-Evidence {
  if ([string]::IsNullOrWhiteSpace($EvidenceOut)) { return }
  $parent = Split-Path -Parent $EvidenceOut
  if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  $result.completed_at = (Get-Date).ToUniversalTime().ToString('o')
  $result | ConvertTo-Json -Depth 8 | Set-Content -Path $EvidenceOut -Encoding utf8
}

function Bridge-Post([hashtable]$Body, [int]$TimeoutSec = 5) {
  $json = $Body | ConvertTo-Json -Depth 8 -Compress
  return Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/api" -f $BridgePort) -Method Post `
    -Headers @{ 'X-GAH-Bridge' = '1' } -ContentType 'application/json' -Body $json -TimeoutSec $TimeoutSec
}

function Wait-ExtensionVersion([string]$Expected, [int]$Seconds = 30) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    try {
      $status = Bridge-Post @{
        op = 'extension_runtime_status'
        client_id = 'host-update-client-001'
        project_id = 'git-agent-harness'
      } 3
      if ($status.status -and [string]$status.status.version -eq $Expected) {
        if (-not $status.control -or [string]$status.control.status -eq 'DONE') { return $status }
      }
    } catch {}
    Start-Sleep -Milliseconds 750
  } while ((Get-Date) -lt $deadline)
  return $null
}

function Request-ExtensionReload([string]$RequestId, [string]$Expected) {
  return Bridge-Post @{
    op = 'extension_reload_request'
    client_id = 'host-update-client-001'
    project_id = 'git-agent-harness'
    request_id = $RequestId
    expected_version = $Expected
  } 5
}

function Restart-ChromeForExtensionLoad {
  $chromePath = $null
  try {
    $chrome = Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
    if ($chrome) { $chromePath = $chrome.Path }
  } catch {}
  if ([string]::IsNullOrWhiteSpace($chromePath)) {
    $candidates = @(
      (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
      (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
      (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($candidate in $candidates) {
      if ($candidate -and (Test-Path $candidate)) { $chromePath = $candidate; break }
    }
  }
  if ([string]::IsNullOrWhiteSpace($chromePath)) { throw 'Chrome executable not found for authorized restart' }
  Start-Process -FilePath $chromePath -ArgumentList @('chrome://restart') | Out-Null
}
function Install-BridgeSupervisor {
  $pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
  $script = Join-Path $TargetRepo 'host\bridge_supervisor.ps1'
  if (-not (Test-Path $script)) { throw "Bridge supervisor script missing: $script" }

  $startup = [Environment]::GetFolderPath('Startup')
  if ([string]::IsNullOrWhiteSpace($startup)) { throw 'Windows Startup folder unavailable' }
  New-Item -ItemType Directory -Force -Path $startup | Out-Null
  $startupCmd = Join-Path $startup 'CAH-Bridge.cmd'
  $cmdLine = '"' + $pwsh + '" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '" -TargetRepo "' + $TargetRepo + '" -BridgeRoot "' + $BridgeRoot + '" -Port ' + $BridgePort + ' -Branch "' + $Branch + '"'
  ('@echo off' + [Environment]::NewLine + 'start "" /min ' + $cmdLine + [Environment]::NewLine) | Set-Content -Path $startupCmd -Encoding ascii

  # Remove only the previous entry for this exact installation, after writing its replacement.
  $legacyStartup = Join-Path $startup 'GAH-Bridge.cmd'
  if (Test-Path -LiteralPath $legacyStartup) {
    $legacyText = Get-Content -LiteralPath $legacyStartup -Raw
    if ($legacyText.Contains($TargetRepo) -and $legacyText.Contains('bridge_supervisor.ps1')) { Remove-Item -LiteralPath $legacyStartup -Force }
  }

  $supervisorState = Join-Path $BridgeRoot 'runtime\bridge-supervisor.json'
  if (Test-Path $supervisorState) {
    try {
      $old = Get-Content $supervisorState -Raw | ConvertFrom-Json
      if ($old.supervisor_pid) { Stop-Process -Id ([int]$old.supervisor_pid) -Force -ErrorAction SilentlyContinue }
    } catch {}
  }

  $launch = 'cmd.exe /d /c start "" /min ' + $cmdLine
  $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $launch }
  if ([int]$created.ReturnValue -ne 0) { throw "Win32_Process.Create failed code=$($created.ReturnValue)" }
}

function Wait-Health([int]$Port, [int]$Seconds = 20) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    try {
      $value = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method Get -TimeoutSec 2
      if ($value.ok -and $value.service -eq 'gah-local-wake-bridge') { return $value }
    } catch {}
    Start-Sleep -Milliseconds 400
  } while ((Get-Date) -lt $deadline)
  throw "Bridge health check failed on port $Port"
}

function Start-Bridge([int]$Port, [string]$Root, [string]$Tag) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root 'logs') | Out-Null
  $python = (& py -c "import sys; print(sys.executable)").Trim()
  if (-not (Test-Path $python)) { throw "Python executable not found: $python" }
  $server = Join-Path $TargetRepo 'local_bridge\server.py'
  $stdout = Join-Path $Root "logs\bridge-$Tag.stdout.log"
  $stderr = Join-Path $Root "logs\bridge-$Tag.stderr.log"

  $tracking = $env:RUNNER_TRACKING_ID
  Remove-Item Env:RUNNER_TRACKING_ID -ErrorAction SilentlyContinue
  try {
    $p = Start-Process -FilePath $python -ArgumentList @(
      $server,
      '--host', '127.0.0.1',
      '--port', "$Port",
      '--root', $Root,
      '--repo-root', $TargetRepo,
      '--git-branch', $Branch
    ) -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  } finally {
    if ($null -ne $tracking) { $env:RUNNER_TRACKING_ID = $tracking }
  }
  return $p
}

try {
  if (-not (Test-Path (Join-Path $TargetRepo '.git'))) {
    throw "Target repository not found: $TargetRepo"
  }

  $dirty = @(& git -C $TargetRepo status --porcelain)
  # Launcher ICOs are deterministic local derivatives of tracked PNG artwork.
  # They may remain after shortcut refreshes and must not block a later CAH self-update.
  $allowedDerived = @(
    '?? assets/brand/icons/gah-start-launcher.ico',
    '?? assets/brand/icons/cah-start-launcher-hq.ico'
  )
  $blockingDirty = @($dirty | Where-Object { $_ -notin $allowedDerived })
  if ($blockingDirty.Count -gt 0) {
    $result.repo_update = 'BLOCKED_DIRTY_TREE'
    $result.errors += ('dirty: ' + ($blockingDirty -join '; '))
    throw "Target repository working tree is dirty; refusing self-update"
  }

  $currentBranch = (& git -C $TargetRepo rev-parse --abbrev-ref HEAD).Trim()
  if ($currentBranch -ne $Branch) {
    $result.repo_update = 'BLOCKED_WRONG_BRANCH'
    throw "Target repository branch is '$currentBranch', expected '$Branch'"
  }

  $result.head_before = (& git -C $TargetRepo rev-parse HEAD).Trim()
  & git -C $TargetRepo fetch --quiet --no-tags origin $Branch
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
  $remoteHead = (& git -C $TargetRepo rev-parse FETCH_HEAD).Trim()
  if ([string]::IsNullOrWhiteSpace($remoteHead)) { throw "FETCH_HEAD missing after fetch" }

  & git -C $TargetRepo merge-base --is-ancestor $result.head_before $remoteHead
  if ($LASTEXITCODE -ne 0) {
    $result.repo_update = 'BLOCKED_NON_FF'
    throw "Local HEAD is not an ancestor of fetched origin/$Branch; refusing non-fast-forward update"
  }

  & git -C $TargetRepo merge --ff-only $remoteHead
  if ($LASTEXITCODE -ne 0) { throw "git merge --ff-only failed" }
  $result.head_after = (& git -C $TargetRepo rev-parse HEAD).Trim()
  $result.repo_update = 'PASS'

  & py (Join-Path $TargetRepo 'extension\build.py') chromium
  if ($LASTEXITCODE -ne 0) { throw "extension build failed" }
  $manifestPath = Join-Path $TargetRepo 'extension\dist\chromium\manifest.json'
  $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
  $result.extension_built_version = [string]$manifest.version
  $result['extension_built_name'] = [string]$manifest.name
  $result['readme_brand'] = (Get-Content (Join-Path $TargetRepo 'README.md') -Raw).Contains('# Chat Agent Harness (CAH)')

  try {
    $statusBody = @{ op='extension_runtime_status'; client_id='host-update-version-guard'; project_id='git-agent-harness' } | ConvertTo-Json
    $runtimeStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$BridgePort/api" -Method Post -Headers @{ 'X-GAH-Bridge'='1' } -ContentType 'application/json' -Body $statusBody -TimeoutSec 3
    $liveVersion = [string]$runtimeStatus.status.version
    if (-not [string]::IsNullOrWhiteSpace($liveVersion)) {
      try {
        $builtV = [version]$result.extension_built_version
        $liveV = [version]$liveVersion
        if ($builtV -lt $liveV) {
          throw "Refusing extension runtime downgrade: live=$liveVersion built=$($result.extension_built_version)"
        }
      } catch [System.Management.Automation.RuntimeException] {
        throw
      } catch {
        # If versions are non-standard, do not silently infer an ordering.
      }
    }
  } catch {
    if ([string]$_.Exception.Message -like 'Refusing extension runtime downgrade:*') { throw }
  }

  if ($result.extension_built_version -ne $ExpectedExtensionVersion) {
    throw "Built extension version $($result.extension_built_version) does not match expected $ExpectedExtensionVersion"
  }
  $result.extension_build = 'PASS'

  $probeRoot = Join-Path $BridgeRoot 'host-update-probe'
  if (Test-Path $probeRoot) { Remove-Item -Recurse -Force $probeRoot }
  $probe = Start-Bridge -Port $ProbePort -Root $probeRoot -Tag 'probe'
  try {
    $probeHealth = Wait-Health -Port $ProbePort -Seconds 20
    $result.bridge_probe = 'PASS'
  } finally {
    if ($probe -and -not $probe.HasExited) {
      Stop-Process -Id $probe.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $probeRoot -ErrorAction SilentlyContinue
  }

  $runtimeFile = Join-Path $BridgeRoot 'runtime\bridge.json'
  if (Test-Path $runtimeFile) {
    try {
      $oldRuntime = Get-Content $runtimeFile -Raw | ConvertFrom-Json
      $oldPid = [int]$oldRuntime.pid
      if ($oldPid -gt 0) {
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 600
      }
    } catch {}
  }

  Install-BridgeSupervisor
  $health = Wait-Health -Port $BridgePort -Seconds 30
  $result.bridge_restart = 'PASS'
  $result.bridge_health = $health

  $ports = @()
  try {
    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'")) {
      $cmd = [string]$proc.CommandLine
      if ($cmd -match '--remote-debugging-port(?:=|\s+)(\d+)') {
        $p = [int]$Matches[1]
        if ($p -gt 0 -and $p -lt 65536) { $ports += $p }
      }
    }
  } catch {}
  $ports = @($ports | Sort-Object -Unique)
  $result.cdp_ports = $ports

  $reloadRequestId = 'extension-reload-' + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  try {
    Request-ExtensionReload -RequestId $reloadRequestId -Expected $ExpectedExtensionVersion | Out-Null
  } catch {
    $result.extension_reload_detail = 'native reload request failed: ' + [string]$_.Exception.Message
  }

  $nativeReady = Wait-ExtensionVersion -Expected $ExpectedExtensionVersion -Seconds 75
  if ($nativeReady) {
    $result.extension_reload = 'PASS'
    $result.extension_live_version = $ExpectedExtensionVersion
    $result.extension_reload_detail = $nativeReady
  }

  if ($result.extension_reload -ne 'PASS' -and $ports.Count -gt 0) {
    $helper = Join-Path $TargetRepo 'host\reload_extension_cdp.py'
    foreach ($port in $ports) {
      $raw = & py $helper --port $port --name ([string]$manifest.name) --expected-version $ExpectedExtensionVersion --discover-timeout 30 2>&1
      $exit = $LASTEXITCODE
      $text = ($raw | Out-String).Trim()
      if ($exit -eq 0) {
        try {
          $value = $text | ConvertFrom-Json
          if ($value.ok) {
            $result.extension_reload = 'PASS'
            $result.extension_live_version = [string]$value.after_version
            $result.extension_reload_detail = $value
            break
          }
        } catch {}
      } else {
        $result.extension_reload_detail = "CDP port ${port}: $text"
      }
    }
  }

  if ($result.extension_reload -ne 'PASS' -and $AllowChromeRestart) {
    Restart-ChromeForExtensionLoad
    $afterRestart = Wait-ExtensionVersion -Expected $ExpectedExtensionVersion -Seconds 90
    if ($afterRestart) {
      $result.extension_reload = 'PASS'
      $result.extension_live_version = $ExpectedExtensionVersion
      $result.extension_reload_detail = @{ method = 'chrome_restart_then_native_hello'; runtime = $afterRestart }
    } else {
      $result.extension_reload = 'BLOCKED_CHROME_RESTART_NO_HELLO'
      $result.extension_reload_detail = 'Chrome restart was requested but extension runtime hello with expected version was not observed within 90 seconds.'
    }
  } elseif ($result.extension_reload -ne 'PASS') {
    if ($ports.Count -eq 0) { $result.extension_reload = 'BLOCKED_NO_CDP' } else { $result.extension_reload = 'BLOCKED_CDP_RELOAD_FAILED' }
    if (-not $result.extension_reload_detail) {
      $result.extension_reload_detail = 'Live extension did not self-report the built version and browser restart was not authorized.'
    }
  }
  if ($CreateDesktopShortcut) {
    try {
      $desktop = [Environment]::GetFolderPath('Desktop')
      if ([string]::IsNullOrWhiteSpace($desktop)) { throw 'Windows Desktop folder unavailable' }
      $shortcutScript = Join-Path $TargetRepo 'host\create_start_shortcut.ps1'
      if (-not (Test-Path $shortcutScript)) { throw "Shortcut helper missing: $shortcutScript" }
      & $shortcutScript -RepoRoot $TargetRepo -Destination $desktop
      if ($LASTEXITCODE -ne 0) { throw "Shortcut helper exit $LASTEXITCODE" }
      $shortcutPath = Join-Path $desktop 'Start CAH.lnk'
      if (-not (Test-Path $shortcutPath)) { throw "Desktop shortcut not created: $shortcutPath" }
      $result.desktop_shortcut = 'PASS'
      $result.desktop_shortcut_path = $shortcutPath
    } catch {
      $result.desktop_shortcut = 'ERROR'
      $result.errors += ('desktop_shortcut: ' + [string]$_.Exception.Message)
    }
  }

  $desktopOk = (-not $CreateDesktopShortcut) -or ($result.desktop_shortcut -eq 'PASS')
  if ($result.repo_update -eq 'PASS' -and
      $result.extension_build -eq 'PASS' -and
      $result.bridge_probe -eq 'PASS' -and
      $result.bridge_restart -eq 'PASS' -and
      $result.extension_reload -eq 'PASS' -and
      $desktopOk) {
    $result.overall = 'PASS'
  } elseif ($result.repo_update -eq 'PASS' -and
            $result.extension_build -eq 'PASS' -and
            $result.bridge_probe -eq 'PASS' -and
            $result.bridge_restart -eq 'PASS') {
    $result.overall = 'PARTIAL'
  } else {
    $result.overall = 'ERROR'
  }
} catch {
  $result.errors += [string]$_.Exception.Message
  if ($result.overall -eq 'RUNNING') { $result.overall = 'ERROR' }
} finally {
  Save-Evidence
}

$result | ConvertTo-Json -Depth 8
if ($result.overall -eq 'ERROR') { exit 1 }
exit 0
