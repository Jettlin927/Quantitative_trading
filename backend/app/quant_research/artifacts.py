from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
import gzip
from hashlib import sha256
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

import pandas as pd

from .run_config import canonical_json_bytes


NULL_VALUE = r"\N"


class ArtifactIntegrityError(RuntimeError):
    pass


def canonical_cell(value: Any) -> str:
    if value is None or value is pd.NA or pd.isna(value):
        return NULL_VALUE
    if isinstance(value, pd.Timestamp):
        return value.isoformat() if value.time() != datetime.min.time() else value.date().isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if not math.isfinite(value):
            return NULL_VALUE
        return format(value, ".17g")
    return str(value)


def write_canonical_csv_gz(
    path: Path,
    *,
    columns: Iterable[str],
    rows: Iterable[Mapping[str, Any]],
    natural_key: Iterable[str],
) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns_tuple = tuple(columns)
    natural_key_tuple = tuple(natural_key)
    if not natural_key_tuple or not set(natural_key_tuple).issubset(columns_tuple):
        raise ValueError("canonical CSV 必须声明属于 columns 的非空 natural_key")
    plain_fd, plain_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".csv", dir=path.parent)
    os.close(plain_fd)
    plain_path = Path(plain_name)
    row_count = 0
    previous_key: tuple[str, ...] | None = None
    try:
        with plain_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(columns_tuple)
            for row in rows:
                rendered = {
                    column: canonical_cell(row.get(column))
                    for column in columns_tuple
                }
                current_key = tuple(rendered[column] for column in natural_key_tuple)
                if any(value == NULL_VALUE for value in current_key):
                    raise ValueError("canonical CSV natural_key 不能为 null")
                if previous_key is not None and current_key <= previous_key:
                    reason = "重复" if current_key == previous_key else "未按升序排列"
                    raise ValueError(f"canonical CSV natural_key {reason}：{current_key}")
                writer.writerow([rendered[column] for column in columns_tuple])
                row_count += 1
                previous_key = current_key
            handle.flush()
            os.fsync(handle.fileno())
        content_sha256 = sha256_file(plain_path)
        compressed_tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with plain_path.open("rb") as source, compressed_tmp.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(compressed_tmp, path)
        _fsync_directory(path.parent)
        return {
            "filename": path.name,
            "columns": list(columns_tuple),
            "naturalKey": list(natural_key_tuple),
            "rowCount": row_count,
            "contentSha256": content_sha256,
            "fileSha256": sha256_file(path),
            "nullValue": NULL_VALUE,
            "compression": "gzip",
            "gzipMtime": 0,
        }
    finally:
        plain_path.unlink(missing_ok=True)
        if "compressed_tmp" in locals():
            compressed_tmp.unlink(missing_ok=True)


def write_dataframe_csv_gz(
    path: Path,
    frame: pd.DataFrame,
    *,
    columns: Iterable[str],
    natural_key: Iterable[str],
) -> dict[str, Any]:
    selected = frame.loc[:, list(columns)]
    return write_canonical_csv_gz(
        path,
        columns=columns,
        rows=selected.to_dict("records"),
        natural_key=natural_key,
    )


def read_canonical_csv_gz(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        compression="gzip",
        dtype=str,
        keep_default_na=False,
        na_values=[NULL_VALUE],
    )


def verify_csv_artifact(path: Path, artifact: Mapping[str, Any]) -> None:
    try:
        file_hash = sha256_file(path)
        digest = sha256()
        with gzip.open(path, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except (OSError, EOFError) as exc:
        raise ArtifactIntegrityError(f"输入文件无法解压：{path.name}") from exc
    if file_hash != artifact.get("fileSha256"):
        raise ArtifactIntegrityError(f"压缩文件 SHA-256 不匹配：{path.name}")
    if digest.hexdigest() != artifact.get("contentSha256"):
        raise ArtifactIntegrityError(f"canonical 内容 SHA-256 不匹配：{path.name}")


def verify_file_artifact(path: Path, artifact: Mapping[str, Any]) -> None:
    try:
        digest = sha256_file(path)
    except OSError as exc:
        raise ArtifactIntegrityError(f"产物文件无法读取：{Path(path).name}") from exc
    if digest != artifact.get("fileSha256") or digest != artifact.get("contentSha256"):
        raise ArtifactIntegrityError(f"产物 SHA-256 不匹配：{Path(path).name}")


def atomic_write_json(path: Path, value: Any) -> dict[str, Any]:
    payload = canonical_json_bytes(value) + b"\n"
    atomic_write_bytes(path, payload)
    digest = sha256(payload).hexdigest()
    return {"filename": path.name, "contentSha256": digest, "fileSha256": digest}


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(name).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
