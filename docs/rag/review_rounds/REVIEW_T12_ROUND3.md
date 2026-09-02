# Parecer de revisão — T12, rodada 3

Data: 2026-09-02
Referência: `REVIEW_RESPONSE_T12_ROUND2.md`
Resultado: **aprovado com ressalvas**

## Escopo e evidências

- Escopo: correção de T12-R2-01 e regressões dos achados T12-01 a T12-03.
- `cd backend && uv run ruff check src tests`: passou.
- `cd backend && uv run ruff format --check src tests`: passou (109 arquivos).
- `cd backend && uv run mypy src tests`: passou (109 arquivos, strict).
- `cd backend && uv run pytest tests/unit -q`: passou (500 passed, 3 skipped).
- Inspeção da migration `0007`: a regra PostgreSQL agora permite offsets
  invertidos somente quando `page_start_id` e `page_end_id` são distintos;
  preserva a regra estrita para página única ou ausência de páginas.
- `cd backend && uv run pytest tests/integration/test_context_pipeline.py -q`:
  não executável neste ambiente: não há daemon Docker em
  `/var/run/docker.sock`; os 10 erros ocorrem no setup do testcontainer, antes
  de qualquer teste do projeto.

## Avaliação

- **T12-R2-01: passa.** `EvidenceRef`, `CitablePassage` e `Passage` agora
  condicionam `char_end > char_start` à mesma página; todos conservam a
  validação da ordem física da página quando ela está disponível. A migration
  alinha a constraint do banco ao contrato do domínio. Os testes positivos e
  negativos cobrem offsets invertidos entre páginas e inversão inválida na
  mesma página.
- **T12-01: passa no código revisado.** O contrato transporta IDs, índices,
  rótulos e offsets de início/fim, e o teste de integração reconstrói uma
  passagem multipágina com offsets invertidos.
- **T12-02: passa no código revisado.** `concept_evidence` é carregado de
  forma rastreável e a seleção prioriza conceitos ainda não cobertos sem
  substituir a ordenação por uma quota rígida.
- **T12-03: passa como evidência estrutural.** A resposta deixou de apresentar
  um spy não conectado como prova de execução; o teste verifica corretamente
  que `ContextService` não aceita provider de geração. A prova end-to-end fica
  para a orquestração de consultas.

## Ressalva

Não foi possível repetir localmente a integração PostgreSQL/migration por
limitação do ambiente de revisão. O código e os testes específicos foram
inspecionados, e a resposta registra uma execução externa de 10 testes T12
contra PostgreSQL real. Uma revisão em ambiente com Docker/Podman deve repetir
esse comando antes do gate final da fase 1.

## Conclusão

O bloqueador T12-R2-01 está corrigido. Não restam achados bloqueadores no
escopo de T12; a aprovação fica com ressalva exclusivamente pela integração
não reproduzida neste ambiente.
