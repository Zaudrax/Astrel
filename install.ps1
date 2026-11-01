# Installe Astrel sous Windows : le travail est fait par install.py.
# Si Windows refuse le script : PowerShell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 install.py @args
} else {
    & python install.py @args
}
exit $LASTEXITCODE
