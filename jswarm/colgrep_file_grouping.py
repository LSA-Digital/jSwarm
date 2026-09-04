"""Shared folder-grouping renderer for ColGREP file-list output.

Provides ``render_grouped_file_list``, a small pure-function helper that
groups a flat list of file paths by their POSIX-style parent folder and
renders a deterministic, indented, human-readable listing:

    docs/
      x.md
    jswarm/tests/
      test_a.py
      test_b.py

Root-level files (no dirname) are grouped under ``root_label`` (default
``"."``). Folders and files are always sorted lexicographically so output
is stable regardless of input order. Optional ``max_folders`` and
``max_files_per_folder`` caps truncate long listings and append a summary
line for the omitted count.

This helper never raises: malformed or non-string entries are skipped
fail-open rather than propagating an exception, so it is safe to call on
untrusted or partially-normalized path lists.
"""

from __future__ import annotations

import posixpath
from typing import Any, Iterable


def render_grouped_file_list(
    paths: Iterable[Any],
    *,
    indent: str = "  ",
    max_folders: int | None = None,
    max_files_per_folder: int | None = None,
    root_label: str = ".",
) -> list[str]:
    """Render ``paths`` as a folder-grouped, indented list of lines.

    Args:
        paths: Iterable of path-like entries. Non-string entries (including
            ``None``) are skipped fail-open.
        indent: Prefix applied verbatim to each rendered file line.
        max_folders: If set, cap the number of rendered folder groups and
            append a "... and N more folders" summary line for the rest.
        max_files_per_folder: If set, cap the number of rendered files per
            folder and append an indented "... and N more" summary line.
        root_label: Header used for root-level files (no dirname).

    Returns:
        A list of rendered lines. Never raises.
    """
    groups: dict[str, set[str]] = {}

    try:
        entries: list[Any] = list(paths)
    except TypeError:
        entries = []

    for entry in entries:
        if not isinstance(entry, str):
            continue
        try:
            normalized = posixpath.normpath(entry)
            dirname, basename = posixpath.split(normalized)
        except Exception:
            continue
        if not basename:
            continue
        if dirname == ".":
            dirname = ""
        groups.setdefault(dirname, set()).add(basename)

    if not groups:
        return []

    folder_keys = sorted(groups.keys())

    if max_folders is not None:
        rendered_folders = folder_keys[:max_folders]
        omitted_folders = len(folder_keys) - len(rendered_folders)
    else:
        rendered_folders = folder_keys
        omitted_folders = 0

    lines: list[str] = []
    for dirname in rendered_folders:
        label = root_label if dirname == "" else f"{dirname}/"
        lines.append(label)

        files_sorted = sorted(groups[dirname])
        if max_files_per_folder is not None:
            rendered_files = files_sorted[:max_files_per_folder]
            omitted_files = len(files_sorted) - len(rendered_files)
        else:
            rendered_files = files_sorted
            omitted_files = 0

        for basename in rendered_files:
            lines.append(f"{indent}{basename}")

        if omitted_files > 0:
            lines.append(f"{indent}… and {omitted_files} more")

    if omitted_folders > 0:
        lines.append(f"… and {omitted_folders} more folders")

    return lines
