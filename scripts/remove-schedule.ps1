param([string]$TaskName = 'JobHarvest-Daily')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Output 'Task is already absent.'; exit 0 }
$known = $task.Actions | Where-Object { $_.WorkingDirectory -eq $projectRoot -and $_.Arguments -eq '-m jobharvest due' }
if (-not $known) { throw 'Task belongs to a different project; it will not be removed.' }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Output "Removed $TaskName. Your stored jobs are preserved."
