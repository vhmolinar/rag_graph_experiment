# Revisão T11 — resumos hierárquicos e conceitos

Data: 2026-09-01
Resultado: **reprovado**

## Escopo revisado

- Base: `c18f816` (T10); implementação T11: `1ddc3c7`.
- Requisitos: SPEC §7.4 e §8.7; T11; AC-12 e AC-15.
- Código inspecionado: `application/enrichment.py`,
  `infrastructure/repositories/enrichment.py`, contratos de provider e testes
  de T11.

## Achados

### T11-01 — bloqueador: enriquecimento não está integrado ao fluxo de indexação

O único chamador de `EnrichmentService.enrich()` é a fixture de teste
`tests/integration/test_enrichment_pipeline.py`. Não há instância do provider
de enriquecimento, configuração operacional, nem chamada no `IndexingService`
ou no CLI. Portanto, após `rag index`, o sistema não gera summaries, conceitos
ou evidências: os entregáveis de T11 só existem como API interna não exposta.

Isso contraria a sequência obrigatória da SPEC §7.4 (“Após a indexação das
passagens”) e impede que o índice hierárquico requerido em §8.7 exista no
sistema em operação. A nota de que a integração ficaria para T14+ é um desvio
sem aprovação e não pode substituir o entregável de T11.

Correção necessária: integrar o enriquecimento à indexação concluída, ou
oferecer uma operação explícita e documentada que seja acionável/configurada;
testar a rota operacional de ponta a ponta, inclusive falha sem publicação
parcial.

### T11-02 — alto: enriquecimento usa passagens de execuções inativas

`EnrichmentService.enrich()` chama
`PassagesRepository.list_by_edition()` em
`backend/src/rag/application/enrichment.py:114`. O próprio repositório declara
em `backend/src/rag/infrastructure/repositories/passages.py:109` que esse método
retorna passagens de **todas** as execuções, inclusive histórico. Após uma
reindexação, ele pode enviar chunks inativos ao modelo e persistir resumos e
conceitos sustentados por eles.

Isso conflita com a garantia de T06/T08 de que a recuperação corrente usa a
execução ativa e compromete AC-15: o enriquecimento novo deixa de representar o
conjunto indexado corrente. Os testes T11 só indexam uma vez e não cobrem esse
caso.

Correção necessária: resolver a `IndexRun` ativa da edição antes de montar
`PassageRef`s e usar `list_by_index_run()`; adicionar integração com duas
reindexações que prove que nenhuma passagem inativa chega ao provider ou vira
suporte.

### T11-03 — médio: idempotência falha quando todos os summaries são rejeitados

A decisão de idempotência é `has_for_edition_version()` e verifica apenas se
existe ao menos uma linha em `summaries`. Se o provider devolver suporte vazio
em todos os escopos — comportamento explicitamente permitido pelo contrato —
nenhuma summary é persistida. A próxima execução da mesma versão repete todas
as chamadas ao modelo e devolve `created=True`, embora não haja mudança de
versão. O mesmo ocorre se não houver conceitos publicáveis.

O teste de “conceitos vazios” mantém summaries válidos e não exerce esse caso.
Isso viola a idempotência por `(edição, versão)` declarada para T11 e torna
custosa e não reprodutível a reexecução de um resultado sem suporte.

Correção necessária: persistir uma execução de enriquecimento/versionamento
concluída (inclusive sem itens publicados), em transação com os itens, e testar
o caso em que todos os summaries e conceitos são rejeitados.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `uv run pytest -q tests/unit/test_enrichment.py tests/unit/test_enrichment_adapter.py tests/unit/test_model_doubles.py` | OK — 38 passed |
| `uv run pytest -q ... tests/integration/test_enrichment_pipeline.py` | Não executável — 10 erros de setup; Docker/Podman indisponível (`FileNotFoundError` no socket do daemon) |
| `uv run ruff check` nos módulos e testes de T11 | OK |
| `uv run mypy` nos módulos e testes de T11 | OK após a resolução do conflito dos doubles |

## Critérios

- AC-12: falha — a persistência interna impede summary como citação, mas o
  enriquecimento não é produzido pelo fluxo operacional e pode usar histórico
  inativo.
- AC-15: falha — a seleção de passagens não é vinculada à execução ativa e o
  caminho sem itens publicados não é idempotente.

## Conclusão

Há uma base bem estruturada de contratos, validação de suportes e repositórios,
mas os dois achados de severidade bloqueadora/alta impedem concluir T11. A
revisão deve ser refeita após correções e execução das integrações em um
ambiente com PostgreSQL disponível.
