param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name InvokeAI-Tools-GUI-v1.1 --version-file windows-version.txt --collect-all customtkinter --hidden-import PIL.Image --hidden-import PIL.PngImagePlugin --hidden-import yaml --hidden-import uuid --hidden-import locale --hidden-import hashlib --hidden-import sqlite3 --add-data 'scripts;scripts' app.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    Write-Host "Built: $PSScriptRoot\dist\InvokeAI-Tools-GUI-v1.1.exe"
} finally {
    Pop-Location
}
