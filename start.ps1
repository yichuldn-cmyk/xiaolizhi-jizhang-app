Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    Write-Host '请先执行 README 中的安装步骤。'
    exit 1
}
& ./.venv/Scripts/python.exe run.py
