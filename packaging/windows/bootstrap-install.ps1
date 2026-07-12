[CmdletBinding()]
param(
    [string]$SetupRoot = $PSScriptRoot,
    [string]$InstalledApp = "$env:ProgramFiles\Sound Capsule\Sound Capsule.exe",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$UvInstallUrl = "https://astral.sh/uv/install.ps1"
$DataRoot = Join-Path $env:LOCALAPPDATA "SoundCapsule"
$LogRoot = Join-Path $DataRoot "Logs"
$LogFile = Join-Path $LogRoot "install.log"
$FailureFile = Join-Path $DataRoot "setup-failed.txt"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
Start-Transcript -Path $LogFile -Append -ErrorAction SilentlyContinue | Out-Null

function Find-Uv {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

try {
    if ($Uninstall) {
        Write-Host "Removing Sound Capsule per-user runtime integrations"
        foreach ($path in @(
            (Join-Path $DataRoot "Helper"),
            (Join-Path $DataRoot "venv"),
            (Join-Path $DataRoot "soundcapsule.cmd")
        )) {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
        $bridge = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Image-Line\FL Studio\Settings\Hardware\Sound Capsule\device_SoundCapsule.py"
        Remove-Item -LiteralPath $bridge -Force -ErrorAction SilentlyContinue
        Write-Host "Sound Capsule per-user runtime integrations removed; settings and Library were preserved"
        exit 0
    }
    Write-Host "Provisioning Sound Capsule"
    $uv = Find-Uv
    if (-not $uv) {
        Write-Host "uv was not found; attempting the official latest uv installer"
        $installer = Join-Path $env:TEMP "sound-capsule-uv-install.ps1"
        Invoke-WebRequest -UseBasicParsing -Uri $UvInstallUrl -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        $uv = Find-Uv
    }
    if (-not $uv) {
        throw "uv could not be located after installation"
    }

    $installScript = Join-Path $SetupRoot "scripts\install.py"
    & $uv run --python 3.12 $installScript --uv-executable $uv --installed-app $InstalledApp
    if ($LASTEXITCODE -ne 0) { throw "Sound Capsule install.py exited with code $LASTEXITCODE" }

    $legacy = Join-Path $env:LOCALAPPDATA "Programs\Sound Capsule"
    if ((Test-Path -LiteralPath $legacy) -and -not $InstalledApp.StartsWith($legacy, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $legacy -Recurse -Force
    }
    $legacyVst = Join-Path $env:LOCALAPPDATA "Programs\Common\VST3\Sound Capsule.vst3"
    $systemVst = Join-Path $env:ProgramFiles "Common Files\VST3\Sound Capsule.vst3"
    if ((Test-Path -LiteralPath $systemVst) -and (Test-Path -LiteralPath $legacyVst)) {
        Remove-Item -LiteralPath $legacyVst -Recurse -Force
    }
    Remove-Item -LiteralPath $FailureFile -Force -ErrorAction SilentlyContinue
    Write-Host "Sound Capsule provisioning complete"
    exit 0
}
catch {
    $message = "Sound Capsule setup could not finish: $($_.Exception.Message). Install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry setup."
    Set-Content -LiteralPath $FailureFile -Value $message -Encoding UTF8
    Write-Error $message
    exit 1
}
finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
}
