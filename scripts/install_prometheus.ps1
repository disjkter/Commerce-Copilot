param(
    [string]$Version = "3.16.0",
    [string]$InstallDirectory = "E:\ProgramFiles\Prometheus",
    [string]$DownloadDirectory = "E:\ProgramFiles\Downloads"
)

$ErrorActionPreference = "Stop"
$executable = Join-Path $InstallDirectory "prometheus.exe"
if (Test-Path -LiteralPath $executable) {
    Write-Host "Prometheus is already installed: $executable" -ForegroundColor Green
    exit 0
}
if (Test-Path -LiteralPath $InstallDirectory) {
    throw "Install directory already exists but prometheus.exe is missing: $InstallDirectory"
}

$archiveName = "prometheus-$Version.windows-amd64.zip"
$releaseBase = "https://github.com/prometheus/prometheus/releases/download/v$Version"
$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("commerce-prometheus-" + [Guid]::NewGuid().ToString("N"))
$archivePath = Join-Path $DownloadDirectory $archiveName
$checksumsPath = Join-Path $DownloadDirectory "prometheus-$Version-sha256sums.txt"
$extractPath = Join-Path $temporaryRoot "extracted"

try {
    New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DownloadDirectory -Force | Out-Null
    Write-Host "Downloading Prometheus $Version from the official GitHub release..." -ForegroundColor Cyan
    Write-Host "Interrupted downloads resume from: $archivePath" -ForegroundColor DarkGray
    & curl.exe --fail --location --continue-at - --retry 5 --retry-delay 3 `
        --connect-timeout 30 `
        --output $archivePath `
        "$releaseBase/$archiveName"
    if ($LASTEXITCODE -ne 0) {
        throw "Prometheus archive download failed with exit code $LASTEXITCODE"
    }
    & curl.exe --fail --location --retry 5 --retry-delay 3 `
        --connect-timeout 30 `
        --output $checksumsPath `
        "$releaseBase/sha256sums.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "Prometheus checksum download failed with exit code $LASTEXITCODE"
    }

    $checksumLine = Get-Content -LiteralPath $checksumsPath | Where-Object {
        $_ -match [regex]::Escape($archiveName) + "$"
    } | Select-Object -First 1
    if (-not $checksumLine) {
        throw "Checksum entry not found for $archiveName"
    }
    $expectedHash = ($checksumLine -split "\s+")[0].ToLowerInvariant()
    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Prometheus archive checksum mismatch"
    }
    Write-Host "SHA-256 verification passed." -ForegroundColor Green

    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath
    $sourceDirectory = Join-Path $extractPath "prometheus-$Version.windows-amd64"
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDirectory "prometheus.exe"))) {
        throw "Downloaded archive does not contain prometheus.exe"
    }

    $installParent = Split-Path -Parent $InstallDirectory
    New-Item -ItemType Directory -Path $installParent -Force | Out-Null
    Move-Item -LiteralPath $sourceDirectory -Destination $InstallDirectory
    Write-Host "Prometheus $Version installed: $executable" -ForegroundColor Green
}
finally {
    $resolvedTemp = [IO.Path]::GetFullPath($temporaryRoot)
    $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($resolvedTemp.StartsWith($systemTemp, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedTemp)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
