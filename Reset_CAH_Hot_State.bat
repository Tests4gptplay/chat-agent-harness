@echo off
setlocal EnableExtensions
cd /d "%~dp0"
py installation\preflight.py
if errorlevel 1 exit /b %ERRORLEVEL%

set "STATE=state\chatgpt.json"
set "LANES=state\lanes.json"
set "BROWSER_ROOT=__CAH_BROWSER_ROOT__"
set "HOST_STATE=__CAH_BROWSER_ROOT__\state.json"
set "BRIDGE_ROOT=__CAH_BRIDGE_ROOT__"

for /f "usebackq delims=" %%B in (`git branch --show-current`) do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="main" (
  echo ERROR: Reset must run from branch main. Current branch: %CURRENT_BRANCH%
  exit /b 2
)

git fetch --quiet --no-tags origin main
if errorlevel 1 exit /b %ERRORLEVEL%
git reset --hard origin/main
if errorlevel 1 exit /b %ERRORLEVEL%

if not exist "%STATE%" (
  echo ERROR: %STATE% not found.
  exit /b 1
)
if not exist "%LANES%" (
  echo ERROR: %LANES% not found.
  exit /b 1
)

for /f "usebackq delims=" %%T in (`powershell.exe -NoLogo -NoProfile -Command "$s=Get-Content -Raw -Encoding UTF8 -LiteralPath '%STATE%' | ConvertFrom-Json; if($s.active_task){[Console]::Write($s.active_task)}"`) do set "TASK_ID=%%T"

if defined TASK_ID (
  echo Reset target active task: %TASK_ID%
) else (
  echo Canonical hot state has no active task; stale runtime cleanup will still run.
)

echo [1/7] Stopping only the native Playwright Host so in-memory state cannot rewrite stale cache...
powershell.exe -NoLogo -NoProfile -Command ^
  "$root='%BROWSER_ROOT%';" ^
  "$hosts=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {" ^
  "  [string]$_.CommandLine -match '(?:^|\s)-m\s+playwright_host(?:\s|$)' -and" ^
  "  [string]$_.CommandLine -match ('--runtime-root\s+\"?' + [regex]::Escape($root) + '\"?(?:\s|$)')" ^
  "});" ^
  "foreach($p in $hosts){Stop-Process -Id ([int]$p.ProcessId) -Force};" ^
  "$deadline=(Get-Date).AddSeconds(15);" ^
  "do{Start-Sleep -Milliseconds 250; $left=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {" ^
  "  [string]$_.CommandLine -match '(?:^|\s)-m\s+playwright_host(?:\s|$)' -and" ^
  "  [string]$_.CommandLine -match ('--runtime-root\s+\"?' + [regex]::Escape($root) + '\"?(?:\s|$)')" ^
  "})}while($left.Count -gt 0 -and (Get-Date) -lt $deadline);" ^
  "if($left.Count -gt 0){throw 'Native Playwright Host did not stop cleanly.'};" ^
  "[Console]::WriteLine(('Stopped native Host count: ' + $hosts.Count))"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [2/7] Clearing semantic ChatGPT history from dedicated CAH projects via official Playwright CLI...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\host\reset_cah_semantics_cli.ps1" -RepoRoot "%CD%"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [3/7] Backing up and clearing browser-local CAH ownership state...
powershell.exe -NoLogo -NoProfile -Command ^
  "$p='%HOST_STATE%';" ^
  "if(Test-Path -LiteralPath $p){" ^
  "  $root=Split-Path -Parent $p;" ^
  "  $backupDir=Join-Path $root 'reset-backups';" ^
  "  New-Item -ItemType Directory -Force -Path $backupDir | Out-Null;" ^
  "  $backup=Join-Path $backupDir ('state-before-reset-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff') + '.json');" ^
  "  Copy-Item -LiteralPath $p -Destination $backup -Force;" ^
  "  $s=Get-Content -Raw -Encoding UTF8 -LiteralPath $p | ConvertFrom-Json;" ^
  "  foreach($lp in @($s.lanes.PSObject.Properties)){" ^
  "    if($null -ne $lp.Value){$lp.Value.task_pools=[pscustomobject]@{}}" ^
  "  };" ^
  "  if($null -ne $s.task_cell){" ^
  "    $s.task_cell.roles=[pscustomobject]@{};" ^
  "    $s.task_cell.planner_successors=[pscustomobject]@{}" ^
  "  };" ^
  "  $json=$s | ConvertTo-Json -Depth 100;" ^
  "  $tmp=$p + '.reset.tmp';" ^
  "  [System.IO.File]::WriteAllText($tmp,$json+[Environment]::NewLine,(New-Object System.Text.UTF8Encoding($false)));" ^
  "  Move-Item -LiteralPath $tmp -Destination $p -Force;" ^
  "  [Console]::WriteLine(('Local Host state backup: ' + $backup))" ^
  "}else{" ^
  "  [Console]::WriteLine('Local Host state does not exist; startup will create a clean state file.')" ^
  "}"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [4/7] Clearing disposable pending/claimed wake transport so old wakes cannot recreate pools...
powershell.exe -NoLogo -NoProfile -Command ^
  "$wake=Join-Path '%BRIDGE_ROOT%' 'wake';" ^
  "$removed=0;" ^
  "foreach($name in @('inbox','claimed')){" ^
  "  $dir=Join-Path $wake $name;" ^
  "  if(Test-Path -LiteralPath $dir){" ^
  "    $files=@(Get-ChildItem -LiteralPath $dir -File -Filter '*.json' -ErrorAction SilentlyContinue);" ^
  "    foreach($f in $files){Remove-Item -LiteralPath $f.FullName -Force; $removed++}" ^
  "  }" ^
  "};" ^
  "[Console]::WriteLine(('Removed pending/claimed wake files: ' + $removed))"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [5/7] Clearing canonical lane pools and resetting canonical hot state to IDLE...
if defined TASK_ID (
  del /q "tasks\%TASK_ID%.json" 2>nul
  del /q "tasks\%TASK_ID%.plan.json" 2>nul
  del /q "state\task_cells\%TASK_ID%.json" 2>nul
  del /q "memory\planner\%TASK_ID%\current.json" 2>nul
  rmdir "memory\planner\%TASK_ID%" 2>nul
)

powershell.exe -NoLogo -NoProfile -Command ^
  "$p=Join-Path (Get-Location) 'state\lanes.json';" ^
  "$s=Get-Content -Raw -Encoding UTF8 -LiteralPath $p | ConvertFrom-Json;" ^
  "foreach($lane in @($s.lanes)){" ^
  "  if($null -ne $lane){" ^
  "    $lane.task_pools=[pscustomobject]@{};" ^
  "    $lane.status='IDLE'" ^
  "  }" ^
  "};" ^
  "$s.updated_at=[DateTime]::UtcNow.ToString('o');" ^
  "$json=$s | ConvertTo-Json -Depth 100;" ^
  "[System.IO.File]::WriteAllText($p,$json+[Environment]::NewLine,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 exit /b %ERRORLEVEL%

powershell.exe -NoLogo -NoProfile -Command ^
  "$p=Join-Path (Get-Location) 'state\chatgpt.json';" ^
  "$s=Get-Content -Raw -Encoding UTF8 -LiteralPath $p | ConvertFrom-Json;" ^
  "$s.phase='IDLE';" ^
  "$s.active_task=$null;" ^
  "$s.active_action=$null;" ^
  "$s.active_dispatch_ref=$null;" ^
  "$s.handoff_packet_ref=$null;" ^
  "$s.fault_boundary='none';" ^
  "$s.next_reads=@();" ^
  "$s.next_action='Await the next current user task.';" ^
  "$s.writeback_reason='Reset_CAH_Hot_State.bat cleared dedicated CAH project conversations via official Playwright CLI, canonical hot state, lane Worker ownership, browser-local bindings, and pending wake transport.';" ^
  "$s.control_request=$null;" ^
  "$s.updated=(Get-Date).ToString('yyyy-MM-dd');" ^
  "$json=$s | ConvertTo-Json -Depth 100;" ^
  "[System.IO.File]::WriteAllText($p,$json+[Environment]::NewLine,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 exit /b %ERRORLEVEL%

git add -- "state/chatgpt.json" "state/lanes.json"
if defined TASK_ID (
  for %%P in (
    "tasks/%TASK_ID%.json"
    "tasks/%TASK_ID%.plan.json"
    "state/task_cells/%TASK_ID%.json"
    "memory/planner/%TASK_ID%/current.json"
  ) do (
    git ls-files --error-unmatch "%%~P" >nul 2>&1
    if not errorlevel 1 git add -u -- "%%~P"
  )
)

git diff --cached --quiet
if errorlevel 1 (
  if defined TASK_ID (
    git commit -m "Reset CAH hot state %TASK_ID% to clean IDLE [skip ci]"
  ) else (
    git commit -m "Clear stale CAH runtime ownership [skip ci]"
  )
  if errorlevel 1 exit /b %ERRORLEVEL%
  git push origin HEAD:main
  if errorlevel 1 exit /b %ERRORLEVEL%
) else (
  echo Canonical Git state already matches clean IDLE target.
)

echo [6/7] Restarting only the native Playwright Host; Bridge and Runners are not restarted...
powershell.exe -NoLogo -NoProfile -Command ^
  "$repo=(Get-Location).Path;" ^
  "$root='%BROWSER_ROOT%';" ^
  "$launcher=Join-Path $repo 'host\start_playwright_host.ps1';" ^
  "if(-not (Test-Path -LiteralPath $launcher)){throw ('Native Host launcher missing: ' + $launcher)};" ^
  "$stdout=Join-Path $root 'host.stdout.log';" ^
  "$stderr=Join-Path $root 'host.stderr.log';" ^
  "$tracking=$env:RUNNER_TRACKING_ID;" ^
  "Remove-Item Env:RUNNER_TRACKING_ID -ErrorAction SilentlyContinue;" ^
  "try{" ^
  "  $args='-NoProfile -ExecutionPolicy Bypass -File \"' + $launcher + '\" -RepoRoot \"' + $repo + '\" -RuntimeRoot \"' + $root + '\"';" ^
  "  Start-Process powershell.exe -ArgumentList $args -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null" ^
  "}finally{if($null -ne $tracking){$env:RUNNER_TRACKING_ID=$tracking}};" ^
  "$deadline=(Get-Date).AddSeconds(30);" ^
  "do{" ^
  "  Start-Sleep -Milliseconds 400;" ^
  "  $hosts=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {" ^
  "    [string]$_.CommandLine -match '(?:^|\s)-m\s+playwright_host(?:\s|$)' -and" ^
  "    [string]$_.CommandLine -match ('--runtime-root\s+\"?' + [regex]::Escape($root) + '\"?(?:\s|$)')" ^
  "  });" ^
  "  try{$cdp=Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 2}catch{$cdp=$null}" ^
  "}while(($hosts.Count -ne 1 -or $null -eq $cdp) -and (Get-Date) -lt $deadline);" ^
  "if($hosts.Count -ne 1){$tail='';if(Test-Path -LiteralPath $stderr){$tail=(Get-Content -LiteralPath $stderr -Tail 40)-join [Environment]::NewLine};throw ('Native Host did not reach one healthy instance. ' + $tail)};" ^
  "if($null -eq $cdp){throw 'CDP 9222 is unavailable after native Host restart.'};" ^
  "[Console]::WriteLine(('Native Playwright Host restarted: PID ' + $hosts[0].ProcessId))"
if errorlevel 1 exit /b %ERRORLEVEL%

echo [7/7] Verifying clean reset invariants...
powershell.exe -NoLogo -NoProfile -Command ^
  "$repo=(Get-Location).Path;" ^
  "$s=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repo 'state\chatgpt.json') | ConvertFrom-Json;" ^
  "if($s.phase -ne 'IDLE' -or $null -ne $s.active_task -or $null -ne $s.control_request){throw 'Canonical chatgpt hot state is not clean IDLE.'};" ^
  "$l=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repo 'state\lanes.json') | ConvertFrom-Json;" ^
  "foreach($lane in @($l.lanes)){if(@($lane.task_pools.PSObject.Properties).Count -ne 0){throw ('Canonical lane pool residue remains: ' + $lane.lane_id)}};" ^
  "$hp='%HOST_STATE%';" ^
  "if(-not (Test-Path -LiteralPath $hp)){throw 'Playwright Host state.json was not recreated.'};" ^
  "$h=Get-Content -Raw -Encoding UTF8 -LiteralPath $hp | ConvertFrom-Json;" ^
  "foreach($lp in @($h.lanes.PSObject.Properties)){if(@($lp.Value.task_pools.PSObject.Properties).Count -ne 0){throw ('Local Host pool residue remains: ' + $lp.Name)}};" ^
  "if(@($h.task_cell.roles.PSObject.Properties).Count -ne 0){throw 'Local Task Cell role bindings remain.'};" ^
  "if(@($h.task_cell.planner_successors.PSObject.Properties).Count -ne 0){throw 'Local Planner successor bindings remain.'};" ^
  "$wake=Join-Path '%BRIDGE_ROOT%' 'wake';" ^
  "foreach($name in @('inbox','claimed')){$dir=Join-Path $wake $name; if(Test-Path $dir){if(@(Get-ChildItem -LiteralPath $dir -File -Filter '*.json' -ErrorAction SilentlyContinue).Count -ne 0){throw ('Pending wake residue remains in ' + $name)}}};" ^
  "$hosts=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {" ^
  "  [string]$_.CommandLine -match '(?:^|\s)-m\s+playwright_host(?:\s|$)' -and" ^
  "  [string]$_.CommandLine -match ('--runtime-root\s+\"?' + [regex]::Escape('%BROWSER_ROOT%') + '\"?(?:\s|$)')" ^
  "});" ^
  "if($hosts.Count -ne 1){throw ('Expected exactly one native Playwright Host after reset; observed ' + $hosts.Count)};" ^
  "try{$cdp=Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 3}catch{throw 'CDP 9222 is unavailable after reset.'};" ^
  "[Console]::WriteLine('CAH hot state reset core invariants verified: canonical IDLE, zero canonical/local Worker pools, zero Task Cell bindings, zero pending wakes, one Host, CDP available.')"
if errorlevel 1 exit /b %ERRORLEVEL%

echo CAH hot state reset to clean IDLE.
exit /b 0
