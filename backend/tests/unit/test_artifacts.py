"""Testes do armazenamento local de artefatos (T04)."""

import hashlib
import io
import os
import time
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from rag.domain.errors import NotFoundError, StorageError
from rag.domain.identifiers import Sha256
from rag.infrastructure.artifacts import (
    ArtifactMediaType,
    ArtifactStore,
    detect_media_type,
    sanitize_filename,
)

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("OEBPS/content.xhtml", "<html/>")
    return buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts", max_size_bytes=10 * 1024 * 1024)


class TestPut:
    def test_roundtrip_pdf(self, store: ArtifactStore) -> None:
        result = store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        assert result.deduplicated is False
        assert result.metadata.media_type is ArtifactMediaType.PDF
        assert result.metadata.size_bytes == len(PDF_BYTES)
        assert store.read_range(_sha(PDF_BYTES), 0, len(PDF_BYTES)) == PDF_BYTES

    def test_roundtrip_epub(self, store: ArtifactStore) -> None:
        data = _epub_bytes()
        result = store.put(io.BytesIO(data), _sha(data))
        assert result.metadata.media_type is ArtifactMediaType.EPUB

    def test_same_content_not_duplicated(self, store: ArtifactStore) -> None:
        first = store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        path = store._object_path(_sha(PDF_BYTES))
        mtime = path.stat().st_mtime_ns
        second = store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        assert second.deduplicated is True
        assert second.metadata == first.metadata
        assert path.stat().st_mtime_ns == mtime

    def test_divergent_hash_fails_and_cleans_temp(self, store: ArtifactStore) -> None:
        wrong = _sha("outro conteúdo".encode())
        with pytest.raises(StorageError):
            store.put(io.BytesIO(PDF_BYTES), wrong)
        assert not store.exists(wrong)
        assert list((store.root / "tmp").glob("*.part")) == []

    def test_oversized_artifact_fails_and_cleans_temp(self, tmp_path: Path) -> None:
        small = ArtifactStore(tmp_path / "a", max_size_bytes=16)
        data = PDF_BYTES
        with pytest.raises(StorageError, match="tamanho máximo"):
            small.put(io.BytesIO(data), _sha(data))
        assert list((small.root / "tmp").glob("*.part")) == []

    def test_unsupported_type_rejected(self, store: ArtifactStore) -> None:
        data = b"plain text, not a book"
        with pytest.raises(StorageError, match="não suportado"):
            store.put(io.BytesIO(data), _sha(data))
        assert not store.exists(_sha(data))

    def test_zip_without_epub_mimetype_rejected(self, store: ArtifactStore) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("file.txt", "hello")
        data = buf.getvalue()
        with pytest.raises(StorageError, match="mimetype"):
            store.put(io.BytesIO(data), _sha(data))

    def test_corrupted_zip_rejected(self, store: ArtifactStore) -> None:
        data = b"PK\x03\x04" + b"\x00" * 64
        with pytest.raises(StorageError):
            store.put(io.BytesIO(data), _sha(data))

    def test_streaming_large_input(self, store: ArtifactStore) -> None:
        data = PDF_BYTES * 5000  # ~200 KiB em muitos chunks
        result = store.put(io.BytesIO(data), _sha(data))
        assert result.metadata.size_bytes == len(data)


class TestPathSafety:
    @pytest.mark.parametrize("evil", ["../evil", "..", "a/b", "z" * 63, "Z" * 64, ""])
    def test_non_hash_keys_rejected_by_domain_type(self, evil: str) -> None:
        from pydantic import TypeAdapter, ValidationError

        adapter: TypeAdapter[str] = TypeAdapter(Sha256)
        with pytest.raises(ValidationError):
            adapter.validate_python(evil)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32", "system32"),
            ("livro legitimo (2ª ed).pdf", "livro_legitimo_2a_ed_.pdf"),
            ("...", "artifact"),
            ("", "artifact"),
        ],
    )
    def test_sanitize_filename(self, raw: str, expected: str) -> None:
        assert sanitize_filename(raw) == expected

    def test_original_filename_stored_sanitized(self, store: ArtifactStore) -> None:
        result = store.put(
            io.BytesIO(PDF_BYTES), _sha(PDF_BYTES), original_filename="../segredo.pdf"
        )
        assert result.metadata.original_filename == "segredo.pdf"

    def test_object_path_stays_under_root(self, store: ArtifactStore) -> None:
        path = store._object_path(_sha(PDF_BYTES))
        assert path.resolve().is_relative_to(store.root.resolve())


class TestReadRange:
    def test_exact_ranges(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        assert store.read_range(_sha(PDF_BYTES), 0, 5) == b"%PDF-"
        middle = store.read_range(_sha(PDF_BYTES), 10, 7)
        assert middle == PDF_BYTES[10:17]
        tail = store.read_range(_sha(PDF_BYTES), len(PDF_BYTES) - 6, 6)
        assert tail == PDF_BYTES[-6:]

    def test_range_clamped_at_end(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        assert store.read_range(_sha(PDF_BYTES), len(PDF_BYTES) - 4, 100) == PDF_BYTES[-4:]

    def test_offset_beyond_size_fails(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        with pytest.raises(StorageError, match="fora do artefato"):
            store.read_range(_sha(PDF_BYTES), len(PDF_BYTES), 1)

    @pytest.mark.parametrize(("offset", "length"), [(-1, 5), (0, 0), (0, -3)])
    def test_invalid_ranges_fail(self, store: ArtifactStore, offset: int, length: int) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        with pytest.raises(StorageError, match="inválida"):
            store.read_range(_sha(PDF_BYTES), offset, length)

    def test_missing_artifact_raises_not_found(self, store: ArtifactStore) -> None:
        with pytest.raises(NotFoundError):
            store.read_range(_sha(b"inexistente" + b" " * 53), 0, 1)
        with pytest.raises(NotFoundError):
            store.metadata(_sha(b"inexistente" + b" " * 53))

    def test_open_stream_seeks(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        with store.open_stream(_sha(PDF_BYTES), offset=10) as fh:
            assert fh.read(7) == PDF_BYTES[10:17]


class TestMetadata:
    def test_roundtrip(self, store: ArtifactStore) -> None:
        stored = store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        loaded = store.metadata(_sha(PDF_BYTES))
        assert loaded == stored.metadata
        assert loaded.created_at.tzinfo is not None

    def test_corrupted_sidecar_fails_closed(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        store._sidecar_path(_sha(PDF_BYTES)).write_text("{not json", encoding="utf-8")
        with pytest.raises(StorageError, match="corrompidos"):
            store.metadata(_sha(PDF_BYTES))

    def test_sidecar_size_divergence_fails_closed(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        meta = store.metadata(_sha(PDF_BYTES))
        tampered = meta.model_copy(update={"size_bytes": meta.size_bytes + 1})
        store._sidecar_path(_sha(PDF_BYTES)).write_text(
            tampered.model_dump_json(), encoding="utf-8"
        )
        with pytest.raises(StorageError, match="divergem"):
            store.metadata(_sha(PDF_BYTES))


class TestConsistencyModel:
    """R04: objeto é autoritativo; sidecar é cache derivado e reparável."""

    def test_object_without_sidecar_is_repaired_on_read(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        store._sidecar_path(_sha(PDF_BYTES)).unlink()  # crash entre objeto e sidecar
        metadata = store.metadata(_sha(PDF_BYTES))
        assert metadata.size_bytes == len(PDF_BYTES)
        assert metadata.media_type is ArtifactMediaType.PDF
        assert store._sidecar_path(_sha(PDF_BYTES)).exists()

    def test_sidecar_without_object_fails_closed(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        sidecar = store._sidecar_path(_sha(PDF_BYTES))
        raw = sidecar.read_bytes()
        store._object_path(_sha(PDF_BYTES)).unlink()  # remoção externa
        with pytest.raises(NotFoundError):
            store.metadata(_sha(PDF_BYTES))
        assert sidecar.read_bytes() == raw  # não apagado na leitura; audit() remove

    def test_corrupted_object_not_accepted_as_deduplicated(self, store: ArtifactStore) -> None:
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        obj = store._object_path(_sha(PDF_BYTES))
        tampered = bytearray(PDF_BYTES)
        tampered[10] ^= 0xFF  # mesmo tamanho, conteúdo divergente
        obj.write_bytes(bytes(tampered))
        with pytest.raises(StorageError, match=r"corrompido|diverge"):
            store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        assert obj.read_bytes() == bytes(tampered)  # nunca sobrescrito
        with pytest.raises(StorageError, match="diverge"):
            store.verify_integrity(_sha(PDF_BYTES))

    def test_audit_repairs_removes_and_reports(self, store: ArtifactStore) -> None:
        ok = _epub_bytes()
        store.put(io.BytesIO(ok), _sha(ok))
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        store._sidecar_path(_sha(PDF_BYTES)).unlink()  # sidecar ausente → reparo
        orphan_sha = "c" * 64
        orphan = store._object_path(orphan_sha).with_suffix(".meta.json")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("{}", encoding="utf-8")  # sidecar órfão → remoção
        corrupted_data = b"%PDF-1.4\nconteudo original\n%%EOF\n"
        store.put(io.BytesIO(corrupted_data), _sha(corrupted_data))
        obj = store._object_path(_sha(corrupted_data))
        tampered = bytearray(corrupted_data)
        tampered[12] ^= 0xFF
        obj.write_bytes(bytes(tampered))  # corrompido → reportado, não apagado

        report = store.audit()
        assert report.objects == 3
        assert report.sidecars_regenerated == 1
        assert report.orphan_sidecars_removed == 1
        assert report.corrupted == [_sha(corrupted_data)]
        assert obj.exists()  # objeto corrompido preservado para ação administrativa
        assert store._sidecar_path(_sha(PDF_BYTES)).exists()
        assert not orphan.exists()

    def test_concurrent_writers_same_hash(self, store: ArtifactStore) -> None:
        import concurrent.futures

        def write() -> bool:
            return store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES)).deduplicated

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: write(), range(2)))
        assert results.count(False) >= 1
        assert store.read_range(_sha(PDF_BYTES), 0, len(PDF_BYTES)) == PDF_BYTES
        assert store.metadata(_sha(PDF_BYTES)).size_bytes == len(PDF_BYTES)
        assert store.verify_integrity(_sha(PDF_BYTES)).sha256 == _sha(PDF_BYTES)

    def test_sidecar_with_wrong_sha_fails_closed(self, store: ArtifactStore) -> None:
        """RR03: sidecar com hash adulterado (mesmo tamanho) não é aceito."""
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        meta = store.metadata(_sha(PDF_BYTES))
        tampered = meta.model_copy(update={"sha256": "d" * 64})
        store._sidecar_path(_sha(PDF_BYTES)).write_text(
            tampered.model_dump_json(), encoding="utf-8"
        )
        with pytest.raises(StorageError, match="hash divergente"):
            store.metadata(_sha(PDF_BYTES))

    def test_sidecar_with_wrong_media_type_fails_verification_and_dedup(
        self, store: ArtifactStore
    ) -> None:
        """RR03: media type adulterado falha na verificação e na deduplicação."""
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        meta = store.metadata(_sha(PDF_BYTES))
        tampered = meta.model_copy(update={"media_type": ArtifactMediaType.EPUB})
        store._sidecar_path(_sha(PDF_BYTES)).write_text(
            tampered.model_dump_json(), encoding="utf-8"
        )
        with pytest.raises(StorageError, match=r"[Mm]edia type"):
            store.verify_integrity(_sha(PDF_BYTES))
        with pytest.raises(StorageError, match=r"[Mm]edia type"):
            store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))

    def test_audit_regenerates_inconsistent_sidecars(self, store: ArtifactStore) -> None:
        """RR03: audit() repara sidecar com sha/tipo divergente (objeto íntegro)."""
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        meta = store.metadata(_sha(PDF_BYTES))
        tampered = meta.model_copy(update={"media_type": ArtifactMediaType.EPUB})
        store._sidecar_path(_sha(PDF_BYTES)).write_text(
            tampered.model_dump_json(), encoding="utf-8"
        )
        report = store.audit()
        assert report.sidecars_regenerated == 1
        assert report.corrupted == []
        repaired = store.metadata(_sha(PDF_BYTES))
        assert repaired.media_type is ArtifactMediaType.PDF
        assert store.verify_integrity(_sha(PDF_BYTES)).sha256 == _sha(PDF_BYTES)

    def test_interrupted_sidecar_tmp_is_not_an_object(self, store: ArtifactStore) -> None:
        """RRR02: temporário de sidecar interrompido não vira objeto corrompido."""
        store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        obj = store._object_path(_sha(PDF_BYTES))
        stale_tmp = obj.with_suffix(".meta.deadbeefdeadbeefdeadbeefdeadbeef.tmp")
        stale_tmp.write_text('{"partial": true', encoding="utf-8")
        report = store.audit()
        assert report.objects == 1
        assert report.corrupted == []
        assert report.sidecar_temps_removed == 1
        assert not stale_tmp.exists()
        assert store.verify_integrity(_sha(PDF_BYTES)).sha256 == _sha(PDF_BYTES)

    def test_sidecar_write_failure_leaves_no_tmp(
        self, store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RRR02: falha antes do replace do sidecar não deixa temporário órfão."""
        real_replace = os.replace

        def boom(src: object, dst: object) -> None:
            if str(dst).endswith(".meta.json"):
                raise OSError("falha simulada na publicação do sidecar")
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError, match="falha simulada"):
            store.put(io.BytesIO(PDF_BYTES), _sha(PDF_BYTES))
        leftovers = list((store._root / "objects").glob("*/*/*.tmp"))
        assert leftovers == []
        monkeypatch.undo()
        # objeto publicado sem sidecar é estado reparável (R04)
        assert store.verify_integrity(_sha(PDF_BYTES)).sha256 == _sha(PDF_BYTES)

    def test_orphan_tmp_is_invisible_and_swept(self, store: ArtifactStore) -> None:
        """Crash antes do replace: só resta .part, nunca apresentado como válido."""
        orphan = store.root / "tmp" / "crash.part"
        orphan.write_bytes(PDF_BYTES)
        assert not store.exists(_sha(PDF_BYTES))
        with pytest.raises(NotFoundError):
            store.metadata(_sha(PDF_BYTES))
        import os as _os
        import time as _time

        old = _time.time() - timedelta(hours=48).total_seconds()
        _os.utime(orphan, (old, old))
        assert store.cleanup_stale_temps(max_age=timedelta(hours=24)) == 1


class TestTempCleanup:
    def test_stale_temps_removed_fresh_kept(self, store: ArtifactStore) -> None:
        tmp_dir = store.root / "tmp"
        stale = tmp_dir / "stale.part"
        fresh = tmp_dir / "fresh.part"
        stale.write_bytes(b"lixo")
        fresh.write_bytes(b"recente")
        old = time.time() - timedelta(hours=48).total_seconds()
        os.utime(stale, (old, old))
        removed = store.cleanup_stale_temps(max_age=timedelta(hours=24))
        assert removed == 1
        assert not stale.exists()
        assert fresh.exists()

    def test_detect_media_type_directly(self, tmp_path: Path) -> None:
        pdf = tmp_path / "x.bin"
        pdf.write_bytes(PDF_BYTES)
        assert detect_media_type(pdf) is ArtifactMediaType.PDF
