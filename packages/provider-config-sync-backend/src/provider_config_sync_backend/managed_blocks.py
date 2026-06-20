"""Managed instruction blocks.

A managed block is a delimited section inside a provider instruction file
(``CLAUDE.md`` / ``AGENTS.md`` / ``GEMINI.md``) owned by an ``(owner, section)``
key and wrapped in sentinel comment lines::

    <!-- BEGIN better-claude:extension:my-ext:rules -->
    ...section content...
    <!-- END better-claude:extension:my-ext:rules -->

This lets a caller (e.g. an installed extension) add, replace, and remove its
instructions surgically without touching surrounding, user-authored content.
Operations are idempotent and atomic; the surrounding text is preserved.

Pure file-layer utility — it knows nothing about providers or scopes. Mapping a
``(scope, provider)`` to a target path lives in :mod:`api`
(:func:`managed_instruction_targets`); this module only splice-manages a single
path it is handed.

Security: ``owner``/``section`` are validated to a safe charset; content is
rejected if it contains our sentinel markers (prevents marker injection);
symlink and non-regular targets are refused; writes are atomic (temp + rename).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_BRAND = "better-claude"
# Owner/section keys: extension ids and section names are validated to
# [a-z0-9_.-] upstream, plus the "extension:" owner prefix uses ":". Dots,
# hyphens, underscores are all safe inside an HTML comment sentinel.
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
# Reject any content that tries to forge our sentinels — guarantees block
# boundaries can never be hijacked by the managed text itself.
_MARKER_RE = re.compile(r"<!-- (BEGIN|END) " + re.escape(_BRAND) + r":")


def _validate_key(value: str, field: str) -> None:
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise ValueError(f"invalid {field}: {value!r}")


def _begin(owner: str, section: str) -> str:
    return f"<!-- BEGIN {_BRAND}:{owner}:{section} -->"


def _end(owner: str, section: str) -> str:
    return f"<!-- END {_BRAND}:{owner}:{section} -->"


def _render_block(owner: str, section: str, content: str) -> str:
    return f"{_begin(owner, section)}\n{content.rstrip(chr(10))}\n{_end(owner, section)}"


def _section_regex(owner: str, section: str) -> re.Pattern:
    return re.compile(re.escape(_begin(owner, section)) + r".*?" + re.escape(_end(owner, section)), re.DOTALL)


def _owner_regex(owner: str) -> re.Pattern:
    begin_prefix = re.escape(f"<!-- BEGIN {_BRAND}:{owner}:")
    end_prefix = re.escape(f"<!-- END {_BRAND}:{owner}:")
    return re.compile(begin_prefix + r"[^\n]*?-->.*?" + end_prefix + r"[^\n]*?-->", re.DOTALL)


def _resolve(path: Path) -> Path:
    """Follow symlinks to the real file. Target paths are always PCS-resolved
    provider instruction files (never extension-supplied), so following a
    symlink honors the user's own config layout (symlinked CLAUDE.md is common).
    """
    return path.resolve(strict=False)


def _read_text(path: Path) -> str | None:
    """Current file text, ``None`` if it does not exist."""
    real = _resolve(path)
    if not real.exists():
        return None
    if not real.is_file():
        raise ValueError(f"refusing managed-block op on non-regular file: {path}")
    return real.read_text(encoding="utf-8")


def _atomic_replace(path: Path, content: str) -> None:
    real = _resolve(path)
    real.parent.mkdir(parents=True, exist_ok=True)
    if not real.parent.is_dir():
        raise ValueError(f"unsafe parent directory: {real.parent}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{real.name}.bc-mb-", dir=real.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, real)
    finally:
        if tmp.exists():
            tmp.unlink()


def _tidy(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text + "\n" if text else ""


def _append_block(current: str, block: str) -> str:
    if not current:
        return block + "\n"
    if current.endswith("\n\n"):
        return current + block + "\n"
    if current.endswith("\n"):
        return current + "\n" + block + "\n"
    return current + "\n\n" + block + "\n"


def upsert_block(path: Path, owner: str, section: str, content: str) -> bool:
    """Insert or replace the block for ``(owner, section)``. Returns whether the file changed."""
    _validate_key(owner, "owner")
    _validate_key(section, "section")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if _MARKER_RE.search(content):
        raise ValueError("content contains a reserved managed-block marker")
    current = _read_text(path) or ""
    rendered = _render_block(owner, section, content)
    regex = _section_regex(owner, section)
    if regex.search(current):
        next_content = regex.sub(lambda _: rendered, current, count=1)
    else:
        next_content = _append_block(current, rendered)
    if next_content == current:
        return False
    _atomic_replace(path, next_content)
    return True


def remove_block(path: Path, owner: str, section: str) -> bool:
    """Strip the block for ``(owner, section)``. Returns whether the file changed."""
    _validate_key(owner, "owner")
    _validate_key(section, "section")
    current = _read_text(path)
    if current is None:
        return False
    next_content, count = _section_regex(owner, section).subn("", current)
    if count == 0:
        return False
    _atomic_replace(path, _tidy(next_content))
    return True


def remove_owner_blocks(path: Path, owner: str) -> int:
    """Strip every block owned by ``owner`` (all sections). Returns the count removed."""
    _validate_key(owner, "owner")
    current = _read_text(path)
    if current is None:
        return 0
    next_content, count = _owner_regex(owner).subn("", current)
    if count == 0:
        return 0
    _atomic_replace(path, _tidy(next_content))
    return count


def has_owner_blocks(path: Path, owner: str) -> bool:
    current = _read_text(path)
    return bool(current) and _owner_regex(owner).search(current) is not None
