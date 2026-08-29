# Tarefas de implementação — fase 1

Este documento é o contrato de trabalho do agente implementador. A ordem considera dependências. Uma tarefa só está concluída quando código, testes, documentação e evidências de aceitação estiverem presentes.

## Regras para o implementador

- Não alterar decisões arquiteturais silenciosamente.
- Registrar dúvidas e desvios propostos em `docs/rag/NOTES.md` antes de implementá-los.
- Não adicionar dependências sem aprovação; usar versões verificadas e pinadas.
- Não implementar itens marcados como fora da fase 1.
- Manter commits e alterações pequenos o suficiente para revisão.
- Associar testes aos critérios `AC-*` da especificação.
- Não declarar uma tarefa pronta com testes ignorados, mocks que não exercitam o contrato ou demonstração exclusivamente manual.

## T01 — Estrutura do projeto e qualidade básica

Dependências: nenhuma.

Entregáveis:

- estrutura separada para backend Python e frontend React/TypeScript;
- configuração de lint, formatação, type checking e testes;
- comandos uniformes em `Makefile` ou equivalente;
- `.env.example` sem segredos;
- `.gitignore` para ambientes, segredos, PDFs, artefatos, estado e caches;
- decisão registrada sobre gerenciadores de dependência.

Testes/evidências:

- instalação reproduzível;
- lint e type checking passam;
- teste mínimo de backend e frontend passa.

Definição de pronto:

- `make lint`, `make typecheck` e `make test` ou equivalentes estão documentados e funcionam.

## T02 — Modelo de domínio e contratos

Dependências: T01.

Entregáveis:

- entidades e value objects da seção 5 da especificação;
- enums de modo, profundidade, estratégia, intenção e status;
- contratos de providers de modelos;
- requests e responses independentes do framework;
- validações de invariantes, inclusive edições, evidências e versões.

Testes/evidências:

- testes unitários para invariantes;
- serialização e rejeição de valores inválidos;
- prova de que o domínio não importa FastAPI, ORM ou SDK de modelo.

Critérios: AC-02, AC-08, AC-09, AC-15.

## T03 — PostgreSQL, pgvector e migrations

Dependências: T02.

Entregáveis:

- schema relacional completo;
- migrations versionadas;
- extensões `vector`, `unaccent` e `pg_trgm`;
- índices FTS e vetorial;
- constraints de integridade e unicidade;
- repositories com queries parametrizadas;
- estratégia de compatibilidade entre dimensão e versão de embedding.

Testes/evidências:

- migration sobe banco vazio e rollback é testado quando seguro;
- integração CRUD para obra, edição, passagem, versões e execução;
- duplicidade por hash é rejeitada;
- plano de consulta demonstra uso dos índices em dataset de teste.

Critérios: AC-01, AC-02, AC-06, AC-15.

## T04 — Armazenamento local de artefatos

Dependências: T02.

Entregáveis:

- store por SHA-256 em volume configurável;
- gravação atômica;
- validação de tipo e tamanho;
- proteção contra path traversal;
- leitura com ranges;
- metadados associados à edição;
- política de arquivos temporários e limpeza em falha.

Testes/evidências:

- o mesmo conteúdo não é duplicado;
- hash divergente falha;
- nomes maliciosos não escapam do diretório;
- range requests retornam os bytes corretos.

Critérios: AC-01, AC-03.

## T05 — Representação canônica e adapters Docling

Dependências: T02, T04.

Entregáveis:

- schema canônico próprio;
- adapter para PDF-texto e EPUB;
- adapter separado para OCR de PDF escaneado;
- preservação de seção, página, ordem e offsets;
- warnings e relatório de inspeção;
- comandos `rag ingest`, `rag ocr` e `rag inspect`;
- `--dry-run` e idempotência.

Testes/evidências:

- fixtures pequenas de PDF-texto, EPUB e PDF escaneado;
- golden files do schema canônico;
- erro parcial não publica edição;
- reexecução idempotente.

Critérios: AC-01, AC-02, AC-03.

## T06 — Chunking e indexação

Dependências: T03, T05.

Entregáveis:

- chunker estrutural e configurável;
- associação exata entre chunk e origem;
- cabeçalho contextual separado do texto citável;
- relações filho/pai;
- persistência de `ChunkingVersion`;
- geração em lote de embeddings;
- comando `rag index`.

Testes/evidências:

- chunks não cruzam capítulos;
- frases não são cortadas sem necessidade;
- offsets recompõem o trecho original;
- mudança de parâmetros cria nova versão;
- dimensão inesperada do embedding falha antes de persistir.

Critérios: AC-03, AC-12, AC-15.

## T07 — Adapters HTTP de modelos

Dependências: T01, T02.

Entregáveis:

- adapter compatível com OpenAI para embeddings;
- adapter compatível com OpenAI para geração;
- contrato explícito para reranking;
- autenticação por ambiente/secret file;
- timeouts, retries transitórios, circuit breaker e limites de concorrência;
- respostas estruturadas validadas;
- doubles locais para testes.

Testes/evidências:

- contract tests com servidor HTTP simulado;
- timeout, 429, 5xx, payload inválido e dimensão inválida;
- confirmação de que chaves e payloads não aparecem em logs.

Critérios: AC-14, AC-16.

## T08 — Busca lexical em português

Dependências: T03, T06.

Entregáveis:

- FTS português;
- normalização de acentos;
- frase exata;
- termos obrigatórios e excluídos;
- tolerância trigram configurável;
- filtros por obra e edição.

Testes/evidências:

- corpus em português com acentos, flexões e erros de digitação;
- exclusões exercitadas no SQL;
- queries parametrizadas verificadas em integração.

Critérios: AC-04, AC-07.

## T09 — Busca vetorial, RRF e reranking

Dependências: T06, T07, T08.

Entregáveis:

- busca por cosseno;
- recuperação independente lexical/vetorial;
- fusão RRF;
- reranking de candidatos;
- scores e posições persistidos;
- filtros aplicados antes de todos os rankings;
- políticas de orçamento por profundidade.

Testes/evidências:

- paráfrases recuperadas em fixture;
- cálculo RRF validado por casos determinísticos;
- reranker altera ordem em caso controlado;
- obra excluída não chega ao reranker.

Critérios: AC-05, AC-06, AC-07.

## T10 — Planejador de consulta

Dependências: T07, T09.

Entregáveis:

- classificação de intenção;
- estratégias literal, híbrida, expandida e automática;
- geração limitada de subperguntas/aliases;
- resolução de filtros naturais;
- prioridade de filtros explícitos;
- diversidade adaptativa;
- explicação estruturada da estratégia selecionada.

Testes/evidências:

- perguntas factuais, conceituais, comparativas e navegacionais;
- inclusão/exclusão ambígua não é aplicada silenciosamente;
- factual privilegia relevância;
- comparativa busca cobertura sem inserir fonte irrelevante.

Critérios: AC-07, AC-11, AC-13.

## T11 — Resumos hierárquicos e conceitos

Dependências: T06, T07.

Entregáveis:

- summaries de seção, capítulo e edição;
- lista obrigatória de passagens de suporte;
- conceitos, aliases e evidências;
- estados de proposta;
- recuperação descendente de resumo/conceito até passagens;
- versionamento de extração.

Testes/evidências:

- resumo sem suporte é rejeitado;
- resposta final nunca cita summary;
- conceito leva às passagens originais;
- reexecução com nova versão não sobrescreve histórico.

Critérios: AC-12, AC-15.

## T12 — Montagem de contexto e modo quote

Dependências: T09, T10, T11.

Entregáveis:

- context packing por profundidade;
- deduplicação e expansão parental;
- preservação de diversidade adaptativa;
- response tipada do modo `quote`;
- referências de página e offsets;
- ausência total de geração de prosa nesse modo.

Testes/evidências:

- snapshots com trechos e metadados;
- nenhuma chamada ao generator em `quote`;
- abertura da origem reproduz o texto;
- orçamento de contexto não é excedido.

Critérios: AC-03, AC-08, AC-11, AC-12.

## T13 — Geração dissertativa e verificação

Dependências: T07, T12.

Entregáveis:

- prompt estruturado com evidências numeradas;
- políticas brief, standard e deep;
- schema `GeneratedAnswer`;
- verificação de existência e suporte das citações;
- detecção de contradição;
- correção/regeneração com limite;
- abstenção;
- nenhuma utilização de conhecimento externo como fallback.

Testes/evidências:

- generator simulado tenta citar ID inexistente;
- afirmação não sustentada é removida ou marcada;
- pergunta sem resposta gera abstenção;
- timeout do verifier não libera resposta não verificada;
- respostas comparativas declaram fonte ausente.

Critérios: AC-09, AC-10, AC-11, AC-14.

## T14 — API FastAPI e segurança

Dependências: T03, T04, T10, T12, T13.

Entregáveis:

- endpoints `/api/v1` especificados;
- validação Pydantic;
- error middleware;
- SSE e cancelamento;
- rate limiting por token bucket ou sliding window;
- CORS restrito;
- security headers;
- logs com request IDs;
- readiness e liveness.

Testes/evidências:

- OpenAPI validada;
- requests inválidos recebem 4xx tipado;
- rate limit retorna 429 e `Retry-After`;
- erro interno não expõe detalhes;
- cancelamento interrompe trabalho;
- range requests do PDF funcionam.

Critérios: AC-03, AC-13, AC-14, AC-18.

## T15 — Contexto de sessão

Dependências: T10, T14.

Entregáveis:

- sessões efêmeras;
- histórico limitado;
- reescrita de follow-up para pergunta autônoma;
- registro da pergunta original e autônoma;
- exclusão de sessão.

Testes/evidências:

- “compare isso com o segundo autor” resolve referências da sessão;
- contexto de outra sessão nunca é usado;
- exclusão remove o histórico conforme política.

Critérios: AC-13.

## T16 — Frontend de consulta

Dependências: T14, T15.

Entregáveis:

- SPA React/TypeScript;
- chat e streaming;
- modos, profundidade e estratégia;
- filtros explícitos;
- chips editáveis para filtros inferidos;
- progresso, cancelamento, erro e abstenção;
- lista de fontes;
- sanitização de Markdown.

Testes/evidências:

- testes de componentes e integração;
- filtros inferidos podem ser removidos e reenviados;
- erros e cancelamento têm estado acessível;
- nenhum HTML não confiável é executado.

Critérios: AC-07, AC-08, AC-10, AC-13, AC-18.

## T17 — Leitor e destaque

Dependências: T04, T14, T16.

Entregáveis:

- integração PDF.js;
- abertura na página física;
- destaque por offsets/coordenadas;
- metadados de edição e seção;
- comportamento explícito para EPUB sem página estável.

Testes/evidências:

- teste end-to-end de citação até o PDF;
- edição correta quando há duas versões da obra;
- fallback acessível quando o destaque exato não é possível.

Critérios: AC-02, AC-03.

## T18 — Rastreabilidade e observabilidade

Dependências: T07, T09, T13, T14.

Entregáveis:

- `AnswerRun` completo;
- logs JSON redigidos;
- métricas da especificação;
- traces OpenTelemetry sem conteúdo textual;
- job de anonimização e expiração em 90 dias.

Testes/evidências:

- uma resposta pode ser reconstruída a partir das versões registradas;
- scanner de logs não encontra tokens, chaves ou conteúdo integral;
- relógio simulado comprova expiração;
- traces correlacionam API, SQL e modelos.

Critérios: AC-06, AC-15, AC-16, AC-17.

## T19 — Benchmark e runner de avaliação

Dependências: T09, T13, T18.

Entregáveis:

- schema de item de avaliação;
- fixtures cobrindo todos os tipos obrigatórios;
- runner versionado;
- métricas de recuperação, resposta e desempenho;
- comparação entre duas configurações;
- relatório persistido e imutável.

Testes/evidências:

- execução determinística com providers simulados;
- resultados anteriores não são sobrescritos;
- regressão artificial é detectada;
- avaliação de abstenção e filtros está incluída.

Critérios: AC-04 a AC-13, AC-19.

## T20 — Docker Compose e documentação operacional

Dependências: T14, T16, T18, T19.

Entregáveis:

- serviços de proxy, SPA, API e PostgreSQL/pgvector;
- volumes persistentes;
- health checks;
- configuração por ambiente;
- guia de inicialização, ingestão, backup e restauração;
- nenhuma credencial em arquivos versionados.

Testes/evidências:

- ambiente sobe do zero;
- smoke test ingere fixture, consulta e abre citação;
- reinício preserva banco e artefatos;
- backup e restauração são exercitados em dados de teste.

Critérios: AC-20.

## Gate final da fase 1

O implementador entrega:

1. matriz de `AC-01` a `AC-20` com links para testes/evidências;
2. saída dos comandos de lint, typecheck, testes unitários, integração e E2E;
3. relatório do benchmark;
4. lista de desvios aprovados;
5. inventário de dependências e respectivas versões;
6. instruções reproduzíveis para iniciar e testar o sistema.

Sem esses itens, a implementação não está pronta para julgamento.
