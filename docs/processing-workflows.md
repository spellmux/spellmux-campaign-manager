# Processing workflows roadmap

Campaign Manager should let a GM choose an intended outcome when adding source material instead
of requiring them to start each processing stage manually. Workflows are dependency-driven: a
downstream stage begins only after its required upstream artifact succeeds.

## Built-in presets

### Store sources only

Import audio, transcript, or notes without starting model work. This supports historical sessions,
manual preparation, and later processing.

### Transcribe

Normalize uploaded audio and produce a timestamped transcript. Diarization and analysis remain
available as later actions.

### Transcribe and diarize

Normalize audio, transcribe it, align timestamps, and run optional speaker diarization. The result
enters transcript and speaker review without requiring a second manual job submission.

### Full overnight processing

Run all enabled stages needed to prepare a Morning Review:

```text
ingest -> normalize -> transcribe -> align -> diarize (optional)
       -> construct speaker-aware transcript -> analyze -> draft typed entries
       -> generate optional image candidates -> Morning Review
```

Image generation should normally be queued after analysis and may be disabled independently.

### Custom

Allow the GM to enable stages and review gates explicitly. A custom workflow can be saved as the
campaign default or instance default.

## Review gates

Some GMs will want unattended overnight processing; others will want to correct speakers before
analysis. Workflows should support gates after transcription, diarization, analysis, and image
generation.

- **Unattended:** analysis uses provisional diarization and clearly labels speaker attribution as
  provisional until reviewed.
- **Speaker-review gate:** pause after diarization; continue analysis after the GM confirms or
  excludes clusters.
- **Approval-only gate:** complete every model stage, but never promote or publish without review.

Changing speaker identities after unattended analysis should offer a targeted reanalysis option.
The original analysis and its provenance remain available for comparison.

## Source-aware execution

The workflow planner skips stages whose prerequisites are absent or whose outputs were supplied:

- audio only can run the complete pipeline;
- audio plus an imported transcript can use the selected transcript and optionally diarize audio;
- transcript only skips audio normalization, transcription, alignment, and diarization;
- notes only can be organized manually or analyzed as a lower-confidence source;
- diarization is always optional;
- analysis can use the GM-selected source when multiple transcripts exist.

The UI should show the resolved workflow before submission, including skipped stages and the
reason for each skip.

## Job orchestration

A workflow run owns individual stage jobs and their dependencies. Do not enqueue every stage as
an immediately runnable job. On successful completion, a stage releases only its eligible
dependents. This prevents analysis from racing transcription or consuming the wrong artifact.

Each workflow run records:

- preset and resolved stages;
- input and output artifact IDs;
- provider/model configuration snapshots;
- stage status, attempts, progress, and errors;
- review gates and the user who released them;
- cancellation, pause, and priority state;
- whether an output is provisional, reviewed, approved, or superseded.

Queue controls operate at both workflow and stage level. Pausing, reprioritizing, or cancelling a
workflow should not require finding every child job. A failed stage can be retried without
repeating successful immutable stages unless the GM explicitly requests a full rerun.

## User experience

The upload form should ask for a processing preset and remember the campaign default. After
submission, the session displays one workflow timeline rather than an unrelated collection of
jobs. The Morning Review landing page should make the final state obvious:

- ready for transcript review;
- waiting for speaker review;
- analysis in progress;
- ready to review findings;
- waiting for image approval;
- ready to publish;
- failed at a named stage with a focused retry action.
