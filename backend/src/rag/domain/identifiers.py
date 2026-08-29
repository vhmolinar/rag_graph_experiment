"""Value objects de identidade e hash."""

import hashlib
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
