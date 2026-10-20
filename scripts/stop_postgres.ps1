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

& $pgIsReady -h 127.0.0.1 -p 5432 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PostgreSQL is already stopped."
    exit 0
}

& $pgCtl -D $dataDirectory -m fast -w stop
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL failed to stop cleanly."
}

Write-Host "PostgreSQL stopped cleanly." -ForegroundColor Green
