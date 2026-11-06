# Wire Gemini + cloud defaults onto the linked Railway service, then deploy.
# Prerequisites: railway login   (in your own terminal)
# Usage:         pwsh -File .\scripts\railway-wire-gemini.ps1

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "== Railway link (select DJ Bot Botty / halo-dj project + service) =="
railway link

Write-Host "`n== Read GEMINI_API_KEY from ~/.env (not printed) =="
$envFile = Join-Path $HOME ".env"
if (-not (Test-Path $envFile)) { throw "~/.env not found" }
$key = ""; $model = "gemini-3.6-flash"; $project = ""
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*GEMINI_API_KEY=(.*)$') { $key = $Matches[1].Trim().Trim('"').Trim("'") }
  if ($_ -match '^\s*GEMINI_MODEL=(.*)$' -and $Matches[1].Trim()) { $model = $Matches[1].Trim().Trim('"').Trim("'") }
  if ($_ -match '^\s*GOOGLE_CLOUD_PROJECT=(.*)$') { $project = $Matches[1].Trim().Trim('"').Trim("'") }
}
if (-not $key) { throw "GEMINI_API_KEY missing in ~/.env" }

Write-Host "== Set Railway variables =="
railway variables --set "CHIEF_PROVIDER=gemini"
railway variables --set "GEMINI_MODEL=$model"
railway variables --set "GEMINI_API_KEY=$key"
if ($project) { railway variables --set "GOOGLE_CLOUD_PROJECT=$project" }
railway variables --set "DJ_BACKEND=sim"
railway variables --set "COOKIE_SECURE=true"
$op = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
railway variables --set "OPERATOR_KEY=$op"
Write-Host "OPERATOR_KEY (save for dashboard localStorage.operatorKey): $op"

Write-Host "`n== Deploy current directory to Railway =="
railway up --detach
Write-Host "Watch: railway logs"
Write-Host "Verify: https://djbotbotty.com/healthz  and  /booth/?tab=host"
