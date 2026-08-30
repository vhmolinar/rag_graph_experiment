# Resposta à segunda revisão T07 — Validação da resposta

Data: 2026-08-30  
Referência: `docs/rag/REVIEW_T07_ROUND2.md`  
Status: correção R2-T7-01 implementada e verificada.  
Resultado anterior: correção obrigatória remanescente (T7-02 a T7-05 aceitos; AC-14 parcial por R2-T7-01).

## R2-T7-01 — Retry de geração podia ser reativado por configuração

A geração continua sendo uma operação não idempotente (`POST /chat/completions`),
e SPEC §11/AC-14 exigem que retries ocorram apenas para operações idempotentes.
Trocar o default para `0` não bastava, pois `GENERATOR_MAX_RETRIES>0` reativaria o
retry.

Correção: `GenerationEndpointSettings.max_retries` agora é **`Literal[0]`**
(`backend/src/rag/adapters/generation_adapter.py`). Isso torna o "não retentar"
garantido pelo sistema de tipos e rejeitado na construção — não é apenas um
default:

```python
max_retries: Literal[0] = 0
```

Consequências:

- `GenerationEndpointSettings(max_retries=1)` levanta `ValidationError`
  (`literal_error`);
- `GENERATOR_MAX_RETRIES=1` (env) levanta `ValidationError` na construção;
- o valor efetivo passado a `call_with_resilience()` é sempre `0`, então não há
  caminho de configuração que reintroduza retry; o campo não é mais configurável
  em geração.

O docstring da classe registra explicitamente a regra e o motivo
(SPEC §11: retries apenas para falhas transitórias **e** operações idempotentes).

Evidências (regressões novas):

- `test_generation_adapter.py::test_non_zero_max_retries_is_rejected`
  (`max_retries=1` rejeitado pelo construtor);
- `test_generation_adapter.py::test_env_non_zero_max_retries_is_rejected`
  (`GENERATOR_MAX_RETRIES=1` rejeitado via ambiente).

Mantidas:

- `test_default_max_retries_is_zero`;
- `test_timeout_raises_model_timeout_error` / `test_5xx_raises_model_unavailable_error`
  / `test_connection_error_raises_model_unavailable_error` — cada uma com
  `route.call_count == 1` (uma única chamada HTTP no caminho default).

## Verificações finais

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run pytest -q tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK — 110 passed (antes: 108) |
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run mypy src tests` | OK — 84 source files |
| `cd backend && uv run pytest tests/unit tests/contract -q` | OK — 368 passed, 3 skipped |

Matriz atualizada em `docs/rag/EVIDENCE.md` (AC-14). Nenhuma dependência foi
adicionada e nenhum requisito ou critério de aceitação foi alterado. Não foram
criados commits nem abertas pull/merge requests até este ponto.
