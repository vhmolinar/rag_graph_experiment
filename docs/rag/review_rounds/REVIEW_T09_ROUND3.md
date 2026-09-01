# Verificação da resposta à revisão T09 — rodada 3

Data: 2026-09-01  
Resposta verificada: `REVIEW_RESPONSE_T09_ROUND2.md`  
Resultado: **aprovado com ressalvas de escopo**.

## Escopo

Foram verificados os bloqueadores R2-T9-01 e R2-T9-02, além das correções T9-01, T9-04 e T9-05 já aceitas na rodada anterior. Esta aprovação cobre T09; critérios que dependem de tarefas posteriores (geração, verificação, API e rastreabilidade completa) permanecem fora desta rodada.

## Resultado dos itens

- **R2-T9-01 — aprovado.** `EmbeddingProvider` exige `embedding_version`; `RetrievalService` valida o tipo antes de qualquer busca; `VectorSearchRepository.search()` exige UUID de versão e aplica o predicado de versão incondicionalmente. Providers sem versão e IDs inválidos falham tipadamente.
- **R2-T9-02 — aprovado.** `run: AnswerRun` é obrigatório, validado na entrada, e toda recuperação salva candidatos e versões via `AnswerRunsRepository`. Não há mais caminho de sucesso sem `run_id` e persistência.
- **T9-01, T9-04 e T9-05 — mantidos como aprovados.** O conjunto ativo, a lista RRF integral e o limite vetorial permanecem cobertos pelas novas integrações.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run ruff format --check src tests` | OK — 92 arquivos já formatados |
| `cd backend && uv run mypy src tests` | OK — 92 arquivos, sem issues |
| `cd backend && uv run pytest -q tests/unit/test_providers.py tests/unit/test_retrieval.py` | OK — 26 passed em 0.51s |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | OK — 33 passed em 9.19s |

Não reexecutei até o fim a combinação completa de testes unitários e de contrato nesta rodada; a evidência reproduzível para os caminhos modificados é a suíte específica acima. A resposta do implementador registra `400 passed, 3 skipped` para a execução integral, resultado que não foi usado como evidência independente neste parecer.

## Critérios afetados

| Critério | Estado nesta rodada | Fundamentação |
|---|---|---|
| AC-05 | passa | Busca vetorial usa versão de embedding obrigatória e compatível, com falha fechada para provider/ID inválido. |
| AC-06 | passa | Rankings lexical, vetorial, RRF integral e reranking são anexados e persistidos em `AnswerRun`. |
| AC-07 | passa no escopo T09 | Filtros e conjunto de indexação ativo são aplicados antes de vetor, fusão e reranking nos testes reais. |
| AC-15 | passa no escopo T09 | A execução registra as versões de política e embedding, e usa somente passagens de versão compatível/conjunto ativo. |

## Ressalvas

- A cobertura completa de AC-07 no estágio de geração/verificação depende de T10/T13.
- O benchmark de qualidade/latência e a rastreabilidade integral de todos os endpoints dependem de T18/T19.

## Conclusão

As correções respondem integralmente aos dois bloqueadores da rodada 2. T09 pode prosseguir como concluída, sujeito às dependências e validações de fase posterior explicitadas acima.
