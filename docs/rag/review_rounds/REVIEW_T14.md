# Revisão independente — T14 (API FastAPI e segurança)

Data: 2026-09-01  
Resultado: **reprovado**

## Escopo revisado

- Commit: `b6c89bd8995bef9a743405da1b20b8161ff90c79` (`Implement T14: FastAPI API and security`)
- Ambiente: Linux, Python 3.12, `uv 0.12.7`; Docker não está instalado neste ambiente.
- Código inspecionado: `backend/src/rag/api/`, migration `0005`, repositórios e testes introduzidos em T14.
- Fontes confrontadas: `SPECIFICATION.md` §§10 e 14, AC-03/13/14/18; `TASKS.md` T14; `NOTES.md` §10.15; `REVIEW_CHECKLIST.md` §§4 e 14.

## Evidências executadas

| Comando | Resultado |
| --- | --- |
| `uv run --project backend pytest backend/tests/unit/test_api_app.py backend/tests/unit/test_api_security.py backend/tests/unit/test_api_events.py backend/tests/unit/test_api_schemas.py -q` | 32 passed |
| `cd backend && uv run pytest tests/unit -q` | 527 passed, 3 skipped, 6 warnings |
| `make lint` | passou (Ruff e ESLint) |
| `make format-check` | passou (Ruff e Prettier) |
| `make typecheck` | passou (mypy e `tsc --noEmit`) |
| `make lock` | passou |
| `make security-scan` | passou; nenhum IOC bloqueado |
| Reprodução ASGI abaixo, com `RATE_LIMIT_PER_MINUTE=1` e duas chamadas a `GET /api/v1/works` | segunda chamada retornou `429`, sem `X-Request-ID`, HSTS ou CSP, e corpo com `request_id: "desconhecido"` |
| `make test-integration` | não executado: `docker` não está instalado; os testes T14 dependem de PostgreSQL por testcontainers |

Comando de reprodução da falha principal (executado na raiz):

```text
uv run --project backend python - <<'PY'
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path('backend/tests/fixtures').resolve()))
import httpx
from pydantic import SecretStr
from model_doubles import (ConceptEmbeddingProvider, FakeGeneratorProvider,
                           FakeRerankerProvider, FakeVerifierProvider)
from rag.api.app import create_app
from rag.api.settings import ApiSettings
from rag.infrastructure.artifacts import ArtifactStore
from rag.infrastructure.config import DatabaseSettings
from rag.infrastructure.db import Database

async def main():
    app = create_app(
        db=Database(DatabaseSettings(host='localhost', port=1, db='x', user='x', password=SecretStr(''))),
        store=ArtifactStore(Path(tempfile.mkdtemp())),
        settings=ApiSettings(rate_limit_per_minute=1, cors_allowed_origins='http://localhost:5173'),
        embedding_provider=ConceptEmbeddingProvider(), reranker_provider=FakeRerankerProvider(),
        generator_provider=FakeGeneratorProvider(), verifier_provider=FakeVerifierProvider(),
        planner_provider=None, generator_model_name='fake',
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        await client.get('/api/v1/works')
        response = await client.get('/api/v1/works')
        print(response.status_code, dict(response.headers), response.json())
asyncio.run(main())
PY
```

Saída relevante observada:

```text
429 {'retry-after': '60'}
{'error': {'code': 'RATE_LIMITED', 'message': 'Limite de requisições excedido.', 'request_id': 'desconhecido'}}
```

## Critérios afetados por T14

| Critério | Situação | Evidência |
| --- | --- | --- |
| AC-03 | evidência insuficiente | O código do endpoint de range foi inspecionado e os testes de integração existem, mas estes não puderam ser executados sem Docker. T14 só contribui parcialmente para este critério; a validação ponta-a-ponta do leitor é T17. |
| AC-13 | pendente por escopo | T14 fornece CRUD e valida `session_id`; a reescrita e o registro da pergunta autônoma são explicitamente T15. Não avaliar como concluído nesta tarefa. |
| AC-14 | evidência insuficiente | Há mapeamento de erros e testes unitários, mas a integração que exercita o fluxo assíncrono e falhas de provider não foi executada. |
| AC-18 | **falha** | A resposta do rate limiter não recebe ID de requisição nem headers de segurança. A configuração CORS continua restrita, mas não compensa a quebra do contrato de erro e dos headers. |

## Achados

### T14-01 — Crítico: resposta 429 viola o envelope correlacionável e perde headers obrigatórios

- Requisito: SPEC §10.1 (erro com `request_id`), SPEC §14 (rate limiting, CORS restrito e headers de segurança), AC-18; checklist §14.
- Evidência: `install_security()` registra os middlewares em `security.py` na ordem `RequestId`, `SecurityHeaders`, `RateLimit`, `CORS`, afirmando que o primeiro será externo. Em Starlette/FastAPI, `add_middleware()` insere cada item no início da lista e a construção da pilha o inverte: nessa sequência, CORS fica externo e `RateLimitMiddleware` fica antes de `SecurityHeadersMiddleware` e `RequestIdMiddleware` na cadeia de execução. Quando o rate limiter responde diretamente, estes dois últimos não executam.
- Reprodução: o comando acima confirma `429`, apenas `Retry-After`, e `request_id: "desconhecido"`; não há `x-request-id`, `strict-transport-security` ou `content-security-policy`.
- Impacto: viola explicitamente a correlação de erros e os headers exigidos. (CORS é a camada externa nesta ordem e ainda pode acrescentar seu header quando a origem é permitida; a falha é especificamente de request ID e security headers.)
- Correção necessária: corrigir a ordem real da pilha (ou fazer `RateLimitMiddleware` aplicar o mesmo ID e headers de modo seguro) para que toda resposta, inclusive `429`, atravesse request-ID e security headers. Adicionar teste de aplicação composta — não apenas do middleware isolado — que sature o limite e exija `Retry-After`, `X-Request-ID`, todos os headers de segurança e o corpo com o mesmo ID; inclua também uma asserção CORS com `Origin` permitido.

### T14-02 — Alta: falhas persistidas de consulta expõem `ErrorOut.request_id` vazio

- Requisito: SPEC §10.1; checklist §14: “Erros possuem código estável e request ID”.
- Evidência: `backend/src/rag/api/schemas.py:140-145`, em `build_query_state()`, cria `ErrorOut(..., request_id="")` para todo `AnswerRun` com status `FAILED`. O mesmo objeto é retornado por `GET /queries/{query_id}` e publicado no evento SSE terminal por `QueryExecutor._emit_result()`.
- Impacto: falhas de modelo/verificação em consultas assíncronas não podem ser correlacionadas ao log por quem consome o estado ou SSE, embora sejam exatamente os erros que o contrato T14 deve tornar tipados e rastreáveis.
- Correção necessária: persistir o `request_id` que iniciou a consulta no `AnswerRun` (ou definir e persistir um ID de execução próprio), devolvê-lo no estado e SSE, e testar uma falha de provider através de HTTP exigindo ID não vazio e correlacionável.

### T14-03 — Média: o mapa de buckets cresce sem limite e não é removido

- Requisito: SPEC §14 (rate limiting por IP/sessão); segurança operacional da T14.
- Evidência: `RateLimitMiddleware._buckets` em `backend/src/rag/api/security.py` é um dicionário por IP; não há TTL, limite de cardinalidade ou coleta de buckets inativos.
- Impacto: muitos IPs distintos fazem a memória do processo crescer permanentemente, uma forma simples de exaustão no serviço exposto mesmo em rede privada.
- Correção necessária: expirar buckets inativos e impor limite de cardinalidade, com relógio injetável e testes de coleta/limite.

## Lacunas de evidência

- Os testes de integração T14 declarados em `docs/rag/EVIDENCE.md` não foram executados pelo implementador e não foram executáveis neste ambiente sem Docker. Isso impede validar PostgreSQL real, source ranges, SSE e cancelamento no caminho HTTP completo.
- Os testes atuais exercitam `RateLimitMiddleware` isoladamente e aceitam qualquer `request_id` não vazio no teste isolado; não exercitam a composição registrada em `create_app()`. Por isso não detectaram T14-01.
- Não há teste para `request_id` no erro terminal persistido/SSE, deixando T14-02 sem cobertura.

## Riscos residuais

- O processamento em tarefa local e o broker SSE são intencionalmente efêmeros nesta fase, mas o ciclo de `POST /queries` e um `GET`/SSE imediatamente subsequente não possui teste de corrida; o `AnswerRun` só é criado pela tarefa em background.
- A checagem de prontidão e os fluxos de catálogo/range requerem a rodada de integração com PostgreSQL antes de aprovação.

## Conclusão

T14 não está pronto para aprovação. A falha T14-01 é reproduzível e quebra AC-18 no próprio caminho de proteção contra abuso; T14-02 também viola o contrato de correlação dos erros assíncronos. Corrigir os achados, acrescentar os testes de composição e executar a suíte de integração em ambiente com Docker/PostgreSQL antes de uma nova revisão.
