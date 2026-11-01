param(
    [int]$Port = 8000,
    [string]$BindHost = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Set FETV3_WEB_PASSWORD and FETV3_WEB_SECRET."
}

Write-Host "Starting FET v3 web on http://$BindHost`:$Port"
python -X utf8 run.py web --host $BindHost --port $Port
