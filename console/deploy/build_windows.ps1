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
# Stage platform-specific public update settings; private signing keys never enter the bundle.
$StagedAssets = Join-Path $BuildDir 'assets'
Copy-Item -LiteralPath (Join-Path $ProjectDir 'src\controller_config\assets') -Destination $StagedAssets -Recurse
$UpdateSource = Join-Path $StagedAssets 'app-update-source.json'
$UpdateConfig = Get-Content -LiteralPath $UpdateSource -Raw | ConvertFrom-Json
if ($env:BORING_WINDOWS_UPDATE_FEED_URL) { $UpdateConfig.windows.feed_url = $env:BORING_WINDOWS_UPDATE_FEED_URL }
if ($env:BORING_WINDOWS_UPDATE_PUBLIC_KEY) {
    if ($UpdateConfig.windows.public_key -and $UpdateConfig.windows.public_key -cne $env:BORING_WINDOWS_UPDATE_PUBLIC_KEY) {
        throw 'Update public key override differs from the configured key; preserve existing users update key'
    }
    $UpdateConfig.windows.public_key = $env:BORING_WINDOWS_UPDATE_PUBLIC_KEY
}
$UpdateConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $UpdateSource -Encoding utf8
$SignTool = $null
if ($env:BORING_WINDOWS_SIGN_CERT_SHA1) {
    $SignTool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    if (-not $SignTool) { throw 'signtool.exe from the Windows SDK is required for signing' }
}
$ValidateUpdateArgs = @($UpdateSource)
if ($SkipInstaller) { $ValidateUpdateArgs += '--skip-installer' }
if ($SignTool) { $ValidateUpdateArgs += '--has-signing-certificate' }
$UpdateChannel = (& $Python (Join-Path $ProjectDir 'tools\validate_windows_update_config.py') @ValidateUpdateArgs).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Desktop update settings or Windows packaging policy is invalid' }
function Sign-Artifact([string]$Path) {
    if (-not $SignTool) { return }
    & $SignTool sign /sha1 $env:BORING_WINDOWS_SIGN_CERT_SHA1 /fd SHA256 /tr https://timestamp.digicert.com /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed: $Path" }
    & $SignTool verify /pa $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed: $Path" }
}
$AppVersion = (& $Python -c 'from controller_config import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $AppVersion) {
    throw 'Unable to read BORING Console Community version'
}

# UI copy is loaded directly from the packaged JSON catalog.
& $Python -c 'from controller_config.text_catalog import get_text_catalog; get_text_catalog()'
if ($LASTEXITCODE -ne 0) { throw 'UI text catalog validation failed' }
$QtTranslations = (& $Python -c 'from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.TranslationsPath))').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to locate Qt translation catalogs' }

& $Python -m nuitka `
    --standalone `
    --enable-plugin=pyside6 `
    --windows-console-mode=disable `
    --output-filename='BORING Console Community.exe' `
    --include-package=controller_config `
    "--include-data-dir=$StagedAssets=controller_config/assets" `
    "--include-data-file=$TrustPolicyMarker=controller_config/assets/device-trust-policy.json" `
    "--include-data-dir=$(Join-Path $ProjectDir 'src\controller_config\translations')=controller_config/translations" `
    "--include-data-files=$QtTranslations/qtbase_ja.qm=PySide6/translations/qtbase_ja.qm" `
    "--include-data-files=$QtTranslations/qtbase_zh_CN.qm=PySide6/translations/qtbase_zh_CN.qm" `
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

Sign-Artifact (Join-Path $PackageDir 'BORING Console Community.exe')
Sign-Artifact (Join-Path $PackageDir 'boring-extension-runner.exe')

Compress-Archive -LiteralPath $PackageDir -DestinationPath (Join-Path $DistDir 'BORING-Console-Community-Windows-portable.zip')
if (-not $SkipInstaller) {
    $Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) { throw 'Inno Setup ISCC.exe is required; use -SkipInstaller only for portable QA' }
    & $Iscc "/DSourceDir=$PackageDir" "/DOutputDir=$DistDir" "/DAppVersion=$AppVersion" (Join-Path $ProjectDir 'deploy\boring-console.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed' }
    Sign-Artifact (Join-Path $DistDir 'BORING-Console-Community-Setup.exe')
}
if ($SignTool) { Write-Host "Built signed Windows artifacts in $DistDir" }
elseif ($UpdateChannel -eq 'trial') { Write-Host "Built unsigned Windows trial installer in $DistDir (Ed25519 update verification enabled)" }
else { Write-Host "Built unsigned development Windows artifacts in $DistDir (desktop updates disabled)" }
