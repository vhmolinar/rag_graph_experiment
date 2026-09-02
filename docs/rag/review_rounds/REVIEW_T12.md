# Parecer de revisão — T12

Data: 2026-09-01
Resultado: **reprovado**

## Escopo revisado

- Commit/branch: `7ad016773786f908e23b200000f426dfaa3e4745` (`T12`)
- Escopo: montagem de contexto e modo `quote` (T12)
- Ambiente: Linux, Python 3.12.14, uv 0.12.7; Docker daemon indisponível.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `git diff --check HEAD^ HEAD` | passou |
| `cd backend && uv run ruff check src tests` | passou |
| `cd backend && uv run ruff format --check src tests` | passou: 106 arquivos |
| `cd backend && uv run mypy src tests` | passou: 106 arquivos |
| `cd backend && uv run pytest tests/unit -q` | passou: 449 passed, 3 skipped |
| `cd backend && uv run pytest tests/unit/test_chunking.py -k 'multiple_pages or cross' -vv` | passou: comprova que o chunker produz chunks que atravessam páginas |
| `cd backend && uv run pytest tests/integration/test_context_pipeline.py -q` | não executável: 8 erros no setup porque `/var/run/docker.sock` não existe |
| `make lint` | falhou fora do backend: `eslint: command not found` (dependências de `frontend` ausentes) |

## Critérios afetados por T12

| Critério | Situação | Evidência |
|---|---|---|
| AC-03 | **falha** | A referência de `quote` não representa o fim de um trecho multipágina. |
| AC-08 | passa no escopo do serviço | `QuoteResponse` só contém `evidences` e `ContextService` não recebe provider de geração. A integração não pôde ser executada. |
| AC-11 | evidência insuficiente | Há limite flexível por edição, mas não diversificação por conceito. A declaração de limitação pertence a T13. |
| AC-12 | passa no escopo do serviço | `parent_text` não é projetado para `EvidenceRef`; testes unitários cobrem a separação. |
| AC-15 | evidência insuficiente | A política recebe versão própria, porém esta ainda não é registrada em `AnswerRun`; a integração PostgreSQL não pôde rodar. |

Os demais AC-01–AC-02, AC-04–AC-07, AC-09–AC-10 e AC-13–AC-20 não são entregáveis de T12 e não foram reavaliados nesta rodada.

## Achados

### T12-01 — bloqueador: citações multipágina não são reproduzíveis

- Requisito: SPEC §7.3, §9.2 e AC-03 exigem página e offsets corretos para toda passagem citada.
- Evidência: o chunker produz intencionalmente `page_start_index` e `page_end_index` distintos e reconstitui o texto usando ambas as páginas ([chunking.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/domain/chunking.py:216), [test_chunking.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/tests/unit/test_chunking.py:171)). Porém `EvidenceRef` expõe somente `physical_page`, um único `printed_label` e um par de offsets ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/domain/answer.py:59)); `get_citable` lê apenas `page_start_id` ([passages.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/infrastructure/repositories/passages.py:105)). O teste T12 só semeia passagens em uma página e tenta recompor o texto por um único slice ([test_context_pipeline.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/tests/integration/test_context_pipeline.py:394)).
- Impacto: para um chunk que começa na página A e termina na B, `char_end` é relativo à página B, mas o cliente recebe apenas A. Não consegue abrir/destacar/recompor a citação; em geral `page_A[char_start:char_end]` será incorreto. Isso viola um bloqueador do checklist.
- Correção necessária: transportar início e fim da localização (IDs/índices e rótulos de ambas as páginas, com offsets relativos a cada uma), incluir `page_end` no join/projeção e adicionar testes de integração do `quote` para uma passagem multipágina — incluindo reconstrução e destaque. Se o contrato optar por fragmentar citações por página, fazê-lo antes de responder e testar ambos os fragmentos.

### T12-02 — média: diversidade por conceito foi omitida

- Requisito: SPEC §8.6 requer, para consultas comparativas ou conceituais amplas, limite flexível por edição **e** diversificação por conceito.
- Evidência: `QueryPlan` já contém `concept_labels`, mas `select_evidences` recebe somente candidatos, orçamento e `needs_diversity`; sua única diversificação é o contador por `edition_id` ([context.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/src/rag/domain/context.py:239)). Não há associação candidato→conceito nem teste que cubra essa dimensão.
- Impacto: uma resposta conceitual pode consumir todo o orçamento em passagens sobre um único conceito, ainda que conceitos distintos relevantes estejam disponíveis.
- Correção necessária: definir e carregar uma associação rastreável entre passagem e conceito (ou uma estratégia equivalente aprovada), aplicá-la durante a seleção sem quota cega e testar um caso em que a diversificação por conceito altera a seleção.

### T12-03 — baixa: teste de “generator não chamado” não observa o sistema sob teste

- Requisito: T12 pede evidência de que `quote` não chama o generator.
- Evidência: o teste instancia `FakeGeneratorProvider`, mas não o injeta em `_quote`, `ContextService` nem em qualquer dependência; a asserção apenas verifica a lista recém-criada ([test_context_pipeline.py](/data/dev/src/ai-stuff/rag_graph_experiment_T12/backend/tests/integration/test_context_pipeline.py:368)). A ausência estrutural do provider no `ContextService` é uma boa proteção local, mas a asserção não prova a afirmação no fluxo de uma consulta.
- Impacto: a evidência declarada no `EVIDENCE.md` é mais forte que o teste. Uma futura orquestração que chamasse geração antes/depois de `ContextService.quote` não seria detectada.
- Correção necessária: quando a orquestração de consultas existir, injetar um generator que falha ao ser chamado e exercitar o fluxo `quote` completo. Até lá, descrever o teste como verificação estrutural, não como spy de chamadas.

## Riscos residuais e limitações da revisão

- Os oito testes de integração próprios de T12 não puderam ser executados por indisponibilidade do daemon Docker, não por um resultado de teste do projeto.
- A falha de `make lint` decorre de dependências ausentes no frontend, fora do diff T12; o lint Python passou.

## Conclusão

T12 não está pronto para aprovação. A incapacidade de representar a página final de uma passagem multipágina quebra AC-03 e pode produzir uma citação que aponta para o trecho errado. Corrigir T12-01 e executar novamente a integração PostgreSQL são condições mínimas para nova revisão; T12-02 e T12-03 também devem ser tratados ou explicitamente justificados e aprovados.
