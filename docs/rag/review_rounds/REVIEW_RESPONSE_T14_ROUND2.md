# Resposta à rodada 2 (REVIEW_T14_ROUND2.md)

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T14_ROUND2.md`
Status: T14-R2-01 corrigido e verificado.
Resultado anterior: **reprovado — T14-01 permanece parcialmente aberto**
(T14-R2-01: CORS ausente na 429).

## T14-R2-01 (Alto) — o 429 não é legível pelo SPA permitido

### Diagnóstico
Confirmado. A pilha da rodada 1 era `RequestId → SecurityHeaders → RateLimit →
CORS` (externo→interno): `CORSMiddleware` ficava INTERNO ao
`RateLimitMiddleware`, e a resposta 429 direta do limiter nunca o alcançava —
`Access-Control-Allow-Origin` ausente na 429, blouqueando a leitura do corpo e
de `Retry-After` pelo navegador do SPA permitido.

### Correção
`install_security()` registra agora na ordem inversa (RateLimit primeiro,
`RequestIdMiddleware` por último), obtendo a pilha desejada (externo→interno):

```text
RequestId → SecurityHeaders → CORS → RateLimit
```

- a resposta 429 direta do limiter atravessa `CORSMiddleware` (externo) e
  expõe `Access-Control-Allow-Origin` para a origem permitida, continuando a
  receber `X-Request-ID` e todos os headers de segurança (externos a CORS);
- o preflight OPTIONS é resolvido por `CORSMiddleware` antes de chegar ao
  limiter (não consome tokens do rate limiting).

### Evidências
- Reprodução da revisão com `Origin: http://localhost:5173` e
  `RATE_LIMIT_PER_MINUTE=1`: a segunda chamada a `/api/v1/works` devolve `429`
  com `access-control-allow-origin: http://localhost:5173`, `x-request-id`,
  `strict-transport-security`, `content-security-policy` e corpo com
  `request_id` == `X-Request-ID`.
- Teste composto atualizado — exige CORS na própria 429 para origem permitida:
  `tests/unit/test_api_app.py::test_rate_limited_429_serves_cors_to_allowed_origin`
  (asserção `access-control-allow-origin == "http://localhost:5173"` na 429).
- Integração (PostgreSQL real): `tests/integration/test_api.py` — 14 passed.

### Verificação
| Comando | Resultado |
| --- | --- |
| `make lint` / `make format-check` / `make typecheck` | OK |
| `cd backend && uv run pytest tests/unit -q` | 611 passed, 3 skipped |
| `make test-integration-podman` | 232 passed, 1 skipped (PostgreSQL real) |
| Reprodução ASGI (429 com origem permitida) | `Access-Control-Allow-Origin` presente + request ID + headers |

T14-01 (request ID e headers), T14-02 e T14-03 permanecem aprovados da rodada
1; com a correção de T14-R2-01 o contrato CORS no caminho de rate limiting
fica completo.
