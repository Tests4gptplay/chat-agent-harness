param(
  [string]$RepoRoot = '',
  [string]$ChromePath = '__CAH_CHROME_PATH__',
  [string]$ProfileDir = "__CAH_PROFILE_ROOT__",
  [int]$CdpPort = 9222,
  [string]$BridgeUrl = 'http://127.0.0.1:8765/api',
  [string]$RuntimeRoot = '__CAH_BROWSER_ROOT__',
  [string]$ForegroundUrl = ''
)

& py (Join-Path (Split-Path -Parent $PSScriptRoot) 'installation\preflight.py')
if ($LASTEXITCODE -ne 0) { throw 'CAH deployment is not configured' }
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

function Read-Cdp {
  try {
    return Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/json/version" -f $CdpPort) -TimeoutSec 2
  } catch {
    return $null
  }
}

if (-not (Read-Cdp)) {
  if (-not (Test-Path -LiteralPath $ChromePath -PathType Leaf)) {
    throw "Chrome not found: $ChromePath"
  }
  Start-Process -FilePath $ChromePath -WindowStyle Minimized -ArgumentList @(
    '--start-minimized',
    '--window-size=1920,1080',
    "--user-data-dir=$ProfileDir",
    '--profile-directory=Default',
    "--remote-debugging-port=$CdpPort",
    '--remote-debugging-address=127.0.0.1',
    '--disable-extensions',
    '--no-first-run',
    '--no-default-browser-check',
    'https://chatgpt.com/'
  ) | Out-Null

  $deadline = (Get-Date).AddSeconds(20)
  do {
    Start-Sleep -Milliseconds 400
    $cdp = Read-Cdp
  } while (-not $cdp -and (Get-Date) -lt $deadline)
  if (-not $cdp) { throw "Chrome CDP $CdpPort did not become ready" }
}

$args = @(
  '-m', 'playwright_host',
  '--bridge-url', $BridgeUrl,
  '--cdp-url', ("http://127.0.0.1:{0}" -f $CdpPort),
  '--runtime-root', $RuntimeRoot
)
if (-not [string]::IsNullOrWhiteSpace($ForegroundUrl)) {
  $args += @('--foreground-url', $ForegroundUrl)
}

Push-Location $RepoRoot
try {
  & py @args
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
