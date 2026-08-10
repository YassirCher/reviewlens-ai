$Root = Split-Path -Parent $PSScriptRoot
Set-Location "$Root/backend"
& ".venv/Scripts/python.exe" -m uvicorn app.main:app --reload --port 8000
