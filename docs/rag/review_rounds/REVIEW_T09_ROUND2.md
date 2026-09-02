# Verificação da resposta à revisão T09 — rodada 2

Data: 2026-08-31
Resposta verificada: `REVIEW_RESPONSE_T09.md`
Resultado: **reprovado — duas correções obrigatórias permanecem**.

## Escopo e evidências executadas

Foram revisados o diff não commitado que acompanha a resposta e os requisitos de T09, SPEC §5.1, §6 e §8.5, em especial AC-05, AC-06 e AC-15.

| Comando | Resultado |
|---|---|
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run ruff format --check src tests` | OK — 92 arquivos já formatados |
| `cd backend && uv run mypy src tests` | OK — 92 arquivos, sem issues |
| `cd backend && uv run pytest -q tests/unit tests/contract` | OK — 397 passed, 3 skipped; 6 warnings de depreciação de Docling |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | OK — 30 passed em 7.34s |

Os comandos passam, mas os dois cenários negativos abaixo ainda não são exercitados pela suíte.

## Itens aceitos

- **T9-01:** aceito. O `WHERE` vetorial agora tem a mesma regra de conjunto ativo/legado do estágio lexical. Os novos testes provam tanto o descarte da execução inativa quanto a regra transitória para linhas legadas.
- **T9-04:** aceito. A lista RRF completa permanece em `RetrievalResult.fused`; somente `rerank_candidates` é limitada a `rerank_top_n`.
- **T9-05:** aceito. `VectorSearchRepository.search()` rejeita tipo/faixa de `limit` inválidos antes de executar SQL, com cobertura de integração.

## Achados pendentes

### R2-T9-01 — Compatibilidade de embedding ainda pode ser omitida silenciosamente

Severidade: **alta**
Requisito: SPEC §8.5 (“embeddings de consulta e documento devem usar versões compatíveis”); AC-05 e AC-15.

`EmbeddingProvider` continua declarando apenas `embed_documents()` e `embed_query()` em `backend/src/rag/domain/providers.py:32-34`. Em `RetrievalService.retrieve()`, a versão é buscada por `getattr(self._embedding, "embedding_version", None)` e, se ausente, `registered_emb_version` permanece `None`; `VectorSearchRepository.search()` recebe então `embedding_version_id=None` e deliberadamente não inclui o predicado de compatibilidade (`backend/src/rag/application/search.py:65-81`, `backend/src/rag/infrastructure/repositories/vector.py:88-91`).

Isso não é apenas hipótese: `tests/unit/test_providers.py` mantém `FakeEmbedding` sem a propriedade e comprova que ele satisfaz `EmbeddingProvider` em runtime. Logo, uma implementação válida pelo contrato atual pode executar recuperação vetorial entre todas as versões de 1024 dimensões, precisamente o problema que T9-02 deveria fechar.

Correção necessária:

1. tornar a identidade/versionamento do embedding obrigatória no contrato usado por `RetrievalService` (por exemplo, uma propriedade `embedding_version` no `Protocol`, ou versão obrigatória no construtor/na chamada);
2. rejeitar a recuperação antes de consultar o banco quando essa versão não estiver disponível ou não puder ser registrada, em vez de remover o filtro;
3. atualizar os doubles e o teste de protocolo; adicionar teste de falha fechada com provider sem versão.

### R2-T9-02 — Persistência dos rankings continua opcional

Severidade: **alta**
Requisito: T09 (“scores e posições persistidos”); SPEC §5.1 e §8.5; AC-06 e AC-15.

`run` é opcional em `RetrievalService.retrieve(..., run: AnswerRun | None = None)` (`backend/src/rag/application/search.py:44-55`). A única chamada a `AnswerRunsRepository.save()` fica dentro de `if run is not None` (`:105-123`). Assim, todas as chamadas já existentes que não passam `run` — inclusive vários testes do próprio pipeline — concluem a recuperação e retornam `RetrievalResult` sem gravar um `AnswerRun` e sem `run_id`.

O novo teste só demonstra a ramificação opt-in; não verifica que o caminho normal seja incapaz de retornar sucesso sem persistir os quatro estágios. A especificação exige que a execução registre candidatos/rankings e versões, e não apenas que um chamador futuro possa optar por fazê-lo.

Correção necessária: fazer de `AnswerRun` persistido uma pré-condição do fluxo de recuperação (por exemplo, `run` obrigatório e criado/persistido pela orquestração antes da chamada), ou mover a persistência a uma fronteira obrigatória que não permita devolver `RetrievalResult` antes de salvar. Adicionar teste negativo que comprove que ausência de `run` falha tipadamente, ou teste de orquestração que prove que o `run` é sempre criado e salvo.

## Critérios afetados

| Critério | Estado |
|---|---|
| AC-05 | falha — R2-T9-01 ainda aceita espaço vetorial incompatível se o provider não expuser atributo informal. |
| AC-06 | falha — R2-T9-02 permite recuperação bem-sucedida sem persistir nenhum ranking. |
| AC-07 | passa no escopo T09 — filtros e conjunto de indexação ativo são aplicados antes de vetor/RRF/reranker nos testes executados. |
| AC-15 | falha — faltam garantia obrigatória de versão de embedding e registro obrigatório da execução. |

## Conclusão

Três dos cinco itens foram corrigidos com boa cobertura. A resposta, porém, transforma as duas garantias de maior risco em comportamentos opcionais: um provider compatível com o `Protocol` pode não fornecer versão, e um chamador pode não fornecer `AnswerRun`. Enquanto esses caminhos retornarem sucesso, a implementação não atende ao contrato de compatibilidade e rastreabilidade de T09.
