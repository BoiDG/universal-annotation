$ErrorActionPreference = 'Stop'
$environmentPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $environmentPython)) {
    throw 'Run Setup.ps1 first to create the app environment.'
}
Start-Process -FilePath $environmentPython -ArgumentList @('-m', 'prompt_scratchpad') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
