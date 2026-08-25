[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectDir,
    [Parameter(Mandatory = $true)][string]$EnvFile,
    [Parameter(Mandatory = $true)][string]$LedgerDatabase,
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [string]$TaskPrefix = 'BulletTradeBaihua'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$project = (Resolve-Path -LiteralPath $ProjectDir).Path
$envPath = (Resolve-Path -LiteralPath $EnvFile).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$startScript = Join-Path $project 'scripts\windows\start_strategy_server.ps1'
$backupScript = Join-Path $project 'scripts\strategy_ledger_backup.py'
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python not found: $python" }

$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$serverAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`" " +
    "-ProjectDir `"$project`" -EnvFile `"$envPath`" -PythonPath `"$python`""
)
$serverSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "$TaskPrefix-Server" -Action $serverAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $account) -Settings $serverSettings `
    -Principal $principal -Description 'BulletTrade QMT StrategyLedger server' -Force | Out-Null

$backupAction = New-ScheduledTaskAction -Execute $python -Argument (
    "`"$backupScript`" backup --database `"$LedgerDatabase`" --output-dir `"$BackupDir`""
)
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "$TaskPrefix-Backup" -Action $backupAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '18:00') -Settings $backupSettings `
    -Principal $principal -Description 'Daily StrategyLedger SQLite backup' -Force | Out-Null

Write-Host "Registered $TaskPrefix-Server and $TaskPrefix-Backup." -ForegroundColor Green
