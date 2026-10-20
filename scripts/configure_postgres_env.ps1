$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$examplePath = Join-Path $projectRoot ".env.example"
$environmentPath = Join-Path $projectRoot ".env"
$postgresRoot = if ($env:POSTGRES_ROOT) {
    $env:POSTGRES_ROOT
}
else {
    "E:\ProgramFiles\PostgreSQL\17"
}
$psql = Join-Path $postgresRoot "bin\psql.exe"

if (-not (Test-Path -LiteralPath $psql)) {
    throw "PostgreSQL client is missing: $psql"
}

$passwordSecure = Read-Host "Enter the commerce_copilot application password" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passwordSecure)

try {
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    if ([string]::IsNullOrWhiteSpace($password)) {
        throw "The application password cannot be empty."
    }

    $env:PGPASSWORD = $password
    $result = & $psql `
        -h 127.0.0.1 `
        -p 5432 `
        -U commerce_copilot `
        -d commerce_copilot `
        -tAc "SELECT current_database() || ':' || current_user"
    if ($LASTEXITCODE -ne 0) {
        throw "The application database connection failed. Check the password."
    }

    $encodedPassword = [Uri]::EscapeDataString($password)
    $databaseUrl = "postgresql+psycopg://commerce_copilot:${encodedPassword}@127.0.0.1:5432/commerce_copilot"
    $sourceLines = if (Test-Path -LiteralPath $environmentPath) {
        Get-Content -LiteralPath $environmentPath
    }
    else {
        Get-Content -LiteralPath $examplePath
    }
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($line in $sourceLines) {
        $lines.Add($line)
    }

    $databaseUrlIndex = -1
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match '^DATABASE_URL=') {
            $databaseUrlIndex = $index
            break
        }
    }
    if ($databaseUrlIndex -ge 0) {
        $lines[$databaseUrlIndex] = "DATABASE_URL=$databaseUrl"
    }
    else {
        $lines.Add("DATABASE_URL=$databaseUrl")
    }

    [IO.File]::WriteAllLines(
        $environmentPath,
        $lines,
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host ""
    Write-Host "PostgreSQL connection verified and .env configured." -ForegroundColor Green
    Write-Host "Connection: $result"
    Write-Host "The password was written only to the Git-ignored .env file."
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}
