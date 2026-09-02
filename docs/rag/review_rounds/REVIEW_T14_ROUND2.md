# Revisão independente — T14, rodada 2

Data: 2026-09-02  
Referência: `REVIEW_T14.md` e `REVIEW_RESPONSE_T14.md`  
Resultado: **reprovado — T14-01 permanece parcialmente aberto**

## Escopo revisado

- Correções locais não commitadas que respondem a T14-01, T14-02 e T14-03.
- Arquivos principais: `backend/src/rag/api/security.py`, `routes/queries.py`,
  `schemas.py`, `domain/runs.py`, migration `0010`, e testes API.

## Evidências executadas

| Comando | Resultado |
| --- | --- |
| `cd backend && uv run pytest tests/unit/test_api_app.py tests/unit/test_api_security.py tests/unit/test_api_schemas.py tests/unit/test_runs.py -q` | 52 passed |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_api.py -q` | 14 passed |
| Reprodução ASGI com `RATE_LIMIT_PER_MINUTE=1`, origem `http://localhost:5173` e segunda chamada a `/api/v1/works` | 429 com `Retry-After`, request ID e headers de segurança, mas **sem** `Access-Control-Allow-Origin` |

## Achado

### T14-R2-01 — Alto: o 429 não é legível pelo SPA permitido

- Requisito: SPEC §§10.1 e 14; AC-18; checklist §14.
- Evidência: após a alteração, a pilha é `RequestId → SecurityHeaders →
  RateLimit → CORS` (externo → interno). A resposta direta do
  `RateLimitMiddleware` não alcança o `CORSMiddleware`, que é interno. A
  reprodução com `Origin: http://localhost:5173` obteve 429 sem o header
  `Access-Control-Allow-Origin`.
- Cobertura insuficiente: o teste declarado em
  `tests/unit/test_api_app.py::test_rate_limited_429_serves_cors_to_allowed_origin`
  verifica CORS apenas no preflight. Para a chamada limitada, ele confere
  request ID e headers de segurança, mas não confere
  `access-control-allow-origin`.
- Impacto: o navegador bloqueia o acesso do SPA configurado ao corpo do 429;
  portanto não é possível tratar a limitação ou ler `Retry-After` no cliente.
- Correção necessária: garantir que o CORS também envolva a resposta 429
  (por exemplo, colocando CORS externamente sem recolocar request ID/headers
  de segurança para dentro do rate limiter, ou emitindo CORS de forma segura
  no limitador), e exigir no teste composto:

  ```text
  access-control-allow-origin == http://localhost:5173
  ```

  na resposta 429 para origem permitida.

## Itens corrigidos nesta rodada

- **T14-02:** aprovado pela inspeção e pelos testes: `request_id` é persistido
  no `AnswerRun`, retornado no estado de falha e incluído no evento SSE terminal.
- **T14-03:** aprovado pela inspeção e pelos testes: buckets recebem TTL e
  limite de cardinalidade/LRU.
- **T14-01 (parcial):** request ID e headers de segurança agora chegam ao 429,
  mas CORS no próprio 429 continua ausente.

## Conclusão

Não aprovar T14 ainda. A correção resolveu a parte de request ID e headers de
segurança de T14-01, mas o contrato CORS continua incompleto no caminho de
rate limiting. Após corrigir T14-R2-01 e executar o teste composto atualizado,
uma nova rodada pode encerrar a revisão.
