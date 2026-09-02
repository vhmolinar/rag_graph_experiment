# Revisão T10 — Rodada 2

Data: 2026-09-01
Referência: `REVIEW_T10.md` e `REVIEW_RESPONSE_T10.md`
Resultado: aprovado com ressalvas

## Escopo e evidências

Revisadas as alterações locais que respondem a T10-01 a T10-04, sobre o
commit-base `5bee16b` da branch `T10`.

| Comando | Resultado |
|---|---|
| `git diff --check` | OK |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache uv run ruff check src tests` | OK |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache uv run ruff format --check src tests` | OK — 99 arquivos |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache uv run mypy src tests` | OK — 99 arquivos |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache uv run pytest -q tests/unit/test_planning.py tests/unit/test_planner_service.py tests/unit/test_query.py tests/unit/test_planner_adapter.py` | OK — 88 passed |
| `cd backend && UV_CACHE_DIR=/tmp/rag-t10-uv-cache DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q tests/integration/test_planning_pipeline.py` | OK — 10 passed |

## Revalidação dos achados

| Achado | Estado | Evidência |
|---|---|---|
| T10-01 | corrigido | `PlannerService.plan()` calcula `merge_filters(request.explicit_filter(), inferred)` e o expõe em `QueryPlan.effective_filters`; a integração passa esse filtro ao `RetrievalService` e prova a ausência da obra excluída nas listas lexical, vetorial, RRF e reranked. |
| T10-02 | corrigido | Estratégia `expanded` sem `PlannerProvider` falha com `ModelUnavailableError`; os casos automático e explícito e os limites de sugestões são cobertos. |
| T10-03 | corrigido | `no`, `na` e `em` são aceitos somente imediatamente antes do título; testes unitários e integração cobrem catálogo real, título sem acento e a negativa `Na semana de Dom Casmurro`. |
| T10-04 | corrigido | `PlannerEndpointSettings` herda `HttpEndpointSettings`; os testes rejeitam chave/secret file em `http://` e aceitam `https://`. |

## Ressalva

### R2-T10-01 — Baixo — matriz AC-07 superdeclara cobertura

- Evidência: `docs/rag/EVIDENCE.md` altera AC-07 para `✅`, mas a própria
  descrição termina com “Falta o estágio de geração/verificação (T13)”. T13
  ainda não está integrado nesta branch.
- Impacto: a evidência nova prova corretamente filtros no planejador e nos
  estágios de recuperação já existentes, mas não prova o critério completo
  “em todos os estágios” enquanto o fluxo de geração/verificação não existe.
- Correção necessária: manter AC-07 como `◐` até T13 incluir uma evidência de
  ponta a ponta, ou reformular a linha para declarar explicitamente que o
  estado `✅` se limita aos estágios implementados — sem promover o AC global
  antes da prova completa.

## Conclusão

Os bloqueadores T10-01 e T10-02 e os achados T10-03/T10-04 estão corrigidos.
T10 é aprovado com a ressalva documental R2-T10-01; ela não invalida a
correção funcional desta tarefa, mas deve ser ajustada antes de usar a matriz
como evidência do gate completo da fase 1.
