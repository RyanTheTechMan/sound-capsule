from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from collections.abc import Callable
import xml.etree.ElementTree as ET

from .capsule import slugify
from .flp import FLPUnsupportedError


def _title_key(title: str) -> str:
    value = title.strip()
    if value.casefold().endswith(".flp"):
        value = value[:-4]
    return slugify(value).casefold()


def _matching_title(path: Path, title: str) -> bool:
    return path.suffix.casefold() == ".flp" and slugify(path.stem).casefold() == _title_key(title)


def _mac_recent_projects() -> list[Path]:
    registry = Path.home() / "Library" / "Preferences" / "Image-Line" / "reg.xml"
    if not registry.is_file():
        return []
    try:
        root = ET.parse(registry).getroot()
    except (ET.ParseError, OSError):
        return []
    groups: list[list[tuple[int, Path]]] = []
    for key in root.iter("Key"):
        if key.attrib.get("Name") != "MRU":
            continue
        values: list[tuple[int, Path]] = []
        for value in key.findall("Value"):
            text = (value.text or "").strip()
            if not text.casefold().endswith(".flp"):
                continue
            try:
                index = int(value.attrib.get("Name", "999999"))
            except ValueError:
                index = 999999
            values.append((index, Path(text).expanduser()))
        if values:
            groups.append(values)
    if not groups:
        return []
    # The project MRU is normally the largest MRU group. Prefer the last group
    # on ties because current FL versions are stored later in Image-Line's XML.
    _, group = max(enumerate(groups), key=lambda item: (len(item[1]), item[0]))
    return [path for _, path in sorted(group, key=lambda item: item[0])]


def _windows_document_roots() -> list[Path]:
    result = [Path.home() / "Documents"]
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if value := os.environ.get(variable):
            result.append(Path(value) / "Documents")
    return result


def _windows_browser_recent_projects(
    document_roots: list[Path] | None = None,
    *,
    user_folders: list[Path] | None = None,
) -> list[Path]:
    """Read the current FL Studio Browser recent-files list on Windows."""
    if user_folders is None:
        document_roots = document_roots or _windows_document_roots()
        user_folders = [
            documents / "Image-Line" / "FL Studio" for documents in document_roots
        ]

    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    staging_root = (local_app_data / "SoundCapsule" / "Staging").resolve()

    result: list[Path] = []
    seen_files: set[str] = set()
    for user_folder in user_folders:
        recent_file = (
            user_folder
            / "Settings"
            / "Browser"
            / "Recent files.scr"
        )
        try:
            lines = recent_file.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        for line in lines:
            value = line.strip()
            if not value.casefold().endswith(".flp"):
                continue
            path = Path(value).expanduser()
            try:
                if path.resolve().is_relative_to(staging_root):
                    continue
            except OSError:
                pass
            key = os.path.normcase(str(path))
            if key not in seen_files:
                seen_files.add(key)
                result.append(path)
    return result


def _windows_recent_projects(fl_user_folder: Path | None = None) -> list[Path]:
    browser_recent = _windows_browser_recent_projects(
        user_folders=[fl_user_folder] if fl_user_folder is not None else None
    )
    try:
        import winreg
    except ImportError:
        return browser_recent
    groups: list[list[tuple[int, Path]]] = []

    def visit(key, depth: int = 0) -> None:
        if depth > 10:
            return
        try:
            value_count, child_count, _ = winreg.QueryInfoKey(key)
        except OSError:
            return
        values: list[tuple[int, Path]] = []
        for index in range(value_count):
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                continue
            if isinstance(value, str) and value.casefold().endswith(".flp"):
                try:
                    order = int(name)
                except (TypeError, ValueError):
                    order = 999999
                values.append((order, Path(value).expanduser()))
        if values:
            groups.append(values)
        for index in range(child_count):
            try:
                name = winreg.EnumKey(key, index)
                with winreg.OpenKey(key, name) as child:
                    visit(child, depth + 1)
            except OSError:
                continue

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Image-Line") as root:
            visit(root)
    except OSError:
        return browser_recent
    if not groups:
        return browser_recent
    _, group = max(enumerate(groups), key=lambda item: (len(item[1]), item[0]))
    registry_recent = [path for _, path in sorted(group, key=lambda item: item[0])]
    combined: list[Path] = []
    seen: set[str] = set()
    for path in browser_recent + registry_recent:
        key = os.path.normcase(str(path.expanduser()))
        if key not in seen:
            seen.add(key)
            combined.append(path)
    return combined


def recent_project_paths(fl_user_folder: Path | None = None) -> list[Path]:
    if platform.system() == "Darwin":
        return _mac_recent_projects()
    if platform.system() == "Windows":
        return _windows_recent_projects(fl_user_folder)
    return []


def indexed_project_paths(title: str, fl_user_folder: Path | None = None) -> list[Path]:
    if platform.system() == "Windows":
        return _windows_indexed_projects(
            title, user_folders=[fl_user_folder] if fl_user_folder is not None else None
        )
    if platform.system() != "Darwin":
        return []
    filename = title.strip()
    if filename.casefold().endswith(".flp"):
        filename = filename[:-4]
    # This is passed as one argv item, not through a shell. Escape only the
    # quote syntax used by Spotlight's query language.
    filename = filename.replace("\\", "\\\\").replace("'", "\\'") + ".flp"
    try:
        result = subprocess.run(
            ["mdfind", f"kMDItemFSName == '{filename}'c"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _windows_indexed_projects(
    title: str,
    document_roots: list[Path] | None = None,
    *,
    user_folders: list[Path] | None = None,
) -> list[Path]:
    """Find an exact project filename in FL's standard Windows project roots."""
    filename = title.strip()
    if not filename:
        return []
    if not filename.casefold().endswith(".flp"):
        filename += ".flp"
    # A window caption contributes only a filename, but keep this provider safe
    # when called directly with unexpected title metadata.
    if Path(filename).name != filename:
        return []
    if user_folders is None:
        document_roots = document_roots or _windows_document_roots()
        user_folders = [
            documents / "Image-Line" / "FL Studio" for documents in document_roots
        ]

    result: list[Path] = []
    seen: set[str] = set()
    for user_folder in user_folders:
        projects = user_folder / "Projects"
        likely = (
            projects / filename,
            projects / Path(filename).stem / filename,
        )
        for path in likely:
            key = os.path.normcase(str(path))
            if key not in seen and path.is_file():
                seen.add(key)
                result.append(path)
        if result:
            continue
        try:
            for directory, _children, files in os.walk(projects):
                for candidate in files:
                    if candidate.casefold() != filename.casefold():
                        continue
                    path = Path(directory) / candidate
                    key = os.path.normcase(str(path))
                    if key not in seen:
                        seen.add(key)
                        result.append(path)
        except OSError:
            continue
    return result


class ProjectLocator:
    def __init__(
        self,
        roots: list[Path] | None = None,
        *,
        cache_path: Path | None = None,
        recent_provider=recent_project_paths,
        indexed_provider=indexed_project_paths,
    ):
        self.roots = roots or []
        self.cache_path = cache_path
        self.recent_provider = recent_provider
        self.indexed_provider = indexed_provider

    def find_current(
        self,
        title: str,
        *,
        changed_after: float | None = None,
        candidate_validator: Callable[[Path], bool] | None = None,
        cache_key: str | None = None,
    ) -> Path:
        recent = self._valid_unique(self.recent_provider())
        rooted = self._root_candidates()
        if changed_after is not None:
            changed = [
                path
                for path in self._valid_unique(recent + rooted)
                if (not title.strip() or _matching_title(path, title))
                and path.stat().st_mtime >= changed_after - 2.0
            ]
            if candidate_validator is not None:
                changed = [path for path in changed if candidate_validator(path)]
            if changed:
                changed.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
                selected = changed[0]
                if title.strip():
                    self._remember(title, selected)
                elif cache_key:
                    self._remember_key(cache_key, selected)
                return selected
        if not title.strip() and cache_key:
            cached = self._cached_key(cache_key)
            if cached is not None:
                valid_cached = self._valid_unique([cached])
                if valid_cached and (
                    candidate_validator is None or candidate_validator(valid_cached[0])
                ):
                    return valid_cached[0]
        if candidate_validator is not None:
            recent = [path for path in recent if candidate_validator(path)]
            rooted = [path for path in rooted if candidate_validator(path)]
        if not title.strip():
            ordered = self._valid_unique(recent + rooted)
            if len(recent) == 1:
                if cache_key:
                    self._remember_key(cache_key, recent[0])
                return recent[0]
            if len(recent) > 1:
                names = ", ".join(str(path) for path in recent[:4])
                raise FLPUnsupportedError(
                    "multiple saved FLPs match the live Channel Rack and FL did not publish a "
                    f"project title; save the current project to disambiguate it: {names}"
                )
            if len(ordered) == 1:
                return ordered[0]
            raise FileNotFoundError(
                "FL did not publish a project title and no saved FLP matched the live Channel Rack; "
                "save the project once and retry"
            )
        cached = self._cached(title)
        indexed = self._valid_unique(self.indexed_provider(title))
        if candidate_validator is not None:
            cached = cached if cached is not None and candidate_validator(cached) else None
            indexed = [path for path in indexed if candidate_validator(path)]
        ordered = self._valid_unique(recent + ([cached] if cached else []) + indexed + rooted)
        matching = [path for path in ordered if _matching_title(path, title)]
        recent_matching = [path for path in recent if _matching_title(path, title)]
        if recent_matching:
            selected = recent_matching[0]
            self._remember(title, selected)
            return selected
        if cached is not None and cached in matching:
            return cached
        if len(matching) == 1:
            self._remember(title, matching[0])
            return matching[0]
        if not matching:
            raise FileNotFoundError(
                f"could not locate the open FLP for {title!r}; save it once in FL Studio and retry"
            )
        names = ", ".join(str(path) for path in matching[:4])
        raise FLPUnsupportedError(
            f"multiple FLPs match {title!r} and FL did not identify the current one: {names}"
        )

    # Compatibility for callers/tests created before automatic discovery.
    def find_recent(self, title: str, *, changed_after: float | None = None) -> Path:
        return self.find_current(title, changed_after=changed_after)

    @staticmethod
    def _valid_unique(paths: list[Path | None]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for candidate in paths:
            if candidate is None:
                continue
            try:
                path = candidate.expanduser().resolve()
                key = os.path.normcase(str(path))
                if key in seen or not path.is_file() or path.suffix.casefold() != ".flp":
                    continue
            except OSError:
                continue
            seen.add(key)
            result.append(path)
        return result

    def _root_candidates(self) -> list[Path]:
        result: list[Path] = []
        for root in self.roots:
            if not root.exists():
                continue
            try:
                result.extend(path for path in root.rglob("*.flp") if not path.name.startswith("."))
            except OSError:
                continue
        return self._valid_unique(result)

    def _cached(self, title: str) -> Path | None:
        if not title.strip():
            return None
        return self._cached_key(_title_key(title))

    def _cached_key(self, key: str) -> Path | None:
        if not key or self.cache_path is None or not self.cache_path.is_file():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            value = payload.get(key, {}).get("path")
            return Path(value) if isinstance(value, str) else None
        except (OSError, ValueError, AttributeError):
            return None

    def _remember(self, title: str, path: Path) -> None:
        if not title.strip():
            return
        self._remember_key(_title_key(title), path)

    def _remember_key(self, key: str, path: Path) -> None:
        if not key or self.cache_path is None:
            return
        payload: dict = {}
        if self.cache_path.is_file():
            try:
                value = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    payload = value
            except (OSError, ValueError):
                pass
        payload[key] = {"path": str(path), "last_seen": time.time()}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2, sort_keys=True).encode()
        with tempfile.NamedTemporaryFile(
            dir=self.cache_path.parent,
            prefix=f".{self.cache_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(self.cache_path)
        finally:
            temporary.unlink(missing_ok=True)
