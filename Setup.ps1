$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $environmentPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        $runtimeCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($runtimeCommand) {
            & $runtimeCommand.Source -3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or newer, then rerun Setup.ps1.' }
            & $runtimeCommand.Source -3 -m venv .venv
        } else {
            $runtimeCommand = Get-Command python -ErrorAction Stop
            & $runtimeCommand.Source -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or newer, then rerun Setup.ps1.' }
            & $runtimeCommand.Source -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the workspace virtual environment.' }
    }
    & $environmentPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip.' }
    & $environmentPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Write-Output 'Ready. Run Launch.cmd to open Prompt Scratchpad.'
} finally {
    Pop-Location
}
