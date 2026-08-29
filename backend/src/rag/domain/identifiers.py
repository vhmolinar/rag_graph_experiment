"""Value objects de identidade e hash."""

import hashlib
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, StringConstraints


def _normalize_sha256(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


Sha256 = Annotated[
    str,
    BeforeValidator(_normalize_sha256),
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
