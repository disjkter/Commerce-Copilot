$ErrorActionPreference = "Stop"

$postgresRoot = if ($env:POSTGRES_ROOT) {
    $env:POSTGRES_ROOT
}
else {
    "E:\ProgramFiles\PostgreSQL\17"
}
$binDirectory = Join-Path $postgresRoot "bin"
$dataDirectory = Join-Path $postgresRoot "data"
$logPath = Join-Path $postgresRoot "postgresql.log"
$psql = Join-Path $binDirectory "psql.exe"
$pgCtl = Join-Path $binDirectory "pg_ctl.exe"
$pgIsReady = Join-Path $binDirectory "pg_isready.exe"

foreach ($requiredPath in @($psql, $pgCtl, $pgIsReady, $dataDirectory)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required PostgreSQL path is missing: $requiredPath"
    }
}

& $pgIsReady -h 127.0.0.1 -p 5432 | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $pgCtl -D $dataDirectory -l $logPath -w start
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL could not be started. Check $logPath"
    }
}

$superSecure = Read-Host "Enter the postgres administrator password" -AsSecureString
$appSecure = Read-Host "Create a password for the commerce_copilot application user" -AsSecureString
$superPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($superSecure)
$appPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($appSecure)

try {
    $superPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($superPointer)
    $appPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($appPointer)
    if ([string]::IsNullOrWhiteSpace($appPassword)) {
        throw "The application password cannot be empty."
    }

    $escapedAppPassword = $appPassword.Replace("'", "''")
    $sql = @"
\set ON_ERROR_STOP on
SELECT 'CREATE ROLE commerce_copilot LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'commerce_copilot')
\gexec
SELECT format('ALTER ROLE commerce_copilot WITH LOGIN PASSWORD %L', '$escapedAppPassword')
\gexec
SELECT 'CREATE DATABASE commerce_copilot OWNER commerce_copilot'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'commerce_copilot')
\gexec
REVOKE ALL ON DATABASE commerce_copilot FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE commerce_copilot TO commerce_copilot;
"@

    $env:PGPASSWORD = $superPassword
    $sql | & $psql -h 127.0.0.1 -p 5432 -U postgres -d postgres
    if ($LASTEXITCODE -ne 0) {
        throw "Database or application user creation failed."
    }

    Write-Host ""
    Write-Host "PostgreSQL project database is ready." -ForegroundColor Green
    Write-Host "Database: commerce_copilot"
    Write-Host "Application user: commerce_copilot"
    Write-Host "Host: 127.0.0.1"
    Write-Host "Port: 5432"
    Write-Host "Keep the application password; it will be added to the local .env next."
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($superPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($superPointer)
    }
    if ($appPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($appPointer)
    }
}
