# Resposta à revisão T09 — rodada 2

Data: 2026-08-31
Referência: `docs/rag/review_rounds/REVIEW_T09_ROUND2.md`
Status: correções R2-T9-01 e R2-T9-02 (bloqueadores/alto risco) implementadas e verificadas.
Resultado anterior: **reprovado com duas correções obrigatórias** (AC-05 e AC-15 vulneráveis à omissão de versão de embedding; AC-06 vulnerável à recuperação sem persistência de rankings).

---

## R2-T9-01 (Alto) — Identidade e compatibilidade de `EmbeddingVersion` tornadas estritas e obrigatórias

### Diagnóstico
Na rodada anterior, `EmbeddingProvider` não exigia a propriedade `embedding_version` no `Protocol`, e `RetrievalService.retrieve()` consultava a versão de forma tolerante via `getattr(..., "embedding_version", None)`. Caso ausente, `registered_emb_version` permanecia `None`, e `VectorSearchRepository.search()` omitia o predicado `p.embedding_version_id = %(embedding_version_id)s`.

### Correção
1. **Contrato do Domínio**: `EmbeddingProvider` em `backend/src/rag/domain/providers.py` agora declara formalmente `@property def embedding_version(self) -> EmbeddingVersion: ...`. Qualquer classe que não implemente a propriedade falha a checagem de protocolo em runtime.
2. **Validação Fail-Closed em `RetrievalService`**: `RetrievalService.retrieve()` agora acessa `self._embedding.embedding_version` imediatamente antes de qualquer interação com o banco ou busca lexical. Se a propriedade estiver ausente ou não for uma instância válida de `EmbeddingVersion`, lança `TypeError` imediatamente.
3. **Busca Vetorial Estrita**: `VectorSearchRepository.search()` agora exige `embedding_version_id: UUID` (não-opcional) e valida seu tipo com `isinstance(embedding_version_id, UUID)`. A cláusula `WHERE` inclui incondicionalmente `p.embedding_version_id = %(embedding_version_id)s`.
4. **Atualização de Doubles e Testes**: Todos os provedores de teste (`FakeEmbedding`, `_FakeEmbeddingProvider`, etc.) foram atualizados com a propriedade `embedding_version`.

### Evidências
- `tests/unit/test_providers.py::test_embedding_provider_without_version_fails_protocol_check`: confirma que classes sem `embedding_version` são rejeitadas por `isinstance(..., EmbeddingProvider)`.
- `tests/unit/test_retrieval.py::TestRetrievalServiceValidation::test_retrieval_service_rejects_provider_without_embedding_version`: valida que `RetrievalService.retrieve()` falha fechado com `TypeError` caso o provider não forneça `EmbeddingVersion`.
- `tests/integration/test_vector_search.py::test_search_requires_valid_embedding_version_id`: valida contra o banco que `VectorSearchRepository.search()` rejeita `embedding_version_id=None` e tipos inválidos.
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_service_rejects_provider_without_embedding_version_against_db`: prova falha fechada no pipeline real.

---

## R2-T9-02 (Alto) — Persistência de `AnswerRun` tornada pré-condição obrigatória

### Diagnóstico
`run` era um parâmetro opcional (`run: AnswerRun | None = None`) em `RetrievalService.retrieve()`, e a chamada a `AnswerRunsRepository.save()` ficava condicionada a `if run is not None`. Isso permitia que chamadas sem `run` concluíssem com sucesso sem gravar o histórico nem os rankings dos quatro estágios.

### Correção
1. **Assinatura Obrigatória**: `RetrievalService.retrieve(conn, *, lexical_query, semantic_query, run: AnswerRun, ...)` torna `run` um argumento obrigatório sem valor padrão.
2. **Validação de Tipo**: Valida `isinstance(run, AnswerRun)` logo na entrada de `retrieve()`, rejeitando `None` e tipos inválidos com `TypeError`.
3. **Persistência Incondicional**: Toda recuperação executa `run.transition()` para `QueryStatus.RUNNING`, anexa todos os `RankedCandidate` das 4 etapas (`LEXICAL`, `VECTOR`, `FUSED`, `RERANKED`), atualiza as versões (`retrieval_policy_version_id` e `embedding_version_id`), e persiste o estado atualizado no PostgreSQL via `AnswerRunsRepository(conn).save(updated_run)`.
4. **Resultado Amarrado ao Run**: `RetrievalResult.run_id` é preenchido com `run.id` em todas as execuções.
5. **Cobertura Integral na Suíte**: Todos os testes do pipeline em `test_retrieval_pipeline.py` agora criam um `AnswerRun` no banco real antes de chamar `retrieve()`, passam `run=run`, e validam a persistência completa dos rankings e versões recarregando o registro do banco.

### Evidências
- `tests/unit/test_retrieval.py::TestRetrievalServiceValidation::test_retrieval_service_rejects_missing_or_invalid_run`: valida que chamar `retrieve()` com `run=None` ou valor inválido lança `TypeError`.
- `tests/integration/test_retrieval_pipeline.py::test_retrieval_service_rejects_missing_run_against_db`: valida no ambiente integrado que a ausência de `run` impede a execução.
- `tests/integration/test_retrieval_pipeline.py::test_pipeline_preserves_all_stages_and_fuses_deterministically`: valida que o caminho padrão persiste `AnswerRun` com os 4 estágios no PostgreSQL e atualiza as versões correspondentes.

---

## Resumo das verificações de qualidade

| Verificação | Comando | Resultado |
|-------------|---------|-----------|
| Lint | `uv run ruff check src/ tests/` | Passou (0 erros) |
| Formatação | `uv run ruff format --check src/ tests/` | Passou (92 arquivos formatados) |
| Typecheck | `uv run mypy src/ tests/` | Passou (92 arquivos, 0 issues) |
| Testes Unitários e Contrato | `uv run pytest -q tests/unit/ tests/contract/` | 400 passed, 3 skipped |
| Testes de Integração T09 | `uv run pytest -v tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | 33 passed |
| Suíte Integral de Integração | `uv run pytest -q tests/integration/` | 175 passed, 1 skipped |
