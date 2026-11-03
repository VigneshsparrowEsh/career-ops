param(
    [ValidateSet('serve','collect','due','init','status','schedule','export','backup','test')]
    [string]$Command = 'serve',
    [int]$Port = 8766,
    [switch]$Scheduler,
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $runtimeFile = Join-Path $PSScriptRoot 'runtime.json'
    if (Test-Path -LiteralPath $runtimeFile) {
        $candidate = (Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json).python
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate }
    }
}
if (-not $PythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $PythonPath = $pythonCommand.Source }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Python 3.10 or later is required. Pass -PythonPath with your Python executable.'
}
Push-Location -LiteralPath $projectRoot
try {
    if ($Command -eq 'test') {
        & $PythonPath -m unittest discover -s tests -v
    } else {
        $arguments = @('-m','jobharvest',$Command)
        if ($Command -eq 'serve') {
            $arguments += @('--port', "$Port")
            if ($Scheduler) { $arguments += '--scheduler' }
        }
        & $PythonPath @arguments
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
