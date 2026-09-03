# SettleGuard - one-shot setup + run (Windows PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements.txt --quiet
python seed.py

if (-not $env:GEMINI_API_KEY) {
  Write-Host "`n[!] GEMINI_API_KEY is not set - the AI triage layer will be disabled." -ForegroundColor Yellow
  Write-Host "    Set it first for full functionality:  `$env:GEMINI_API_KEY = 'your-key'`n" -ForegroundColor Yellow
}

python app.py
