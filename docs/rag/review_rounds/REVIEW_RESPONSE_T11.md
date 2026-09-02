# Resposta à revisão T11

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T11.md`
Status: correções T11-01 a T11-03 implementadas e verificadas.
Resultado anterior: **reprovado** (T11-01 bloqueador; T11-02 alto; T11-03 médio).

---

## T11-01 (Bloqueador) — Enriquecimento não integrado ao fluxo de indexação

### Diagnóstico
O único chamador de `EnrichmentService.enrich()` era a fixture de teste; não
havia provedor configurado, instância operacional nem chamada no `IndexingService`
ou no CLI. O desvio de NOTES.md §10.12 item 12 ("integração fica para T14+")
não tinha aprovação e não podia substituir o entregável de T11.

### Correção
1. Novo comando operacional **`rag enrich <edition-id>`** (`cli/main.py`),
   acionável e configurado:
   - `_enrich_async` instancia `EnrichmentService` com
     `OpenAiCompatibleEnrichmentProvider(EnrichmentEndpointSettings())`
     (env `ENRICHMENT_*`), reusa `Database`, e exibe o `EnrichmentReport`
     (contagens e avisos) no mesmo estilo do resto do CLI;
   - `create_app` ganhou `enrichment_provider_factory` para injeção em teste;
   - exit codes 0/1/2 e logs JSON redigidos, como os demais comandos.
2. Rota operacional testada ponta a ponta (integração, PostgreSQL real):
   `rag ingest` → `rag index` → `rag enrich` cria a hierarquia; reexecução da
   mesma versão é idempotente; falha fechada (suporte fora do escopo) publica
   ZERO summaries e ZERO execuções — sem publicação parcial.
3. NOTES.md atualizado: item 12 revocado e substituído pelo comando `rag enrich`;
   novo registro §12 documenta as correções desta revisão.

### Evidências
- `tests/integration/test_cli.py::TestEnrichCommand::test_enrich_after_index_creates_hierarchy`
  — `rag enrich` cria seção/capítulo/edição + conceitos sobre a execução ativa.
- `tests/integration/test_cli.py::TestEnrichCommand::test_enrich_reexecution_same_version_is_idempotent`
  — `enriquecimento[existente]` na reexecução da mesma versão.
- `tests/integration/test_cli.py::TestEnrichCommand::test_enrich_failure_publishes_nothing`
  — falha fechada: `count(summaries) == 0` e `count(enrichment_runs) == 0`.
- `tests/integration/test_cli.py::TestEnrichCommand::test_enrich_unknown_edition_exit_one`.

---

## T11-02 (Alto) — Enriquecimento usa passagens de execuções inativas

### Diagnóstico
`EnrichmentService.enrich()` usava `PassagesRepository.list_by_edition()`, que
devolve passagens de TODAS as execuções (histórico incluído); após uma
reindexação podia enviar chunks inativos ao provedor e persistir sínteses/
conceitos sustentados por eles, conflitando com a garantia T06/T08 de execução
ativa e comprometendo AC-15.

### Correção
1. `EnrichmentService.enrich()` resolve a `IndexRun` ativa da edição
   (`IndexRunsRepository.get_active`) e usa
   `PassagesRepository.list_by_index_run(active_run.id)` — o mesmo conjunto
   corrente da recuperação (T6-01). Sem execução ativa → `IngestionError`
   fechado ("rode `rag index` primeiro").
2. Nova integração com DUAS reindexações prova que nenhuma passagem inativa
   chega ao provedor (requests registrados pelo double) nem vira suporte
   publicada.

### Evidências
- `tests/integration/test_enrichment_pipeline.py::test_two_reindexations_never_use_inactive_passages`
  — `--force` minta execução nova com passagens disjuntas; na segunda execução
  os `PassageRef`s enviados ao provedor e os `supporting_passage_ids` publicados
  pertencem só à execução ativa.

---

## T11-03 (Médio) — Idempotência falha quando todos os summaries são rejeitados

### Diagnóstico
A idempotência era decidida por `has_for_edition_version()` (existência de ao
menos uma linha em `summaries`). Se o provedor devolver suporte vazio em todos
os escopos (comportamento permitido pelo contrato), a execução não fica
registrada e a reexecução da mesma versão repete todas as chamadas ao modelo
devolvendo `created=True`.

### Correção
1. Migration nova **`0005_enrichment_runs.py`**: tabela `enrichment_runs`
   (edição + versão de síntese + versão de extrator), com
   `UNIQUE (edition_id, summarizer_version_id)`.
2. Modelo `EnrichmentRun` (`domain/knowledge.py`) e `EnrichmentRunsRepository`
   (`get_for_edition_version`, `create_if_absent`, `count_for_edition`).
3. `EnrichmentService.enrich()`:
   - a idempotência consulta `enrichment_runs` (execução concluída), não
     `summaries`;
   - o registro da execução é inserido na MESMA transação dos itens — existe
     MESMO quando nenhum item é publicado; falha rollbacka tudo (itens e
     execução); `ON CONFLICT DO NOTHING` + `rowcount` cobrem corridas de
     concorrência.
4. Teste novo do caso "todos os summaries e conceitos rejeitados".

### Evidências
- `tests/integration/test_enrichment_pipeline.py::test_all_items_rejected_is_still_idempotent`
  — suporte vazio em todos os escopos + zero conceitos: `created=True` na
  primeira, `created=False` na segunda, `summaries == []`, `runs == 1`.
- `tests/integration/test_enrichment_pipeline.py::test_reexecution_same_version_is_idempotent`
  — continua a valer com itens publicados.
- `tests/integration/test_cli.py::TestEnrichCommand::test_enrich_reexecution_same_version_is_idempotent`
  — rota operacional idempotente.

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
| `make lock` | OK — 165 pacotes, lockfiles consistentes |
| `python3 scripts/security_scan.py .` | OK — nenhum IOC bloqueado |

Arquivos alterados:
- `backend/alembic/versions/0005_enrichment_runs.py` (nova migration)
- `backend/src/rag/application/enrichment.py` (T11-02, T11-03)
- `backend/src/rag/cli/main.py` (T11-01)
- `backend/src/rag/domain/knowledge.py` (`EnrichmentRun`)
- `backend/src/rag/infrastructure/repositories/enrichment.py` (`EnrichmentRunsRepository`)
- `backend/tests/integration/test_cli.py` (`TestEnrichCommand`)
- `backend/tests/integration/test_enrichment_pipeline.py` (testes T11-02/T11-03)
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

Os dois achados de severidade bloqueadora/alto e o de severidade médio de
REVIEW_T11.md foram corrigidos e verificados com testes de integração contra
PostgreSQL real (201 passed, 1 skipped). A matriz `AC-01` a `AC-20` foi
atualizada em `docs/rag/EVIDENCE.md` (AC-12 e AC-15). A revisão pode ser
refeita.
