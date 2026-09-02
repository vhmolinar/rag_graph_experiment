# Resposta à revisão T07 — Adapters HTTP de modelos

Data: 2026-08-30
Referência: `docs/rag/REVIEW_T07.md`
Status: correções T7-01 a T7-05 implementadas e verificadas.
Resultado anterior da revisão: **correções obrigatórias** (AC-14 e AC-16 parcial; T07 bloqueada até T7-01 e T7-02).

Sem mudança de arquitetura e sem requisito/critério de aceitação alterado. Nenhuma
dependência nova foi adicionada. As correções reforçam SPEC §11 e AC-14/AC-16,
que a implementação original não atendia integralmente.

## T7-01 (bloqueador) — Geração retentada apesar de não ser idempotente

`POST /chat/completions` não é idempotente: um timeout pode ocorrer depois de o
provedor já ter gerado a resposta, e repetir pode consumir recursos duas vezes e
produzir conteúdo diferente para a mesma execução.

Correção: `GenerationEndpointSettings.max_retries` agora tem **default 0**
(`backend/src/rag/adapters/generation_adapter.py`). O docstring da classe registra
a razão (SPEC §11: retries apenas para falhas transitórias **e** operações
idempotentes). Embedding e reranking (idempotentes) mantêm retries.

Evidências (agora exercitam o comportamento default, antes fixado em 0 nos testes):

- `test_generation_adapter.py::test_default_max_retries_is_zero`;
- `test_generation_adapter.py::test_timeout_raises_model_timeout_error` — `route.call_count == 1`;
- `test_generation_adapter.py::test_connection_error_raises_model_unavailable_error` — `route.call_count == 1`;
- `test_generation_adapter.py::test_5xx_raises_model_unavailable_error` — `route.call_count == 1`.

## T7-02 (bloqueador) — Credenciais Bearer por HTTP em texto claro

A regra T6-04 (nunca enviar `Authorization: Bearer` para `http://`) foi
centralizada em `HttpEndpointSettings` (`backend/src/rag/adapters/model_settings.py`)
e agora vale para os três endpoints — embedding, geração e reranking. O mixin
valida `base_url` http(s) e rejeita credencial não vazia quando o esquema não é
`https://`, na construção dos settings.

Erros de configuração não citam chave, URL configurada nem caminho de secret file;
`hide_input_in_errors` impede que o `str()` do `ValidationError` ecoe o input
(que conteria o `Path` do secret file ou a URL).

Evidências:

- `test_generation_adapter.py::TestGenerationEndpointSettings::test_http_with_api_key_is_rejected`;
- `test_generation_adapter.py::TestGenerationEndpointSettings::test_http_with_api_key_file_is_rejected`
  (garante que a chave do arquivo é resolvida antes da checagem de https);
- `test_generation_adapter.py::TestGenerationEndpointSettings::test_https_with_api_key_is_accepted`;
- `test_reranker_adapter.py::TestRerankerEndpointSettings::test_http_with_api_key_is_rejected`;
- `test_reranker_adapter.py::TestRerankerEndpointSettings::test_https_with_api_key_is_accepted`;
- `test_embedding_adapter.py::TestEndpointSettings::test_https_required_when_api_key_set`
  (regressão — comportamento T6-04 preservado);
- `test_model_settings.py::test_error_message_does_not_leak_secret_file_path`.

## T7-03 — Resposta do reranker não validada por schema

A resposta do reranker passou a ser validada por modelos Pydantic
(`_RerankItem` / `_RerankResponse` em `reranker_adapter.py`), com:

- índice inteiro não negativo (`index: int = Field(ge=0)`);
- score **finito** (`NaN`/`Infinity` rejeitados por `field_validator`, em vez de
  aceitos por `float()`);
- cardinalidade exata igual aos documentos enviados e cobertura de índices
  `0..n-1` sem repetição/lacuna (o dict não sobrescreve mais duplicatas, nem
  para um único documento).

Malformações viram `ModelResponseError` (falha fechada, nunca contamina o ranking).

Evidências:

- `test_reranker_adapter.py::test_duplicate_index_for_single_document_raises_model_response_error`;
- `test_reranker_adapter.py::test_nan_score_raises_model_response_error`;
- `test_reranker_adapter.py::test_infinity_score_raises_model_response_error`;
- `test_reranker_adapter.py::test_invalid_score_type_raises_model_response_error`;
- regressão: `test_returns_scores_in_document_order_regardless_of_response_order`,
  `test_duplicate_index_raises_model_response_error`, `test_missing_index_raises_model_response_error`.

## T7-04 — Circuit breaker não limitava half-open a uma chamada

`CircuitBreaker` (`resilience.py`) agora guarda `_probe_in_progress`. Enquanto a
tentativa de teste (half-open) está em andamento, as chamadas concorrentes são
rejeitadas com `CircuitBreakerOpenError`; o flag é limpo em `on_success`/`on_failure`.
Como `before_call` não tem `await`, o check-e-set é atômico no loop de eventos.

Evidências:

- `test_resilience.py::test_half_open_allows_single_probe_concurrently`;
- `test_resilience.py::test_half_open_probe_cleared_on_failure`;
- `test_resilience.py::test_half_open_lets_only_one_probe_reach_endpoint`
  (integração via `call_with_resilience`: exatamente uma chamada chega ao endpoint).
- Regressões mantidas: `test_half_opens_after_reset_timeout`, `test_half_open_success_closes`,
  `test_half_open_failure_reopens_immediately`.

## T7-05 — Configurações falhavam tarde e sem validação uniforme

`ResilienceSettings` valida todos os limites na construção (`Field`):
`timeout_seconds > 0`, `max_retries >= 0`, `retry_backoff_seconds > 0`,
`retry_backoff_multiplier >= 1`, `max_concurrency > 0`,
`circuit_breaker_failure_threshold >= 1`, `circuit_breaker_reset_seconds > 0`.
`HttpEndpointSettings` centraliza a validação de URL e a regra credencial-sobre-https,
compartilhada pelos três endpoints. Valores inválidos geram `ValidationError`
previsível na construção, em vez de erro genérico de `httpx`/`asyncio`.

Evidências:

- `test_generation_adapter.py::TestGenerationEndpointSettings::test_non_positive_timeout_is_rejected`,
  `test_non_positive_deep_timeout_is_rejected`, `test_zero_max_concurrency_is_rejected`,
  `test_negative_max_retries_is_rejected`, `test_invalid_base_url_is_rejected`;
- `test_reranker_adapter.py::TestRerankerEndpointSettings::test_non_positive_timeout_is_rejected`,
  `test_zero_max_concurrency_is_rejected`;
- `test_model_settings.py` (erros de secret file sem caminho local).

## Verificações finais

Executados na mesma máquina sobre o diff desta correção:

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run mypy src tests` | OK — 84 source files |
| `cd backend && uv run pytest tests/unit tests/contract -q` | OK — 366 passed, 3 skipped |
| `cd backend && uv run pytest -q tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK — 108 passed (antes: 84) |

Matriz atualizada em `docs/rag/EVIDENCE.md` (AC-14 e AC-16) e registro do
implementador em `docs/rag/NOTES.md` §10.8 item 9.

Nenhuma dependência foi adicionada e nenhum requisito ou critério de aceitação
foi alterado. Não foram criados commits nem abertas pull/merge requests.
