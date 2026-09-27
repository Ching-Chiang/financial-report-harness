[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$EnvPath = Join-Path $ProjectRoot '.venv'
$Python = Join-Path $EnvPath 'Scripts\python.exe'
$MarkerPath = Join-Path $EnvPath 'harness-project.txt'
if ((Test-Path -LiteralPath $EnvPath) -and
    ((-not (Test-Path -LiteralPath $MarkerPath)) -or
     ((Get-Content -LiteralPath $MarkerPath -Raw).Trim() -ne $ProjectRoot))) {
    $ResolvedEnv = [IO.Path]::GetFullPath($EnvPath)
    if ($ResolvedEnv -ne [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.venv'))) { throw 'Invalid environment path' }
    Move-Item -LiteralPath $ResolvedEnv -Destination ($ResolvedEnv + '.old-' + [guid]::NewGuid().ToString('N'))
}
if (-not (Test-Path -LiteralPath $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $EnvPath
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $EnvPath
    } else {
        throw 'Python 3.10+ is required. No MinerU installation or model download is needed.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& $Python -I -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ required.' }
& $Python -I -m pip install --require-hashes -r (Join-Path $ProjectRoot 'harness\financial-report\requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Helper dependency installation failed.' }
$ProjectRoot | Set-Content -LiteralPath $MarkerPath -Encoding UTF8
Write-Host 'Helper scripts installed. Set MINERU_API_TOKEN before online parsing.'
Write-Host 'No MinerU package, model weights, CUDA, or agent SDK was installed.'
