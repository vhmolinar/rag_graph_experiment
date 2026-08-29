# Decisões, premissas e itens adiados

Este arquivo registra o contexto que não deve ser redescoberto ou reinterpretado silenciosamente durante a implementação.

## 1. Decisões confirmadas

### Produto

- O acervo e as consultas serão em português.
- A escala inicial é inferior a 1.000 livros.
- Todo o acervo é consultado por padrão.
- O usuário pode incluir ou excluir obras pela interface ou pelo texto da pergunta.
- Filtros inferidos do texto serão exibidos como chips editáveis.
- O contexto de conversa dura somente durante a sessão.
- O sistema responde somente com conhecimento sustentado pelo acervo.

### Respostas

- Há dois modos:
  - `quote`: trechos literais ranqueados, sem inferência;
  - `dissertative`: síntese, referências e verificação.
- O modo dissertativo oferece profundidade breve, padrão e aprofundada.
- Toda resposta dissertativa passa por verificação de afirmações e citações.
- Inferências precisam ser identificadas.
- Falta de evidência exige abstenção.
- Perguntas comparativas devem considerar diversidade entre livros quando houver suporte.

### Recuperação

- Busca lexical e semântica são ambas obrigatórias.
- Estratégias: literal, híbrida, expandida e automática.
- A estratégia automática é o padrão.
- PostgreSQL + pgvector é suficiente para a escala; não haverá vector database separado.
- FTS e busca vetorial produzem rankings independentes, fundidos por RRF e reranqueados.
- Qwen3 Embedding e Qwen3 Reranker são as famílias assumidas, mas os nomes e tamanhos são configuração.
- O tamanho do modelo será decidido por benchmark, não por preferência arquitetural.

### Compreensão de livros

- “Compreensão abstrata” não será atribuída apenas aos embeddings.
- Haverá índice hierárquico de resumos por seção, capítulo e edição.
- Perfis de conceitos e aliases serão ligados às passagens que os sustentam.
- Conceitos serão propostos automaticamente e poderão ser curados futuramente.
- Não haverá banco de grafos na fase 1.
- Resumo não é evidência citável.

### Documentos

- A primeira versão aceita PDF-texto e EPUB.
- OCR é um comando separado, executado manualmente quando necessário.
- Docling é usado atrás de um schema canônico próprio.
- Apenas texto será semanticamente indexado na fase 1.
- Tabelas, diagramas e imagens não serão interpretados.
- Cada edição é armazenada e citada separadamente.
- A citação ideal contém obra, edição, seção, página e trecho destacável.

### Stack

- Backend Python com FastAPI e contratos Pydantic.
- Núcleo tipado próprio, sem LangChain.
- LangGraph só poderá ser proposto posteriormente se houver necessidade comprovada de workflow durável/agentivo.
- Frontend React + TypeScript como SPA.
- PDF.js para leitura.
- Docker Compose em VM Linux.
- Arquivos em volume local endereçado por hash.
- PostgreSQL para domínio, busca textual, vetores e rastreabilidade.
- Geração e embeddings usam endpoints compatíveis com OpenAI.
- Reranking usa endpoint HTTP explícito.

### Operação e dados

- A fase 1 é privada e usada por uma pessoa.
- Autenticação não será improvisada na fase 1.
- Conteúdo de telemetria será anonimizado e retido por 90 dias.
- Consultas normais nunca se tornam contribuições públicas automaticamente.
- Cada resposta registra versões de livros, extração, chunks, embeddings, modelos, prompts, parâmetros e evidências.

## 2. Motivos das principais escolhas

### PostgreSQL + pgvector

Para menos de 1.000 livros, um banco especializado acrescentaria deploy, backup, consistência e observabilidade sem ganho demonstrado. PostgreSQL cobre:

- dados relacionais;
- FTS português;
- busca vetorial;
- filtros;
- rastreabilidade;
- avaliação.

Qdrant ou OpenSearch só devem ser reconsiderados se benchmarks mostrarem limite real de latência, qualidade ou operação.

### Núcleo sem LangChain

O sistema depende de contratos rigorosos de evidência, filtros, versionamento e verificação. Essas regras devem ser visíveis no código e testáveis sem abstrações de chains. Isso não proíbe usar uma biblioteca pequena em um adapter, mas impede que tipos do framework atravessem o domínio.

### Docling delimitado

Docling oferece parsing estrutural e OCR, mas seu formato interno pode mudar. Um schema canônico próprio:

- reduz acoplamento;
- permite trocar o parser;
- facilita golden tests;
- estabiliza offsets e proveniência.

### Índice hierárquico em vez de apenas chunks

Perguntas como “qual é a concepção de liberdade do autor?” exigem localizar argumentos distribuídos. Resumos e conceitos ajudam a localizar regiões relevantes; a resposta continua descendo até as passagens originais antes de citar.

### Verificação obrigatória

Uma resposta fluente com referências não garante que cada referência sustente a afirmação. A segunda etapa é parte do contrato do modo dissertativo, mesmo com custo maior.

## 3. Premissas que o implementador deve validar

- Os endpoints de modelo oferecem capacidade e limites adequados para lotes e concorrência.
- O modelo de embedding escolhido funciona bem em português.
- A dimensão do embedding cabe no índice selecionado e permanece registrada.
- O reranker aceita o tamanho máximo das passagens.
- O gerador consegue produzir saída estruturada com confiabilidade suficiente.
- PDFs de teste permitem extrair offsets úteis para destaque.
- Uma configuração FTS portuguesa atende ao acervo; stemming e `unaccent` precisam de testes reais.
- A VM possui CPU, memória e disco suficientes para PostgreSQL, API e frontend. Modelos não rodam nessa VM.

Quando uma premissa falhar, o agente deve registrar evidência e propor mudança antes de alterar a arquitetura.

## 4. Pontos que devem ser calibrados

Não fixar estes valores como verdades arquiteturais:

- tamanho e sobreposição dos chunks;
- dimensão do embedding;
- quantidade de candidatos lexical e vetorial;
- constante RRF;
- quantidade enviada ao reranker;
- máximo de evidências por resposta;
- limite flexível por livro;
- limiar de abstenção;
- número de iterações de geração/verificação;
- timeouts por modelo e profundidade;
- tolerância trigram;
- tamanho do histórico de sessão.

Todos devem ser configuração versionada e avaliados no benchmark.

## 5. Plataforma colaborativa — adiada

A colaboração não faz parte da fase 1, mas as decisões já tomadas são:

- ficará em página separada do chat;
- usuários serão escolhidos e conhecidos pelo administrador;
- participantes não conhecerão a identidade uns dos outros;
- perguntas e respostas passarão por avaliação cega;
- respostas candidatas poderão ser humanas ou produzidas por versões do RAG;
- itens validados conterão pergunta, escopo, evidências, rubrica, respostas exemplares e condição de abstenção;
- haverá três camadas:
  1. contribuições;
  2. conjunto validado para desenvolvimento;
  3. conjunto privado e imutável para teste;
- aprovação usará múltiplas revisões, reputação por competência e adjudicação de disputas;
- votação simples não será suficiente.

Antes de implementar essa fase, será necessário especificar:

- convites, pseudônimos e recuperação de conta;
- papéis e permissões;
- critérios de reputação;
- prevenção de conluio;
- atribuição e licenciamento das contribuições;
- moderação e recursos;
- privacidade e retenção;
- proteção do conjunto privado;
- critérios de promoção entre as três camadas.

## 6. Autenticação e autorização — adiadas

A fase 1 permanece em rede privada/local. Antes de exposição pública:

- selecionar um provedor OIDC;
- aplicar autenticação no router;
- definir autorização por papéis;
- proteger documentos e endpoints administrativos;
- implementar rate limits por identidade;
- revisar cookies e sessão;
- executar threat model.

Não implementar login e armazenamento de senhas do zero.

## 7. Direitos autorais — pendente

A situação jurídica do acervo ainda não foi definida. Portanto:

- a fase 1 não deve ser publicada;
- `license_status` existe por edição;
- limites de citação/exportação precisam de decisão antes de produção;
- o modo `quote` não deve ser tratado como autorização para reconstruir uma obra;
- uma revisão jurídica deve decidir armazenamento, exibição, download e retenção.

Esta pendência é bloqueadora para produção, não para o protótipo privado acordado.

## 8. Decisões explicitamente recusadas na fase 1

- vector database separado por “modernidade”;
- OpenSearch apenas para FTS;
- LangChain como estrutura central;
- agentes autônomos para responder;
- GraphRAG completo e banco de grafos;
- RAG multimodal;
- conhecimento geral do modelo como fallback;
- memória permanente do usuário;
- ingestão automática complexa;
- fila distribuída sem necessidade demonstrada;
- Kubernetes;
- plataforma colaborativa no mesmo release;
- autenticação própria improvisada.

## 9. Protocolo de alteração

Uma mudança relevante deve incluir:

1. decisão atual;
2. evidência de que ela falhou ou ficou insuficiente;
3. alternativas avaliadas;
4. impacto em dados, API, testes e operação;
5. critérios de migração e rollback;
6. aprovação do responsável.

O agente implementador pode sugerir alterações, mas não aprová-las unilateralmente.

---

## 10. Registro do implementador — 2026-08-28 (fase 1, T01)

Entradas abaixo foram aprovadas pelo usuário na sessão de abertura da implementação ou
registram interpretações declaradas antes do código. Nada nesta seção altera critérios
`AC-*`, a especificação ou o checklist de revisão.

### 10.1 Decisões aprovadas pelo usuário

1. **Conjunto de dependências da fase 1 aprovado** com versões pinadas verificadas nos
   registros oficiais em 2026-08-28 (PyPI, npm, Docker Hub). Inventário vigente sempre
   derivado de `backend/uv.lock` e `frontend/package-lock.json`. Introdução é faseada:
   cada tarefa declara somente o que consome (menor incremento), dentro do conjunto
   aprovado. Conjunto aprovado:
   - Backend runtime: fastapi 0.141.1, uvicorn 0.52.4, pydantic 2.13.5,
     pydantic-settings 2.15.0, psycopg[binary,pool] 3.3.4 + psycopg-pool 3.3.1,
     pgvector 0.5.0, alembic 1.19.1 (migrations em SQL puro; SQLAlchemy entra apenas
     como dependência transitiva de ferramenta, nunca no código da aplicação),
     httpx 0.28.1, typer 0.27.2, structlog 26.1.0, pyyaml 6.0.3, docling 2.123.1,
     prometheus-client 0.26.0, opentelemetry-sdk 1.44.0,
     opentelemetry-instrumentation-fastapi 0.65b0, opentelemetry-instrumentation-httpx
     0.65b0, opentelemetry-exporter-otlp-proto-http 1.44.0.
   - Backend dev/test: pytest 9.1.1, pytest-asyncio 1.4.0, coverage 7.16.0,
     ruff 0.16.5, mypy 2.3.1, types-pyyaml 6.0.12.20260815, pip-audit 2.10.1,
     respx 0.23.1, testcontainers 4.15.0, freezegun 1.5.5.
   - Frontend runtime: react 19.2.8, react-dom 19.2.8, react-router-dom 7.18.3,
     pdfjs-dist 6.2.108, react-markdown 10.1.0, rehype-sanitize 6.0.0.
     Sem axios: cliente usa `fetch`/`EventSource` nativos.
   - Frontend dev: typescript 5.9.3, vite 8.2.2, @vitejs/plugin-react 6.1.1,
     vitest 4.1.11, @testing-library/react 16.3.3, @testing-library/user-event 14.6.6,
     jsdom 30.0.1, @playwright/test 1.62.1, eslint 10.9.1, @eslint/js 10.0.1,
     typescript-eslint 8.68.0, eslint-config-prettier 10.1.8, prettier 3.9.6,
     globals 17.11.0, @types/react 19.2.18, @types/react-dom 19.2.5.
   - Imagens Docker (pinagem final em T20): pgvector/pgvector:0.8.6-pg17-bookworm,
     python:3.12-slim-bookworm, node:22-bookworm-slim, nginx estável.
2. **Gerenciador de dependências Python: uv 0.12.7** (lockfile `uv.lock`, instalação
   reproduzível via `uv sync --frozen`). Decisão registrada conforme exigência da T01.
   Racional: resolução rápida, lockfile multiplataforma, export para `pip-audit`.
3. **TypeScript 5.9.3** (não 7.0.2): ecossistema eslint/vitest mais estável na linha 5.x.
4. **Registry npm:** `frontend/.npmrc` local aponta para `https://registry.npmjs.org/`
   porque o registry global da máquina é um Artifactory privado fora do escopo.
4a. **Desvio aprovado em 2026-08-28 (preparação de T05):** `docling 2.123.1` exige
   `typer >=0.12.5,<0.27.0`, incompatível com o pin aprovado `typer 0.27.2`. O usuário
   aprovou fixar `typer 0.26.8` (maior estável <0.27.0, verificado no PyPI em
   2026-08-28). A alteração ainda **não** foi aplicada a `pyproject.toml`/`uv.lock`
   (parada solicitada ao fim de T04); será efetivada no início de T05.
5. **Contrato de OCR (decisão do usuário, difere da proposta inicial do implementador):**
   - `Edition.source_sha256` identifica a **varredura original imutável** e
     `source_type=pdf_scan` permanece sobre a edição;
   - o PDF com camada de texto produzido por `rag ocr` é persistido como **artefato
     derivado versionado**, com hash próprio e referência ao artefato original;
   - citações endereçam a edição e a identidade de página do original;
   - o leitor pode exibir o derivado OCR para destaque pesquisável, mantendo
     proveniência ao original.
   Impacto no modelo: artefatos derivados precisam de registro próprio (tipo, hash,
   artefato de origem, versão de OCR). Será detalhado em T04/T05.

### 10.2 Interpretações declaradas (não bloqueantes; podem ser revistas pelo usuário)

1. **Processamento assíncrono de consultas:** `POST /queries` retorna 202 com
   `query_id`; execução em tarefa asyncio em processo (escala de um usuário; fila
   distribuída foi recusada na seção 8). SSE publica eventos de estágio; cancelamento
   é cooperativo via flag verificada entre estágios.
2. **Leitor para EPUB:** o leitor é centrado em PDF (PDF.js). Citações de EPUB exibem
   texto da passagem e caminho de seção com aviso explícito de ausência de paginação
   estável; não há renderer de EPUB na fase 1.
3. **Retenção (AC-17):** anonimização é aplicada à telemetria no momento da escrita;
   job agendado remove conteúdo de pergunta/resposta (original e anonimizado) com mais
   de 90 dias, verificável por teste com relógio simulado; artefatos técnicos de
   reprodução (versões, parâmetros) persistem conforme política administrativa.
4. **Formato de erro:** segue o objeto estruturado da especificação §10.1
   (`{"error": {code, message, request_id}}`), que já proíbe stack traces.
5. **Idioma:** interface e erros ao usuário em português; código e identificadores em
   inglês.
6. **Rate limiting:** middleware próprio de token bucket (sem dependência nova),
   calibrável por configuração.
7. **Playwright:** pacote pinado desde T01; download de browsers adiado para T16/T17.

### 10.3 Incidentes de cadeia de suprimentos

Nenhum. Verificado em 2026-08-28: sem `plain-crypto-js`, sem axios 1.14.1/0.30.4 (axios
não é dependência), sem referência a `sfrclak.com`. `make security-scan` automatiza essa
verificação; `make audit` executa `pip-audit --strict` sobre o lockfile exportado e
`npm audit` (0 vulnerabilidades em T01).
