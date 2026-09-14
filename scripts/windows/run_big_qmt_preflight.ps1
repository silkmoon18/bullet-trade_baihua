[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectDir,
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [Parameter(Mandatory = $true)][string]$PreflightScript,
    [Parameter(Mandatory = $true)][string]$ReportFile,
    [string]$PythonPath = '',
    [string]$ServerTaskName = 'BulletTradeBaihua-Server',
    [int]$ServerPort = 58620
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$project = (Resolve-Path -LiteralPath $ProjectDir).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$preflightPath = (Resolve-Path -LiteralPath $PreflightScript).Path
if (-not $PythonPath) { $PythonPath = Join-Path $project '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python not found: $PythonPath"
}

$reportParent = Split-Path -Parent $ReportFile
if ($reportParent -and -not (Test-Path -LiteralPath $reportParent)) {
    New-Item -ItemType Directory -Path $reportParent -Force | Out-Null
}

function Disable-StrategyTrading {
    $text = [IO.File]::ReadAllText($envPath)
    $text = [regex]::Replace(
        $text,
        '(?m)^QMT_STRATEGY_TRADING_ENABLED=.*$',
        'QMT_STRATEGY_TRADING_ENABLED=false'
    )
    $text = [regex]::Replace(
        $text,
        '(?m)^QMT_STRATEGY_SIMULATION_VALIDATION_ENABLED=.*$',
        'QMT_STRATEGY_SIMULATION_VALIDATION_ENABLED=false'
    )
    [IO.File]::WriteAllText(
        $envPath,
        $text,
        (New-Object Text.UTF8Encoding($false))
    )
}

try {
    # Windows PowerShell 5.1 converts native stderr into NativeCommandError
    # records. Use process-level redirection so normal BulletTrade INFO logs
    # remain plain text and success depends only on the process exit code.
    $stdoutPath = [IO.Path]::GetTempFileName()
    $stderrPath = [IO.Path]::GetTempFileName()
    try {
        $process = Start-Process `
            -FilePath $PythonPath `
            -ArgumentList ('"{0}"' -f $preflightPath) `
            -WindowStyle Hidden `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath
        $preflightExitCode = $process.ExitCode
        $stdout = [IO.File]::ReadAllText($stdoutPath)
        $stderr = [IO.File]::ReadAllText($stderrPath)
        [IO.File]::WriteAllText(
            $ReportFile,
            $stdout + $stderr,
            (New-Object Text.UTF8Encoding($false))
        )
    }
    finally {
        Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    }
    if ($preflightExitCode -ne 0) {
        throw "preflight returned exit code $preflightExitCode"
    }
    exit 0
}
catch {
    Disable-StrategyTrading
    Stop-ScheduledTask -TaskName $ServerTaskName -ErrorAction SilentlyContinue
    $serverProcessIds = @(
        Get-NetTCPConnection -State Listen -LocalPort $ServerPort -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    foreach ($processId in $serverProcessIds) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Start-ScheduledTask -TaskName $ServerTaskName
    ($_ | Out-String) | Add-Content -LiteralPath $ReportFile -Encoding utf8
    exit 1
}
