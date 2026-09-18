param(
    [string]$Config = "config.example.json",
    [string]$Output = "reports",
    [int]$Top = 30,
    [int]$Port = 8765,
    [int]$Cooldown = 30
)

$ErrorActionPreference = "Stop"
$PythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "未找到 .venv。请先执行: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ."
}

$Arguments = @(
    "-m", "ashare_screener", "serve",
    "--config", (Join-Path $PSScriptRoot $Config),
    "--output", (Join-Path $PSScriptRoot $Output),
    "--top", $Top,
    "--port", $Port,
    "--cooldown", $Cooldown
)

& $PythonPath @Arguments
exit $LASTEXITCODE
