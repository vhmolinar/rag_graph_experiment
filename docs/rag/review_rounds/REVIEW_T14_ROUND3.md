# Revisão independente — T14, rodada 3

Data: 2026-09-02  
Referência: `REVIEW_T14_ROUND2.md` e `REVIEW_RESPONSE_T14_ROUND2.md`  
Resultado: **aprovado para os achados de T14**

## Escopo revisado

- Correção de T14-R2-01 em `backend/src/rag/api/security.py`.
- Teste composto correspondente em `backend/tests/unit/test_api_app.py`.
- Regressão dos caminhos de erro, rate limit e SSE em testes unitários e de
  integração da API.

## Evidências executadas

| Comando | Resultado |
| --- | --- |
| Reprodução ASGI com `RATE_LIMIT_PER_MINUTE=1`, `Origin: http://localhost:5173` e segunda chamada a `/api/v1/works` | `429`; `access-control-allow-origin: http://localhost:5173`, `Retry-After`, `X-Request-ID`, HSTS e CSP presentes; `error.request_id == X-Request-ID` |
| `cd backend && uv run pytest tests/unit/test_api_app.py tests/unit/test_api_security.py tests/unit/test_api_schemas.py tests/unit/test_runs.py -q` | 52 passed |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_api.py -q` | 14 passed |

## Verificação do achado T14-R2-01

`install_security()` registra a pilha efetiva como:

```text
RequestId → SecurityHeaders → CORS → RateLimit
```

Assim, a resposta direta do rate limiter atravessa CORS, headers de segurança
e request ID. O teste
`test_rate_limited_429_serves_cors_to_allowed_origin` agora exige
`access-control-allow-origin == "http://localhost:5173"` na própria resposta
429, além de cobrir o preflight.

## Conclusão

T14-R2-01 foi corrigido e reproduzido. Os achados anteriores T14-01, T14-02 e
T14-03 permanecem resolvidos pelos testes executados. Esta rodada não reavalia
itens explicitamente adiados para T15, T17, T18, T19 ou T20.
