# Build velocity's Windows app (decided 2026-11-11): an installer and a portable zip, with
# Python and everything else inside, and no OCR.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#   -> build\velocity-<version>-windows-setup.exe and build\velocity-<version>-windows-portable.zip
#
# Needs uv and Inno Setup 6 (https://jrsoftware.org/isinfo.php). It builds in its own
# environment (build\venv), so your development environment is untouched. The release
# workflow (.github/workflows/release.yml) runs this same script.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Build = Join-Path $Root "build"

Write-Host "==> Python environment for the app (no OCR)"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Build "venv"
uv sync --locked --no-default-groups --group app --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
$Py = Join-Path $Build "venv\Scripts\python.exe"
$Version = (& $Py -c "import velocity; print(velocity.__version__)").Trim()

Write-Host "==> Bundling velocity $Version with PyInstaller"
foreach ($dir in "dist", "work") { Remove-Item -Recurse -Force (Join-Path $Build $dir) -ErrorAction SilentlyContinue }
& (Join-Path $Build "venv\Scripts\pyinstaller.exe") --noconfirm --clean `
  --distpath (Join-Path $Build "dist") --workpath (Join-Path $Build "work") packaging\velocity.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "==> Smoke test"
& $Py packaging\smoke_test.py (Join-Path $Build "dist\velocity\velocity-cli.exe")
if ($LASTEXITCODE -ne 0) { throw "smoke test failed" }

Write-Host "==> Portable zip"
$Zip = Join-Path $Build "velocity-$Version-windows-portable.zip"
Remove-Item -Force $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $Build "dist\velocity") -DestinationPath $Zip

Write-Host "==> Installer (Inno Setup)"
$Iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "iscc" }
$env:VELOCITY_VERSION = $Version
& $Iscc packaging\velocity.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-ChildItem $Build -Filter "velocity-$Version-windows-*" | ForEach-Object {
  Write-Host ("==> {0} ({1:N0} MB)" -f $_.Name, ($_.Length / 1MB))
}
