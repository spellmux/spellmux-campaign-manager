# Windows GPU worker

Transcription, diarization, and speaker enrollment read audio and write derived
artifacts, so they run in a worker beside the accelerator rather than behind an
HTTP endpoint. The worker polls the same durable queue as the always-on worker
and needs no API change; see [remote compute](../../docs/remote-compute.md) for
why analysis and image generation are routed differently.

## What the host needs

- Python 3.11 or newer, and `ffmpeg` on `PATH` (transcription normalizes audio).
- An NVIDIA GPU with a current driver. `faster-whisper` runs on CTranslate2, not
  torch, so GPU transcription needs only the cuBLAS and cuDNN wheels the install
  script adds. Torch is installed only with `-IncludeDiarization`.
- Read/write access to the artifacts share, and TCP access to PostgreSQL.
- Disk for models. `distil-large-v3` is roughly 1.5 GB; put the install on a roomy
  volume rather than the system drive.

## Install

Ship the source tree, then install. Use `scp` rather than piping through a shell:
binary over standard input is slow and unreliable here.

```bash
git -c core.autocrlf=false archive --format=tar --output=cm.tar HEAD
ssh USER@HOST 'powershell -NoProfile -Command "New-Item -ItemType Directory -Force D:\campaign-worker\src, D:\campaign-worker\logs, D:\campaign-worker\models"'
scp cm.tar USER@HOST:D:/campaign-worker/src.tar
ssh USER@HOST 'powershell -NoProfile -Command "Set-Location D:\campaign-worker\src; tar -xf D:\campaign-worker\src.tar"'
scp deploy/sharn/*.ps1 USER@HOST:D:/campaign-worker/
ssh USER@HOST 'powershell -NoProfile -ExecutionPolicy Bypass -File D:\campaign-worker\install-worker.ps1'
```

The install script is idempotent, so the same command updates an existing host.
It finishes by reporting the CUDA device count; if that is `0`, the GPU is not
visible and transcription would silently run on the CPU.

## Configure

Create `worker.env` beside the scripts. It holds a database credential, so keep it
out of version control.

```ini
CAMPAIGN_DATABASE_URL=postgresql+psycopg://campaign:PASSWORD@DBHOST:5432/campaign
CAMPAIGN_ARTIFACT_ROOT=\\FILESERVER\campaign-artifacts
CAMPAIGN_MODEL_ROOT=D:\campaign-worker\models
CAMPAIGN_TRANSCRIPTION_PROVIDER=faster-whisper
CAMPAIGN_WHISPER_MODEL=distil-large-v3
CAMPAIGN_WHISPER_DEVICE=cuda
CAMPAIGN_WHISPER_COMPUTE_TYPE=int8_float16
```

Two values matter more than they look:

**The artifact root must be a UNC path, not a mapped drive letter.** Drive letters
belong to one logon session and are invisible to a task running unattended, so a
mapped drive works while you are watching and fails afterwards.

**Generate this file with a tool that does not reinterpret backslashes.** A shell
heredoc will quietly collapse `\\FILESERVER` to `\FILESERVER`, which is not a UNC
path, and the first artifact read fails.

## Register

```powershell
.\register-task.ps1 -UserName YOURACCOUNT
```

The task must run as a **real user account with stored credentials**. A task or
service running as SYSTEM cannot reach the share: it starts cleanly and then fails
on the first artifact read. The password is prompted for, never passed as an
argument.

## Hand a job kind over

A job kind must be owned by **exactly one** worker. Orphan recovery requeues jobs
that a stopped worker left running, and it assumes single ownership, so two
workers serving the same kind means restarting one can requeue work still in
progress on the other.

Moving transcription is therefore one change in two places:

1. set `CAMPAIGN_TRANSCRIPTION_PROVIDER=faster-whisper` in `worker.env` here,
2. set `CAMPAIGN_TRANSCRIPTION_PROVIDER=disabled` on the always-on worker,

then restart both. Keep the always-on worker installed but disabled so the
capability can be moved back by changing configuration rather than rebuilding.

## Check it

```powershell
Get-ScheduledTaskInfo -TaskName CampaignWorker
Get-Content D:\campaign-worker\logs\worker.log -Tail 20 -Wait
```

A healthy start logs the supported job kinds. If transcription is enabled and
absent from that list, the provider is not set or its dependencies are missing.

## When something is wrong

**`cuda devices: 0`, or transcription is slow.** CTranslate2 loads cuBLAS and
cuDNN when it is imported and Python does not search `PATH` for dependent DLLs, so
the wheel directories are registered by a `sitecustomize.py` the run wrapper
writes. A `.pth` file cannot do this: its line is executed in a namespace where a
comprehension cannot see its own imports.

**Artifact reads fail while the share works by hand.** Check which account the
task runs as. Interactive logons reach the share; SSH sessions and SYSTEM do not.

**The share is unreachable from a session that should have it.** The Windows SMB
redirector can wedge such that no share on that server is reachable, including
`IPC$`. `Restart-Service LanmanWorkstation -Force`, and reboot if that is not
enough. An unreachable artifact root is reported as such rather than as a
credential error.

**Nothing is claimed even though jobs are queued.** Heavy jobs are serialized
across every worker, so a job elsewhere blocks this one until it finishes. That is
deliberate: it stops two model stages competing for one accelerator.
