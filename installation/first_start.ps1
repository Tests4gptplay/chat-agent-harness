param([string]$RepoRoot='__CAH_REPO_ROOT__')
$ErrorActionPreference='Stop'
& py (Join-Path $RepoRoot 'installation\preflight.py')
if($LASTEXITCODE -ne 0){throw 'Configure the operational copy first'}
# Bridge must already be prepared; do not launch a second Host.
& (Join-Path $RepoRoot 'host\start_playwright_host.ps1') -RepoRoot $RepoRoot -RuntimeRoot '__CAH_BROWSER_ROOT__' -ChromePath '__CAH_CHROME_PATH__' -ProfileDir '__CAH_PROFILE_ROOT__' -ForegroundUrl 'https://chatgpt.com/c/CAHFOREGROUNDPLACEHOLDER'
