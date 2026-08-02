# Contributing

Thanks for helping improve Spellmux Campaign Manager.

## Before opening a change

- Search existing issues and discussions before proposing overlapping work.
- Open an issue first for substantial features, schema changes, or new external integrations.
- Never include campaign recordings, transcripts, credentials, model files, or copyrighted
  sourcebooks in commits or test fixtures.

## Development workflow

1. Fork the repository and create a focused branch.
2. Install the development dependencies with `python -m pip install -e ".[dev]"`.
3. Add tests for behavior changes.
4. Run `python -m pytest` and `python -m ruff check .`.
5. Open a pull request explaining the user impact and validation performed.

Keep migrations additive and reversible. Preserve provider boundaries so transcription,
diarization, analysis, publishing, and future image generation remain replaceable.

By contributing, you agree that your contribution is licensed under AGPL-3.0-or-later.
