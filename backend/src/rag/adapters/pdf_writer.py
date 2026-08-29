"""Gravador de PDF com camada de texto invisível (T05; contrato OCR §10.1.5).

O derivado OCR é um PDF novo: cada página tem o texto reconhecido posicionado
nas coordenadas da varredura original com modo de renderização invisível
(Tr 3) — pesquisável e destacável sobre a imagem, sem alterar o original.

Escrita atômica: arquivo temporário + fsync + rename, alinhado ao modelo de
consistência do artifact store (T04). Não é um gravador PDF genérico: cobre
somente o formato de camada de texto produzido pelo pipeline de OCR.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from rag.domain.errors import StorageError
from rag.domain.identifiers import Sha256


@dataclass(frozen=True)
class OcrLine:
    """Linha reconhecida. Coordenadas em pontos PDF (origem inferior-esquerda)."""

    text: str
    x: float
    y: float
    height: float


@dataclass(frozen=True)
class OcrPage:
    width: float
    height: float
    lines: tuple[OcrLine, ...]


def _escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\()":
            out.append(f"\\{ch}")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\{ch.encode('latin-1')[0]:03o}")
    return "".join(out)


def _build(pages: list[OcrPage]) -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # placeholder: Pages
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    page_obj_ids: list[int] = []
    next_id = 4
    for page in pages:
        page_id, contents_id = next_id, next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)
        stream_lines = []
        for line in page.lines:
            size = max(1.0, line.height)
            stream_lines.append(
                f"BT /F1 {size:.1f} Tf 3 Tr {line.x:.2f} {line.y:.2f} Td "
                f"({_escape(line.text)}) Tj ET"
            )
        stream = "\n".join(stream_lines).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width:.2f} "
            f"{page.height:.2f}] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {contents_id} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()

    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(header) + len(body))
        body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(header) + len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(
        f"{offset:010d} 00000 n \n" for offset in offsets
    )
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n"
    return header + body + xref.encode() + trailer.encode()


def write_text_layer_pdf(pages: list[OcrPage], output: Path) -> Sha256:
    """Grava o PDF de camada de texto atomicamente e retorna o sha256."""
    import hashlib

    if not pages:
        raise StorageError("Derivado OCR sem páginas não pode ser gravado.")
    data = _build(pages)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.parent / f".{output.name}.{uuid4().hex}.tmp"
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, output)
        dir_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Sha256(hashlib.sha256(data).hexdigest())
