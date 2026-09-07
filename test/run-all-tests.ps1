# SSH Alias MCP Full Test Suite
# 4 YAML configs × 3 client environments = 12 test combinations
# Each combination runs the unified live suite (test/test_live_e2e.py) under pytest.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SSH Alias MCP Full Test Suite" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Color helper functions
function Write-Pass { param($msg) Write-Host "[PASS] $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Yellow }

# Switch OpenSSH DefaultShell
function Switch-OpenSSHShell {
    param([string]$ShellPath)
    Write-Info "Switching OpenSSH DefaultShell -> $ShellPath"
    Start-Process powershell -ArgumentList "-NoProfile", "-Command", 
        "Set-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name 'DefaultShell' -Value '$ShellPath'; Restart-Service sshd -Force" -Verb RunAs -Wait
    $result = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name 'DefaultShell').DefaultShell
    if ($result -eq $ShellPath) {
        Write-Pass "OpenSSH shell switched successfully"
    } else {
        Write-Fail "OpenSSH shell switch failed: $result"
        exit 1
    }
}

# Run the live pytest suite for one YAML config / client environment combination
function Run-Test {
    param(
        [string]$Number,
        [string]$Yaml,
        [string]$Client,
        [bool]$IsWSL = $false,
        [bool]$IsCmd = $false
    )

    Write-Host ""
    Write-Host "--- Test $($Number): $($Yaml) x $($Client) ---" -ForegroundColor Cyan

    if ($IsWSL) {
        $wslRoot = "/mnt/" + $RootDir.Substring(0, 1).ToLower() + ($RootDir.Substring(2) -replace "\\", "/")
        $cmd = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate ts && cd $($wslRoot) && SSH_TEST_SERVER=$($Yaml) python -m pytest test/test_live_e2e.py -m live -q"
        wsl -d Ubuntu-24.04 -e bash -c $cmd
        $exitCode = $LASTEXITCODE
    }
    elseif ($IsCmd) {
        $cmd = "set SSH_TEST_SERVER=$($Yaml) && python -m pytest test\test_live_e2e.py -m live -q"
        Push-Location $RootDir
        cmd /c $cmd
        $exitCode = $LASTEXITCODE
        Pop-Location
    }
    else {
        $env:SSH_TEST_SERVER = $Yaml
        Push-Location $RootDir
        python -m pytest test\test_live_e2e.py -m live -q
        $exitCode = $LASTEXITCODE
        Pop-Location
        Remove-Item env:SSH_TEST_SERVER -ErrorAction SilentlyContinue
    }

    if ($exitCode -eq 0) {
        Write-Pass "Test $($Number) passed"
        return $true
    }
    else {
        Write-Fail "Test $($Number) failed"
        return $false
    }
}

$passed = 0
$failed = 0

# ===== YAML 1: remote server (port forwarding) =====
Write-Host ""
Write-Host "========== YAML 1: Remote Server (port forwarding) ==========" -ForegroundColor Green
# No OpenSSH switch needed (connects to remote server via port forwarding)

if (Run-Test -Number 1 -Yaml "test-win-bash" -Client "PowerShell") { $passed++ } else { $failed++ }
if (Run-Test -Number 2 -Yaml "test-win-bash" -Client "cmd" -IsCmd:$true) { $passed++ } else { $failed++ }
if (Run-Test -Number 3 -Yaml "test-win-bash" -Client "WSL bash" -IsWSL:$true) { $passed++ } else { $failed++ }

# ===== YAML 2: PowerShell server =====
Write-Host ""
Write-Host "========== YAML 2: PowerShell Server ==========" -ForegroundColor Green
Switch-OpenSSHShell -ShellPath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

if (Run-Test -Number 4 -Yaml "test-powershell" -Client "PowerShell") { $passed++ } else { $failed++ }
if (Run-Test -Number 5 -Yaml "test-powershell" -Client "cmd" -IsCmd:$true) { $passed++ } else { $failed++ }
if (Run-Test -Number 6 -Yaml "test-powershell" -Client "WSL bash" -IsWSL:$true) { $passed++ } else { $failed++ }

# ===== YAML 3: Cmd server =====
Write-Host ""
Write-Host "========== YAML 3: Cmd Server ==========" -ForegroundColor Green
Switch-OpenSSHShell -ShellPath "C:\Windows\System32\cmd.exe"

if (Run-Test -Number 7 -Yaml "test-cmd" -Client "PowerShell") { $passed++ } else { $failed++ }
if (Run-Test -Number 8 -Yaml "test-cmd" -Client "cmd" -IsCmd:$true) { $passed++ } else { $failed++ }
if (Run-Test -Number 9 -Yaml "test-cmd" -Client "WSL bash" -IsWSL:$true) { $passed++ } else { $failed++ }

# ===== YAML 4: WSL Bash server =====
Write-Host ""
Write-Host "========== YAML 4: WSL Bash Server ==========" -ForegroundColor Green
Switch-OpenSSHShell -ShellPath "C:\WINDOWS\system32\bash.exe"

if (Run-Test -Number 10 -Yaml "test-local-bash" -Client "PowerShell") { $passed++ } else { $failed++ }
if (Run-Test -Number 11 -Yaml "test-local-bash" -Client "cmd" -IsCmd:$true) { $passed++ } else { $failed++ }
if (Run-Test -Number 12 -Yaml "test-local-bash" -Client "WSL bash" -IsWSL:$true) { $passed++ } else { $failed++ }

# Restore default
Write-Host ""
Write-Host "========== Restoring default configuration ==========" -ForegroundColor Green
Switch-OpenSSHShell -ShellPath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Test Complete!" -ForegroundColor Cyan
Write-Host "  Passed: $($passed) | Failed: $($failed) | Total: 12" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if ($failed -gt 0) {
    exit 1
}
