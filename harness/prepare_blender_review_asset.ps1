param(
  [Parameter(Mandatory=$true)][string]$TaskId,
  [Parameter(Mandatory=$true)][int]$Iteration
)

$ErrorActionPreference = 'Stop'
if ($TaskId -notmatch '^[A-Za-z0-9._-]{3,256}$') { throw 'Unsafe task id' }
if ($Iteration -lt 1 -or $Iteration -gt 99) { throw 'Iteration out of range' }

function Assert-SafeRepoPath([string]$p) {
  if ([string]::IsNullOrWhiteSpace($p)) { throw 'empty repository path' }
  if ([IO.Path]::IsPathRooted($p) -or $p -match '(^|[\\/])\.\.([\\/]|$)') { throw "unsafe repository path: $p" }
  $root = [IO.Path]::GetFullPath((Get-Location).Path)
  $full = [IO.Path]::GetFullPath((Join-Path $root $p))
  if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "path escapes repository: $p" }
  return $full
}

Add-Type -AssemblyName System.Drawing

$iterName = ('iter_{0:d2}' -f $Iteration)
$baseRel = "cases/$TaskId/camera"
$outDirRel = "$baseRel/review_assets/$iterName"
$outDir = Assert-SafeRepoPath $outDirRel
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$chunkDir = Join-Path $outDir 'chunks'
New-Item -ItemType Directory -Force -Path $chunkDir | Out-Null

$sources = @(
  [ordered]@{ label='FINAL'; ref="$baseRel/renders/$iterName.png" },
  [ordered]@{ label='BLOCKOUT'; ref="$baseRel/stages/$iterName/01_blockout.png" },
  [ordered]@{ label='MATERIALS'; ref="$baseRel/stages/$iterName/04_materials.png" },
  [ordered]@{ label='LIGHTING'; ref="$baseRel/stages/$iterName/05_lighting.png" }
)

$tileW = 256
$tileH = 256
$labelH = 28
$count = $sources.Count
$sheet = New-Object System.Drawing.Bitmap($($tileW * $count), $($tileH + $labelH))
$g = [System.Drawing.Graphics]::FromImage($sheet)
$g.Clear([System.Drawing.Color]::FromArgb(232,232,232))
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$font = New-Object System.Drawing.Font('Arial', 11, [System.Drawing.FontStyle]::Bold)
$brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(35,35,35))
$records = @()

try {
  for ($i=0; $i -lt $count; $i++) {
    $src = $sources[$i]
    $srcPath = Assert-SafeRepoPath ([string]$src.ref)
    if (-not (Test-Path $srcPath)) { throw "source missing: $($src.ref)" }
    $img = [System.Drawing.Image]::FromFile($srcPath)
    try {
      $scale = [Math]::Min($tileW / [double]$img.Width, $tileH / [double]$img.Height)
      $w = [Math]::Max(1, [int][Math]::Round($img.Width * $scale))
      $h = [Math]::Max(1, [int][Math]::Round($img.Height * $scale))
      $x = ($i * $tileW) + [int](($tileW - $w) / 2)
      $y = $labelH + [int](($tileH - $h) / 2)
      $g.DrawImage($img, $x, $y, $w, $h)
      $g.DrawString([string]$src.label, $font, $brush, ($i * $tileW + 6), 5)
      $records += [ordered]@{
        ref = [string]$src.ref
        label = [string]$src.label
        sha256 = (Get-FileHash -Algorithm SHA256 $srcPath).Hash.ToLowerInvariant()
        width = $img.Width
        height = $img.Height
      }
    } finally { $img.Dispose() }
  }
} finally {
  $g.Dispose()
  $font.Dispose()
  $brush.Dispose()
}

$jpgRel = "$outDirRel/contact_sheet.jpg"
$jpg = Assert-SafeRepoPath $jpgRel
$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
if ($null -eq $codec) { throw 'JPEG encoder unavailable' }
$enc = New-Object System.Drawing.Imaging.EncoderParameters(1)
$enc.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]84)
try { $sheet.Save($jpg, $codec, $enc) } finally { $sheet.Dispose(); $enc.Dispose() }

$bytes = [IO.File]::ReadAllBytes($jpg)
$b64 = [Convert]::ToBase64String($bytes)
$chunkSize = 32000
$chunkRefs = @()
Get-ChildItem $chunkDir -Filter 'chunk_*.txt' -ErrorAction SilentlyContinue | Remove-Item -Force
for ($offset=0; $offset -lt $b64.Length; $offset += $chunkSize) {
  $len = [Math]::Min($chunkSize, $b64.Length - $offset)
  $idx = [int]($offset / $chunkSize)
  $name = ('chunk_{0:d3}.txt' -f $idx)
  $rel = "$outDirRel/chunks/$name"
  [IO.File]::WriteAllText((Assert-SafeRepoPath $rel), $b64.Substring($offset,$len), [Text.Encoding]::ASCII)
  $chunkRefs += $rel
}

$packetRel = "$outDirRel/review_packet.json"
$packet = [ordered]@{
  v = 1
  task_id = $TaskId
  iteration = $Iteration
  created_at = (Get-Date).ToUniversalTime().ToString('o')
  purpose = 'Compact visual transport for managed Worker review; original PNGs remain canonical visual evidence.'
  contact_sheet = [ordered]@{
    ref = $jpgRel
    sha256 = (Get-FileHash -Algorithm SHA256 $jpg).Hash.ToLowerInvariant()
    size_bytes = (Get-Item $jpg).Length
    width = $tileW * $count
    height = $tileH + $labelH
    jpeg_quality = 84
    base64_encoding = 'concatenate chunks in listed order, base64-decode to JPEG'
    chunks = $chunkRefs
  }
  sources = $records
}
$packet | ConvertTo-Json -Depth 8 | Set-Content (Assert-SafeRepoPath $packetRel) -Encoding utf8

[ordered]@{
  ok = $true
  task_id = $TaskId
  iteration = $Iteration
  attachment_ref = $jpgRel
  packet_ref = $packetRel
  output_dir = $outDirRel
  sha256 = $packet.contact_sheet.sha256
  size_bytes = $packet.contact_sheet.size_bytes
} | ConvertTo-Json -Compress
