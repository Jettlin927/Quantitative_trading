"""Owner-only 有界文件读取。"""

from __future__ import annotations

import os
from pathlib import Path
import stat


class OwnerOnlyFileError(RuntimeError):
    pass


def read_owner_only_file(path: str | Path, *, maximum_bytes: int) -> bytes:
    """拒绝符号链接、非普通文件、非当前 uid 及 group/other 权限。"""

    try:
        descriptor = os.open(
            Path(path),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
                or not metadata.st_mode & stat.S_IRUSR
            ):
                raise OwnerOnlyFileError("permissions")
            raw = os.read(descriptor, maximum_bytes + 1)
        finally:
            os.close(descriptor)
    except OwnerOnlyFileError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise OwnerOnlyFileError("invalid") from exc
    if len(raw) > maximum_bytes:
        raise OwnerOnlyFileError("invalid")
    return raw
