## What

<!-- 一句话说明改了什么。 -->

## Why

<!-- 为什么需要这个改动 — 关联的 issue、用户场景或 bug 现象。 -->

## Checklist

- [ ] `uv run ruff check .` 通过
- [ ] `uv run mypy packages/*/src` 通过
- [ ] `uv run pytest` 通过
- [ ] 如改了某个 plugin 包:
  - [ ] `packages/<pkg>/pyproject.toml` 的 `version` 已 bump
  - [ ] `packages/<pkg>/CHANGELOG.md` 顶部加了对应 `## [X.Y.Z] - YYYY-MM-DD` 区块
- [ ] 如改了 packaging 配置(`pyproject.toml`、entry-point、`tests/packaging/`):
      `uv run python scripts/check-package.py dikw-converter-<format>` 本地通过
- [ ] 没有新加 server / engine 侧导入(参见 CLAUDE.md "Layering invariants")
