# Revisão T10 — Planejador de consulta

Data: 2026-09-01  
Resultado: reprovado

## Escopo revisado

- commit/branch: `5bee16b` (`T10`), com a implementação original da tarefa em `c18f816`;
- ambiente: Python 3.12, `uv`, PostgreSQL de teste por Testcontainers/Podman;
- critérios diretamente afetados: AC-07, AC-11 e AC-16. AC-13 permanece para T15.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache uv run pytest -q tests/unit/test_planning.py tests/unit/test_planner_service.py tests/unit/test_query.py tests/unit/test_planner_adapter.py` | OK — 74 passed |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q tests/integration/test_planning_pipeline.py` | OK — 7 passed |
| reprodução de `resolve_natural_filters` contra catálogo em memória | `No Dom Casmurro` e `Em Dom Casmurro` retornam filtros vazios; `Somente Dom Casmurro` retorna inclusão. |

O alvo `make test-unit` não aceita `ARGS` e executou toda a suíte: `428 passed, 3 skipped`. Os comandos acima são os usados para a evidência específica de T10.

## Critérios

| Critério | Estado | Fundamentação |
|---|---|---|
| AC-01 a AC-06 | não reavaliado | Fora dos entregáveis de T10. |
| AC-07 | falha | O plano não calcula/carrega o filtro efetivo obrigatório; filtros naturais positivos usuais também não são resolvidos. |
| AC-08 a AC-10 | não reavaliado | Fora dos entregáveis de T10. |
| AC-11 | evidência insuficiente | Há sinal de diversidade, mas `expanded` pode não expandir; aplicação e limitação ficam para T12/T13. |
| AC-12 | não reavaliado | Entregável de T11/T12. |
| AC-13 | evidência insuficiente | Reescrita e registro de sessão são T15. |
| AC-14 a AC-15 | não reavaliado | Fora dos entregáveis de T10. |
| AC-16 | falha | O adapter do planner pode enviar `Bearer` por HTTP simples. |
| AC-17 a AC-20 | não reavaliado | Fora dos entregáveis de T10. |

## Achados

### T10-01 — Alto — precedência de filtros não é aplicada pelo planejador

- Requisito: SPEC §8.2 exige que filtros inferidos não substituam explícitos e que exclusões explícitas prevaleçam. T10 lista essa prioridade como entregável; AC-07 exige exclusão em todos os estágios.
- Evidência: `merge_filters()` existe em `backend/src/rag/domain/planning.py:538`, mas `PlannerService.plan()` não a chama: obtém `inferred` nas linhas 60–61 e devolve somente `QueryPlan.inferred_filters` na linha 72. `QueryPlan` não possui filtro efetivo (`backend/src/rag/domain/query.py:164-174`). Os testes chamam a função pura diretamente (`tests/unit/test_planning.py:191-223`; `tests/integration/test_planning_pipeline.py:141-149`), sem provar o plano de serviço consumível pela recuperação.
- Impacto: o consumidor não recebe uma decisão de escopo pronta e pode omitir ou aplicar outra precedência; não há garantia implementada de que exclusão explícita siga no plano até a recuperação.
- Correção necessária: calcular `effective_filters = merge_filters(request.explicit_filter(), inferred)` em `PlannerService`, expô-lo em `QueryPlan` mantendo os inferidos para chips, e testar conflitos no plano produzido pelo serviço antes da recuperação.

### T10-02 — Alto — estratégia `expanded` pode não executar expansão

- Requisito: SPEC §8.3 define `expanded` como híbrida com sinônimos e subperguntas controladas; essa geração limitada é entregável de T10. Comparativas escolhem `expanded` para cobertura (SPEC §8.6; AC-11).
- Evidência: `PlannerService` aceita provider opcional (`backend/src/rag/application/planning.py:35`) e só o chama se não for `None` (linha 49). Sem provider, perguntas conceituais/comparativas viram `expanded` nas linhas 433–439 de `domain/planning.py`, mas devolvem as tuplas vazias inicializadas nas linhas 46–48. `test_expanded_is_respected` (`tests/unit/test_planner_service.py:106-113`) verifica só o enum.
- Impacto: o plano e a explicação afirmam expansão, mas não há expansão a executar; reduz cobertura comparativa e torna a estratégia efetiva incorreta para o cliente.
- Correção necessária: exigir provider para `expanded`, falhando tipadamente quando ausente, ou implementar expansão determinística limitada. Cobrir automático e explícito sem provider e o conteúdo/limite da expansão.

### T10-03 — Médio — filtros positivos naturais comuns não são resolvidos

- Requisito: SPEC §8.2 prevê filtros inferidos retornados ao cliente. NOTES §10.11, item 6, lista `no`, `na` e `em` como sinais de inclusão.
- Evidência: `_INCLUSION_CUES` em `backend/src/rag/domain/planning.py:303-313` não contém esses sinais. A reprodução com catálogo `Dom Casmurro` retornou `EditionFilter()` para `No Dom Casmurro, qual é o tema?` e `Em Dom Casmurro, qual é o tema?`; `Somente Dom Casmurro...` incluiu a obra. Não há teste para `no`, `na` ou `em`.
- Impacto: consultas naturais que delimitam obra não restringem a busca nem geram chips, podendo recuperar obras fora do escopo pretendido.
- Correção necessária: implementar sinais posicionais de inclusão sem confundir preposições fora de uma menção e cobrir `no/na/em`, inclusive título sem acento, no domínio e com catálogo real.

### T10-04 — Médio — adapter do planner envia segredo via HTTP

- Requisito: SPEC §§11 e 14 e AC-16 exigem manuseio seguro de credenciais. T07 já recusa Bearer em HTTP nos adapters de modelos.
- Evidência: `PlannerEndpointSettings` herda apenas `ModelAuthSettings` e `ResilienceSettings` (`backend/src/rag/adapters/planner_adapter.py:41-48`), não `HttpEndpointSettings`. `_headers()` cria `Authorization: Bearer` (linhas 51–56), e `tests/unit/test_planner_adapter.py:66-80` confirma esse envio para `http://planner.test/v1` com chave.
- Impacto: configurar o planner em HTTP transmite o segredo em claro.
- Correção necessária: herdar `HttpEndpointSettings` ou validação equivalente; recusar `http://` com `api_key`/`api_key_file` e testar recusa HTTP/aceitação HTTPS.

## Riscos residuais

- Diversidade aplicada, descida hierárquica e declaração de limitação dependem de T11–T13; não é possível aprovar AC-11 com o booleano atual.
- A integração de T10 não chama recuperação com o filtro produzido. Após T10-01, adicionar teste que prove o filtro efetivo no SQL, vetorial, fusão e reranker.

## Conclusão

T10 não atende à definição de pronto. T10-01 e T10-02 impedem aprovação: o contrato não entrega filtro efetivo obrigatório e pode declarar expansão sem expandir. Corrigir também T10-03 e T10-04 antes de nova revisão.
