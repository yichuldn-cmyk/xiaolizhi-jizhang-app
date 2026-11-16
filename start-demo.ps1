Set-Location -LiteralPath $PSScriptRoot
$env:DATA_DIR = Join-Path $PSScriptRoot 'data/demo'
$env:PORT = '8001'
& ./.venv/Scripts/python.exe manage.py demo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '演示页面 http://127.0.0.1:8001，账号 demo，密码 demo-lizhi-2026'
& ./.venv/Scripts/python.exe run.py
