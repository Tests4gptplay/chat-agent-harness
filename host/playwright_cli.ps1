param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Session,
    [string]$ToolsRoot = '__CAH_TOOLS_ROOT__',
    [string]$WorkDir = '',
    [string]$CdpUrl = 'http://127.0.0.1:9222',
    [switch]$Attach,
    [string[]]$CliArgs = @('--help')
)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $ToolsRoot 'runtime.json') -Raw | ConvertFrom-Json
if (-not $WorkDir) { $WorkDir = Join-Path $config.workspace "browser-cli\$Session" }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$cli = Join-Path $ToolsRoot 'node_modules\@playwright\cli\playwright-cli.js'
Push-Location $WorkDir
try {
    if ($Attach) { $CliArgs = @('attach',"--cdp=$CdpUrl") }
    & $config.node $cli "-s=$Session" @CliArgs
    exit $LASTEXITCODE
} finally { Pop-Location }
