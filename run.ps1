[CmdletBinding(PositionalBinding=$false)]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
$ErrorActionPreference = 'Stop'
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$MarkerPath = Join-Path $PSScriptRoot '.venv\harness-project.txt'
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $MarkerPath)) {
    Write-Error 'Helper environment missing. Run .\setup.ps1, or use a Python environment with requirements.lock installed.'
    exit 2
}
if ((Get-Content -LiteralPath $MarkerPath -Raw).Trim() -ne $PSScriptRoot) {
    Write-Error 'Project moved. Run .\setup.ps1 to recreate the small helper environment.'
    exit 2
}
& $Python -I -X utf8 (Join-Path $PSScriptRoot 'harness\financial-report\scripts\cli.py') @CommandArgs
exit $LASTEXITCODE
