# Changelog

All notable changes to `dikw-converter-mineru` are recorded here.

The format follows [Keep a Changelog 1.1][kac] and this project
adheres to [Semantic Versioning 2.0][semver].

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

## [0.1.0] - 2026-05-13

### Added

- First release. Converter plugin that uploads PDF / DOCX / PPTX / XLSX
  (and their legacy variants) to the MinerU online API and writes the
  returned markdown plus assets into the `dikw client import` layout.
- Registers the `mineru` entry-point under `dikw.client.converters`.
- Requires `MinerUAPIKey` to be set in the environment; rejects calls
  without it before any network traffic.
- Defensive ZIP extraction: refuses zip-slip paths, validates shape,
  and cleans up orphan assets if the run aborts mid-stream.

[Unreleased]: https://github.com/opendikw/dikw-plugins/compare/dikw-converter-mineru-v0.1.0...HEAD
[0.1.0]: https://github.com/opendikw/dikw-plugins/releases/tag/dikw-converter-mineru-v0.1.0
