# Resposta à revisão T10

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T10.md`
Status: correções T10-01 a T10-04 implementadas e verificadas.
Resultado anterior: **reprovado** (T10-01/T10-02 bloqueadores; T10-03/T10-04 também a corrigir).

---

## T10-01 (Alto) — Precedência de filtros não era aplicada pelo planejador

### Diagnóstico
`merge_filters()` existia no domínio, mas `PlannerService.plan()` devolvia
somente `QueryPlan.inferred_filters`; `QueryPlan` não possuía filtro efetivo.
O consumidor não recebia uma decisão de escopo pronta.

### Correção
1. `QueryPlan` ganhou `effective_filters: EditionFilter` (`domain/query.py`),
   mantendo `inferred_filters` para os chips editáveis.
2. `PlannerService.plan()` calcula
   `effective_filters = merge_filters(request.explicit_filter(), inferred)`
   (`application/planning.py`) e o expõe no plano.
3. Testes de conflito/precedência no plano produzido pelo serviço (unit e
   integração), e teste que prova o filtro efetivo consumido pela recuperação.

### Evidências
- `tests/unit/test_planner_service.py::TestEffectiveFilters::test_plan_carries_merged_effective_filters`
  — `plan.effective_filters == merge_filters(request.explicit_filter(), inferred)`;
  `inferred_filters` permanece separado.
- `tests/unit/test_planner_service.py::TestEffectiveFilters::test_explicit_edition_exclusion_preserved_in_effective`
  — a decisão explícita prevalece no filtro efetivo, sem conflito include/exclude.
- `tests/integration/test_planning_pipeline.py::test_plan_carries_effective_filters_against_real_catalog`
  — contra catálogo real.
- `tests/integration/test_planning_pipeline.py::test_effective_filters_flow_into_retrieval_stages`
  — o filtro efetivo do plano é consumido por `RetrievalService`: a obra
  excluída não aparece no SQL (lexical/vetorial), fusão RRF nem reranker
  (AC-07); o texto da obra excluída nunca chega ao reranker.

---

## T10-02 (Alto) — Estratégia `expanded` podia não executar expansão

### Diagnóstico
Sem provedor, perguntas conceituais/comparativas viram `expanded` mas
devolviam tuplas vazias — o plano e a explicação afirmavam expansão sem
expansão a executar.

### Correção
A estratégia `expanded` agora **exige provedor**: sem provedor (automática ou
explícita), `PlannerService.plan()` falha fechada com `ModelUnavailableError`.
Não se implementou expansão determinística (a alternativa da revisão): a
geração limitada de subperguntas/aliases é a responsabilidade reservada ao
`PlannerProvider` (NOTES.md §10.11 item 3); um substituto heurístico seria um
fallback silencioso de qualidade enganosa.

### Evidências
- `tests/unit/test_planner_service.py::TestExpandedRequiresProvider::test_explicit_expanded_without_provider_fails_closed`
  — `expanded` explícito sem provedor → `ModelUnavailableError`.
- `tests/unit/test_planner_service.py::TestExpandedRequiresProvider::test_automatic_expanded_without_provider_fails_closed`
  — `automatic` de conceitual e comparativa sem provedor → `ModelUnavailableError`.
- Conteúdo/limite da expansão: `test_expanded_calls_provider_and_carries_suggestion`
  (semantic_query/subperguntas/aliases/rótulos chegam ao plano),
  `test_expanded_is_respected` (expanded com provedor produz subperguntas),
  `test_planning_pipeline.py::test_provider_called_only_on_expanded`
  (`len(expanded_plan.subquestions) <= 5`) e
  `test_planner_adapter.py::test_subquestions_over_limit_raises_model_response_error`
  (contrato `PlannedQuery` rejeita >5).

---

## T10-03 (Médio) — Filtros positivos naturais comuns não eram resolvidos

### Diagnóstico
`_INCLUSION_CUES` não contínha `no`/`na`/`em`, que NOTES.md §10.11 item 6 já
listava como sinais de inclusão; `No Dom Casmurro` e `Em Dom Casmurro`
retornavam filtros vazios.

### Correção
`no`/`na`/`em` adicionados como sinais **posicionais** de inclusão
(`_POSITIONAL_INCLUSION_CUES`): só contam quando a palavra imediatamente
antes da menção, sem confundir preposições fora de uma menção ("Na semana de
Dom Casmurro" não infere). Menção sem sinais continua não aplicada
silenciosamente.

### Evidências
- `tests/unit/test_planning.py::TestResolveNaturalFilters::test_no_means_inclusion_when_adjacent`,
  `::test_na_means_inclusion_when_adjacent`, `::test_em_means_inclusion_when_adjacent`
  — cada preposição adjacente infere inclusão.
- `::test_positional_preposition_away_from_mention_is_not_a_filter` e
  `::test_em_as_question_word_is_not_a_filter` — preposição não adjacente não infere.
- `::test_word_level_cue_matching_avoids_substring_false_positive` permanece
  verde (sem falsos positivos por substring).
- `tests/integration/test_planning_pipeline.py::test_no_na_em_infer_inclusion_against_real_catalog`
  — `no`/`em` infirrem inclusão contra catálogo real; "Na semana de Dom
  Casmurro" vazio; título sem acento ("No Memorias Postumas...") resolve.

Reprodução da revisão confirmada corrigida: `No Dom Casmurro` e `Em Dom
Casmurro` retornam inclusão; `Somente Dom Casmurro` permanece inclusão.

---

## T10-04 (Médio) — Adapter do planner podia enviar segredo via HTTP

### Diagnóstico
`PlannerEndpointSettings` herdava só `ModelAuthSettings`/`ResilienceSettings`,
permitindo `Authorization: Bearer` por `http://`.

### Correção
`PlannerEndpointSettings` passou a herdar `HttpEndpointSettings` (mesma regra
dos adapters de T07, T7-02/T6-04): credencial Bearer sobre `http://` é
recusada na construção; sem credencial, `http://` permanece permitido.

### Evidências
- `tests/unit/test_planner_adapter.py::TestEndpointSecurity::test_http_with_api_key_is_rejected`
  e `::test_http_with_api_key_file_is_rejected` — `ValidationError` ("https://").
- `::test_https_with_api_key_is_accepted` e `::test_https_with_api_key_file_is_accepted`
  — aceitação.
- `::test_http_without_credential_is_accepted` — sem credencial `http://` vale.
- `::test_sends_bearer_auth` permanece, agora contra `https://planner.test/v1`.

---

## Resumo das verificações de qualidade

| Verificação | Comando | Resultado |
|-------------|---------|-----------|
| Lint | `uv run ruff check src/ tests/` | Passou (0 erros) |
| Formatação | `uv run ruff format --check src/ tests/` | Passou (99 arquivos) |
| Typecheck | `uv run mypy src/ tests/` | Passou (99 arquivos, 0 issues) |
| Suíte T10 | `uv run pytest -q tests/unit/test_planning.py tests/unit/test_planner_service.py tests/unit/test_query.py tests/unit/test_planner_adapter.py` | 88 passed |
| Unitários completos | `uv run pytest -q tests/unit/` | 442 passed, 3 skipped |
| Integração T10 | `uv run pytest -q tests/integration/test_planning_pipeline.py` | 10 passed (PostgreSQL real; podman + ryuk desativado) |
| Integração completa | `uv run pytest -q tests/integration/` | 185 passed, 1 skipped |

Nenhuma dependência nova; nenhuma migration nova; contrato `QueryPlan`
adiciona o campo opcional `effective_filters` (backward-compatible com os
consumidores existentes). `EVIDENCE.md` matriz AC-07/AC-11/AC-16 e seção T10
atualizadas; `NOTES.md` §11 registra as correções.
