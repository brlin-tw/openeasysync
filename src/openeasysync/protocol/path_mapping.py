# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-platform Unix <-> Win32 path mapping and drive normalization."""

import os
import sys


class PathMapper:
    """Translates filesystem paths between host OS and wire protocol."""

    def __init__(self, virtual_root: str | None = None):
        # If virtual_root is specified, paths are scoped relative to it (useful for sandboxing/testing)
        self.virtual_root = os.path.abspath(virtual_root) if virtual_root else None

    def wire_to_local(self, wire_path: str) -> str:
        """Converts a wire path (typically Windows-style C:\\path\\to\\file) to local OS path."""
        clean = wire_path.strip().replace("\\", "/")

        if self.virtual_root:
            # Strip drive letter if present (e.g. C:/foo/bar -> foo/bar)
            if len(clean) >= 2 and clean[1] == ":":
                clean = clean[2:].lstrip("/")
            else:
                clean = clean.lstrip("/")
            return os.path.normpath(os.path.join(self.virtual_root, clean))

        if sys.platform == "win32":
            return os.path.normpath(wire_path.replace("/", "\\"))

        # On Linux/macOS:
        # If path starts with drive letter like C:/foo, map to /foo
        if len(clean) >= 2 and clean[1] == ":":
            rest = clean[2:]
            return "/" + rest.lstrip("/") if rest else "/"

        return os.path.normpath(clean)

    def local_to_wire(self, local_path: str) -> str:
        """Converts a local OS path to wire path."""
        abs_path = os.path.abspath(local_path)

        if self.virtual_root and abs_path.startswith(self.virtual_root):
            rel = os.path.relpath(abs_path, self.virtual_root)
            if rel == ".":
                return "C:\\"
            return "C:\\" + rel.replace("/", "\\")

        if sys.platform == "win32":
            return abs_path.replace("/", "\\")

        # Linux/macOS path
        rel_from_root = abs_path.lstrip("/")
        if not rel_from_root:
            return "C:\\"
        return "C:\\" + rel_from_root.replace("/", "\\")
