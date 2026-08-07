<#
.SYNOPSIS
    Launch the Campaign Manager worker on a Windows GPU host.

.DESCRIPTION
    Reads configuration from worker.env beside this script, registers the CUDA
    runtime DLL directories, and runs campaign-worker in the foreground so a
    scheduled task or service supervises it.

    Two things this deliberately does:

    CTranslate2 loads cuBLAS and cuDNN at import time and Python 3.8+ will not
    search PATH for them, so the directories from the nvidia wheels are added
    explicitly. Without this the worker falls back to CPU or fails to import.

    The artifact root is a UNC path, not a mapped drive. Drive letters belong to
    one logon session and are invisible to a service, so a mapped drive would work
    interactively and then fail unattended.
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\campaign-worker'
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $Root 'venv\Scripts\python.exe'
$envFile = Join-Path $Root 'worker.env'

if (-not (Test-Path $python)) { throw "Virtual environment missing at $python" }
if (-not (Test-Path $envFile)) { throw "Configuration missing at $envFile" }

foreach ($line in Get-Content $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $split = $trimmed.IndexOf('=')
    if ($split -lt 1) { continue }
    $name = $trimmed.Substring(0, $split).Trim()
    $value = $trimmed.Substring($split + 1).Trim()
    Set-Item -Path "Env:$name" -Value $value
}

# sitecustomize is imported automatically at interpreter startup, so the console
# script picks this up without being wrapped. A .pth cannot be used here: its line
# is exec'd in a namespace where a comprehension cannot see the imports.
$siteDir = & $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
Remove-Item (Join-Path $siteDir 'cm_cuda_dlls.pth') -Force -ErrorAction SilentlyContinue
$shim = @'
"""Register the CUDA runtime DLL directories shipped by the nvidia wheels.

CTranslate2 loads cuBLAS and cuDNN when it is imported, and Python 3.8+ does not
search PATH for dependent DLLs, so without this the GPU is invisible.
"""

import glob
import os
import sysconfig

_base = os.path.join(sysconfig.get_paths()["purelib"], "nvidia")
for _pattern in ("*/bin", "*/lib"):
    for _path in glob.glob(os.path.join(_base, _pattern)):
        if os.path.isdir(_path):
            try:
                os.add_dll_directory(_path)
            except OSError:
                pass
'@
Set-Content -Path (Join-Path $siteDir 'sitecustomize.py') -Value $shim -Encoding ascii

# Unbuffered, or a crash loses whatever the worker was about to say.
$env:PYTHONUNBUFFERED = '1'

# Not $root: PowerShell variables are case-insensitive, so that would
# silently overwrite the $Root parameter and break every path below.
$artifactRoot = $env:CAMPAIGN_ARTIFACT_ROOT
Write-Host "Starting campaign-worker (artifact root: $artifactRoot)"

# Fail fast and say why. A task that cannot reach the share otherwise starts
# cleanly and only fails on the first artifact read, long after the cause.
if ($artifactRoot) {
    try {
        $null = Get-ChildItem -LiteralPath $artifactRoot -ErrorAction Stop | Select-Object -First 1
        Write-Host "Artifact root is readable."
    } catch {
        Write-Host "WARNING: artifact root is NOT readable as $env:USERNAME : $($_.Exception.Message)"
        Write-Host "Jobs needing artifacts will fail until this session can reach the share."
    }
}

# Invoked directly rather than through Start-Process: with redirection and no
# console attached that raises UnauthorizedAccessException.
#
# The worker logs to stderr, and under redirection PowerShell wraps a native
# command's stderr in error records. With Stop that makes the first log line a
# terminating error, so the worker appeared to exit immediately having said
# nothing. Both streams are left alone and flow to whatever the caller captures.
$ErrorActionPreference = 'Continue'
$exe = Join-Path $Root 'venv\Scripts\campaign-worker.exe'
& $exe
Write-Host "campaign-worker exited with code $LASTEXITCODE"
