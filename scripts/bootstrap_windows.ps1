<#
.SYNOPSIS
    Bootstrap and smoke-test ConvoBox on Windows.

.DESCRIPTION
    Every dependency this project uses ships a Windows wheel, and the
    codebase itself has zero platform-specific code (verified: no
    sys.platform/platform.system branches, no subprocess/shell-outs,
    pathlib everywhere) -- but as of this script being written, ConvoBox
    has only ever actually been run on macOS. Nothing here has been
    verified on Windows. This script is that first real test: it sets up
    the environment and runs everything that doesn't need a microphone,
    then reports a clear pass/fail summary.

    Run this from the repo root, in PowerShell, after cloning:
        git clone <repo-url> convobox
        cd convobox
        .\scripts\bootstrap_windows.ps1
#>

$ErrorActionPreference = "Stop"
$results = @()

function Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    try {
        & $Action
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            throw "exited with code $LASTEXITCODE"
        }
        $script:results += [pscustomobject]@{ Step = $Name; Status = "PASS"; Detail = "" }
        Write-Host "PASS: $Name" -ForegroundColor Green
    } catch {
        $script:results += [pscustomobject]@{ Step = $Name; Status = "FAIL"; Detail = $_.Exception.Message }
        Write-Host "FAIL: $Name -- $($_.Exception.Message)" -ForegroundColor Red
    }
}

Step "Python >= 3.11" {
    $pyVersion = (python --version 2>&1).ToString()
    Write-Host $pyVersion
    $verNum = ($pyVersion -replace 'Python ', '').Trim()
    $parts = $verNum.Split('.')
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        throw "found Python $verNum, need >= 3.11 (install from python.org or winget)"
    }
}

Step "uv package manager" {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Host "uv not found on PATH, installing via pip --user..."
        python -m pip install --user uv
    }
    uv --version
}

Step "uv sync --extra dev (installs torch, faster-whisper, silero-vad, piper-tts, sounddevice, ...)" {
    uv sync --extra dev
}

Step "pytest: 63 tests, pure logic + mocked hardware, no mic/models needed" {
    .\.venv\Scripts\python.exe -m pytest tests/ -q
}

Step "mypy: type check across src/, scripts/, tests/" {
    .\.venv\Scripts\python.exe -m mypy src/ scripts/ tests/ --ignore-missing-imports
}

Step "list audio devices (sounddevice / PortAudio actually loading on Windows)" {
    .\.venv\Scripts\python.exe scripts\spike.py --list-devices
}

Write-Host ""
$doModels = Read-Host "Download real models and run the TTS/STT round-trip + spike smoke test? Downloads ~300MB, takes a few minutes, needs no microphone. [y/N]"
if ($doModels -eq "y" -or $doModels -eq "Y") {
    Step "download Piper voice (en_US-lessac-medium)" {
        uv run python -m piper.download_voices en_US-lessac-medium --download-dir .models/piper
    }
    Step "real TTS -> STT round trip (real Piper + real faster-whisper)" {
        .\.venv\Scripts\python.exe scripts\roundtrip_smoketest.py
    }
    Step "spike.py smoke test (fake mic feed of real synthesized speech, real VAD/STT/safeword)" {
        .\.venv\Scripts\python.exe scripts\spike_smoketest.py
    }
} else {
    Write-Host "Skipped model downloads. Run them later with:" -ForegroundColor Yellow
    Write-Host "  uv run python -m piper.download_voices en_US-lessac-medium --download-dir .models/piper"
    Write-Host "  .\.venv\Scripts\python.exe scripts\roundtrip_smoketest.py"
    Write-Host "  .\.venv\Scripts\python.exe scripts\spike_smoketest.py"
}

Write-Host ""
Write-Host "=================== SUMMARY ===================" -ForegroundColor Cyan
$results | Format-Table -AutoSize
$failed = $results | Where-Object { $_.Status -eq "FAIL" }
if ($failed) {
    Write-Host "$($failed.Count) step(s) failed -- see output above." -ForegroundColor Red
    Write-Host "This is genuinely useful signal: nothing in this repo has been Windows-tested before this run." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "All steps passed. This is the first time ConvoBox has been verified on Windows." -ForegroundColor Green
    Write-Host "Still untested even after this: a REAL microphone through scripts\spike.py (this script only fakes the mic)." -ForegroundColor Yellow
}
