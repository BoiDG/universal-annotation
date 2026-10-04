param(
    [string]$DesktopDirectory = [Environment]::GetFolderPath('DesktopDirectory'),
    [string]$StartMenuDirectory = [Environment]::GetFolderPath('Programs')
)

$ErrorActionPreference = 'Stop'
$environmentPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$appIcon = Join-Path $PSScriptRoot 'src\prompt_scratchpad\assets\app.ico'
foreach ($requiredPath in @($environmentPython, $appIcon)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Missing $requiredPath. Run Setup.ps1 first."
    }
}

$shortcutShell = New-Object -ComObject WScript.Shell
foreach ($shortcutDirectory in @($DesktopDirectory, $StartMenuDirectory) | Select-Object -Unique) {
    if (-not $shortcutDirectory) { throw 'Windows did not provide a shortcut directory.' }
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
    $shortcutPath = Join-Path $shortcutDirectory 'Universal Annotation.lnk'
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $environmentPython) {
        throw "An unrelated shortcut already exists at $shortcutPath. Rename it before retrying."
    }
    $shortcut.TargetPath = $environmentPython
    $shortcut.Arguments = '-m prompt_scratchpad'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = "$appIcon,0"
    $shortcut.Description = 'Open the Universal Annotation prompt scratchpad'
    $shortcut.Save()
    Write-Output "Created $shortcutPath"
}
