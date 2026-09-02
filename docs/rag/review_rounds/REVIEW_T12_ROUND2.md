# Parecer de revisão — T12, rodada 2

Data: 2026-09-01
Referência: `REVIEW_RESPONSE_T12.md`
Resultado: **reprovado**

## Escopo e evidências

- Alterações revisadas: correções declaradas para T12-01 a T12-03.
- `cd backend && uv run ruff check src tests`: passou.
- `cd backend && uv run ruff format --check src tests`: passou (109 arquivos).
- `cd backend && uv run mypy src tests`: passou (109 arquivos, strict).
- `cd backend && uv run pytest tests/unit -q`: passou (493 passed, 3 skipped).
- Teste manual determinístico de uma referência multipágina válida, início no
  offset 100 da página 10 e fim no offset 3 da página 11: falhou com
  `char_end deve ser > char_start`.
- A integração PostgreSQL declarada na resposta não foi repetida neste ambiente,
  pois o daemon Docker não está disponível.

## Achado

### T12-R2-01 — bloqueador: validator ainda rejeita offsets válidos de passagens multipágina

- Requisito: SPEC §7.3, §9.2 e AC-03. O chunker define `char_start` relativo
  à página inicial e `char_end` relativo à página final para chunks que
  atravessam páginas.
- Evidência: `EvidenceRef._offsets_coherent` e
  `CitablePassage._offsets_coherent` rejeitam incondicionalmente
  `char_end <= char_start` ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/domain/answer.py:88), [context.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/domain/context.py:161)). Essa comparação só é válida quando início e fim estão na mesma página. Para páginas distintas, por exemplo início 100 na página A e fim 3 na B, os offsets são corretos e a reconstrução é possível, mas o modelo falha antes de produzir o `quote`.
- Impacto: a correção T12-01 funciona apenas quando, acidentalmente, o offset
  final da segunda página é maior que o offset inicial da primeira. Chunks
  multipágina comuns continuam impossíveis de citar, portanto AC-03 permanece
  falhando.
- Correção necessária: validar `char_end > char_start` somente quando
  `physical_page == page_end` (ou quando não há informação de página por
  compatibilidade). Para páginas distintas, exigir a ordem das páginas e
  apenas limites não negativos; adicionar testes positivo e negativo para
  `char_start > char_end` em páginas diferentes e na mesma página.

## Avaliação dos demais itens da resposta

- T12-02: a associação `concept_evidence` e a preferência em duas passadas
  atendem materialmente à diversificação por conceito; os testes unitários
  exercitam a alteração de ordem. A redação de que posições sempre ficam
  vazias não descreve literalmente a segunda passada, que readmite repetições
  por relevância, mas isso não é por si uma violação da SPEC.
- T12-03: agora é corretamente descrito como teste estrutural; a prova no
  fluxo completo depende da orquestração futura.

## Conclusão

O bloqueador anterior não está integralmente corrigido. T12 só pode avançar
após a correção de T12-R2-01 e uma execução bem-sucedida do teste de
integração multipágina contra PostgreSQL.
