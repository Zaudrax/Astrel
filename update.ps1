# Met à jour Astrel sous Windows : le travail est fait par update.py.
# Si Windows refuse le script : PowerShell -ExecutionPolicy Bypass -File .\update.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 update.py @args
} else {
    & python update.py @args
}
exit $LASTEXITCODE
