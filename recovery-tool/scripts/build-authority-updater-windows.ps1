param([string]$AuthorityPath = "", [switch]$AcceptanceTest)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Build this executable on Windows x64." }

$toolRoot = Split-Path $PSScriptRoot -Parent
$buildRoot = Join-Path $toolRoot "build/windows-authority-updater"
$distRoot = Join-Path $toolRoot "dist"
$resources = Join-Path $buildRoot "resources"
$venv = Join-Path $buildRoot "venv"
New-Item -ItemType Directory -Force $buildRoot, $distRoot | Out-Null

function Invoke-Python {
    param([string[]]$PythonArgs)
    & $script:buildPython @PythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Python command failed: $($PythonArgs -join ' ')" }
}

if (-not (Test-Path (Join-Path $venv "Scripts/python.exe"))) {
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 x64 is required on the build machine." }
}
$script:buildPython = Join-Path $venv "Scripts/python.exe"
Invoke-Python -PythonArgs @("-c", "import struct; assert struct.calcsize('P') == 8, 'x64 Python required'")
Invoke-Python -PythonArgs @("-m", "pip", "install", "--disable-pip-version-check",
    "-r", (Join-Path $toolRoot "requirements-authority-update.txt"),
    "-r", (Join-Path $toolRoot "requirements-build.txt"))

if (-not $AuthorityPath) { throw "Pass -AuthorityPath with an extracted firmware package." }
$AuthorityPath = (Resolve-Path $AuthorityPath).Path
if (Test-Path $resources) { Remove-Item $resources -Recurse -Force }
New-Item -ItemType Directory -Force $resources | Out-Null
$bundleArgs = @((Join-Path $toolRoot "packaging/macos/prepare_bundle.py"),
    "--tool-root", $toolRoot, "--output", $resources, "--authority-only",
    "--authority", $AuthorityPath)
if ($AcceptanceTest) { $bundleArgs += "--acceptance-test" }
Invoke-Python -PythonArgs $bundleArgs

$meta = Get-Content (Join-Path $resources "bundle-metadata.json") -Raw | ConvertFrom-Json
$packageName = "BORING-MIST-$($meta.build_id)-windows-x64"
if ($AcceptanceTest) { $packageName = "BORING-MIST-TEST-$($meta.build_id)-windows-x64" }
# The ZIP file name carries the build; the folder inside stays short because
# every packaged file path is nested under it (Windows 260-character limit).
$delivery = Join-Path $buildRoot "BORING-MIST"
if (Test-Path $delivery) { Remove-Item $delivery -Recurse -Force }
New-Item -ItemType Directory -Force $delivery | Out-Null
Invoke-Python -PythonArgs @((Join-Path $toolRoot "packaging/make_icon.py"))
Invoke-Python -PythonArgs @("-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--contents-directory", "lib",
    "--name", "BORING-MIST-BLE-Recovery", "--distpath", $delivery,
    "--workpath", (Join-Path $buildRoot "pyinstaller"),
    "--specpath", $buildRoot, "--hidden-import", "serial.tools.list_ports",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "webview.platforms.winforms",
    "--icon", (Join-Path $toolRoot "packaging/icons/recovery-icon.ico"),
    "--add-data", "$(Join-Path $toolRoot 'packaging/icons/recovery-icon.ico'):icons",
    "--add-data", "$(Join-Path $toolRoot 'static/authority-update.html'):static",
    "--add-data", "$(Join-Path $toolRoot 'static/authority-update.css'):static",
    "--add-data", "$(Join-Path $toolRoot 'static/authority-update.js'):static",
    "--add-data", "$(Join-Path $resources 'releases'):releases",
    "--add-data", "$(Join-Path $resources 'bundled-authority.json'):.",
    (Join-Path $toolRoot "authority_update_app.py"))

# Folder build: the EXE starts from its own folder instead of unpacking to
# %TEMP% on every launch, so start-up is faster, nothing is left behind after
# a crash and antivirus scans the files once instead of on every run.
$appExe = Join-Path $delivery "BORING-MIST-BLE-Recovery/BORING-MIST-BLE-Recovery.exe"
if (-not (Test-Path $appExe)) { throw "Folder build did not produce $appExe" }
# Operators unpack to a short folder such as C:\BORING (about 10 characters).
# Keep every packaged path well inside the 260-character Windows limit.
$maxRelative = 180
$deliveryParent = (Resolve-Path $buildRoot).Path
$longest = Get-ChildItem $delivery -Recurse -File |
    ForEach-Object { $_.FullName.Substring($deliveryParent.Length).TrimStart('\', '/') } |
    Sort-Object Length -Descending | Select-Object -First 1
if ($longest.Length -gt $maxRelative) {
    throw "Packaged path too long ($($longest.Length) > $maxRelative): $longest"
}
Write-Host "Longest packaged path: $($longest.Length) characters"
$readme = Get-Content (Join-Path $toolRoot "packaging/windows/README-template.txt") -Raw -Encoding UTF8
$readme.Replace("@BUILD_ID@", $meta.build_id) | Set-Content (Join-Path $delivery "使用说明.txt") -Encoding UTF8
if (-not $AcceptanceTest) {
    # Acceptance-test firmware is unsigned: there is no Console import ZIP.
    $officialZips = @(Get-ChildItem (Join-Path $AuthorityPath "online/Firmware-BORING-*.zip"))
    if ($officialZips.Count -ne 1) { throw "Expected one signed official Console import ZIP." }
    Copy-Item $officialZips[0].FullName $delivery
}
$zip = Join-Path $distRoot "$packageName.zip"
Compress-Archive -Path $delivery -DestinationPath $zip -Force
Write-Host "Created $zip"
