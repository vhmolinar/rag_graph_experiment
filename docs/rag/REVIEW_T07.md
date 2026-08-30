# Revisão T07 — Adapters HTTP de modelos

Data: 2026-08-30  
Branch revisada: `T07`  
Commit da tarefa: `2506792` (`Implement T07: HTTP model adapters (generation, reranking, resilience)`)  
Estado revisado: `d10b50e` (`fix: merged T06`)  
Resultado: **correções obrigatórias**

## Resumo executivo

T07 entrega adapters separados para embedding, geração e reranking, contratos
de domínio sem SDK externo, erros tipados, saída de geração validada como
`GeneratedAnswer`, secret file, limite de concorrência, retry/circuit breaker
e doubles determinísticos locais.

Porém, a implementação não satisfaz integralmente SPEC §11 e AC-14/AC-16. O
adapter de geração retenta uma operação não idempotente, geração e reranking
podem enviar chave Bearer por HTTP sem TLS, e a resposta do reranker não é
validada por schema nem falha fechada para todos os payloads inválidos.

## Bloqueadores

### T7-01 — Geração é retentada apesar de não ser idempotente

Arquivos:

- `backend/src/rag/adapters/generation_adapter.py`;
- `backend/src/rag/adapters/resilience.py`;
- `backend/tests/unit/test_generation_adapter.py`.

SPEC §11 permite retries somente para falhas transitórias **e operações
idempotentes**. `OpenAiCompatibleGeneratorProvider.generate()` sempre chama
`call_with_resilience()` com `max_retries` configurável, cujo default é dois.
Timeout, erro de conexão ou 5xx em `POST /chat/completions` inicia nova
geração.

Não existe idempotency key, identificador aceito pelo endpoint ou garantia de
que a primeira chamada não foi processada. Um timeout pode ocorrer depois de o
provedor já ter gerado a resposta; repetir pode consumir recursos duas vezes e
produzir conteúdo diferente para a mesma execução. Isso viola SPEC §11 e
enfraquece AC-14. Os testes de geração fixam `max_retries=0`, portanto não
exercitam o comportamento default.

Correção esperada:

- não retentar geração por padrão, ou fazê-lo somente com mecanismo de
  idempotência comprovado e configurado para o endpoint;
- testar timeout/5xx do gerador e verificar uma única chamada HTTP quando não
  houver idempotency key.

### T7-02 — Generator e reranker enviam credenciais por HTTP em texto claro

Arquivos:

- `backend/src/rag/adapters/generation_adapter.py`;
- `backend/src/rag/adapters/reranker_adapter.py`;
- `backend/tests/unit/test_generation_adapter.py`;
- `backend/tests/unit/test_reranker_adapter.py`.

`_headers()` adiciona `Authorization: Bearer <api_key>` sem validar o esquema
de `base_url`. Os defaults e testes usam `http://`; o teste de geração
confirma que a chave é enviada nesse transporte. O adapter de embeddings já
rejeita a combinação de credencial com HTTP (T6-04), mas a proteção não foi
aplicada a geração e reranking.

Isso expõe secret-file e variáveis de ambiente caso um endpoint não-local ou
rota de rede seja configurado incorretamente. Não é compatível com o
tratamento exigido para segredos nem com AC-16.

Correção esperada:

- validar URL HTTP(S) para os três endpoints;
- rejeitar credencial não vazia quando `base_url` não usar `https://`, salvo
  exceção local explícita, restrita e aprovada;
- não colocar chave, URL completa ou caminho de secret file em erro/log;
- testar rejeição para generator/reranker e aceitação em HTTPS.

## Correções importantes

### T7-03 — Resposta do reranker não é validada por schema

Arquivo: `backend/src/rag/adapters/reranker_adapter.py`.

T07 exige respostas estruturadas validadas e testes de payload inválido. O
reranker acessa dicionários sem schema Pydantic:

```python
by_index = {int(item["index"]): float(item["relevance_score"]) for item in results}
```

`NaN` e infinito podem passar por `float()` e contaminar o ranking. O dict
também sobrescreve resultados duplicados; para uma lista de um documento, duas
entradas de índice `0` passam na verificação atual. Não há validação
declarativa de lista, tipo/intervalo de índice ou finitude do score.

Correção esperada:

- criar modelos Pydantic para envelope e itens;
- exigir cardinalidade igual à entrada, índices únicos e cobertura exata;
- rejeitar `NaN`/infinito e payloads malformados com `ModelResponseError`;
- testar duplicata para um documento, valores não finitos e tipos inválidos.

### T7-04 — Circuit breaker não limita half-open a uma chamada

Arquivo: `backend/src/rag/adapters/resilience.py`.

A documentação declara que o breaker permite “uma tentativa de teste” após o
reset. A primeira chamada muda `OPEN` para `HALF_OPEN`; enquanto aguarda rede,
qualquer outra chamada encontra estado diferente de `OPEN` e também é
liberada. Com `max_concurrency > 1`, o endpoint recebe várias tentativas
half-open simultâneas.

Correção esperada:

- representar uma probe half-open em andamento e rejeitar ou enfileirar as
  demais chamadas;
- testar concorrência após reset e provar que apenas uma chamada chega ao
  endpoint.

### T7-05 — Configurações falham tarde e sem validação uniforme

Arquivos:

- `backend/src/rag/adapters/model_settings.py`;
- `backend/src/rag/adapters/generation_adapter.py`;
- `backend/src/rag/adapters/reranker_adapter.py`.

Ao contrário de `EmbeddingEndpointSettings`, os settings de geração e
reranking não validam URL, timeout positivo, backoff, concorrência ou reset.
Valores como timeout negativo ou `max_concurrency=0` falham depois, como erro
genérico de `httpx`/`asyncio`, em vez de erro de configuração previsível.

Correção esperada: centralizar validações comuns, rejeitar valores inválidos
na construção de settings e testar todos os limites sem expor segredo ou
caminho local.

## Evidências verificadas

Executados sobre `d10b50e`:

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run pytest -q tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK — 84 passed |
| `cd backend && uv run ruff check src/rag/adapters tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK |
| `cd backend && uv run mypy src/rag/adapters tests/contract/test_embedding_adapter.py tests/unit/test_embedding_adapter_resilience.py tests/unit/test_generation_adapter.py tests/unit/test_reranker_adapter.py tests/unit/test_resilience.py tests/unit/test_model_settings.py tests/unit/test_model_doubles.py` | OK — 16 source files |

Os testes cobrem fluxo feliz, timeout/429/5xx tipados, `GeneratedAnswer`, o
breaker básico e logs próprios de resiliência. Eles não cobrem T7-01 a T7-05;
logo, não são evidência suficiente para esses requisitos.

## Julgamento

- AC-14: **parcial** — as falhas principais são tipadas, mas retry de geração
  não idempotente viola SPEC §11 e pode duplicar uma execução.
- AC-16: **parcial** — logs próprios não carregam payloads/chaves, mas
  generator/reranker podem transmitir credenciais por HTTP e settings podem
  expor caminho de secret file em erros de configuração.

T07 permanece bloqueada até T7-01 e T7-02 serem corrigidos. T7-03 a T7-05
devem ser resolvidos na mesma rodada com testes de regressão para demonstrar
contratos e resiliência robustos.
