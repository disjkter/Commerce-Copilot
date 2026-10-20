$ErrorActionPreference = "Stop"

$postgresRoot = if ($env:POSTGRES_ROOT) {
    $env:POSTGRES_ROOT
}
else {
    "E:\ProgramFiles\PostgreSQL\17"
}
$pgCtl = Join-Path $postgresRoot "bin\pg_ctl.exe"
$pgIsReady = Join-Path $postgresRoot "bin\pg_isready.exe"
$dataDirectory = Join-Path $postgresRoot "data"
$logPath = Join-Path $postgresRoot "postgresql.log"

& $pgIsReady -h 127.0.0.1 -p 5432 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "PostgreSQL is already running on 127.0.0.1:5432."
    exit 0
}

& $pgCtl -D $dataDirectory -l $logPath -w start
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL failed to start. Check $logPath"
}

Write-Host "PostgreSQL started on 127.0.0.1:5432." -ForegroundColor Green
