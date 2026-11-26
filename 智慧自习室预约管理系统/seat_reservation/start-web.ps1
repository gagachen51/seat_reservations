$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" run.py --db var/delivery.db serve --lan --port 8010
if ($LASTEXITCODE -ne 0) { throw 'Web server stopped with an error. See the output above.' }
