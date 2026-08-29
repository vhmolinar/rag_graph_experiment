"""Prova de que o domínio não importa FastAPI, ORM, Docling ou SDKs de modelo (T02).

Varre a AST de todos os módulos em rag/domain e rejeita imports proibidos.
"""

import ast
from pathlib import Path

import rag.domain

FORBIDDEN_ROOTS = {
    "fastapi",
    "starlette",
    "uvicorn",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "pgvector",
    "docling",
    "httpx",
    "requests",
    "openai",
    "langchain",
    "langgraph",
}

ALLOWED_THIRD_PARTY = {"pydantic"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_has_no_forbidden_imports() -> None:
    domain_dir = Path(rag.domain.__file__).parent
    offenders: dict[str, set[str]] = {}
    for module_path in sorted(domain_dir.rglob("*.py")):
        roots = _imported_roots(module_path)
        bad = roots & FORBIDDEN_ROOTS
        if bad:
            offenders[module_path.name] = bad
    assert not offenders, f"imports proibidos no domínio: {offenders}"


def test_domain_only_uses_stdlib_pydantic_and_rag() -> None:
    import sys

    stdlib = set(sys.stdlib_module_names)
    domain_dir = Path(rag.domain.__file__).parent
    for module_path in sorted(domain_dir.rglob("*.py")):
        for root in _imported_roots(module_path):
            assert root in stdlib or root in ALLOWED_THIRD_PARTY or root == "rag", (
                f"{module_path.name} importa {root!r}, fora do permitido"
            )
