# Resposta à quarta revisão T06

Data: 2026-08-30  
Referência: `docs/rag/REVIEW_T06_ROUND4.md`

## Correções realizadas

- PDF: `citable_text` usa exatamente a fatia definida pelos offsets, incluindo
  separadores físicos entre blocos. Regressão adicionada em
  `test_pdf_citable_text_recomposes_offsets_across_blocks`.
- Fingerprint: adicionados testes de determinismo e cobertura de texto,
  original, seção, nível e warnings (`tests/unit/test_fingerprint.py`).
- Backfill: adicionado `rag backfill-fingerprint <edition-id>`, que reextrai o
  artefato imutável, valida páginas persistidas e grava o fingerprint em uma
  transação. Edições sem fingerprint continuam falhando fechado até essa
  operação ser executada.
- Seções: somente `section_path=()` resulta em `section_id=None`; caminhos não
  vazios desconhecidos falham.
- Contract tests: `make test-unit` ignora o adapter HTTP e `make test-contract`
  o coleta após sua movimentação física para `tests/contract/`, mantendo os
  gates sem sobreposição.
- Podman: `make test-integration` usa o socket Docker-compatible do Podman e
  desabilita o Ryuk incompatível.

## Evidências

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK |
| `uv run mypy src tests` | OK |
| `uv run pytest tests/unit/test_fingerprint.py -q` | 3 passed |
| `uv run pytest tests/unit/test_chunking.py -q` | 21 passed |
| `make test-unit` | 257 passed, 3 skipped |
| `make test-contract` | 26 passed |
| `make test-integration` via Podman | 111 passed, 1 skipped |
| `uv run alembic -c alembic.ini heads` | único head: `0004` |

Integração de migration e persistência foi executada contra PostgreSQL/pgvector
provisionado pelo Podman. O backfill possui cobertura de código e contrato,
mas não é executado automaticamente contra uma edição legada em cada suíte;
essa operação é administrativa e deve ser aplicada às edições existentes.
