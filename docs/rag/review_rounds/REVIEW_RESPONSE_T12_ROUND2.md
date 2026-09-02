# Resposta à revisão T12 — rodada 2

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T12_ROUND2.md`
Status: correção T12-R2-01 implementada e verificada.
Resultado anterior: **reprovado** (T12-R2-01 bloqueador).

---

## T12-R2-01 (Bloqueador) — Validator ainda rejeita offsets válidos de passagens multipágina

### Diagnóstico
`EvidenceRef._offsets_coherent` e `CitablePassage._offsets_coherent` rejeitam
incondicionalmente `char_end <= char_start`. Essa comparação só é válida
quando início e fim estão na MESMA página; para uma passagem multipágina,
`char_start` é relativo à página de início e `char_end` à página de fim
(NOTES.md §10.6 item 3) — ex.: início no offset 100 da página 10 e fim no
offset 3 da página 11 é corretto e reproduzível, mas o modelo falhava antes
de produzir o `quote`. O mesmo defeito existia em `Passage` e na constraint
CHECK `passages_check1` do banco (migration 0001), que bloquearia a própria
ingestão/persistência de chunks multipágina com offsets invertidos.

### Correção
1. **Domínio** — `EvidenceRef._offsets_coherent` (`domain/answer.py`) e
   `CitablePassage._offsets_coherent` (`domain/context.py`) passaram a aplicar
   `char_end > char_start` somente quando os offsets referem à MESMA página:
   `page_end is None or physical_page is None or page_end == physical_page`.
   Para páginas distintas, valida a ordem das páginas
   (`page_end >= physical_page`) e só limites não negativos.
2. **`Passage`** (`domain/library.py`) — mesma correção, usando
   `page_start_id == page_end_id` (páginas distintas → offsets invertidos
   permitidos; sem páginas ou mesma página → `char_end > char_start`).
3. **Banco** — migration nova `0007_passages_multipage_offset_check.py`:
   `passages_check1` passa de `char_end IS NULL OR char_end > char_start` para
   `char_end IS NULL OR (page_start_id IS NOT NULL AND page_end_id IS NOT NULL
   AND page_start_id <> page_end_id) OR char_end > char_start`. Reversível.
4. **Testes**:
   - Unit positivos/negativos nos três modelos: offsets invertidos entre
     páginas são VÁLIDOS (`char_start=100`, `char_end=3` em páginas 10/11);
     na mesma página ou sem informação de página, `char_end <= char_start`
     continua rejeitado (`tests/unit/test_answer.py`, `tests/unit/test_context.py`,
     `tests/unit/test_library.py`).
   - Integração: a passagem multipágina de
     `test_quote_multipage_passage_reproduces_text` passou a usar offsets
     invertidos (`char_start=30` na página 0, `char_end=13` na página 1) —
     o caso realista que o validator anterior rejeitava — e reproduz o
     trecho exato contra PostgreSQL real (migration `0007` aplicada).

### Evidências
- `tests/unit/test_answer.py::TestEvidenceRef::test_multipage_offsets_valid_when_inverted_between_pages`
- `tests/unit/test_answer.py::TestEvidenceRef::test_same_page_offsets_require_char_end_gt_char_start`
- `tests/unit/test_context.py::TestCitablePassageOffsets` (invertidos válidos; mesma página rejeitado)
- `tests/unit/test_library.py::TestPassage::test_multipage_offsets_valid_when_inverted_between_pages`
- `tests/integration/test_context_pipeline.py::test_quote_multipage_passage_reproduces_text`
  — offsets invertidos entre páginas, reconstrução e destaque contra PostgreSQL real.

---

## Avaliação dos demais itens da rodada

- T12-02 e T12-03 foram avaliados como atendidos na rodada 2; nenhuna
  correção adicional foi aplicada.

---

## Evidências executadas (2026-09-01, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `git diff --check HEAD` | passou |
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 109 arquivos |
| `uv run mypy src tests` | OK — 109 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 500 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration/test_context_pipeline.py -q` | OK — 10 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 211 passed, 1 skipped (PostgreSQL real) |

Arquivos alterados nesta rodada:
- `backend/alembic/versions/0007_passages_multipage_offset_check.py` (nova migration)
- `backend/src/rag/domain/answer.py` (T12-R2-01)
- `backend/src/rag/domain/context.py` (T12-R2-01)
- `backend/src/rag/domain/library.py` (T12-R2-01 — `Passage`)
- `backend/tests/unit/test_answer.py`, `backend/tests/unit/test_context.py`,
  `backend/tests/unit/test_library.py` (T12-R2-01)
- `backend/tests/integration/test_context_pipeline.py` (offsets invertidos)
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

O bloqueador T12-R2-01 foi corrigido nos três modelos do domínio e na
constraint CHECK do banco (migration `0007`), com testes positivos e
negativos para `char_start > char_end` em páginas diferentes e na mesma
página, e a integração multipágina reexecutada com offsets invertidos contra
PostgreSQL real (211 passed, 1 skipped). A revisão pode ser refeita.
