# Launch DJ Bot Botty (Chief + Visualizer) on Windows
# Usage: right-click → Run with PowerShell, or: pwsh -File .\Start-DJBotBotty.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:DJ_BACKEND = if ($env:DJ_BACKEND) { $env:DJ_BACKEND } else { "sim" }
$env:COOKIE_SECURE = if ($env:COOKIE_SECURE) { $env:COOKIE_SECURE } else { "false" }
$env:CHIEF_DESKTOP = "1"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "C:\Users\cclot\ai-dj-assistant\.venv\Scripts\python.exe"
}
if (-not (Test-Path $py)) {
    Write-Error "Python venv not found. Run: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}

Write-Host "Starting DJ Bot Botty from $PSScriptRoot"
Write-Host "  Booth      http://127.0.0.1:8000/booth/"
Write-Host "  Overlay    http://127.0.0.1:8000/overlay/"
Write-Host "  Visualizer http://127.0.0.1:8000/visualizer/"
Write-Host "Press Ctrl+C to stop."
& $py desktop.py
