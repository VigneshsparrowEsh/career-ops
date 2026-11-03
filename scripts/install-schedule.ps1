param([string]$PythonPath, [string]$TaskName = 'JobHarvest-Daily')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configuration = Get-Content -LiteralPath (Join-Path $projectRoot 'config.json') -Raw | ConvertFrom-Json
if ($configuration.schedule.timezone -notin @('Asia/Kolkata','Asia/Calcutta')) {
    throw 'This Windows installer supports India time. Use the Python schedule command for other timezones.'
}
if (-not $PythonPath) {
    $runtimeFile = Join-Path $PSScriptRoot 'runtime.json'
    if (Test-Path -LiteralPath $runtimeFile) { $PythonPath = (Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json).python }
}
if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Python executable does not exist.' }
$indiaZone = [TimeZoneInfo]::FindSystemTimeZoneById('India Standard Time')
$indiaNow = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $indiaZone)
$indiaTarget = [DateTime]::SpecifyKind([DateTime]::ParseExact($indiaNow.ToString('yyyy-MM-dd') + ' ' + $configuration.schedule.time, 'yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture), [DateTimeKind]::Unspecified)
if ($indiaTarget -le $indiaNow) { $indiaTarget = $indiaTarget.AddDays(1) }
$targetUtc = [TimeZoneInfo]::ConvertTimeToUtc($indiaTarget, $indiaZone)
$targetLocal = [TimeZoneInfo]::ConvertTimeFromUtc($targetUtc, [TimeZoneInfo]::Local)
$action = New-ScheduledTaskAction -Execute $PythonPath -Argument '-m jobharvest due' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $targetLocal
# Store an explicit UTC boundary so the task follows India time if the PC timezone changes.
$trigger.StartBoundary = $targetUtc.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $known = $existing.Actions | Where-Object { $_.WorkingDirectory -eq $projectRoot -and $_.Arguments -eq '-m jobharvest due' }
    if (-not $known) { throw "A different task already uses the name $TaskName. Choose another -TaskName." }
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Collect public job feeds into the JobHarvest database daily at the configured India time. Runs while this user is signed in; catches up after missed starts.' -Force | Out-Null
Write-Output "Installed $TaskName at $($configuration.schedule.time) India time. Logs: $projectRoot\data\jobharvest.log"
