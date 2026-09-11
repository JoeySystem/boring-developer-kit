param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SoftwareDir = (Resolve-Path (Join-Path $ProjectDir '..')).Path
$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$BuildDir = Join-Path $ProjectDir 'build\windows'
$DistDir = Join-Path $ProjectDir 'dist\windows'

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'Windows package must be built on Windows'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing project Python: $Python"
}
& $Python -c 'import nuitka'
if ($LASTEXITCODE -ne 0) { throw 'Nuitka is not installed in .venv' }
if ($BuildDir -ne (Join-Path $ProjectDir 'build\windows')) {
    throw "Unexpected build directory: $BuildDir"
}
if ($DistDir -ne (Join-Path $ProjectDir 'dist\windows')) {
    throw "Unexpected dist directory: $DistDir"
}
foreach ($Target in @($BuildDir, $DistDir)) {
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
    }
}
New-Item -ItemType Directory -Path $BuildDir, $DistDir | Out-Null

$env:PYTHONPATH = "$(Join-Path $ProjectDir 'src');$(Join-Path $SoftwareDir 'sdk\python')"
$TrustPolicyMarker = Join-Path $BuildDir 'device-trust-policy.json'
& $Python (Join-Path $ProjectDir 'tools\stage_device_trust_policy.py') `
    (Join-Path $ProjectDir 'src\controller_config\assets\device-trust-roots.json') `
    $TrustPolicyMarker
if ($LASTEXITCODE -ne 0) { throw 'Production device trust policy staging failed' }
$AppVersion = (& $Python -c 'from controller_config import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $AppVersion) {
    throw 'Unable to read BORING Console Community version'
}

& (Join-Path $ProjectDir '.venv\Scripts\pyside6-lrelease.exe') `
    (Join-Path $ProjectDir 'src\controller_config\translations\boring_configurator_en_US.ts') `
    -qm (Join-Path $ProjectDir 'src\controller_config\translations\boring_configurator_en_US.qm')
if ($LASTEXITCODE -ne 0) { throw 'Qt translation compilation failed' }

& $Python -m nuitka `
    --standalone `
    --enable-plugin=pyside6 `
    --windows-console-mode=disable `
    --output-filename='BORING Console Community.exe' `
    --include-package=controller_config `
    "--include-data-dir=$(Join-Path $ProjectDir 'src\controller_config\assets')=controller_config/assets" `
    "--include-data-file=$TrustPolicyMarker=controller_config/assets/device-trust-policy.json" `
    "--include-data-dir=$(Join-Path $ProjectDir 'src\controller_config\translations')=controller_config/translations" `
    "--output-dir=$BuildDir\console" `
    (Join-Path $ProjectDir 'deploy\console_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'BORING Console Community Nuitka build failed' }

$ConsoleDist = Get-ChildItem -LiteralPath (Join-Path $BuildDir 'console') -Directory -Filter '*.dist'
if (@($ConsoleDist).Count -ne 1) { throw 'Expected exactly one Windows standalone directory' }
$PackageDir = Join-Path $DistDir 'BORING Console Community'
Copy-Item -LiteralPath $ConsoleDist.FullName -Destination $PackageDir -Recurse

& $Python -m nuitka `
    --onefile `
    --enable-plugin=pyside6 `
    --windows-console-mode=disable `
    --include-module=controller_config.extension_runner `
    --include-package=boring_console_sdk `
    --output-filename=boring-extension-runner.exe `
    "--output-dir=$BuildDir\runner" `
    (Join-Path $ProjectDir 'deploy\runner_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'Private extension Runner build failed' }
$Runner = Join-Path $BuildDir 'runner\boring-extension-runner.exe'
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw 'Private extension Runner executable is missing'
}
Copy-Item -LiteralPath $Runner -Destination (Join-Path $PackageDir 'boring-extension-runner.exe')
& $Python (Join-Path $ProjectDir 'tools\stage_protocol_resources.py') `
    (Join-Path $SoftwareDir 'protocol') (Join-Path $PackageDir 'protocol')
if ($LASTEXITCODE -ne 0) { throw 'Protocol resource staging failed' }

Compress-Archive -LiteralPath $PackageDir -DestinationPath (Join-Path $DistDir 'BORING-Console-Community-Windows-portable.zip')
if (-not $SkipInstaller) {
    $Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) { throw 'Inno Setup ISCC.exe is required; use -SkipInstaller only for portable QA' }
    & $Iscc "/DSourceDir=$PackageDir" "/DOutputDir=$DistDir" "/DAppVersion=$AppVersion" (Join-Path $ProjectDir 'deploy\boring-console.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed' }
}
Write-Host "Built unsigned Windows artifacts in $DistDir"
