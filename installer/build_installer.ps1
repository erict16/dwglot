# Frontend -> PyInstaller -> Inno Setup 6. ODA is not copied into the installer.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$version = (& python (Join-Path $Root "installer\pack_version.py")).Trim()
if (-not $version) { throw "APP_VERSION is empty" }

Write-Host "==> React frontend..." -ForegroundColor Cyan
Push-Location frontend
if (-not (Test-Path node_modules)) { npm install }
npm run build
Pop-Location

Write-Host "==> PyInstaller..." -ForegroundColor Cyan
pyinstaller --clean --noconfirm Dwglot.spec

$exe = Join-Path $Root "dist\Dwglot\Dwglot.exe"
if (-not (Test-Path $exe)) {
    throw "Missing $exe"
}
$cli = Join-Path $Root "dist\Dwglot\dwglot-cli.exe"
if (-not (Test-Path $cli)) {
    throw "Missing $cli"
}
$runtime = Join-Path $Root "dist\Dwglot\_internal"
if (-not (Test-Path $runtime)) {
    throw "Missing $runtime"
}

$isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php"
}

Write-Host "==> Inno Setup v$version..." -ForegroundColor Cyan
& $iscc "/DMyAppVersion=$version" (Join-Path $Root "installer\Dwglot_Setup.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }

$setup = Join-Path $Root "installer\Output\Dwglot_v${version}_Setup.exe"
if (Test-Path $setup) {
    Write-Host "Done: $setup" -ForegroundColor Green
} else {
    throw "Installer was not written: $setup"
}
