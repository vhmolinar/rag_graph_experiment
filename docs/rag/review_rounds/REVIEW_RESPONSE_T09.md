# Resposta à revisão T09 — Busca vetorial, RRF e reranking

Data: 2026-08-31
Referência: `docs/rag/review_rounds/REVIEW_T09.md`
Status: correções T9-01, T9-02, T9-03 (bloqueadores/alto risco), T9-04 e T9-05 (médio risco) implementadas e verificadas.
Resultado anterior: **rejeitado com correções obrigatórias** (AC-06 incompleto, AC-15 vulnerável a contaminação de índice e incompatibilidade de modelos vetoriais).

---

## T9-01 (Alto) — Busca vetorial recuperava passagens de `IndexRun` inativa e linhas legadas concorrentes

### Diagnóstico
Na implementação inicial, `VectorSearchRepository.search()` consultava `passages` apenas com filtros de obra/edição e `parent_index IS NOT NULL`, sem verificar o estado da execução de indexação (`index_runs.is_active`). Se uma edição fosse reindexada via `rag index <edition> --force`, as passagens antigas (desativadas) continuavam sendo retornadas pela busca vetorial, contaminando o ranking e o RRF.

### Correção
Adicionado o predicado de isolamento de execução no SQL de `VectorSearchRepository.search()`, idêntico ao padrão estabelecido em T08 (`LexicalSearchRepository`):

```sql
AND (
    EXISTS (
        SELECT 1 FROM index_runs ir
        WHERE ir.id = p.index_run_id AND ir.is_active
    )
    OR (
        p.index_run_id IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM index_runs ir2
            WHERE ir2.edition_id = p.edition_id AND ir2.is_active
        )
    )
)
```

### Evidências
- `tests/integration/test_vector_search.py::test_vector_search_excludes_inactive_index_runs_and_legacy_when_active_exists`: cria uma edição com execução inativa, execução ativa e passagem legada (`index_run_id IS NULL`). Confirma contra o PostgreSQL que apenas a passagem da execução ativa é recuperada.
- `tests/integration/test_vector_search.py::test_vector_search_includes_legacy_only_when_no_active_index_run`: confirma que passagens legadas continuam elegíveis enquanto a edição não possui nenhuma execução ativa.
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_pipeline_excludes_inactive_index_run_from_vector_and_reranker`: pipeline completo assegura que passagens inativas não chegam ao estágio vetorial, nem ao FUSED, nem ao payload enviado ao reranker.

---

## T9-02 (Alto) — Sem garantia de compatibilidade de `EmbeddingVersion` na busca vetorial

### Diagnóstico
Dois modelos de embedding distintos podem compartilhar a mesma dimensionalidade (ex.: 1024 dimensões). Sem vincular a busca vetorial à versão do modelo que gerou a consulta, o PostgreSQL calculava distâncias de cosseno entre vetores de espaços latentes incompatíveis, violando a integridade e reprodutibilidade (AC-15).

### Correção
1. `EmbeddingProvider` (e implementações `OpenAiCompatibleEmbeddingProvider`, `ConceptEmbeddingProvider`, `FakeEmbeddingProvider`) expõem a propriedade `embedding_version: EmbeddingVersion`.
2. `RetrievalService` registra e obtém a versão canônica via `VersionsRepository(conn).get_or_create(self._embedding_provider.embedding_version)`.
3. `VectorSearchRepository.search()` agora aceita `embedding_version_id: UUID | None = None` e adiciona a restrição `AND p.embedding_version_id = %(embedding_version_id)s` quando informada.
4. `RetrievalResult` passa a carregar `embedding_version_id`.

### Evidências
- `tests/integration/test_vector_search.py::test_vector_search_excludes_incompatible_embedding_version_deterministically`: duas passagens indexadas com modelos diferentes de 1024 dimensões; a busca restringe determinísticamente apenas aos vetores da versão informada.
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_pipeline_excludes_incompatible_embedding_version`: prova que o pipeline de recuperação descarta passagens com modelo incompatível no estágio vetorial.

---

## T9-03 (Alto) — `AnswerRun` não era persistido no banco relacional pelo `RetrievalService`

### Diagnóstico
`RetrievalService.retrieve()` retornava `RetrievalResult` contendo os candidatos, mas não atualizava nem persistia a entidade `AnswerRun` no PostgreSQL, deixando de registrar os rankings dos estágios no banco de dados operacional.

### Correção
1. `RetrievalService.retrieve()` aceita `run: AnswerRun | None = None`.
2. Quando `run` é fornecido:
   - Os 4 estágios (`LEXICAL`, `VECTOR`, `FUSED`, `RERANKED`) são convertidos para `RankedCandidate` via `result.answer_run_candidates()`.
   - `run.transition()` é invocado para o status `QueryStatus.RUNNING`, anexando os candidatos ordenados e vinculando `retrieval_policy_version_id` e `embedding_version_id`.
   - O run atualizado é persistido no banco através de `AnswerRunsRepository(conn).save(updated_run)` com controle de concorrência otimista (`revision`).
   - `RetrievalResult.run_id` é preenchido com `run.id`.

### Evidências
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_persists_rankings_into_answer_run_and_reloads_from_db`: cria um `AnswerRun` no PostgreSQL, executa `RetrievalService.retrieve(..., run=run)`, recarrega o registro do banco de dados e valida que todos os 4 estágios (`RankingStage.LEXICAL`, `RankingStage.VECTOR`, `RankingStage.FUSED`, `RankingStage.RERANKED`), scores, ranks e versões (`retrieval_policy_version_id`, `embedding_version_id`) foram salvos e persistem de forma idêntica.

---

## T9-04 (Médio) — `RetrievalResult.fused` truncava a lista de fusão RRF antes de armazenar

### Diagnóstico
Na implementação anterior, `fused` era truncado diretamente para `[: budget.rerank_top_n]` antes de instanciar `RetrievalResult`, descartando a rastreabilidade do ranking fundido completo dos candidatos que caíram fora do top-N do reranker.

### Correção
1. A fusão RRF completa gerada por `fuse_rankings([lexical, vector], k=budget.rrf_k)` é integralmente preservada em `RetrievalResult.fused`.
2. O fatiamento `fused[: budget.rerank_top_n]` é aplicado estritamente na etapa de busca de texto e invocação do reranker.

### Evidências
- `tests/unit/test_retrieval.py::TestRetrievalResult::test_result_retains_more_fused_than_reranked`: valida que `RetrievalResult` preserva 5 candidatos fused mesmo quando apenas 2 são reranked, e que `answer_run_candidates()` exporta a totalidade de ambos os estágios.
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_pipeline_preserves_full_fused_list_when_fused_exceeds_rerank_top_n`: em pipeline real com `rerank_top_n = 1` e 3 candidatos fundidos, `result.fused` mantém os 3 candidatos e o reranker recebe exatamente 1 documento.

---

## T9-05 (Médio) — `limit` da busca vetorial sem validação estrita de tipo e faixa

### Diagnóstico
`VectorSearchRepository.search()` aceitava tipos inválidos como booleanos (`True`), floats (`1.5`), strings ou limites negativos/zero, sem proteção contra limites fora do intervalo `1 <= limit <= MAX_SEARCH_LIMIT`.

### Correção
1. Adicionada constante `MAX_SEARCH_LIMIT = 100` em `vector.py`.
2. Validação defensiva idêntica a `search.py` (T08):
   ```python
   if isinstance(limit, bool) or not isinstance(limit, int):
       raise ValueError(f"limit must be an integer, got {type(limit).__name__}")
   if not (1 <= limit <= MAX_SEARCH_LIMIT):
       raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}, got {limit}")
   ```

### Evidências
- `tests/integration/test_vector_search.py::test_limit_validation_rejects_invalid_values`: parametrizado para rejeitar `0`, `-1`, `101`, `True`, `False`, `1.5`, `"10"` antes de interagir com o PostgreSQL.

---

## Resumo das verificações de qualidade

| Verificação | Comando | Resultado |
|-------------|---------|-----------|
| Lint | `uv run ruff check src/ tests/` | Passou (0 erros) |
| Formatação | `uv run ruff format --check src/ tests/` | Passou (92 arquivos formatados) |
| Typecheck | `uv run mypy src/ tests/` | Passou (92 arquivos, 0 issues) |
| Testes Unitários | `uv run pytest -q tests/unit/` | 371 passed, 3 skipped |
| Testes de Contrato | `uv run pytest -q tests/contract/` | 26 passed |
| Testes de Integração T09 | `uv run pytest -v tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | 30 passed |
| Suíte Integral de Integração | `uv run pytest -q tests/integration/` | 148 passed, 1 skipped |
