# Keep the Ubuntu WSL distro running from Windows start-up, so the growth-engine timer ticks even when
# nobody is logged in or the PC has just rebooted. Run once in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File keep-wsl-running.ps1 -Distro Ubuntu -User <linux user>
param(
  [string]$Distro = "Ubuntu",
  [Parameter(Mandatory = $true)][string]$User
)
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -u $User --exec /bin/sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "growth-engine WSL keepalive" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
Write-Output "Registered. WSL ($Distro) now starts with Windows and stays up; systemd runs the growth-engine timer inside it."
