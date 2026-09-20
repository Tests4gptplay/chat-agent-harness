param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Destination = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $RepoRoot).Path
$bat = Join-Path $repo "Start_CAH.bat"
$iconSource = Join-Path $repo "assets\brand\icons\cah-start-launcher.png"
# Use a new filename so Windows Explorer cannot reuse the old single-frame ICO cache.
$icon = Join-Path $repo "assets\brand\icons\cah-start-launcher-hq.ico"
$iconBuilder = Join-Path $repo "host\build_windows_icon.ps1"
$iconSizes = @(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

if (-not (Test-Path $bat)) {
    throw "CAH launcher not found: $bat"
}
if (-not (Test-Path $iconSource)) {
    throw "CAH launcher icon source not found: $iconSource"
}
if (-not (Test-Path $iconBuilder)) {
    throw "CAH icon builder not found: $iconBuilder"
}

$needsIcon = -not (Test-Path $icon)
if (-not $needsIcon) {
    $iconTime = (Get-Item $icon).LastWriteTimeUtc
    $needsIcon = ((Get-Item $iconSource).LastWriteTimeUtc -gt $iconTime) -or
                 ((Get-Item $iconBuilder).LastWriteTimeUtc -gt $iconTime)
}
if ($needsIcon) {
    & $iconBuilder -Source $iconSource -Destination $icon -Sizes $iconSizes
    if ($LASTEXITCODE -ne 0) { throw "CAH icon builder exit $LASTEXITCODE" }
}
if (-not (Test-Path $icon)) {
    throw "CAH Windows icon was not created: $icon"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$link = Join-Path $Destination "Start CAH.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $bat
$shortcut.WorkingDirectory = $repo
$shortcut.Description = "Start Chat Agent Harness"
$shortcut.IconLocation = "$icon,0"
$shortcut.Save()

# Retire only the old shortcut belonging to this installation, not arbitrary user links.
$legacyLink = Join-Path $Destination "Start GAH.lnk"
if (Test-Path -LiteralPath $legacyLink) {
    $legacy = $shell.CreateShortcut($legacyLink)
    $oldTarget = Join-Path $repo "Start_GAH.bat"
    if ([string]::Equals($legacy.TargetPath, $oldTarget, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $legacyLink -Force
    }
}

# Recreating the link with a new ICO path normally invalidates Explorer's icon cache.
# Ask Explorer to refresh its icon view as a best-effort final step.
$ie4uinit = Join-Path $env:SystemRoot "System32\ie4uinit.exe"
if (Test-Path $ie4uinit) {
    try {
        & $ie4uinit -show | Out-Null
    } catch {
        Write-Warning "Explorer icon refresh failed: $($_.Exception.Message)"
    }
}

Write-Host "Created: $link"
Write-Host "Icon: $icon"
