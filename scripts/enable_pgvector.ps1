$ErrorActionPreference = "Stop"

$postgresRoot = if ($env:POSTGRES_ROOT) {
    $env:POSTGRES_ROOT
}
else {
    "E:\ProgramFiles\PostgreSQL\17"
}
$psql = Join-Path $postgresRoot "bin\psql.exe"
$controlFile = Join-Path $postgresRoot "share\extension\vector.control"

if (-not (Test-Path -LiteralPath $psql)) {
    throw "PostgreSQL client is missing: $psql"
}
if (-not (Test-Path -LiteralPath $controlFile)) {
    throw "pgvector binaries are not installed under $postgresRoot"
}

$passwordSecure = Read-Host "Enter the postgres administrator password" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passwordSecure)

try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    $env:PGPASSWORD = $password
    & $psql `
        -h 127.0.0.1 `
        -p 5432 `
        -U postgres `
        -d commerce_copilot `
        -v ON_ERROR_STOP=1 `
        -c "CREATE EXTENSION IF NOT EXISTS vector;" `
        -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not enable pgvector in the commerce_copilot database."
    }
    Write-Host "pgvector is enabled for commerce_copilot." -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}
