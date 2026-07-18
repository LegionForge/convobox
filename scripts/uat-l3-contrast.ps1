<#
.SYNOPSIS
  Helper for the [L3] headset AEC on-vs-off contrast test.

.DESCRIPTION
  The two run_convobox.py instances cannot run concurrently (single-instance
  socket lock on 127.0.0.1:47613). This script stops the current listening
  session, then launches the requested config with its OWN log file so the two
  conditions can be diffed.

  The current session is disposable (operator: "I can always restart this with
  another session"), so stopping it is expected.

.EXAMPLE
  # Stop the AEC-off session and start the AEC-on contrast:
  .\scripts\uat-l3-contrast.ps1 -Mode AecOn

  # Stop the AEC-on contrast and go back to the normal AEC-off config:
  .\scripts\uat-l3-contrast.ps1 -Mode AecOff
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('AecOn', 'AecOff')]
    [string]$Mode
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot | Split-Path -Parent

function Stop-ConvoBox {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*run_convobox.py*' }
    if (-not $procs) {
        Write-Host 'No running run_convobox.py found (already stopped?).'
        return
    }
    foreach ($p in $procs) {
        Write-Host "Stopping run_convobox.py pid=$($p.ProcessId) ..."
        Stop-Process -Id $p.ProcessId -Force
    }
    Start-Sleep -Seconds 2
}

function Start-ConvoBox {
    param(
        [string]$ConfigName,
        [string]$LogName
    )
    $venvPython = Join-Path $root '.venv\Scripts\python.exe'
    $scriptPath = Join-Path $root 'scripts\run_convobox.py'
    $config     = Join-Path $root $ConfigName
    $logPath    = Join-Path $root $LogName

    if (-not (Test-Path $venvPython)) { throw "venv python not found: $venvPython" }
    if (-not (Test-Path $config))     { throw "config not found: $config" }

    Write-Host "Launching $ConfigName -> log $LogName (Ctrl+C in its window to stop)"
    # run_convobox.py logs to STDERR (basicConfig with no filename). Start-Process
    # refuses to redirect stdout+stderr to the same file, so give stdout its own
    # file and keep stderr as the primary UAT log.
    $outLog = $logPath -replace '\.log$', '.stdout.log'
    Start-Process -FilePath $venvPython -ArgumentList @(
        $scriptPath, '--config', $config
    ) -RedirectStandardOutput $outLog -RedirectStandardError $logPath `
        -WorkingDirectory $root -WindowStyle Normal
}

switch ($Mode) {
    'AecOn' {
        Stop-ConvoBox
        Start-ConvoBox -ConfigName 'convobox.uat-aec-on.yaml' -LogName 'uat-aec-on.log'
        Write-Host ''
        Write-Host 'Now run the same barge-in exercise as session #81.'
        Write-Host 'Watch for: "AEC stats for last response", "NO ECHO DETECTED",'
        Write-Host '  "dropped (overlap gate, echo-cancellation active):",'
        Write-Host '  "dropped (spoken-echo filter, barge-in was our own echo):".'
        Write-Host 'Then run:  .\scripts\uat-l3-contrast.ps1 -Mode AecOff'
    }
    'AecOff' {
        Stop-ConvoBox
        Start-ConvoBox -ConfigName 'convobox.yaml' -LogName 'uat-aec-off.log'
        Write-Host ''
        Write-Host 'Normal AEC-off session restored (baseline for the diff).'
    }
}
