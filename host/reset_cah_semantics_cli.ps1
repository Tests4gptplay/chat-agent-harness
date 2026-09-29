param(
    [Parameter(Mandatory=$false)]
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),

    [Parameter(Mandatory=$false)]
    [string]$Session = ('cah-reset-semantics-' + $PID)
)

& py (Join-Path (Split-Path -Parent $PSScriptRoot) 'installation\preflight.py')
if ($LASTEXITCODE -ne 0) { throw 'CAH deployment is not configured' }
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$Cli = Join-Path $RepoRoot 'host\playwright_cli.ps1'
$Template = Join-Path $RepoRoot 'host\reset_cah_semantics_cli.js'
$LanesPath = Join-Path $RepoRoot 'state\lanes.json'

if(-not (Test-Path -LiteralPath $Cli)){ throw "Playwright CLI wrapper missing: $Cli" }
if(-not (Test-Path -LiteralPath $Template)){ throw "Semantic reset CLI template missing: $Template" }
if(-not (Test-Path -LiteralPath $LanesPath)){ throw "Lane topology missing: $LanesPath" }

$targets = @(
    [pscustomobject]@{
        kind='task_cell'
        display_name='CAH Task Cell'
        project_key='g-p-CAHTASKCELLPLACEHOLDER'
        project_root_url='https://chatgpt.com/g/g-p-CAHTASKCELLPLACEHOLDER-cah-task-cell/project'
    }
)

$lanes = Get-Content -Raw -Encoding UTF8 -LiteralPath $LanesPath | ConvertFrom-Json
foreach($lane in @($lanes.lanes)){
    if($null -eq $lane){ continue }
    $key = [string]$lane.project_key
    $url = [string]$lane.project_root_url
    $name = [string]$lane.display_name
    if(-not $key.StartsWith('g-p-')){ throw "Invalid CAH lane project key: $($lane.lane_id)" }
    if(-not $url.StartsWith('https://chatgpt.com/')){ throw "Invalid CAH lane project URL: $($lane.lane_id)" }
    $targets += [pscustomobject]@{
        kind='lane'
        display_name=$name
        project_key=$key
        project_root_url=$url
    }
}

$seen = @{}
$unique = @()
foreach($target in $targets){
    if($seen.ContainsKey($target.project_key)){ continue }
    $seen[$target.project_key] = $true
    $unique += $target
}

$targetsJson = $unique | ConvertTo-Json -Depth 20 -Compress
$templateText = Get-Content -Raw -Encoding UTF8 -LiteralPath $Template
if(-not $templateText.Contains('__CAH_TARGETS_JSON__')){
    throw 'Semantic reset CLI template placeholder is missing.'
}
$code = $templateText.Replace('__CAH_TARGETS_JSON__', $targetsJson)
$tempCode = Join-Path $env:TEMP ('cah-reset-semantics-' + [guid]::NewGuid().ToString('N') + '.js')
[System.IO.File]::WriteAllText(
    $tempCode,
    $code,
    (New-Object System.Text.UTF8Encoding($false))
)

& $Cli -Session $Session -Attach
if($LASTEXITCODE -ne 0){
    Remove-Item -LiteralPath $tempCode -Force -ErrorAction SilentlyContinue
    throw 'Playwright CLI attach failed.'
}

try {
    & $Cli -Session $Session -CliArgs @('run-code','--filename',$tempCode)
    if($LASTEXITCODE -ne 0){ throw 'Playwright CLI semantic reset failed.' }
} finally {
    try { & $Cli -Session $Session -CliArgs @('detach') } catch {}
    Remove-Item -LiteralPath $tempCode -Force -ErrorAction SilentlyContinue
}
