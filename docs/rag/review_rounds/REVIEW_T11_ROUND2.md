# Revisão T11 — rodada 2

Data: 2026-09-01
Resposta verificada: `REVIEW_RESPONSE_T11.md`
Resultado: **reprovado**

## Itens corrigidos

- **T11-01:** corrigido. `rag enrich <edition-id>` cria um caminho operacional
  configurado e testável, sem acoplar o domínio ao CLI.
- **T11-03:** corrigido no caso declarado. `enrichment_runs` é gravada na mesma
  transação dos itens, inclusive quando todos são rejeitados; a reexecução sem
  itens publicados não volta a chamar o provider.

## Achado novo

### R2-T11-01 — alto: a chave de idempotência ignora a execução de indexação

`EnrichmentService` agora seleciona corretamente a `IndexRun` ativa e usa
`list_by_index_run()`. Porém, `enrichment_runs` é única somente por
`(edition_id, summarizer_version_id)`. A `index_run_id` — isto é, o conjunto de
passagens efetivamente enviado ao provider — não integra nem a tabela nem a
decisão de idempotência.

Cenário reproduzível por inspeção:

1. indexar uma edição e executar `rag enrich` com o modelo A;
2. reindexar a mesma edição, por `rag index --force` ou por uma versão nova de
   chunking/embedding, tornando outro conjunto de passagens ativo;
3. executar `rag enrich` novamente com o mesmo modelo A.

No passo 3, `get_for_edition_version(edition_id, summarizer_version_id)`
encontra a execução anterior e retorna `created=False`; o serviço não envia a
`IndexRun` nova ao provider. As summaries/conceitos disponíveis continuam
sustentados pelos chunks da execução inativa, embora T11-02 exija representar
o conjunto corrente.

O novo teste `test_two_reindexations_never_use_inactive_passages` não detecta
isso porque troca `model_name` de `model-a` para `model-b`, produzindo uma nova
`summarizer_version_id` e, portanto, evitando a colisão que ocorreria em
operação normal.

Isso viola SPEC §7.4 (enriquecimento após a indexação), §8.7 e AC-15: a
identidade e a reprodução da síntese devem incluir o conjunto de passagens de
origem. A correção necessária é associar `EnrichmentRun` à `index_run_id` e
incluí-lo na chave de idempotência, preservando o histórico. Acrescentar um
teste com duas reindexações e o **mesmo** modelo de enriquecimento, que exija
uma segunda execução e prove que seus suportes pertencem apenas à nova run.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `uv run pytest -q tests/unit/test_enrichment.py tests/unit/test_enrichment_adapter.py tests/unit/test_model_doubles.py tests/unit/test_cli.py` | OK — 48 passed |
| `uv run ruff check src tests` | OK |
| `uv run ruff format --check src tests` | OK — 105 arquivos |
| `uv run mypy src tests` | OK — 105 arquivos, strict |
| `uv run pytest -q tests/integration/test_enrichment_pipeline.py tests/integration/test_cli.py` | Não executável neste ambiente: 26 erros de setup porque o socket Docker/Podman não está disponível |

## Conclusão

As correções anteriores são válidas, mas a ausência de `index_run_id` na
identidade de `enrichment_runs` deixa o enriquecimento desatualizado após uma
reindexação normal. T11 permanece reprovada até a chave, migration e teste de
reindexação com o mesmo modelo serem corrigidos e as integrações serem
reexecutadas.
