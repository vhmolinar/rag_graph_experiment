# Resposta à revisão T11 — rodada 2

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T11_ROUND2.md`
Status: correção R2-T11-01 implementada e verificada.
Resultado anterior: **reprovado** (R2-T11-01, alto).

---

## R2-T11-01 (Alto) — A chave de idempotência ignora a execução de indexação

### Diagnóstico
`enrichment_runs` era única somente por `(edition_id, summarizer_version_id)`:
`index_run_id` — o conjunto de passagens efetivamente enviado ao provedor — não
integrava a tabela nem a decisão de idempotência. Reindexar a edição
(`rag index --force` ou versão nova de chunking/embedding) e reexecutar
`rag enrich` com o MESMO modelo encontrava a execução anterior e devolvia
`created=False`, deixando as sínteses sustentadas por chunks da execução
inativa. O teste de T11-02 não detectava o caso porque trocava `model_name`,
evitando a colisão que ocorreria em operação normal.

### Correção
1. **Migration `0005` atualizada** (`enrichment_runs`):
   - coluna `index_run_id uuid NOT NULL` com FK composta
     `(index_run_id, edition_id) → index_runs(id, edition_id)` (R01 — a
     execução de indexação deve pertencer à edição da síntese);
   - unicidade por `(edition_id, index_run_id, summarizer_version_id)`.
2. **Modelo `EnrichmentRun`** (`domain/knowledge.py`) ganhou `index_run_id`; a
   docstring declara a identidade `(edição, execução de indexação, versão de
   síntese)`.
3. **`EnrichmentRunsRepository`**: `get_for_edition_version` →
   `get_for_edition_run_version(edition_id, index_run_id, summarizer_version_id)`;
   `create_if_absent` usa `ON CONFLICT (edition_id, index_run_id,
   summarizer_version_id)`.
4. **`EnrichmentService.enrich()`** passa `active_run.id` como `index_run_id`
   na consulta de idempotência e na criação do `EnrichmentRun` — reindexar com o
   mesmo modelo NÃO é no-op: a identidade mudou, exige nova execução sobre o
   conjunto corrente (T11-02, SPEC §7.4/§8.7).
5. **Teste de duas reindexações reforçado** com o MESMO modelo de
   enriquecimento nos dois passos, exercitando a colisão que ocorre em operação
   normal.

### Evidências
- `tests/integration/test_enrichment_pipeline.py::test_two_reindexations_never_use_inactive_passages`
  — com o MESMO `model_name`:
  - a reindexação (`--force`) minta execução ativa nova, com passagens
    disjuntas da anterior;
  - a segunda execução de enriquecimento é `created=True` (NÃO no-op) mesmo
    com `summarizer_version_id` idêntico — prova que `index_run_id` integra a
    chave;
  - os `PassageRef`s enviados ao provedor na segunda execução pertencem SÓ à
    execução ativa (nada inativo chega ao provedor);
  - duas execuções de enriquecimento (`runs == 2`) e duas gerações de sínteses
    (10) conviven — histórico preservado (AC-15);
  - a segunda geração (últimas 5 por `created_at`) refere SÓ a passagens da
    execução ativa.
- `tests/integration/test_cli.py::TestEnrichCommand` continua a valer (rota
  operacional; reexecução sem reindexação idempotente).

---

## Evidências executadas (2026-09-01, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 105 arquivos |
| `uv run mypy src tests` | OK — 105 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 469 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration -q` | OK — 201 passed, 1 skipped (PostgreSQL real) |

Arquivos alterados nesta rodada:
- `backend/alembic/versions/0005_enrichment_runs.py` (coluna `index_run_id`, FK
  composta, unicidade por `(edition_id, index_run_id, summarizer_version_id)`)
- `backend/src/rag/application/enrichment.py` (chave de idempotência +
  `index_run_id` no `EnrichmentRun`)
- `backend/src/rag/domain/knowledge.py` (`EnrichmentRun.index_run_id`)
- `backend/src/rag/infrastructure/repositories/enrichment.py`
  (`get_for_edition_run_version`, `ON CONFLICT` atualizado)
- `backend/tests/integration/test_enrichment_pipeline.py` (teste de duas
  reindexações com o mesmo modelo)
- `docs/rag/NOTES.md` (§12 item 4), `docs/rag/EVIDENCE.md`

## Conclusão

A chave de idempotência de `enrichment_runs` agora inclui `index_run_id`: o
enriquecimento representa sempre o conjunto de passagens corrente, e o teste de
duas reindexações com o mesmo modelo comprova o comportamento exigido. A
revisão pode ser refeita.
