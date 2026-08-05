# Remote compute

The reference deployment keeps PostgreSQL, artifact storage, and the HTTP API on
one always-on host, and moves model work to whatever hardware is available. This
records how capabilities are routed and why they are not all routed the same way.

## Two mechanisms, not one

Offloading takes one of two shapes depending on what the stage needs.

**Service capabilities** run behind an HTTP API on the remote machine. The
application sends a request and receives a result, so it needs no shared
filesystem. Analysis works this way today: administrators register an
Ollama-compatible endpoint under Compute Workers and the dispatcher selects the
highest-priority healthy one. Image generation belongs here too, because the
diffusion ecosystem already exposes HTTP APIs and embedding a pipeline in the
worker would gain nothing.

**Worker capabilities** need the artifact bytes and a filesystem. Transcription,
diarization, and speaker enrollment all read audio and write derived artifacts, so
they want a worker process beside the accelerator with access to artifact storage.
`claim_next_job` polls the durable queue, so a second worker on another machine
requires no protocol change and no API change.

The `capabilities` field on a compute worker record therefore describes intent,
not one implementation: a URL is meaningful for a service capability and not for a
worker capability.

| Capability | Mechanism | Needs artifact access |
| --- | --- | --- |
| analysis | service (Ollama) | no |
| image_generation | service (ComfyUI or similar) | no |
| transcription | worker process | yes |
| diarization | worker process | yes |
| speaker_enrollment | worker process | yes |

## Planned layout

A single workstation GPU is the intended target for all of the above. Service
capabilities are reachable over the LAN. Worker capabilities need one narrow
export of the artifacts directory; exporting the whole application data directory
would expose the database files and publishing repository and is not required.

Running a worker natively is preferred over a container on a Windows
workstation: accelerator access is direct, the artifact share mounts natively, and
no container runtime has to stay resident. The cost is a second dependency set and
a second deployment target, so that deployment should be scripted rather than
performed by hand.

## Single-accelerator constraints

`HEAVY_JOB_KINDS` serializes heavy work across every worker, so two model stages
never execute at once. That is necessary but not sufficient on one card, because
serializing jobs does not evict models:

- a language model server may hold its weights resident after a request, so
  `CAMPAIGN_ANALYSIS_KEEP_ALIVE_SECONDS` releases them sooner than the gap
  between analysis chunks;
- transcription and image generation model sizes have to be chosen to fit
  alongside whatever remains resident, rather than assuming the whole card.

## Failover

Analysis falls back to the bundled endpoint when no managed worker is healthy.
Worker capabilities have no equivalent: whichever host owns a job kind is the only
host that can run it. Keep the always-on worker installed but disabled, so the
capability can be moved back by changing configuration.

A job kind must be enabled on exactly one worker. Orphan recovery requeues jobs
left running by a stopped worker, and it assumes single ownership; two workers
serving the same kind means a restart on one can requeue work in flight on the
other.
