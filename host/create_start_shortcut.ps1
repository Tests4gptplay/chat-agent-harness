param(
 [string]$RepoRoot=(Split-Path -Parent $PSScriptRoot),
 [string]$Destination=(Split-Path -Parent $PSScriptRoot),
 [string]$IconPath=''
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path -LiteralPath $RepoRoot).Path
$bat=Join-Path $repo 'Start_CAH.bat'
if(-not (Test-Path -LiteralPath $bat)){throw 'CAH launcher not found'}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$shell=New-Object -ComObject WScript.Shell
$link=Join-Path $Destination 'Start CAH.lnk'
$shortcut=$shell.CreateShortcut($link)
$shortcut.TargetPath=$bat
$shortcut.WorkingDirectory=$repo
$shortcut.Description='Start Chat Agent Harness'
if($IconPath){
 if(-not (Test-Path -LiteralPath $IconPath)){throw 'Optional icon not found'}
 $shortcut.IconLocation=$IconPath
}
$shortcut.Save()
Write-Output "Created: $link"
