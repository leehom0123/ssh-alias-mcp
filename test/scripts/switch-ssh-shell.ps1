# Switch OpenSSH DefaultShell (Admin required)
# Usage: .\switch-ssh-shell.ps1 -Shell cmd|powershell|bash
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("cmd", "powershell", "bash")]
    [string]$Shell
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Admin rights required" -ForegroundColor Red
    exit 1
}

$registryPath = "HKLM:\SOFTWARE\OpenSSH"

switch ($Shell) {
    "cmd"        { $shellValue = "C:\Windows\System32\cmd.exe" }
    "powershell" { $shellValue = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" }
    "bash"       { $shellValue = "C:\WINDOWS\system32\bash.exe" }
}

try {
    Set-ItemProperty -Path $registryPath -Name "DefaultShell" -Value $shellValue -ErrorAction Stop
    Write-Host "Set DefaultShell = $shellValue" -ForegroundColor Green
} catch {
    Write-Host "Registry error: $_" -ForegroundColor Red
    exit 1
}

try {
    Restart-Service sshd -Force -ErrorAction Stop
    Write-Host "Restarted sshd service" -ForegroundColor Green
} catch {
    Write-Host "Service restart error: $_" -ForegroundColor Red
    exit 1
}

Start-Sleep -Seconds 3
$service = Get-Service sshd
if ($service.Status -eq "Running") {
    Write-Host "sshd status: Running" -ForegroundColor Green
} else {
    Write-Host "sshd status: $($service.Status)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done! Ready for testing." -ForegroundColor Green
