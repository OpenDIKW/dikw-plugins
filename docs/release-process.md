# Release process

How a `dikw-converter-*` plugin gets from a workspace package to a
versioned PyPI release. This is the operational manual for maintainers;
plugin authors should read [`plugin-author-guide.md`](plugin-author-guide.md)
§ 6 for the author-side checklist that precedes this.

## Tag convention

```
dikw-converter-<format>-v<X.Y.Z>
```

- `<format>` matches the package directory name under `packages/`
  (e.g. `dikw-converter-epub`).
- `<X.Y.Z>` is the [SemVer 2.0][semver] version exactly as it appears
  in that package's `pyproject.toml`. Pre-release suffixes
  (`-rc.1`, `.dev0`, …) are accepted by the workflow's regex.
- Push the tag — that alone triggers `.github/workflows/release.yml`.
  Nothing else publishes; the tag is the sole release switch.

[semver]: https://semver.org/spec/v2.0.0.html

## What the pipeline does

`.github/workflows/release.yml`, in order:

1. **Parse the tag** into `package` and `version` using the regex
   `^(dikw-converter-[a-z0-9-]+)-v([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*)$`.
2. **Checkout** this repo plus a sibling checkout of `dikw-core@main`
   (same layout as local dev).
3. **Verify** the tag version equals `packages/<package>/pyproject.toml`'s
   `project.version` — a tag without a matching version bump fails fast.
4. **Sync** the uv workspace with `uv sync --all-extras`.
5. **Extract** the CHANGELOG block via
   `scripts/extract_changelog.py packages/<package>/CHANGELOG.md <version>`
   into `${RUNNER_TEMP}/release-notes.md`. Missing block ⇒ fail.
6. **Run the per-package unit tests** with
   `uv run pytest packages/<package> -v`. These are the plugin's own
   tests — no `-k` filter so every test in the package's `tests/` dir
   runs.
7. **Run the artifact-level packaging gate** with
   `uv run pytest tests/packaging -k <package-underscore-form> -v`,
   where the underscore form is `<package>` with `-` replaced by `_`
   (matches the parametrize ids set in
   `tests/packaging/conftest.py`). Steps 6 and 7 are separate so the
   artifact gate's `-k` filter cannot accidentally deselect the
   package's own unit tests.
8. **Build** with `uv build --package <package> --out-dir dist`.
9. **`twine check --strict`** on `dist/*.whl dist/*.tar.gz`
   (defense-in-depth; the artifact tests already did this, but build
   environments can drift).
10. **Publish** to PyPI via `pypa/gh-action-pypi-publish` using OIDC
    trusted publishing — no API token in repo secrets.
11. **Create** a GitHub Release whose body is the extracted CHANGELOG
    block, with the wheel and sdist attached.

## PyPI Trusted Publisher setup

This is a one-time PyPI-side configuration per package name. Two
flavours:

- **First-ever release of a new package name** (the name has never
  existed on PyPI): use the [Pending Publisher form][pending]. PyPI
  reserves the name to be claimed on first publish from a matching
  workflow.
- **Subsequent releases / pre-existing names**: under
  `https://pypi.org/manage/project/<package>/settings/publishing/`,
  add a GitHub Trusted Publisher with:
  - **Owner**: `opendikw`
  - **Repository**: `dikw-plugins`
  - **Workflow filename**: `release.yml`
  - **Environment**: (leave blank)

[pending]: https://pypi.org/manage/account/publishing/

No API tokens are stored in GitHub secrets — OIDC issues a short-lived
token at publish time scoped to that one workflow run.

## Local pre-release gate

Before tagging, always run:

```bash
uv run python scripts/check-package.py dikw-converter-<format>
```

This composes the same checks `release.yml` runs:

- `scripts/extract_changelog.py` — fails if no CHANGELOG entry for the
  current `pyproject.toml` version, and prints the would-be release
  notes for human review.
- `uv run pytest tests/packaging -k <package> -v` — the six artifact
  tests per package. Build, `twine check --strict`, entry-point
  registration, METADATA consistency, install-into-clean-venv
  discovery, and CHANGELOG presence are all driven by the fixtures
  — no separate `uv build` invocation is needed.

If the local gate fails, the CI gate will fail the same way — fix and
re-run before tagging. To inspect the actual wheel/sdist files
(filenames, sizes), run `uv build --package <package>` directly.

## Rollback

If a release was accidentally published or a critical bug surfaces
post-publish:

1. **Yank on PyPI** (does not delete; signals "don't auto-install"):

   ```bash
   pip install --upgrade twine
   twine yank dikw-converter-<format>==X.Y.Z --reason "<why>"
   ```

   Or use the project settings page under `https://pypi.org/manage/project/<package>/release/<version>/`.
   PyPI does not allow deleting a released version — yanking is the
   maximal correction.

2. **Delete the GitHub Release** (the tag stays, but the published
   artifacts and notes go away):

   ```bash
   gh release delete dikw-converter-<format>-vX.Y.Z
   ```

3. **Cut a fix release** at `X.Y.(Z+1)`. Do not retag `X.Y.Z` — PyPI's
   `skip-existing: true` would silently skip the re-publish and you'd
   ship nothing.

## Hotfix flow

For a regression in a released version:

1. Branch off the release tag if `main` has moved on:

   ```bash
   git checkout -b hotfix/<format>-X.Y.Z+1 dikw-converter-<format>-vX.Y.Z
   ```

2. Bump version to `X.Y.(Z+1)`, add a CHANGELOG entry under
   `### Fixed`, commit.
3. Run `scripts/check-package.py`.
4. Merge the hotfix back to `main`, then tag from `main` (so the tag
   commit is part of the linear history).

## Files involved

- `.github/workflows/release.yml` — the pipeline driving everything.
- `scripts/extract_changelog.py` — used by `release.yml` and
  `check-package.py`.
- `scripts/check-package.py` — local pre-release orchestrator.
- `tests/packaging/` — artifact-level gate.
- `packages/<package>/pyproject.toml` — the source of truth for
  `name`, `version`, `dependencies`, and the entry-points table.
- `packages/<package>/CHANGELOG.md` — the source of truth for release
  notes.
