# Clear all log files in the logs/ directory
# Run from project root: .\scripts\clear_logs.ps1

$logsDir = Join-Path $PSScriptRoot "..\logs"

Get-ChildItem -Path $logsDir -File | ForEach-Object {
    Clear-Content -Path $_.FullName -ErrorAction SilentlyContinue
    Write-Host "Cleared: $($_.Name)"
}

Write-Host "`nDone — all log files cleared."
