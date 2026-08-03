@echo off
REM ============================================================================
REM Quorum Edition Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users running CMD.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/Quorum-Agent/hermes-agent/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
REM   Invoke-WebRequest https://raw.githubusercontent.com/Quorum-Agent/hermes-agent/main/scripts/install.ps1 -OutFile install.ps1
REM ============================================================================

echo.
echo  Quorum Edition Installer
echo  Downloading verified Quorum PowerShell installer...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "$ErrorActionPreference='Stop'; $p=Join-Path $env:TEMP 'quorum-install.ps1'; try { Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Quorum-Agent/hermes-agent/quorum-v0.19.1/scripts/install.ps1' -OutFile $p -UseBasicParsing; if ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() -ne '46c37bfa70c277d23fe086e2b11f5612181a75e2493f5d9455140115fba509fb') { throw 'Quorum install.ps1 checksum verification failed' }; & $p; if (-not $?) { exit 1 } } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    Download install.ps1 from the Quorum-Agent/hermes-agent repository and verify its SHA-256.
    echo.
    pause
    exit /b 1
)
