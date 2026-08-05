<#
.SYNOPSIS
    Install or update the Campaign Manager worker on a Windows GPU host.

.DESCRIPTION
    Transcription and diarization need the artifact bytes, so they run in a worker
    beside the accelerator rather than behind an HTTP endpoint. This prepares that
    worker: a virtual environment, the package, and the CUDA runtime libraries
    CTranslate2 loads at import time.

    faster-whisper runs on CTranslate2 rather than torch, so GPU transcription
    needs only the cuBLAS and cuDNN wheels. Torch is installed separately, and
    only if diarization is also moving to this host.

    Run this from an ordinary session. Registering the scheduled task needs the
    account password and is deliberately left to register-task.ps1.
#>
[CmdletBinding()]
param(
    [string]$Root = 'D:\campaign-worker',
    [switch]$IncludeDiarization,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $Root 'venv\Scripts\python.exe'
$source = Join-Path $Root 'src'

function Write-Step { param($Message) Write-Host "==> $Message" }

if (-not (Test-Path $source)) { throw "Source tree not found at $source" }

Write-Step "Preparing virtual environment"
if (-not (Test-Path $python)) {
    python -m venv (Join-Path $Root 'venv')
}
& $python -m pip install --quiet --upgrade pip

if (-not $SkipInstall) {
    Write-Step "Installing campaign-manager with transcription support"
    Push-Location $source
    try {
        & $python -m pip install --quiet '.[transcription]'
        if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit $LASTEXITCODE" }
    } finally { Pop-Location }

    # CTranslate2 loads these DLLs at import time and does not vendor them.
    Write-Step "Installing CUDA runtime libraries for CTranslate2"
    & $python -m pip install --quiet nvidia-cublas-cu12 nvidia-cudnn-cu12
    if ($LASTEXITCODE -ne 0) { throw "CUDA runtime install failed with exit $LASTEXITCODE" }

    if ($IncludeDiarization) {
        Write-Step "Installing torch (CUDA 12) and pyannote for diarization"
        & $python -m pip install --quiet torch torchaudio --index-url https://download.pytorch.org/whl/cu121
        & $python -m pip install --quiet '.[diarization]'
    }
}

Write-Step "Versions"
& $python -m pip list --format=freeze 2>$null |
    Select-String -Pattern '^(campaign-manager|faster-whisper|ctranslate2|nvidia-cu|torch)' |
    ForEach-Object { "    $($_.Line)" }

Write-Step "Checking GPU visibility for CTranslate2"
# The DLL directories have to be registered before CTranslate2 is imported,
# which is why the run wrapper does the same thing.
$probe = @'
import os, glob, sys
base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
for pattern in ("*/bin", "*/lib"):
    for path in glob.glob(os.path.join(base, pattern)):
        if os.path.isdir(path):
            os.add_dll_directory(path)
import ctranslate2
print("ctranslate2", ctranslate2.__version__)
print("cuda devices:", ctranslate2.get_cuda_device_count())
'@
$probePath = Join-Path $Root 'gpu_probe.py'
Set-Content -Path $probePath -Value $probe -Encoding ascii
& $python $probePath

Write-Step "Done. Register the scheduled task with register-task.ps1 to run it unattended."
