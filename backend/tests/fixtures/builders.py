"""Construtores determinísticos de fixtures de documento (T05).

PDF-texto mínimo com xref válido e EPUB mínimo com spine — pequenos,
gerados em tempo de teste, legalmente utilizáveis (texto sintético próprio).
"""

import io
import zipfile


def _pdf_escape(text: str) -> str:
    """Escapa string literal PDF; não-ASCII vira octal WinAnsi/Latin-1."""
    out = []
    for ch in text:
        if ch in "\\()":
            out.append(f"\\{ch}")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\{ch.encode('latin-1')[0]:03o}")
    return "".join(out)


def make_text_pdf(pages: list[list[str]]) -> bytes:
    """PDF-texto mínimo: cada página é uma lista de linhas (primeira linha em
    corpo maior, simulando título)."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # placeholder: Pages
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    page_obj_ids: list[int] = []
    next_id = 4
    page_objects: list[bytes] = []
    for lines in pages:
        page_id, contents_id = next_id, next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)
        stream_lines = []
        y = 720
        for i, line in enumerate(lines):
            size = 18 if i == 0 else 12
            stream_lines.append(f"BT /F1 {size} Tf 72 {y} Td ({_pdf_escape(line)}) Tj ET")
            y -= 30
        stream = "\n".join(stream_lines).encode("latin-1")
        page_objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {contents_id} 0 R >>".encode()
        )
        page_objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects.extend(page_objects)

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


def make_epub(chapters: list[tuple[str, list[str]]]) -> bytes:
    """EPUB 3 mínimo: cada capítulo é (título, [parágrafos])."""
    manifest_items = []
    spine_items = []
    files: dict[str, str] = {}
    for i, (title, paragraphs) in enumerate(chapters, start=1):
        name = f"ch{i}.xhtml"
        body = "".join(f"<p>{p}</p>" for p in paragraphs)
        files[f"OEBPS/{name}"] = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>{title}</title></head><body><h1>{title}</h1>{body}"
            "</body></html>"
        )
        manifest_items.append(
            f'<item id="ch{i}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="ch{i}"/>')
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">fixture-1</dc:identifier>'
        "<dc:title>Fixture</dc:title><dc:language>pt</dc:language>"
        "</metadata>"
        f"<manifest>{''.join(manifest_items)}</manifest>"
        f"<spine>{''.join(spine_items)}</spine></package>"
    )
    files["META-INF/container.xml"] = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()
