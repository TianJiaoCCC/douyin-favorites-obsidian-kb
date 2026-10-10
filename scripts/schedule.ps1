#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')][string]$Time = '20:00',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$TaskName = 'ObsidianPersonalKB'
)
$ErrorActionPreference = 'Stop'
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$entry = Join-Path $PSScriptRoot 'kb.py'
& $PythonExe -X utf8 $entry --config $ConfigPath ready
if ($LASTEXITCODE -ne 0) { throw 'A complete baseline and an external adapter are required before scheduling.' }
$description = 'Daily new-item synchronization managed by douyin-favorites-obsidian-kb.'
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old -and $old.Description -ne $description) { throw 'Task name belongs to another task.' }
$arguments = '-X utf8 "' + $entry + '" --config "' + $ConfigPath + '" sync'
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory (Split-Path $ConfigPath)
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::FromHours(2))
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $TaskName
if ($registered.Actions.Arguments -ne $arguments) { throw 'Task read-back verification failed.' }
Write-Host ('Configured daily at ' + $Time + ' in the Windows local timezone.')
Write-Host ('Next run: ' + (Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)
Write-Host 'Windows must remain signed in and awake. This setup did not run a synchronization.'
