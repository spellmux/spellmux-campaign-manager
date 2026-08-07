# Local models and hardware roadmap

This document records target model profiles rather than hard dependencies. Campaign Manager
must remain usable on CPU-only hosts, while allowing individual workers to use larger local
models when suitable hardware is available. Model defaults should be configurable and verified
with campaign-specific benchmarks before becoming release defaults.

Recommendations were last reviewed in August 2026.

## Pipeline model profiles

| Stage | Accessible | Recommended | Quality |
| --- | --- | --- | --- |
| Transcription | faster-whisper small/medium | Whisper large-v3-turbo | Whisper large-v3 |
| Diarization | pyannote on CPU | pyannote Community-1 on GPU | Community-1 plus reviewed campaign voice references |
| Session extraction | Qwen3 4B Q4 | Qwen3 8B Q4 | 20B-35B quantized model |
| Consolidation and prose | Qwen3 4B Q4 | Qwen3 8B Q4 | gpt-oss-20b or a benchmarked Qwen MoE model |
| Retrieval | compact local embedding model | Qwen3-Embedding-0.6B | Qwen3-Embedding-4B plus reranking |
| Images | compact Stable Diffusion model | optimized FLUX.1-schnell | FLUX or Qwen-Image with quantization/offload |

Analysis should use bounded transcript chunks and a measured context window. Reserving a very
large context does not improve a prompt that never uses it; it consumes RAM or VRAM through the
KV cache. Keep a fast profile and a quality profile, and store the selected provider, model,
quantization, context, prompt version, and generation settings with every artifact.

For higher-quality analysis, prefer a staged workflow:

1. extract evidence-backed findings from bounded transcript chunks;
2. checkpoint each completed chunk;
3. retrieve relevant Campaign Guide and prior-session context;
4. consolidate and deduplicate findings with a stronger model;
5. generate recaps and typed proposals with source timestamps;
6. validate the structured result before GM review.

## Hardware targets

### Baseline: CPU or 4 GB GPU

The application remains functional with slower transcription and a 4B analysis model. A 4 GB
GPU can accelerate appropriately sized models, but does not justify a context allocation that
fills VRAM with unused cache. Overnight processing is expected.

### Existing upgrade path: 8 GB GPU

An RTX 2080 Super-class system is suitable for Whisper, GPU diarization, Qwen3 8B Q4, and
optimized image generation when jobs run sequentially. Twenty-billion-parameter models require
substantial system-memory offload and are not the normal profile for this tier.

### Value target: 24 GB GPU

A used RTX 3090-class system is the likely price/performance target if the workflow proves
valuable. Pair it with 64-128 GB system RAM, fast NVMe storage for models and caches, adequate
cooling, and a quality power supply. This tier can run gpt-oss-20b-class analysis and materially
better image models without making workstation hardware a project requirement.

### High-end consumer: 32 GB GPU

A 32 GB consumer GPU supports quantized 20B-35B models with useful context headroom and stronger
image generation. GPU-heavy jobs should still be serialized so transcription, diarization,
analysis, and image generation do not evict one another or degrade Foundry/Plex workloads.

### Workstation: 48-80 GB GPU memory

This tier enables very large local language and image models, including gpt-oss-120b at the
upper end. It is an optional provider profile, not a reasonable baseline for a shareable
self-hosted project.

Multiple GPUs do not automatically behave like one unified pool of VRAM. Some runtimes can
shard models, but communication and placement overhead make one sufficiently large GPU simpler
and often more effective. Separate GPUs remain useful for isolation, such as Plex on one GPU and
Campaign Manager workers on another.

## Campaign visual profiles

Image generation should be optional, asynchronous, reviewable, and campaign-specific. A visual
profile should contain:

- a reusable style prompt and negative prompt;
- palette, line weight, paper/background, framing, and aspect-ratio guidance;
- default resolution, steps, sampler, and model;
- optional reference images, LoRAs, ControlNet inputs, and deterministic seeds;
- separate presets for portraits, creatures, items, locations, handouts, and maps;
- provenance and approval status for every generated candidate.

### Wonderland pen-and-ink profile

Wonderland should initially target monochrome pen-and-ink illustrations inspired by classic
tabletop sourcebooks: confident outlines, restrained cross-hatching, sparse or blank paper
backgrounds, limited shading, and no decorative text unless requested.

The semantic simplicity of an illustration does not by itself make diffusion inference much
cheaper. The efficiency comes from accepting a deliberately constrained output:

- generate at 512-768 pixels instead of 1024 pixels where appropriate;
- use a fast or distilled model with fewer inference steps;
- avoid photorealistic textures, complex lighting, and crowded backgrounds;
- generate a small contact sheet before requesting a high-resolution approved version;
- reuse composition, pose, and style references to reduce retries;
- upscale only approved images;
- store one campaign style profile instead of rebuilding a long prompt for every entity.

This style is well suited to modest hardware because minor detail loss is less objectionable,
small source images remain useful in wiki layouts, and a consistent visual vocabulary matters
more than photorealism. Line-art quality and character consistency still require review; a
simple-looking image is not guaranteed to be easy for every model.

Tactical maps require a different workflow. Generative art may supply textures, landmarks, or
concepts, but exact grids, walls, scale, and Foundry-ready geometry should be produced or
validated deterministically.

## Evaluation before purchasing hardware

Keep a small private benchmark set containing difficult names, overlapping speech, music,
rules discussion, NPC/PC ambiguity, and known session facts. Compare candidate profiles on:

- transcript word/name accuracy;
- diarization corrections required;
- supported versus invented findings;
- entity classification and deduplication;
- JSON/schema success rate;
- recap usefulness and spoiler handling;
- elapsed time, peak RAM/VRAM, power use, and retries;
- image approval rate and consistency with the campaign visual profile.

Hardware decisions should follow these measurements. The near-term path is to validate the full
workflow on existing hardware, evaluate the RTX 2080 Super system, and consider a 24 GB RTX 3090
only if quality or overnight throughput remains the limiting factor.
