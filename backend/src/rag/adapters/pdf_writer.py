"""Gravador de PDF com camada de texto invisível (T05; contrato OCR §10.1.5).

O derivado OCR é o PRÓPRIO documento original (mesmas páginas, mesma imagem,
mesma contagem, dimensões e rotação) com objetos de texto invisíveis
(render mode 3) inseridos nas coordenadas reconhecidas — pesquisável e
destacável sobre a imagem da varredura, sem alterar o arquivo original em
disco. Não constrói um PDF do zero: usa `pypdfium2` (dependência direta desde
a correção R5-09 — antes só transitiva via Docling) para abrir o original e
sobrepor a camada.

Publicação em UM único artefato (correção R6-01): a proveniência OCR
(`ocr_adapter.py`) é embutida como ANEXO dentro do próprio PDF derivado antes
da gravação, não como um sidecar em arquivo separado. Um segundo arquivo
publicado por um segundo `os.replace` nunca é atômico em conjunto com o
primeiro — a janela entre os dois é sempre observável por uma falha exatamente
ali. Com um único arquivo, a garantia de atomicidade já provida por
"temporário + fsync + rename" (usada pelo artifact store desde T04) basta:
não há segundo arquivo para dessincronizar.
"""

import ctypes
import hashlib
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from rag.domain.errors import StorageError
from rag.domain.identifiers import Sha256

_FONT = "Helvetica"
# Tolerância para a checagem de geometria (correção R6-04): diferenças de
# arredondamento entre o que o motor reportou e o que a página realmente
# tem não devem falhar a ingestão, mas uma divergência real (motor leu a
# página errada, ou com DPI/escala equivocada) deve.
_GEOMETRY_TOLERANCE_PT = 1.0


@dataclass(frozen=True)
class OcrLine:
    """Linha reconhecida. Coordenadas em pontos PDF (origem inferior-esquerda).

    `width`, quando informado pelo motor (bounding box completo), é usado
    para ajustar a largura do texto invisível ao trecho detectado (correção
    R5-07) — sem isso, o texto usa a largura natural da fonte, que pode não
    coincidir com a seleção esperada sobre a imagem.
    """

    text: str
    x: float
    y: float
    height: float
    width: float | None = None


@dataclass(frozen=True)
class OcrPage:
    """Página reconhecida. `physical_index` (0-based) é o índice físico da
    página no PDF original — verificado explicitamente contra a posição na
    lista antes de gravar (correção R5-07: nunca associar por posição na
    lista apenas). `width`/`height` são verificados contra a geometria real
    da página (correção R6-04: geometria declarada nunca era validada)."""

    physical_index: int
    width: float
    height: float
    lines: tuple[OcrLine, ...]


def _fit_width(text_obj: object, target_width: float) -> None:
    """Escala horizontalmente o objeto de texto já posicionado para que sua
    largura natural passe a coincidir com `target_width`, mantendo a borda
    esquerda fixa (âncora em `x`).

    Falha fechado (correção R6-04): se os limites do objeto não puderem ser
    obtidos, o ajuste não pode ser confirmado — silenciar isso deixaria o
    derivado publicado com um alinhamento não verificado.
    """
    left, _bottom, right, _top = (ctypes.c_float() for _ in range(4))
    if not pdfium_c.FPDFPageObj_GetBounds(text_obj, left, _bottom, right, _top):
        raise StorageError("Falha ao obter os limites do texto do OCR para ajuste de largura.")
    natural_width = right.value - left.value
    if natural_width <= 0 or target_width <= 0:
        return
    scale_x = target_width / natural_width
    pdfium_c.FPDFPageObj_Transform(text_obj, scale_x, 0, 0, 1, left.value * (1 - scale_x), 0)


def _insert_invisible_text(  # type: ignore[no-any-unimported]
    document: pdfium.PdfDocument, page: pdfium.PdfPage, line: OcrLine
) -> None:
    if not line.text:
        return
    size = max(1.0, line.height)
    text_obj = pdfium_c.FPDFPageObj_NewTextObj(document, _FONT.encode("utf-8"), size)
    if not text_obj:
        raise StorageError("Falha ao criar objeto de texto do derivado OCR.")
    encoded = (line.text + "\x00").encode("utf-16-le")
    buffer = ctypes.create_string_buffer(encoded, len(encoded))
    ok = pdfium_c.FPDFText_SetText(text_obj, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)))
    if not ok:
        raise StorageError("Falha ao definir o texto reconhecido no derivado OCR.")
    ok = pdfium_c.FPDFTextObj_SetTextRenderMode(text_obj, pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE)
    if not ok:
        raise StorageError("Falha ao tornar invisível o texto do derivado OCR.")
    pdfium_c.FPDFPageObj_Transform(text_obj, 1, 0, 0, 1, line.x, line.y)
    if line.width is not None:
        _fit_width(text_obj, line.width)
    if not pdfium_c.FPDFPage_InsertObject(page, text_obj):
        raise StorageError("Falha ao inserir o texto do OCR na página.")


def _check_geometry(page: pdfium.PdfPage, ocr_page: OcrPage) -> None:  # type: ignore[no-any-unimported]
    actual_width, actual_height = page.get_width(), page.get_height()
    if (
        abs(actual_width - ocr_page.width) > _GEOMETRY_TOLERANCE_PT
        or abs(actual_height - ocr_page.height) > _GEOMETRY_TOLERANCE_PT
    ):
        raise StorageError(
            "Dimensões reportadas pelo motor de OCR não correspondem à página do "
            "original (proveniência geométrica incoerente).",
            context={
                "pagina": str(ocr_page.physical_index),
                "motor": f"{ocr_page.width:.2f}x{ocr_page.height:.2f}",
                "original": f"{actual_width:.2f}x{actual_height:.2f}",
            },
        )


def embed_attachment(document: pdfium.PdfDocument, name: str, data: bytes) -> None:  # type: ignore[no-any-unimported]
    """Embute `data` como anexo nomeado dentro do PDF (correção R6-01: torna
    a proveniência parte do MESMO artefato publicado, eliminando qualquer
    janela de dessincronia entre PDF e metadados).

    `new_attachment`/`set_data` (pypdfium2) já falham fechado internamente
    (`PdfiumError`) — convertidos aqui para o tipo de erro do módulo.
    """
    try:
        attachment = document.new_attachment(name)
        attachment.set_data(data)
    except pdfium.PdfiumError as exc:
        raise StorageError("Falha ao embutir a proveniência no derivado OCR.", cause=exc) from exc


def read_attachment(pdf_path: Path, name: str) -> bytes | None:
    """Lê o anexo `name` embutido em `pdf_path`, ou `None` se não existir."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(document.count_attachments()):
            attachment = document.get_attachment(index)
            if attachment.get_name() == name:
                return bytes(attachment.get_data())
        return None
    finally:
        document.close()


def build_text_layer_pdf(
    pages: list[OcrPage], source_pdf: Path, *, attachment: tuple[str, bytes] | None = None
) -> bytes:
    """Sobrepõe a camada de texto às páginas de `source_pdf` e retorna os
    bytes do PDF resultante (sem gravar em disco).

    `pages` deve corresponder 1:1, na mesma ordem, no mesmo índice físico E
    na mesma geometria (largura/altura, com tolerância) às páginas de
    `source_pdf` — qualquer divergência falha fechado. `attachment`, se
    informado, é embutido no documento antes da gravação (nome, bytes) —
    usado para a proveniência OCR (correção R6-01).
    """
    if not pages:
        raise StorageError("Derivado OCR sem páginas não pode ser gravado.")

    document = pdfium.PdfDocument(str(source_pdf))
    try:
        if len(document) != len(pages):
            raise StorageError(
                "Quantidade de páginas do OCR não corresponde ao original.",
                context={"original_pages": len(document), "ocr_pages": len(pages)},
            )
        for index, ocr_page in enumerate(pages):
            if ocr_page.physical_index != index:
                raise StorageError(
                    "Página do OCR fora de ordem: não corresponde ao índice físico "
                    "esperado no original.",
                    context={"esperado": index, "recebido": ocr_page.physical_index},
                )
            page = document.get_page(index)
            try:
                _check_geometry(page, ocr_page)
                for line in ocr_page.lines:
                    _insert_invisible_text(document, page, line)
                if not pdfium_c.FPDFPage_GenerateContent(page):
                    raise StorageError(
                        "Falha ao confirmar a geração do conteúdo da página do derivado OCR.",
                        context={"pagina": str(index)},
                    )
            finally:
                page.close()
        if attachment is not None:
            name, data = attachment
            embed_attachment(document, name, data)
        buffer = BytesIO()
        document.save(buffer)
    finally:
        document.close()
    return buffer.getvalue()


def _fsync_dir(path: Path) -> None:
    dir_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def publish_file(data: bytes, output: Path) -> Sha256:
    """Grava `data` atomicamente em `output` (temporário + fsync + rename +
    fsync do diretório) e retorna seu sha256. Único ponto de publicação de
    arquivo deste módulo — usado tanto por `write_text_layer_pdf` quanto por
    `ocr_adapter.ocr_pdf` (que já embutiu a proveniência em `data` antes de
    chamar esta função)."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.parent / f".{output.name}.{uuid4().hex}.tmp"
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, output)
        _fsync_dir(output.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Sha256(hashlib.sha256(data).hexdigest())


def write_text_layer_pdf(pages: list[OcrPage], output: Path, source_pdf: Path) -> Sha256:
    """Constrói e grava atomicamente o derivado com camada de texto (sem
    proveniência embutida). Retorna o sha256 do derivado."""
    data = build_text_layer_pdf(pages, source_pdf)
    return publish_file(data, output)
