"""Builds a compact map of the website repo for the model to reference.

The index exists to let the model name plausible edit locations, and to give us
a whitelist to check its answers against. Files are read only to pull a one-line
hint; nothing is executed and nothing is written back.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Directories that never contain source worth suggesting.
SKIP_DIRS = {
    ".git", ".next", ".svelte-kit", ".nuxt", ".cache", ".venv", "venv",
    "node_modules", "dist", "build", "out", "coverage", "__pycache__",
    "vendor", ".idea", ".vscode", ".DS_Store",
}

# Extensions worth indexing for a website. Binaries and lockfiles are noise.
INDEX_EXTENSIONS = {
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro",
    ".py", ".rb", ".php", ".go", ".rs",
    ".md", ".mdx", ".json", ".yml", ".yaml", ".toml",
}

SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "composer.lock", "Cargo.lock",
}

_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HTML_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_MD_HEADING = re.compile(r"^#\s+(.+)$", re.M)
_JS_EXPORT = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|const|class|let|var)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
_PY_DEF = re.compile(r"^(?:def|class)\s+([A-Za-z_]\w*)", re.M)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

MAX_HINT_BYTES = 8000  # only the head of a file is read for hints
MAX_HINT_CHARS = 90


@dataclass
class RepoIndex:
    root: Path | None = None
    entries: list[tuple[str, str]] = field(default_factory=list)  # (path, hint)
    truncated: bool = False

    @property
    def paths(self) -> set[str]:
        return {p for p, _ in self.entries}

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def render(self) -> str:
        """Prompt-ready listing. Stable ordering keeps the cache prefix warm."""
        if self.is_empty:
            return ""
        lines = [f"Repository root: {self.root}", ""]
        lines.extend(f"{path} — {hint}" if hint else path for path, hint in self.entries)
        if self.truncated:
            lines.append("[... index truncated; more files exist ...]")
        return "\n".join(lines)


def _tracked_files(root: Path) -> list[Path] | None:
    """Ask git for the file list so .gitignore is honored exactly.

    Reimplementing gitignore semantics is a losing game; when the repo is a git
    checkout, git already knows the answer.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [root / line for line in result.stdout.splitlines() if line]


def _walked_files(root: Path) -> list[Path]:
    """Fallback for non-git directories."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def _hint(path: Path) -> str:
    """One line describing the file, for a model deciding where an edit goes."""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:MAX_HINT_BYTES]
    except OSError:
        return ""

    suffix = path.suffix.lower()
    raw = ""

    if suffix in (".html", ".htm"):
        match = _HTML_TITLE.search(head) or _HTML_H1.search(head)
        if match:
            raw = _TAG.sub("", match.group(1))
    elif suffix in (".md", ".mdx"):
        match = _MD_HEADING.search(head)
        if match:
            raw = match.group(1)
    elif suffix in (".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro"):
        names = _JS_EXPORT.findall(head)
        if names:
            raw = "exports " + ", ".join(names[:5])
    elif suffix == ".py":
        names = _PY_DEF.findall(head)
        if names:
            raw = "defines " + ", ".join(names[:5])

    raw = _WS.sub(" ", raw).strip()
    return raw[:MAX_HINT_CHARS]


def build_index(root: Path | None, max_files: int = 400) -> RepoIndex:
    """Index the repo, or return an empty index when there is nothing to read.

    An empty index is a normal state, not an error: until the website code
    exists locally, extraction runs fine and simply produces no file
    suggestions.
    """
    if root is None or not root.is_dir():
        return RepoIndex()

    candidates = _tracked_files(root)
    if candidates is None:
        candidates = _walked_files(root)

    keep: list[Path] = []
    for path in candidates:
        if path.name in SKIP_FILENAMES:
            continue
        if path.suffix.lower() not in INDEX_EXTENSIONS:
            continue
        if not path.is_file():
            continue
        keep.append(path)

    keep.sort(key=lambda p: str(p.relative_to(root)))
    truncated = len(keep) > max_files
    keep = keep[:max_files]

    entries = [(str(p.relative_to(root)), _hint(p)) for p in keep]
    return RepoIndex(root=root, entries=entries, truncated=truncated)


def resolve_path(index: RepoIndex, raw: str) -> str | None:
    """Map a model-supplied path onto a real indexed file, or None.

    The model can produce a confident, plausible, non-existent path. Exact
    matches win; a bare filename is accepted only when exactly one indexed file
    carries that name, so an ambiguous guess is discarded rather than resolved
    arbitrarily.
    """
    candidate = raw.strip().lstrip("./")
    if not candidate:
        return None
    if candidate in index.paths:
        return candidate

    name = Path(candidate).name
    matches = [p for p in index.paths if Path(p).name == name]
    return matches[0] if len(matches) == 1 else None


def validate_paths(index: RepoIndex, paths: list[str]) -> list[str]:
    """Resolve a list of paths, dropping unverifiable ones and duplicates."""
    resolved = [r for r in (resolve_path(index, p) for p in paths) if r]
    seen: set[str] = set()
    return [p for p in resolved if not (p in seen or seen.add(p))]
