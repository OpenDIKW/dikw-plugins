"""Artifact-level invariants for every ``packages/dikw-converter-*``.

Each test runs once per package via the parametrized ``package``
fixture in ``conftest.py``. Together they enforce the contract:

* ``uv build --package`` produces a sdist and a wheel with the
  declared version.
* ``twine check --strict`` accepts both artifacts.
* The wheel's ``entry_points.txt`` matches ``pyproject.toml`` exactly.
* The wheel's ``METADATA`` is consistent with ``pyproject.toml`` —
  no dependency dropped, no version drift.
* Installing the wheel into a clean venv exposes the converter
  through ``importlib.metadata.entry_points`` so ``dikw client``
  can actually discover it.
* The package's ``CHANGELOG.md`` has an entry for the current version.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import subprocess
import zipfile
from email import message_from_string
from pathlib import Path
from types import ModuleType

from conftest import ENTRY_POINT_GROUP, WORKSPACE_ROOT, BuiltDist, PackageMeta
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _load_extract_changelog() -> ModuleType:
    path = WORKSPACE_ROOT / "scripts" / "extract_changelog.py"
    spec = importlib.util.spec_from_file_location("extract_changelog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_extract_changelog = _load_extract_changelog()


def test_build_produces_wheel_and_sdist(
    package: PackageMeta, built_dist: BuiltDist
) -> None:
    assert built_dist.wheel.is_file()
    assert built_dist.sdist.is_file()
    expected_prefix = f"{package.module_name}-{package.version}"
    assert built_dist.wheel.name.startswith(expected_prefix), (
        f"wheel {built_dist.wheel.name!r} does not start with "
        f"{expected_prefix!r} — version drift between filename and pyproject"
    )
    assert built_dist.sdist.name == f"{expected_prefix}.tar.gz"


def test_twine_check_passes(package: PackageMeta, built_dist: BuiltDist) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "twine",
            "check",
            "--strict",
            str(built_dist.wheel),
            str(built_dist.sdist),
        ],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"twine check failed for {package.name}:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_wheel_declares_dikw_entry_point(
    package: PackageMeta, built_dist: BuiltDist
) -> None:
    with zipfile.ZipFile(built_dist.wheel) as z:
        ep_path = next(
            (n for n in z.namelist() if n.endswith(".dist-info/entry_points.txt")),
            None,
        )
        assert ep_path is not None, "wheel has no entry_points.txt"
        content = z.read(ep_path).decode("utf-8")
    cp = configparser.ConfigParser()
    cp.read_string(content)
    actual = dict(cp[ENTRY_POINT_GROUP]) if ENTRY_POINT_GROUP in cp else {}
    assert actual == package.entry_points, (
        f"entry_points.txt mismatch for {package.name}:\n"
        f"  pyproject : {package.entry_points}\n  wheel     : {actual}"
    )


def test_wheel_metadata_matches_pyproject(
    package: PackageMeta, built_dist: BuiltDist
) -> None:
    with zipfile.ZipFile(built_dist.wheel) as z:
        meta_path = next(
            (n for n in z.namelist() if n.endswith(".dist-info/METADATA")),
            None,
        )
        assert meta_path is not None, "wheel has no METADATA"
        raw = z.read(meta_path).decode("utf-8")
    msg = message_from_string(raw)
    assert msg["Name"] == package.name
    assert msg["Version"] == package.version
    assert msg["Requires-Python"] == package.requires_python
    expected_deps = {_canonicalize_requirement(d) for d in package.dependencies}
    raw_actual = msg.get_all("Requires-Dist") or []
    # Drop ``Requires-Dist: pkg ; extra == "x"`` rows — those come from
    # ``[project.optional-dependencies]`` extras, not the base deps the
    # test is comparing against.
    actual_deps = {
        _canonicalize_requirement(d)
        for d in raw_actual
        if not _requirement_is_extras_only(d)
    }
    assert actual_deps == expected_deps, (
        f"Requires-Dist mismatch for {package.name}:\n"
        f"  pyproject : {sorted(expected_deps)}\n"
        f"  METADATA  : {sorted(actual_deps)}"
    )


def test_install_in_venv_exposes_entry_point(
    package: PackageMeta, ephemeral_venv: Path
) -> None:
    probe = (
        "import importlib.metadata as md, json, sys\n"
        "dist_name = sys.argv[1]\n"
        f"group = {ENTRY_POINT_GROUP!r}\n"
        "found = {}\n"
        "for e in md.entry_points(group=group):\n"
        "    if e.dist is None or e.dist.metadata['Name'] != dist_name:\n"
        "        continue\n"
        "    cls = e.load()\n"
        "    inst = cls()\n"
        "    found[e.name] = {\n"
        "        'value': e.value,\n"
        "        'name_attr': inst.name,\n"
        "        'extensions': list(inst.extensions),\n"
        "        'has_convert': callable(getattr(inst, 'convert', None)),\n"
        "    }\n"
        "sys.stdout.write(json.dumps(found))\n"
    )
    result = subprocess.run(
        [str(ephemeral_venv), "-c", probe, package.name],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"entry-point probe failed for {package.name} (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    found: dict[str, dict[str, object]] = json.loads(result.stdout)
    assert set(found.keys()) == set(package.entry_points.keys()), (
        f"entry-point keys exposed by installed wheel do not match pyproject "
        f"for {package.name}: {sorted(found.keys())} vs {sorted(package.entry_points)}"
    )
    for key, spec in package.entry_points.items():
        entry = found[key]
        assert entry["value"] == spec
        assert entry["has_convert"] is True
        assert isinstance(entry["name_attr"], str) and entry["name_attr"]
        assert isinstance(entry["extensions"], list)


def test_changelog_has_entry_for_current_version(package: PackageMeta) -> None:
    changelog = package.dir / "CHANGELOG.md"
    assert changelog.is_file(), (
        f"{changelog} is missing — every package must keep a CHANGELOG.md "
        f"with an entry per release"
    )
    text = changelog.read_text(encoding="utf-8")
    try:
        body = _extract_changelog.extract(text, package.version)
    except ValueError as exc:
        raise AssertionError(
            f"{changelog}: {exc} — bump the version and the CHANGELOG "
            f"in the same commit"
        ) from exc
    assert body.strip(), f"{changelog}: '## [{package.version}]' section is empty"


def _canonicalize_requirement(spec: str) -> tuple[str, str, str, frozenset[str]]:
    """Parse a PEP 508 requirement into a canonical tuple for set comparison.

    Hatchling may re-emit ``dikw-core>=0.0.1`` as ``dikw-core >=0.0.1`` or
    reorder marker whitespace; the ``packaging`` lib parses both into the
    same ``Requirement`` so the test catches real drift instead of cosmetic.
    ``canonicalize_name`` collapses ``dikw-core`` and ``dikw_core`` to the
    same form so the comparison survives PEP 503 name normalization.
    """
    req = Requirement(spec)
    return (
        canonicalize_name(req.name),
        str(req.specifier),
        str(req.marker) if req.marker else "",
        frozenset(req.extras),
    )


def _requirement_is_extras_only(spec: str) -> bool:
    """True if ``spec`` is a Requires-Dist row gated on ``extra == "..."``.

    Hatchling emits one of these per entry in ``[project.optional-dependencies]``.
    They are not part of the base install footprint, so the metadata test
    filters them out — base deps in ``pyproject.toml`` already covers them.
    """
    marker = Requirement(spec).marker
    return marker is not None and "extra" in str(marker)
