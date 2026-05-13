# Changelog

All notable changes to `dikw-converter-example` are recorded here.

The format follows [Keep a Changelog 1.1][kac] and this project
adheres to [Semantic Versioning 2.0][semver].

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

This package is a **reference stub** — its versions only exist so the
copy-as-template workflow in `docs/plugin-author-guide.md` round-trips
through the full release pipeline.

## [Unreleased]

## [0.0.1] - 2026-05-13

### Added

- Initial scaffolding. Implements the converter Protocol with a no-op
  `.example` → markdown conversion that copies the input to
  `assets/<input>.example` and emits a stub `<stem>.md`. Intended only
  as a template; not useful in production.

[Unreleased]: https://github.com/opendikw/dikw-plugins/compare/dikw-converter-example-v0.0.1...HEAD
[0.0.1]: https://github.com/opendikw/dikw-plugins/releases/tag/dikw-converter-example-v0.0.1
