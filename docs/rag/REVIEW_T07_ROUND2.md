# Segunda revisão T07 — Validação da resposta à revisão inicial

Data: 2026-08-30  
Branch revisada: `T07`  
Commit revisado: `6e7aea6` (`Fix T07 review findings T7-01 through T7-05`)  
Resposta avaliada: `docs/rag/REVIEW_RESPONSE_T07.md`  
Resultado: **correção obrigatória remanescente**

## Resumo executivo

A resposta corrigiu e comprovou T7-02 a T7-05:

- endpoints de embedding, geração e reranking agora recusam credencial em
  `http://`, validam URL e não expõem caminho de secret file em erros;
- o reranker passou a validar o envelope por Pydantic, rejeitando scores não
  finitos e cardinalidade/índices inválidos;
- o circuit breaker admite uma única probe half-open por ciclo de reset;
- configurações de timeout e resiliência são validadas na construção.

Os testes focalizados, lint e typecheck passaram. Contudo, T7-01 não está
integralmente resolvido: a geração continua permitindo retry de uma operação
não idempotente quando `GENERATOR_MAX_RETRIES` é configurado com valor maior
que zero. Alterar somente o default para zero não impõe a regra obrigatória de
SPEC §11.

## Bloqueador remanescente

### R2-T7-01 — Retry de geração pode ser reativado por configuração

Arquivos:

- `backend/src/rag/adapters/generation_adapter.py`;
- `backend/src/rag/adapters/resilience.py`;
- `backend/tests/unit/test_generation_adapter.py`.

Requisitos afetados:

- SPEC §11: retries apenas para falhas transitórias e operações idempotentes;
- AC-14: falha de modelo deve permanecer explícita, sem comportamento que
  duplique uma execução de geração;
- checklist §16: retry ocorre apenas em falha transitória idempotente.

`GenerationEndpointSettings.max_retries` agora tem default `0`, e os testes
provam corretamente que o caminho default realiza uma única chamada para
timeout, erro de conexão e 5xx. Porém, o campo continua aceitando qualquer
inteiro não negativo:

```python
max_retries: int = Field(default=0, ge=0)
```

`OpenAiCompatibleGeneratorProvider.generate()` continua encaminhando esse
valor diretamente a `call_with_resilience()`:

```python
max_retries=self._settings.max_retries
```

Portanto, definir `GENERATOR_MAX_RETRIES=1` faz uma falha transitória em
`POST /chat/completions` ser retentada. Essa operação não é idempotente: a
primeira solicitação pode já ter sido processada antes do timeout, e a segunda
pode consumir recursos novamente e produzir resposta distinta para a mesma
execução.

O cenário não é coberto pelos testes novos, pois todos verificam apenas o
default `0`; nenhum constrói `GenerationEndpointSettings(max_retries=1)` e
confirma que a configuração é rejeitada ou que a chamada continua única.

Correção esperada:

- remover `max_retries` da configuração de geração, mantendo o valor efetivo
  fixo em zero; ou
- rejeitar qualquer valor diferente de zero em
  `GenerationEndpointSettings`; ou
- introduzir idempotency key/contrato de endpoint documentado e testado antes
  de permitir retry — isso seria uma expansão que exige decisão explícita;
- adicionar regressão que prove que `GENERATOR_MAX_RETRIES=1` é rejeitado, ou
  que a geração não é retentada independentemente desse valor.

## Correções aceitas

### T7-02 — Credencial sobre HTTPS

Aceita. `HttpEndpointSettings` centraliza a validação de URL e recusa chave
resolvida por ambiente ou secret file quando `base_url` não usa `https://`.
Foi confirmado também que `hide_input_in_errors` e mensagens sanitizadas não
expõem o caminho de secret file.

### T7-03 — Contrato do reranker

Aceita. `_RerankResponse` e `_RerankItem` validam o envelope, a finitude do
score e o índice não negativo. O adapter exige a mesma cardinalidade dos
documentos e cobertura exata de índices. Os novos casos para duplicata de
documento único, `NaN`, infinito e score inválido falham fechado.

### T7-04 — Half-open concorrente

Aceita. `_probe_in_progress` impede que uma segunda chamada atravesse o
breaker enquanto a primeira probe half-open aguarda o endpoint. O teste com
`asyncio.gather` demonstra exatamente uma chamada ao endpoint.

### T7-05 — Validação de settings

Aceita. `Field` valida timeouts, retries, backoff, concorrência e reset; URL
inválida é rejeitada antes da construção dos clients. Os testes cobrem os
limites relevantes de generator e reranker.

## Evidências verificadas

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run pytest -q tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK — 108 passed |
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run mypy src tests` | OK — 84 source files |
| construção direta de `GenerationEndpointSettings` com secret file ausente | erro sanitizado; caminho local não apareceu na mensagem |

## Julgamento

- AC-14: **parcial** — o fluxo default de geração não retenta, mas a
  configuração ainda pode reativar retry de uma operação não idempotente.
- AC-16: **atendido no escopo de T07** — adapters não enviam credencial por
  HTTP, settings não expõem caminho de secret file e os logs próprios de
  resiliência não recebem payloads/chaves.

T07 permanece bloqueada somente por R2-T7-01. Após impedir de forma
inequívoca retries configuráveis para geração, a tarefa poderá ser aprovada
quanto aos requisitos T07/AC-14/AC-16.
