<#
.SYNOPSIS
    Register the Campaign Manager worker as a scheduled task on a Windows GPU host.

.DESCRIPTION
    The worker has to run as a real user account with stored credentials. A task
    or service running as SYSTEM cannot reach the artifact share, so it would
    start cleanly and then fail on the first artifact read.

    The task starts at boot and runs whether that user is logged on or not, so
    the host recovers unattended. Output is appended to a log file because Task
    Scheduler does not capture standard output.

    The password is prompted for and handed to the scheduler as a credential; it
    is never taken as a parameter, so it stays out of shell history and process
    listings.

.EXAMPLE
    .\register-task.ps1 -UserName robti
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UserName,
    [string]$Root = 'D:\campaign-worker',
    [string]$TaskName = 'CampaignWorker'
)

$ErrorActionPreference = 'Stop'

$runner = Join-Path $Root 'run-worker.ps1'
$logDir = Join-Path $Root 'logs'
$log = Join-Path $logDir 'worker.log'
foreach ($required in @($runner, (Join-Path $Root 'worker.env'))) {
    if (-not (Test-Path $required)) { throw "Missing $required; run install-worker.ps1 and ship worker.env first" }
}
New-Item -ItemType Directory -Force $logDir | Out-Null

# Redirection has to happen inside PowerShell: a task action is not a shell.
$command = "& '$runner' *>> '$log'"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$command`""

$trigger = New-ScheduledTaskTrigger -AtStartup

# The worker is a long-lived poll loop, so no execution time limit, one instance
# only, and restart it if it dies rather than leaving the queue unattended.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Write-Host "Enter the Windows password for $UserName (stored by the scheduler, not echoed)."
$secret = Read-Host -AsSecureString "Password"
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))

try {
    # LogonType Password is what makes the task able to reach network shares
    # while nobody is logged on. Interactive would only work at the console.
    $principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Password -RunLevel Limited
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -User $UserName -Password $plain -Force | Out-Null
} finally {
    $plain = $null
    [GC]::Collect()
}

Write-Host "Registered '$TaskName'. Starting it now."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 12

$info = Get-ScheduledTaskInfo -TaskName $TaskName
"state          : $((Get-ScheduledTask -TaskName $TaskName).State)"
"last result    : 0x{0:X}" -f $info.LastTaskResult
"log tail:"
if (Test-Path $log) { Get-Content $log -Tail 12 | ForEach-Object { "    $_" } } else { "    (no log yet)" }
