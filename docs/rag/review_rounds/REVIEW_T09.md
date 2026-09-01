# Revisão independente — T09

Data: 2026-08-31  
Resultado: **reprovado**

## Escopo revisado

- Commit da tarefa: `e6a3487` (`Implement T09: vector search, RRF fusion and reranking`).
- Branch observada: `T09`.
- Requisitos: SPEC §6 e §8.5; T09 de `TASKS.md`; AC-05, AC-06 e AC-07.
- Dependências verificadas: T06, T07 e T08 estão presentes na árvore. A revisão também considerou a correção T8-01, pois ela define que recuperação deve consultar somente o conjunto de indexação ativo.
- Ambiente: Python 3.12 / `uv`; PostgreSQL de integração disponível via socket Podman.

## Achados

### T9-01 — A busca vetorial inclui reindexações inativas

Severidade: **alta**  
Requisito: SPEC §6, §8.5; AC-15; T09 (recuperação vetorial e rastreabilidade).  
Evidência: `VectorSearchRepository.search()` em `backend/src/rag/infrastructure/repositories/vector.py` constrói o `WHERE` somente com `p.embedding_version_id IS NOT NULL` e filtros de obra/edição. Não há condição sobre `index_runs.is_active` nem a compatibilidade legada que existe em `LexicalSearchRepository.search()` (`backend/src/rag/infrastructure/repositories/search.py`).

Impacto: após uma reindexação, passagens da execução inativa continuam elegíveis no estágio vetorial, podem vencer a fusão RRF e têm seu texto enviado ao reranker. Assim, uma mesma consulta mistura o conjunto lexical ativo com vetores e evidências obsoletos; o resultado deixa de reproduzir o conjunto corrente e pode citar conteúdo removido/alterado.

Correção necessária: aplicar no repositório vetorial, antes de `ORDER BY` e `LIMIT`, a mesma política explícita do estágio lexical: passagem da `index_run` ativa, ou linha legada (`index_run_id IS NULL`) apenas quando a edição não possui execução ativa. Acrescentar integração com duas execuções da mesma edição que prove que a inativa e a legada não entram em `vector`, `fused` nem no payload do reranker.

### T9-02 — Não há garantia de compatibilidade entre embedding da consulta e dos documentos

Severidade: **alta**  
Requisito: SPEC §8.5 (“embeddings de consulta e documento devem usar versões compatíveis”); AC-05; AC-15.  
Evidência: `RetrievalService.retrieve()` recebe de `EmbeddingProvider.embed_query()` somente `list[float]`; não recebe nem registra uma `EmbeddingVersion`. Em seguida, `VectorSearchRepository.search()` aceita qualquer passagem com `embedding_version_id IS NOT NULL` e só compara o comprimento do vetor com a dimensão fixa da coluna (`1024`).

Impacto: dois modelos/versões de embedding com dimensão 1024 são aceitos e comparados por cosseno como se compartilhassem o mesmo espaço vetorial. A recuperação pode ficar semanticamente incorreta sem erro, e a execução não contém informação suficiente para demonstrar que o vetor de consulta era compatível com os documentos recuperados.

Correção necessária: tornar a versão do embedding da consulta explícita no contrato de aplicação/provider (ou resolvê-la por configuração versionada) e restringir a busca à versão compatível da execução de indexação ativa. Persistir essa versão junto à execução. Cobrir versões de mesma dimensão e modelo/versão incompatíveis com falha fechada ou exclusão determinística.

### T9-03 — Os rankings não são persistidos pela implementação de T09

Severidade: **alta**  
Requisito: T09 (“scores e posições persistidos”); SPEC §5.1 e §8.5; AC-06.  
Evidência: `RetrievalService.retrieve()` retorna um `RetrievalResult`, mas não recebe `AnswerRun`, não chama `RunsRepository` e não salva os candidatos. `RetrievalResult.answer_run_candidates()` apenas prepara uma tupla. O teste `test_candidates_persist_into_answer_run_append_only` valida uma transição de modelo em memória; não persiste uma execução produzida pelo serviço nem lê o registro de volta do PostgreSQL.

Impacto: o requisito é de registro efetivo de ranking lexical, vetorial, RRF e reranking, não apenas de uma estrutura que um futuro chamador poderá salvar. No estado atual, uma consulta T09 não deixa os scores/posições exigidos em `AnswerRun`; portanto AC-06 não está demonstrado nem implementado de ponta a ponta.

Correção necessária: integrar a atualização append-only do `AnswerRun`/repositório no fluxo de recuperação (ou introduzir uma fronteira de orquestração já usada por T09 que o faça) e criar teste de integração que execute a recuperação, persista, recarregue a execução e confira os quatro estágios, scores, ranks e versões.

### T9-04 — O estágio RRF registrado é truncado antes do reranking

Severidade: **média**  
Requisito: SPEC §8.5 (“guardar scores e posições de todos os estágios”); AC-06.  
Evidência: em `backend/src/rag/application/search.py`, `fused = fuse_rankings(... )[: budget.rerank_top_n]` sobrescreve a lista RRF completa com a fatia enviada ao reranker. Logo, candidatos que vieram de `lexical_top_k`/`vector_top_k`, foram ranqueados por RRF, mas ficaram abaixo de `rerank_top_n` desaparecem do único campo `RetrievalResult.fused` e não podem ser registrados.

Impacto: perde-se a posição e o score do estágio RRF para parte dos candidatos, impossibilitando auditar por que um item não chegou ao reranker e contrariando a rastreabilidade de todos os estágios.

Correção necessária: manter o ranking RRF completo em `RetrievalResult.fused`; derivar uma variável separada, por exemplo `rerank_candidates = fused[:rerank_top_n]`, exclusivamente para carregar textos e chamar o provider. Testar cenário em que a fusão tem mais itens que `rerank_top_n`.

### T9-05 — O limite do repositório vetorial pode burlar o orçamento

Severidade: **média**  
Requisito: T09 (políticas de orçamento por profundidade); SPEC §8.5.  
Evidência: `VectorSearchRepository.search()` envia o argumento `limit` diretamente a `LIMIT %(limit)s`, sem validar tipo ou faixa. Ao contrário, `LexicalSearchRepository` limita a `1..MAX_SEARCH_LIMIT` e rejeita `bool`, `float`, string, zero e negativo. PostgreSQL interpreta `LIMIT -1` como ausência de limite.

Impacto: qualquer chamador que use diretamente o repositório pode recuperar o acervo inteiro, aumentar custo de RRF/reranking e invalidar o orçamento versionado. Os testes de T09 só exercitam `limit` positivo.

Correção necessária: centralizar ou reutilizar a validação do estágio lexical antes de abrir cursor/executar SQL, com teto coerente com os máximos de `RetrievalBudget`; adicionar testes negativos para `0`, `-1`, valor acima do teto, `True`, `float` e string.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `cd backend && uv run ruff check src/rag/domain/retrieval.py src/rag/application/search.py src/rag/infrastructure/repositories/vector.py tests/unit/test_retrieval.py tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | OK |
| `cd backend && uv run ruff format --check ...` (mesmos seis arquivos) | OK — 6 arquivos formatados |
| `cd backend && uv run mypy ...` (mesmos seis arquivos) | OK — sem issues |
| `cd backend && uv run pytest -q tests/unit/test_retrieval.py tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | 18 testes unitários passaram; 16 integrações falharam na preparação porque o socket Docker padrão não existia |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q tests/integration/test_vector_search.py tests/integration/test_retrieval_pipeline.py` | OK — 16 passed em 8.32s |

Os testes existentes passam, mas não cobrem os caminhos descritos em T9-01 a T9-05. Não foram executados os comandos globais de frontend/E2E, benchmark e Docker Compose: eles não são entregáveis de T09 e dependem de tarefas posteriores.

## Critérios de aceitação

| Critério | Estado | Fundamentação |
|---|---|---|
| AC-01 | evidência insuficiente | Fora do escopo T09. |
| AC-02 | evidência insuficiente | Fora do escopo T09. |
| AC-03 | evidência insuficiente | Fora do escopo T09. |
| AC-04 | evidência insuficiente | T08, não revalidado nesta rodada. |
| AC-05 | falha | Há fixture positiva, mas T9-02 permite comparar espaços vetoriais incompatíveis silenciosamente. |
| AC-06 | falha | T9-03 não persiste rankings; T9-04 descarta parte do estágio RRF. |
| AC-07 | evidência insuficiente | Exclusões por obra/edição passam no fluxo exercitado; T9-01 permite evidência de indexação inativa em estágios vetoriais, e os cenários de exclusão inferida/confirmada pertencem a T10/T13. |
| AC-08 | evidência insuficiente | Fora do escopo T09. |
| AC-09 | evidência insuficiente | Fora do escopo T09. |
| AC-10 | evidência insuficiente | Fora do escopo T09. |
| AC-11 | evidência insuficiente | Fora do escopo T09. |
| AC-12 | evidência insuficiente | Fora do escopo T09. |
| AC-13 | evidência insuficiente | Fora do escopo T09. |
| AC-14 | evidência insuficiente | T09 propaga falha do reranker em teste; contrato completo é T07/T13/T14 e não foi revalidado. |
| AC-15 | falha | T9-01 e T9-02 não asseguram conjunto/versão compatível; T9-03 não registra a execução. |
| AC-16 | evidência insuficiente | Fora do escopo T09. |
| AC-17 | evidência insuficiente | Fora do escopo T09. |
| AC-18 | evidência insuficiente | Fora do escopo T09. |
| AC-19 | evidência insuficiente | Fora do escopo T09. |
| AC-20 | evidência insuficiente | Fora do escopo T09. |

## Riscos residuais

- O HNSW com filtros continua exigindo benchmark/`EXPLAIN` representativo para avaliar recall e latência na escala-alvo; esta tarefa não apresenta essa evidência.
- Os valores iniciais da política ainda não foram calibrados por benchmark, conforme já previsto para T19.

## Conclusão

T09 implementa a forma básica de cosseno, RRF e reranking, e sua suíte nominal passa no PostgreSQL real. Contudo, os achados T9-01 a T9-03 impedem declarar a tarefa pronta: o fluxo pode recuperar versões inativas ou embeddings incompatíveis e não persiste os rankings exigidos. T9-04 e T9-05 agravam a perda de rastreabilidade e permitem burlar o orçamento. Corrigir todos os cinco itens e executar os novos testes de integração é necessário antes de nova aprovação.
