"""Armazenamento local de artefatos endereçado por SHA-256 (T04, endurecido em R04).

Layout: ``{root}/objects/{sha[:2]}/{sha[2:4]}/{sha}`` + sidecar ``{sha}.meta.json``.

Modelo de consistência (R04):

- o **objeto é autoritativo**; o sidecar é um cache derivado e reparável;
- publicação: tmp em ``{root}/tmp/`` (mesmo volume) → verificação de
  hash/tamanho/tipo → fsync → ``os.replace`` do objeto → fsync do diretório →
  sidecar (tmp + fsync + ``os.replace``) → fsync do diretório;
- janelas de crash: (a) antes do replace do objeto — resta apenas ``.part``
  órfão, varrido por ``cleanup_stale_temps``; (b) depois do objeto e antes do
  sidecar — ``metadata()`` repara o sidecar após reverificar o hash do objeto;
  (c) depois do sidecar — estado completo; (d) durante a escrita do sidecar —
  o temporário ``.meta.<uuid>.tmp`` é removido em ``finally`` e, se restar por
  crash de processo, ``audit()`` o remove sem contá-lo como objeto/corrupção;
- sidecar sem objeto nunca é apresentado como válido: ``metadata()`` falha
  fechado e ``audit()`` remove o órfão;
- deduplicação revalida o objeto existente (tamanho e hash) antes de aceitá-lo;
  conteúdo divergente da chave falha fechado e nunca é sobrescrito;
- durabilidade: fsync de arquivo e diretório em cada publicação; um crash de
  energia no exato instante do replace pode ainda exigir ``audit()`` — limite
  documentado e verificável.

Nenhum caminho externo é aceito: a única chave é o hash validado pelo tipo de
domínio ``Sha256``.
"""

import hashlib
import json
import os
import re
import unicodedata
import zipfile
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter

from rag.domain.errors import NotFoundError, StorageError
from rag.domain.identifiers import Sha256

_CHUNK = 1024 * 1024  # 1 MiB
_SHA_ADAPTER: TypeAdapter[str] = TypeAdapter(Sha256)


class ArtifactMediaType(StrEnum):
    PDF = "application/pdf"
    EPUB = "application/epub+zip"


class ArtifactMetadata(BaseModel, frozen=True):
    sha256: Sha256
    media_type: ArtifactMediaType
    size_bytes: int = Field(ge=0)
    created_at: datetime
    original_filename: str | None = None


class StoredArtifact(BaseModel, frozen=True):
    metadata: ArtifactMetadata
    deduplicated: bool


class AuditReport(BaseModel, frozen=True):
    """Resultado da varredura de integridade do volume (R04)."""

    objects: int = Field(ge=0)
    orphan_sidecars_removed: int = Field(ge=0)
    sidecars_regenerated: int = Field(ge=0)
    sidecar_temps_removed: int = Field(default=0, ge=0)
    corrupted: list[str]  # hashes cujo conteúdo diverge da chave; exigem ação admin


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# RRR02: temporário de sidecar interrompido: <sha>.meta.<uuid4hex>.tmp
_SIDECAR_TMP_NAME = re.compile(r"^[0-9a-f]{64}\.meta\.[0-9a-f]{32}\.tmp$")


def sanitize_filename(name: str) -> str:
    """Reduz um nome fornecido por usuário a um basename seguro (metadado apenas)."""
    normalized = unicodedata.normalize("NFKC", name).replace("\\", "/")
    base = Path(normalized).name
    cleaned = _FILENAME_SAFE.sub("_", base).strip("._")
    return cleaned[:128] or "artifact"


def detect_media_type(path: Path) -> ArtifactMediaType:
    """Detecta o tipo pelo conteúdo (magic bytes); nunca confia em nome/extensão."""
    try:
        with path.open("rb") as fh:
            header = fh.read(512)
    except OSError as exc:
        raise StorageError(
            "Não foi possível ler o artefato.", cause=exc, context={"path": str(path)}
        ) from exc
    if header.startswith(b"%PDF-"):
        return ArtifactMediaType.PDF
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(path) as zf:
                try:
                    mimetype = zf.read("mimetype").decode("ascii").strip()
                except KeyError:
                    raise StorageError("Arquivo ZIP sem mimetype EPUB válido.") from None
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise StorageError("Arquivo ZIP/EPUB inválido ou corrompido.", cause=exc) from exc
        if mimetype == "application/epub+zip":
            return ArtifactMediaType.EPUB
        raise StorageError("Arquivo ZIP sem mimetype EPUB válido.")
    raise StorageError("Tipo de artefato não suportado; esperado PDF ou EPUB.")


class ArtifactStore:
    def __init__(self, root: Path, *, max_size_bytes: int = 256 * 1024 * 1024) -> None:
        self._root = root
        self._max_size = max_size_bytes
        (self._root / "objects").mkdir(parents=True, exist_ok=True)
        (self._root / "tmp").mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(
        self,
        source: BinaryIO,
        sha256: Sha256,
        *,
        original_filename: str | None = None,
    ) -> StoredArtifact:
        """Grava `source` verificando hash, tamanho e tipo. Idempotente por hash.

        Deduplicação revalida o objeto existente (tamanho e hash) antes de
        aceitá-lo; objeto corrompido falha fechado e não é sobrescrito (R04).
        """
        existing = self._object_path(sha256)
        if existing.exists():
            metadata = self._validated_existing(sha256)
            return StoredArtifact(metadata=metadata, deduplicated=True)

        tmp = self._root / "tmp" / f"{uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with tmp.open("wb") as out:
                while chunk := source.read(_CHUNK):
                    size += len(chunk)
                    if size > self._max_size:
                        raise StorageError("Artefato excede o tamanho máximo permitido.")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if digest.hexdigest() != sha256:
                raise StorageError(
                    "Hash do conteúdo diverge do hash informado.",
                    context={"expected": sha256, "actual": digest.hexdigest()},
                )
            media_type = detect_media_type(tmp)
            metadata = ArtifactMetadata(
                sha256=sha256,
                media_type=media_type,
                size_bytes=size,
                created_at=datetime.now(UTC),
                original_filename=(
                    sanitize_filename(original_filename) if original_filename else None
                ),
            )
            existing.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, existing)  # objeto publicado primeiro (autoritativo)
            self._fsync_dir(existing.parent)
            self._write_sidecar(sha256, metadata)
            return StoredArtifact(metadata=metadata, deduplicated=False)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def exists(self, sha256: Sha256) -> bool:
        return self._object_path(sha256).exists()

    def metadata(self, sha256: Sha256) -> ArtifactMetadata:
        """Lê metadados; objeto ausente falha fechado mesmo com sidecar presente.

        Níveis de confiança (RR03): esta leitura confere existência do objeto,
        igualdade entre `sha256` do sidecar e a chave, e `size_bytes` contra o
        objeto — sem reler o conteúdo. Verificação completa (hash do conteúdo e
        media type detectado) é `verify_integrity`. Objeto sem sidecar é
        reparado após reverificação do hash (regra R04).
        """
        obj = self._object_path(sha256)
        if not obj.exists():
            raise NotFoundError("Artefato não encontrado.", context={"sha256": sha256})
        sidecar = self._sidecar_path(sha256)
        if not sidecar.exists():
            return self._repair_sidecar(sha256)
        try:
            metadata = ArtifactMetadata.model_validate(
                json.loads(sidecar.read_text(encoding="utf-8"))
            )
        except (ValueError, TypeError) as exc:
            raise StorageError(
                "Metadados do artefato corrompidos.", cause=exc, context={"sha256": sha256}
            ) from exc
        if metadata.sha256 != sha256:
            raise StorageError(
                "Metadados declararam hash divergente da chave.",
                context={"sha256": sha256, "sidecar_sha256": metadata.sha256},
            )
        actual_size = obj.stat().st_size
        if metadata.size_bytes != actual_size:
            raise StorageError(
                "Metadados divergem do objeto armazenado.",
                context={
                    "sha256": sha256,
                    "sidecar_size": metadata.size_bytes,
                    "object_size": actual_size,
                },
            )
        return metadata

    def verify_integrity(self, sha256: Sha256) -> ArtifactMetadata:
        """Verificação completa: hash do conteúdo, chave, tamanho e media type
        detectado conferem com o sidecar (R04/RR03)."""
        obj = self._object_path(sha256)
        if not obj.exists():
            raise NotFoundError("Artefato não encontrado.", context={"sha256": sha256})
        digest, size = self._hash_object(obj)
        if digest != sha256:
            raise StorageError(
                "Conteúdo do artefato diverge da chave SHA-256.",
                context={"sha256": sha256, "actual": digest},
            )
        metadata = self.metadata(sha256)
        if metadata.size_bytes != size:
            raise StorageError(
                "Metadados divergem do objeto armazenado.",
                context={
                    "sha256": sha256,
                    "sidecar_size": metadata.size_bytes,
                    "object_size": size,
                },
            )
        detected = detect_media_type(obj)
        if metadata.media_type != detected:
            raise StorageError(
                "Media type dos metadados diverge do conteúdo.",
                context={
                    "sha256": sha256,
                    "sidecar_media_type": str(metadata.media_type),
                    "detected": str(detected),
                },
            )
        return metadata

    def audit(self) -> AuditReport:
        """Varre o volume: remove sidecars órfãos, repara sidecars ausentes e
        reporta objetos corrompidos (nunca apaga objeto sem ação administrativa)."""
        objects_root = self._root / "objects"
        objects = 0
        regenerated = 0
        removed_orphans = 0
        removed_temps = 0
        corrupted: list[str] = []
        seen: set[str] = set()
        for obj in sorted(objects_root.glob("*/*/*")):
            if obj.suffix == ".json" or not obj.is_file():
                continue
            # RRR02: temporário de sidecar interrompido não é objeto nem
            # corrupção — é removido e contabilizado separadamente.
            if _SIDECAR_TMP_NAME.match(obj.name):
                obj.unlink()
                removed_temps += 1
                continue
            sha = obj.name
            objects += 1
            seen.add(sha)
            digest, _ = self._hash_object(obj)
            if digest != sha:
                corrupted.append(sha)
                continue
            if not obj.with_suffix(".meta.json").exists():
                self._repair_sidecar(_SHA_ADAPTER.validate_python(sha))
                regenerated += 1
                continue
            # sidecar presente: sha/tamanho/tipo divergentes são regenerados
            # (o objeto já teve o hash verificado acima) — política documentada.
            try:
                existing_meta = ArtifactMetadata.model_validate(
                    json.loads(obj.with_suffix(".meta.json").read_text(encoding="utf-8"))
                )
                consistent = (
                    existing_meta.sha256 == sha
                    and existing_meta.size_bytes == obj.stat().st_size
                    and existing_meta.media_type == detect_media_type(obj)
                )
            except (ValueError, TypeError, StorageError):
                consistent = False
            if not consistent:
                self._repair_sidecar(_SHA_ADAPTER.validate_python(sha))
                regenerated += 1
        for sidecar in sorted(objects_root.glob("*/*/*.meta.json")):
            if sidecar.name[: -len(".meta.json")] not in seen:
                sidecar.unlink()
                removed_orphans += 1
        return AuditReport(
            objects=objects,
            orphan_sidecars_removed=removed_orphans,
            sidecars_regenerated=regenerated,
            sidecar_temps_removed=removed_temps,
            corrupted=corrupted,
        )

    def read_range(self, sha256: Sha256, offset: int, length: int) -> bytes:
        """Lê `length` bytes a partir de `offset`, limitado ao tamanho do objeto."""
        if offset < 0 or length <= 0:
            raise StorageError(
                "Faixa de bytes inválida.", context={"offset": offset, "length": length}
            )
        path = self._object_path(sha256)
        if not path.exists():
            raise NotFoundError("Artefato não encontrado.", context={"sha256": sha256})
        size = path.stat().st_size
        if offset >= size:
            raise StorageError(
                "Faixa de bytes fora do artefato.",
                context={"offset": offset, "size_bytes": size},
            )
        with path.open("rb") as fh:
            fh.seek(offset)
            return fh.read(min(length, size - offset))

    def open_stream(self, sha256: Sha256, offset: int = 0) -> BinaryIO:
        """Abre stream posicionado em `offset` (uso do leitor/HTTP range em T14/T17)."""
        if offset < 0:
            raise StorageError("Offset inválido.", context={"offset": offset})
        path = self._object_path(sha256)
        if not path.exists():
            raise NotFoundError("Artefato não encontrado.", context={"sha256": sha256})
        fh = path.open("rb")
        fh.seek(offset)
        return fh

    def cleanup_stale_temps(self, *, max_age: timedelta = timedelta(hours=24)) -> int:
        """Remove temporários órfãos (falha/crash) mais velhos que `max_age`."""
        cutoff = datetime.now(UTC).timestamp() - max_age.total_seconds()
        removed = 0
        for tmp in (self._root / "tmp").glob("*.part"):
            try:
                if tmp.stat().st_mtime < cutoff:
                    tmp.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def _object_path(self, sha256: Sha256) -> Path:
        path = self._root / "objects" / sha256[:2] / sha256[2:4] / sha256
        resolved_root = self._root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise StorageError("Caminho de artefato fora do volume configurado.")
        return path

    def _sidecar_path(self, sha256: Sha256) -> Path:
        return self._object_path(sha256).with_suffix(".meta.json")

    @staticmethod
    def _hash_object(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    def _validated_existing(self, sha256: Sha256) -> ArtifactMetadata:
        """Revalida objeto já presente antes de aceitá-lo como deduplicado (R04/RR03):
        hash do conteúdo, chave, tamanho e media type do sidecar."""
        digest, _ = self._hash_object(self._object_path(sha256))
        if digest != sha256:
            raise StorageError(
                "Objeto existente corrompido: conteúdo diverge da chave SHA-256. "
                "Intervenção administrativa necessária; o objeto não foi alterado.",
                context={"sha256": sha256, "actual": digest},
            )
        return self.verify_integrity(sha256)

    def _repair_sidecar(self, sha256: Sha256) -> ArtifactMetadata:
        """Regenera o sidecar a partir do objeto, após reverificar o hash."""
        obj = self._object_path(sha256)
        digest, size = self._hash_object(obj)
        if digest != sha256:
            raise StorageError(
                "Conteúdo do artefato diverge da chave SHA-256.",
                context={"sha256": sha256, "actual": digest},
            )
        metadata = ArtifactMetadata(
            sha256=sha256,
            media_type=detect_media_type(obj),
            size_bytes=size,
            created_at=datetime.fromtimestamp(obj.stat().st_mtime, UTC),
            original_filename=None,
        )
        self._write_sidecar(sha256, metadata)
        return metadata

    def _write_sidecar(self, sha256: Sha256, metadata: ArtifactMetadata) -> None:
        sidecar = self._sidecar_path(sha256)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp = sidecar.with_suffix(f".{uuid4().hex}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(metadata.model_dump_json())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, sidecar)
            self._fsync_dir(sidecar.parent)
        finally:
            # RRR02: falha antes do replace não pode deixar temporário órfão
            # (após o replace o caminho já não existe — unlink é no-op).
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
