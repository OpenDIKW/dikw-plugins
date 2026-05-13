"""Session-scoped fixtures for artifact-level packaging tests.

These tests build each ``packages/dikw-converter-*`` package with
``uv build``, then install the resulting wheel into an ephemeral venv
to verify that ``dikw client`` would actually discover it. One build
and one venv per package per pytest run.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
import venv
from dataclasses import dataclass, field
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGES_DIR = WORKSPACE_ROOT / "packages"
ENTRY_POINT_GROUP = "dikw.client.converters"


def _discover_packages() -> list[str]:
    return sorted(
        d.name
        for d in PACKAGES_DIR.iterdir()
        if d.is_dir()
        and d.name.startswith("dikw-converter-")
        and (d / "pyproject.toml").is_file()
    )


@dataclass(frozen=True)
class PackageMeta:
    """Snapshot of a workspace package's ``pyproject.toml``."""

    name: str
    """Distribution name, e.g. ``dikw-converter-epub``."""

    dir: Path
    """Absolute path to the package's source directory under ``packages/``."""

    version: str

    entry_points: dict[str, str] = field(default_factory=dict)
    """Mapping under ``[project.entry-points."dikw.client.converters"]``."""

    requires_python: str = ""

    dependencies: list[str] = field(default_factory=list)

    @property
    def module_name(self) -> str:
        """Importable module name with underscores, e.g. ``dikw_converter_epub``."""
        return self.name.replace("-", "_")


@dataclass(frozen=True)
class BuiltDist:
    wheel: Path
    sdist: Path
    dist_dir: Path


def _load_package_meta(pkg_name: str) -> PackageMeta:
    pkg_dir = PACKAGES_DIR / pkg_name
    with (pkg_dir / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    project = data["project"]
    entry_points_table = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    return PackageMeta(
        name=project["name"],
        dir=pkg_dir,
        version=project["version"],
        entry_points=dict(entry_points_table),
        requires_python=project.get("requires-python", ""),
        dependencies=list(project.get("dependencies", [])),
    )


@pytest.fixture(
    scope="session",
    params=_discover_packages(),
    # Use the importable module form (underscores) as the test id so
    # ``pytest -k dikw_converter_epub`` selects cleanly. Hyphenated ids
    # are parsed by pytest's ``-k`` evaluator as a subtraction expression
    # (``a - b - c``) and only work by Python integer-arithmetic accident.
    ids=lambda name: name.replace("-", "_"),
)
def package(request: pytest.FixtureRequest) -> PackageMeta:
    """One PackageMeta per ``packages/dikw-converter-*`` directory."""
    return _load_package_meta(str(request.param))


@pytest.fixture(scope="session")
def built_dist(
    package: PackageMeta, tmp_path_factory: pytest.TempPathFactory
) -> BuiltDist:
    """Build the package once per session via ``uv build --package``.

    Output goes to a session-scoped tmp dir so we don't pollute
    the workspace's ``dist/``.
    """
    out_dir = tmp_path_factory.mktemp(f"dist-{package.name}")
    subprocess.run(
        [
            "uv",
            "build",
            "--package",
            package.name,
            "--out-dir",
            str(out_dir),
        ],
        cwd=WORKSPACE_ROOT,
        check=True,
    )
    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, f"expected 1 wheel for {package.name}, got {wheels}"
    assert len(sdists) == 1, f"expected 1 sdist for {package.name}, got {sdists}"
    return BuiltDist(wheel=wheels[0], sdist=sdists[0], dist_dir=out_dir)


@pytest.fixture(scope="session")
def core_install_source() -> str | None:
    """Where to install ``dikw-core`` from when seeding the ephemeral venv.

    Prefer the sibling checkout that workspace dev uses (and that
    ``release.yml`` mirrors); fall back to PyPI by returning ``None``.
    """
    sibling = WORKSPACE_ROOT.parent / "dikw-core"
    if sibling.is_dir() and (sibling / "pyproject.toml").is_file():
        return str(sibling)
    return None


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


@pytest.fixture(scope="session")
def ephemeral_venv(
    package: PackageMeta,
    built_dist: BuiltDist,
    core_install_source: str | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Clean venv with ``dikw-core`` + the built wheel installed.

    Returns the venv's python interpreter path. Caller spawns subprocess
    probes against it to verify import / entry-point discovery from the
    consumer's perspective.
    """
    venv_dir = tmp_path_factory.mktemp(f"venv-{package.name}")
    venv.create(venv_dir, with_pip=True, clear=True)
    python = _venv_python(venv_dir)
    if core_install_source is not None:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", core_install_source],
            check=True,
        )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(built_dist.wheel)],
        check=True,
    )
    return python
