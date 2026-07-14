[CmdletBinding()]
param(
    [string]$SetupRoot,
    [string]$InstalledApp = "$env:ProgramFiles\Sound Capsule\Sound Capsule.exe",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($SetupRoot)) {
    $SetupRoot = $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($SetupRoot) -and $MyInvocation.MyCommand.Path) {
    $SetupRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($SetupRoot)) {
    throw "Sound Capsule could not determine its setup directory"
}
$UvInstructionsUrl = "https://docs.astral.sh/uv/getting-started/installation/"
$DataRoot = Join-Path $env:LOCALAPPDATA "SoundCapsule"
$LogRoot = Join-Path $DataRoot "Logs"
$LogFile = Join-Path $LogRoot "install.log"
$FailureFile = Join-Path $DataRoot "setup-failed.txt"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
Start-Transcript -Path $LogFile -Append -ErrorAction SilentlyContinue | Out-Null

function Find-Uv {
    $command = Get-Command uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
        return $command.Source
    }

    # GUI apps and DAWs can retain a pre-install PATH, and some hosts launch
    # PowerShell without HOME. Resolve Windows' profile folders independently
    # so Retry Setup can still find both the official and winget installations.
    $profileRoots = @(
        $env:USERPROFILE,
        $HOME,
        [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
    $localAppDataRoots = @(
        $env:LOCALAPPDATA,
        [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    $candidates = @()
    foreach ($profileRoot in $profileRoots) {
        $candidates += [IO.Path]::Combine($profileRoot, ".local", "bin", "uv.exe")
        $candidates += [IO.Path]::Combine($profileRoot, ".cargo", "bin", "uv.exe")
    }
    foreach ($localAppDataRoot in $localAppDataRoots) {
        $candidates += [IO.Path]::Combine(
            $localAppDataRoot, "Microsoft", "WinGet", "Links", "uv.exe"
        )
        $packagesRoot = [IO.Path]::Combine($localAppDataRoot, "Microsoft", "WinGet", "Packages")
        if (Test-Path -LiteralPath $packagesRoot -PathType Container) {
            $wingetPackages = Get-ChildItem -LiteralPath $packagesRoot -Directory -Filter "astral-sh.uv_*" -ErrorAction SilentlyContinue
            foreach ($wingetPackage in $wingetPackages) {
                $candidates += [IO.Path]::Combine($wingetPackage.FullName, "uv.exe")
            }
        }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Show-UvRequired {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $answer = $shell.Popup(
            "Sound Capsule setup needs uv before it can configure the local helper and FL Studio bridge.`n`nWould you like to open the uv installation page?",
            0,
            "uv is required",
            0x34
        )
        if ($answer -eq 6) {
            Start-Process $UvInstructionsUrl
        }
    }
    catch {
        Write-Warning "Could not show the uv instructions popup: $($_.Exception.Message)"
    }
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
        try {
            $sharedData = Get-ItemPropertyValue `
                -LiteralPath "HKCU:\Software\Image-Line\Shared\Paths" `
                -Name "Shared data" `
                -ErrorAction Stop
            $sharedData = [Environment]::ExpandEnvironmentVariables($sharedData).Trim()
            $flUserFolder = if ((Split-Path -Leaf $sharedData) -ieq "FL Studio") {
                $sharedData
            }
            else {
                Join-Path $sharedData "FL Studio"
            }
            $bridge = Join-Path $flUserFolder "Settings\Hardware\Sound Capsule\device_SoundCapsule.py"
            Remove-Item -LiteralPath $bridge -Force -ErrorAction SilentlyContinue
        }
        catch {
            Write-Warning "Could not read FL Studio's user data folder from the registry: $($_.Exception.Message)"
        }
        Write-Host "Sound Capsule per-user runtime integrations removed; settings and Library were preserved"
        exit 0
    }
    Write-Host "Provisioning Sound Capsule"
    $uv = Find-Uv
    if (-not $uv) {
        $message = "uv is required. Install it from $UvInstructionsUrl, then launch Sound Capsule and choose Retry Setup."
        Set-Content -LiteralPath $FailureFile -Value $message -Encoding UTF8
        Show-UvRequired
        Write-Error $message
        exit 1
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
