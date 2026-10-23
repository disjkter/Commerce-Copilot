$ErrorActionPreference = "Stop"

$model = if ($env:EMBEDDING_MODEL) {
    $env:EMBEDDING_MODEL
}
else {
    "BAAI/bge-m3"
}
$modelDirectory = if ($env:EMBEDDING_MODEL_PATH) {
    $env:EMBEDDING_MODEL_PATH
}
else {
    "E:\ProgramFiles\ModelCache\bge-m3"
}
$cacheDirectory = if ($env:HF_HOME) {
    $env:HF_HOME
}
else {
    "E:\ProgramFiles\ModelCache\huggingface"
}

if (-not (Get-Command hf -ErrorAction SilentlyContinue)) {
    throw "Hugging Face CLI 'hf' is required."
}

New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $cacheDirectory | Out-Null
$env:HF_HOME = $cacheDirectory

hf download $model `
    --local-dir $modelDirectory `
    --max-workers 1 `
    --exclude "onnx/*" `
    --exclude "imgs/*" `
    --exclude "*.jpg" `
    --exclude "*.webp"
if ($LASTEXITCODE -ne 0) {
    throw "BGE model download failed."
}

Write-Host "BGE model downloaded to $modelDirectory" -ForegroundColor Green
