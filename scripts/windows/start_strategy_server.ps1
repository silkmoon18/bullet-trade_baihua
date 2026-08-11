[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectDir,
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$project = (Resolve-Path -LiteralPath $ProjectDir).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
if (-not $PythonPath) { $PythonPath = Join-Path $project '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python not found: $PythonPath" }

Set-Location -LiteralPath $project
& $PythonPath -m bullet_trade --env-file $envPath server
exit $LASTEXITCODE
