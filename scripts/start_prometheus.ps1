param(
    [string]$PrometheusExe = "E:\ProgramFiles\Prometheus\prometheus.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot "ops\prometheus\prometheus.yml"
$storagePath = Join-Path $projectRoot "runtime\prometheus"
$stdoutLogPath = Join-Path $projectRoot "runtime\prometheus.stdout.log"
$stderrLogPath = Join-Path $projectRoot "runtime\prometheus.stderr.log"

if (-not (Test-Path -LiteralPath $PrometheusExe)) {
    throw "Prometheus executable not found: $PrometheusExe"
}
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Prometheus config not found: $configPath"
}

$listener = Get-NetTCPConnection -LocalPort 9090 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Prometheus is already running on http://127.0.0.1:9090" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Path $storagePath -Force | Out-Null
$arguments = @(
    "--config.file=$configPath"
    "--storage.tsdb.path=$storagePath"
    "--web.listen-address=127.0.0.1:9090"
)

$process = Start-Process `
    -FilePath $PrometheusExe `
    -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $PrometheusExe) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLogPath `
    -RedirectStandardError $stderrLogPath `
    -PassThru

Write-Host "Prometheus started (PID $($process.Id)): http://127.0.0.1:9090" -ForegroundColor Green
