$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = if ($env:COMMERCE_PYTHON) {
    $env:COMMERCE_PYTHON
}
elseif (Test-Path -LiteralPath (Join-Path $projectRoot ".venv\Scripts\python.exe")) {
    Join-Path $projectRoot ".venv\Scripts\python.exe"
}
else {
    (Get-Command python -ErrorAction Stop).Source
}

Push-Location $projectRoot
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic migration failed."
    }
}
finally {
    Pop-Location
}
