param(
    [string]$ToolsRoot = '__CAH_TOOLS_ROOT__',
    [string]$Workspace = '__CAH_WORK_ROOT__',
    [string]$NodePath = 'node',
    [string]$NpmCli = ''
)
& py (Join-Path (Split-Path -Parent $PSScriptRoot) 'installation\preflight.py')
if ($LASTEXITCODE -ne 0) { throw 'CAH deployment is not configured' }
$ErrorActionPreference = 'Stop'
$node = (Get-Command $NodePath -ErrorAction Stop).Source
if ([int]((& $node --version).TrimStart('v').Split('.')[0]) -lt 20) { throw 'Pinned Playwright dependencies require Node.js 20 or newer.' }
New-Item -ItemType Directory -Force -Path $ToolsRoot,$Workspace | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'playwright-tools\package.json'),(Join-Path $PSScriptRoot 'playwright-tools\package-lock.json') -Destination $ToolsRoot -Force
if ($NpmCli) {
    & $node $NpmCli ci --ignore-scripts --no-audit --no-fund --prefix $ToolsRoot
} else {
    & npm.cmd ci --ignore-scripts --no-audit --no-fund --prefix $ToolsRoot
}
if ($LASTEXITCODE -ne 0) { throw 'Pinned Playwright tools installation failed.' }
$config = @{ node=$node; workspace=[IO.Path]::GetFullPath($Workspace) } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $ToolsRoot 'runtime.json'), $config, (New-Object Text.UTF8Encoding($false)))
& $node (Join-Path $ToolsRoot 'node_modules\@playwright\mcp\cli.js') --version
if ($LASTEXITCODE -ne 0) { throw 'Playwright MCP version check failed.' }
& $node (Join-Path $ToolsRoot 'node_modules\@playwright\cli\playwright-cli.js') --version
if ($LASTEXITCODE -ne 0) { throw 'Playwright CLI version check failed.' }
Write-Output "Installed pinned MCP + CLI in $ToolsRoot. Existing Chrome profile was not modified."
