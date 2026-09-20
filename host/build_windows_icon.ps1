param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination,
    [int[]]$Sizes = @(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$srcPath = (Resolve-Path $Source).Path
$destPath = [IO.Path]::GetFullPath($Destination)
$destDir = Split-Path -Parent $destPath
New-Item -ItemType Directory -Force -Path $destDir | Out-Null

$normalizedSizes = @(
    $Sizes |
        Where-Object { $_ -ge 16 -and $_ -le 256 } |
        Sort-Object -Unique
)
if ($normalizedSizes.Count -eq 0) {
    throw "At least one icon size between 16 and 256 is required."
}

$sourceImage = [System.Drawing.Image]::FromFile($srcPath)
$tempFiles = New-Object System.Collections.Generic.List[string]
$frames = New-Object System.Collections.Generic.List[object]

try {
    foreach ($size in $normalizedSizes) {
        $canvas = [System.Drawing.Bitmap]::new(
            $size,
            $size,
            [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
        )
        $graphics = [System.Drawing.Graphics]::FromImage($canvas)
        $tempPng = Join-Path $destDir (([IO.Path]::GetRandomFileName()) + ".png")
        $tempFiles.Add($tempPng) | Out-Null

        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

            $scale = [Math]::Min($size / [double]$sourceImage.Width, $size / [double]$sourceImage.Height)
            $drawWidth = [int][Math]::Round($sourceImage.Width * $scale)
            $drawHeight = [int][Math]::Round($sourceImage.Height * $scale)
            $x = [int][Math]::Floor(($size - $drawWidth) / 2)
            $y = [int][Math]::Floor(($size - $drawHeight) / 2)
            $destRect = [System.Drawing.Rectangle]::new($x, $y, $drawWidth, $drawHeight)

            $graphics.DrawImage($sourceImage, $destRect)
            $graphics.Flush()

            # Each ICO frame is a lossless PNG. Keeping native 16/20/24/32/40/48
            # frames prevents Explorer from crudely scaling one 256px frame down.
            $canvas.Save($tempPng, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $graphics.Dispose()
            $canvas.Dispose()
        }

        $pngBytes = [IO.File]::ReadAllBytes($tempPng)
        $frames.Add([pscustomobject]@{
            Size = [int]$size
            Bytes = $pngBytes
        }) | Out-Null
    }

    $stream = [IO.File]::Create($destPath)
    $writer = [IO.BinaryWriter]::new($stream)
    try {
        $writer.Write([UInt16]0)                 # ICONDIR.reserved
        $writer.Write([UInt16]1)                 # ICONDIR.type = icon
        $writer.Write([UInt16]$frames.Count)     # ICONDIR.count

        $offset = 6 + (16 * $frames.Count)
        foreach ($frame in $frames) {
            $dimension = if ($frame.Size -eq 256) { [Byte]0 } else { [Byte]$frame.Size }
            $writer.Write($dimension)
            $writer.Write($dimension)
            $writer.Write([Byte]0)               # color count
            $writer.Write([Byte]0)               # reserved
            $writer.Write([UInt16]1)             # planes
            $writer.Write([UInt16]32)            # bit depth
            $writer.Write([UInt32]$frame.Bytes.Length)
            $writer.Write([UInt32]$offset)
            $offset += $frame.Bytes.Length
        }

        foreach ($frame in $frames) {
            $writer.Write($frame.Bytes)
        }
    } finally {
        $writer.Dispose()
        $stream.Dispose()
    }
} finally {
    $sourceImage.Dispose()
    foreach ($tempFile in $tempFiles) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Built lossless multi-resolution Windows icon: $destPath"
Write-Host ("Frames: " + (($normalizedSizes | ForEach-Object { "$($_)x$($_)" }) -join ", "))
