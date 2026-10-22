$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $projectRoot "src"
$previousPythonPath = $env:PYTHONPATH
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
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourcePath;$previousPythonPath"
    }
    else {
        $sourcePath
    }
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic migration failed."
    }
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
    Pop-Location
}
