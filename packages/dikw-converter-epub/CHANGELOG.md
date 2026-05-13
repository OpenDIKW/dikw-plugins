# Changelog

All notable changes to `dikw-converter-epub` are recorded here.

The format follows [Keep a Changelog 1.1][kac] and this project
adheres to [Semantic Versioning 2.0][semver].

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

## [Unreleased]

## [0.1.0] - 2026-05-13

### Added

- First release. Pure-Python `.epub` → markdown converter plugin for
  `dikw client import`. Uses only the standard library; no external
  parser dependency.
- Registers the `epub` entry-point under `dikw.client.converters`.
- Writes `<stem>.md` alongside `assets/` containing extracted images
  and the original EPUB for provenance, with image references rewritten
  to point inside `assets/`.

[Unreleased]: https://github.com/opendikw/dikw-plugins/compare/dikw-converter-epub-v0.1.0...HEAD
[0.1.0]: https://github.com/opendikw/dikw-plugins/releases/tag/dikw-converter-epub-v0.1.0
