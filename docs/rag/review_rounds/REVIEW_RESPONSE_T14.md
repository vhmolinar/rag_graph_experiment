# Resposta à revisão T14

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T14.md`
Status: correções T14-01 a T14-03 implementadas e verificadas; suíte de
integração executada contra PostgreSQL real (via socket Docker-compatible do
Podman).
Resultado anterior: **reprovado** (T14-01 crítico; T14-02 alto; T14-03 médio).

---

## T14-01 (Crítico) — resposta 429 viola o envelope correlacionável e perde headers obrigatórios

### Diagnóstico
Confirmado. A reprodução da revisão (`RATE_LIMIT_PER_MINUTE=1`, duas chamadas a
`GET /api/v1/works`) entregava `429`, apenas `Retry-After`, sem `X-Request-ID`,
sem HSTS/CSP, e corpo com `request_id: "desconhecido"`.

Em Starlette, `add_middleware()` insere cada item no início da lista e
`build_middleware_stack()` a inverte ao empilhar: o **último** registrado fica
mais externo. A ordem de registro original (`RequestId`, `SecurityHeaders`,
`RateLimit`, `CORS`) produzía a pilha real `CORS → RateLimit → SecurityHeaders →
RequestId` (externo→interno); quando o rate limiter respondia 429 diretamente,
`SecurityHeaders` e `RequestId` (internos) nunca executavam.

### Correção
`install_security()` agora registra na ordem inversa (CORS primeiro,
`RequestIdMiddleware` por último), obtendo a ordem desejada e documentada
(externo→interno): `RequestId → SecurityHeaders → RateLimit → CORS`. Toda
resposta — inclusive `429` — atravessa `RequestIdMiddleware` (scope state +
`X-Request-ID`) e `SecurityHeadersMiddleware`.

### Evidências
- Reprodução da revisão reexecutada: `429` com `X-Request-ID` = body
  `request_id`, `strict-transport-security`, `content-security-policy`,
  `x-content-type-options`, `x-frame-options`, `referrer-policy`,
  `permissions-policy` e `Retry-After`.
- Testes de aplicação composta (não do middleware isolado), que saturen o
  limite e exigen `Retry-After`, `X-Request-ID`, todos os headers de segurança
  e corpo com o MESMO ID do header; asserção CORS com origem permitida:
  - `tests/unit/test_api_app.py::test_rate_limited_429_keeps_envelope_and_security_headers`
  - `tests/unit/test_api_app.py::test_rate_limited_429_serves_cors_to_allowed_origin`
- Integração (PostgreSQL real):
  `tests/integration/test_api.py::test_rate_limit_returns_429_with_retry_after`
  agora exige `X-Request-ID`, HSTS, CSP e `request_id == X-Request-ID` na 429.

---

## T14-02 (Alto) — falhas persistidas de consulta expõem `ErrorOut.request_id` vazio

### Diagnóstico
Confirmado: `build_query_state()` criava `ErrorOut(..., request_id="")` para
todo `AnswerRun` com status `FAILED`, exposto por `GET /queries/{id}` e pelo
evento SSE terminal.

### Correção
O `request_id` da requisição que iniciou a consulta é agora persistido no
`AnswerRun` (campo `request_id`, migration
`0010_answer_runs_request_id.py`): a rota `POST /queries` o cria com o ID do
scope (`request.state.request_id`), o campo é imutável por transição
(`_ALLOWED_CHANGE_FIELDS` não o incluye; o repository o compara em `save()`),
e `build_query_state()` o devolve no envelope de erro — idêntico no estado
(`GET`) e no evento SSE terminal.

### Evidências
- Unit: `tests/unit/test_api_schemas.py::test_build_query_state_failed_carries_safe_error`
  (projeção carrega `request_id`).
- Integração (PostgreSQL real):
  - `tests/integration/test_api.py::test_failed_query_error_request_id_is_correlatable`
    — falha de provedor via HTTP; `state.error.request_id` não vazio e IGUAL ao
    `X-Request-ID` da resposta do POST;
  - `tests/integration/test_api.py::test_failed_query_sse_terminal_carries_request_id`
    — o evento SSE terminal carrega o mesmo ID correlacionável.

---

## T14-03 (Média) — o mapa de buckets cresce sem limite e não é removido

### Diagnóstico
Confirmado: `RateLimitMiddleware._buckets` era um dicionário por IP sem TTL,
limite de cardinalidade ou coleta.

### Correção
- TTL: buckets inativos expiram após `bucket_ttl_seconds` (coleta periódica,
  máximo uma passada por TTL);
- cardinalidade: com `max_buckets`, o bucket menos recente é desalojado (LRU);
- relógio inyectável mantido (`now`), parâmetros configurableis no middleware e
  em `install_security()`.

### Evidências
- `tests/unit/test_api_security.py::test_rate_limit_buckets_expire_after_ttl`
- `tests/unit/test_api_security.py::test_rate_limit_buckets_are_recreated_after_ttl`
- `tests/unit/test_api_security.py::test_rate_limit_caps_bucket_cardinality`

---

## Lacunas de evidência e risco residual da revisão

A revisão não pôde executar `make test-integration` (Docker ausente). Neste
ambiente Docker não está instalado, mas o socket Docker-compatible do Podman
está disponível: a suíte de integração foi executada com
`make test-integration-podman` e **passa inteira** (232 passed, 1 skipped;
`tests/integration/test_api.py`: 14 passed). Isto valida PostgreSQL real,
source ranges, SSE, cancelamento e falhas de provedor no caminho HTTP completo.

Para chegar a esse estado foi necessário corrigir, além dos achados, vários
quebras de rodada fundidas no tronco (registradas em `NOTES.md` §21 e
`EVIDENCE.md` §T14):

1. **Cadeia de migrations com revisão `0005` duplicada** (T11
   `0005_enrichment_runs` vs T14 `0005_answer_runs_error_message`) — Alembic
   "Multiple head revisions"; linearizada: erro-message → `0009`, request_id →
   `0010`.
2. **`RetrievalService.retrieve` (T09/T13 fundido) exige `run` e persiste
   candidatos/versões** — `QueryExecutor._retrieve` o injeta e recarrega o run
   (latência append-only); sem isto `make typecheck` falhava e o fluxo
   quebrava na integração.
3. **Corrida POST→GET** (risco residual assinalado na revisão): `AnswerRun` é
   agora criado SINCRONAMENTE na rota (status `queued`) antes do 202 — o run
   existe logo após a criação e não há mais `404` transitório.
4. **Harness de integração**: `build()` injeta `FakePlannerProvider` por
   padrão (o default de produção aponta para `localhost:8003`) e o seed usa a
   `embedding_version` do provedor (a busca vetorial filtra por
   `embedding_version_id`).
5. **Teste de integração SSE**: com `httpx.ASGITransport`, `client.stream()`
   fica disponível só quando a aplicação conclúu o corpo; o teste lê até o
   evento terminal `result` (subscrição ativa ou replay). Eventos de estágio
   são cobertos pelo broker (`tests/unit/test_api_events.py`).

## Comandos de verificação

| Comando | Resultado |
| --- | --- |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK (ruff + prettier) |
| `make typecheck` | OK (mypy 138 arquivos; tsc) |
| `cd backend && uv run pytest tests/unit -q` | 611 passed, 3 skipped |
| `cd backend && uv run pytest tests/contract -q` | 26 passed |
| `make test-integration-podman` | 232 passed, 1 skipped (PostgreSQL real via testcontainers/podman) |
| `make security-scan` | OK; nenhum IOC bloqueado |
| `make lock` | OK |

Ambiente: Linux, Python 3.12, uv 0.12.7; PostgreSQL 17 (pgvector) via
testcontainers sobre o socket Docker-compatible do Podman (Docker não está
instalado neste ambiente).

## Conclusão

Os três achados da revisão estão corrigidos e cobertos por testes de
composição/integração; a suíte de integração foi executada contra PostgreSQL
real e passa. As quebras de rodada fundidas no tronco que impediam a execução
da integração foram corrigidas e registradas (`NOTES.md` §21). Permanece fora
de escopo: validação ponta-a-ponta do leitor (T17), reescrita autônoma de
follow-up (T15) e a rodada completa de benchmark (T19).

A rodada 2 (T14-R2-01) responde em `docs/rag/review_rounds/REVIEW_RESPONSE_T14_ROUND2.md`.
