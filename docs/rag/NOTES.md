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
4a. **Desvio aprovado em 2026-08-28 e aplicado em 2026-08-29 (T05):** `docling 2.123.1`
   exige `typer >=0.12.5,<0.27.0`, incompatível com o pin aprovado `typer 0.27.2`. O usuário
   aprovou fixar `typer 0.26.8` (maior estável <0.27.0, verificado no PyPI em
   2026-08-28). Aplicado a `pyproject.toml`/`uv.lock` no início de T05.
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

### 10.3 Registro do implementador — 2026-08-29 (fase 1, correções de revisão T05)

Interpretações declaradas ao corrigir `docs/rag/REVIEW_T05.md` (T5-01 a T5-10),
não bloqueantes; podem ser revistas pelo usuário.

1. **Derivado OCR reimplementado sem nova dependência:** `pdf_writer.py` passou
   a usar `pypdfium2` (já transitiva via Docling) para abrir o PDF original e
   inserir objetos de texto invisíveis diretamente nas páginas existentes, em
   vez de construir um PDF novo do zero. Corrige T5-01 (imagem do original
   preservada) e T5-02 (Unicode via `FPDFText_SetText`, que aceita UTF-16
   nativamente) na mesma mudança, sem adicionar pacote algum.
2. **Proveniência do OCR via sidecar de arquivo:** `rag ocr` grava
   `<output>.provenance.json` (hash de entrada/saída, engine/versão, contagem
   de páginas) ao lado do derivado; `rag ingest` valida esse sidecar antes de
   aceitar `ocr_artifact`. Não há tabela nova no banco para isso — o sidecar
   viaja com o arquivo no disco, como o próprio derivado, até a ingestão.
3. **Notas de rodapé e legendas são texto citável:** `footnote`/`caption`
   deixaram de ser descartados com warning e passaram a `_PARAGRAPH_LABELS`.
   Não há decisão anterior em contrário — a lacuna foi um artefato do mapeamento
   inicial de T05, não uma escolha deliberada.
4. **Warnings de extração persistidos:** novo campo `Edition.extraction_warnings`
   (migration `0002`, `jsonb`) para que `rag inspect` os exiba depois que o
   processo de ingestão termina.
5. **Logs do CLI: JSON, não key-value:** alinhado ao formato usado no resto do
   sistema (SPEC/T18: "logs JSON redigidos"). Traceback nunca vai ao console;
   só é gravado se `RAG_DEBUG_LOG` apontar para um arquivo.

### 10.4 Registro do implementador — 2026-08-29 (fase 1, segunda rodada de revisão T05)

Decisões e interpretações ao corrigir `docs/rag/REVIEW_T05_ROUND2.md`
(R5-01 a R5-11).

1. **`pypdfium2` promovido a dependência direta** (correção R5-09) —
   **aprovado explicitamente pelo usuário** nesta sessão, conforme protocolo
   §9 (o implementador não aprova dependências novas unilateralmente).
   `pyproject.toml` declara `pypdfium2==5.13.0` (mesma versão já resolvida
   transitivamente); lockfile inalterado em conteúdo.
2. **RapidOCR fixado no backend `torch`:** o padrão da própria lib
   (`onnxruntime`) nunca foi dependência do projeto; `torch` já era
   transitiva via Docling. Nenhuma dependência nova; sem essa fixação, o
   e2e opcional falhava com `ImportError: onnxruntime is not installed`.
3. **Granularidade de `engine_version` tem limite documentado:** o modo
   `auto` não é resolvido a um motor concreto no registro de proveniência —
   Docling não expõe publicamente qual motor o modo automático escolheu, e
   introspectar isso dependeria de internals não estáveis entre versões.
   Da mesma forma, versões de binário (Tesseract) e hashes de modelo
   (RapidOCR/ocrmac) não são capturados — o objetivo central do contrato de
   proveniência (impedir associar artefato de outro livro) já é garantido
   pelos hashes de entrada/saída, independente dessa granularidade adicional.
   Recomendação: evitar `--engine auto` quando atribuição exata de motor for
   necessária.
4. **Alinhamento geométrico do texto invisível é parcial, por desenho:**
   `OcrLine.width` ajusta a largura à bbox detectada; a altura continua
   vindo só de `bbox.b`/`bbox.t` sem refinamento adicional (não era a lacuna
   apontada). Alinhamento pixel-perfeito de seleção será validado quando o
   leitor (T17) implementar destaque real sobre o PDF — este momento não é
   o ponto de verificação apropriado para essa garantia mais forte.

### 10.5 Registro do implementador — 2026-08-29 (fase 1, terceira rodada de revisão T05)

Decisões e interpretações ao corrigir `docs/rag/REVIEW_T05_ROUND3.md`
(R6-01 a R6-06).

1. **Proveniência do OCR embutida no PDF, não mais em sidecar** (correção
   R6-01, bloqueadora): a revisão provou que dois `os.replace()`
   consecutivos nunca são atômicos em conjunto. Em vez de um protocolo de
   commit de dois arquivos mais elaborado (manifesto versionado, diretório
   com troca atômica — ambos ainda coordenam dois objetos), a proveniência
   passou a ser um ANEXO dentro do próprio PDF derivado, publicado como um
   único arquivo por uma única troca atômica. `OcrProvenance.output_sha256`
   foi removido — não há mais um segundo artefato com o qual comparar.
2. **`engine_version` renomeado para `adapter_version`** (correção R6-05) —
   **aprovado explicitamente pelo usuário** ao ser questionado entre
   investir mais para resolver `auto`/versões de binário/hashes de modelo,
   ou renomear e documentar o escopo parcial. O nome antigo sugeria uma
   garantia de reprodução completa que o campo nunca ofereceu; o item 3 do
   registro anterior (§10.4) permanece válido em conteúdo, só a nomenclatura
   mudou.
3. **Redação de logging de terceiros é best-effort, não uma garantia geral:**
   `_harden_third_party_logging()` cobre especificamente RapidOCR (logger
   `logging.getLogger("RapidOCR")`) e avisos do Torch (`warnings.warn`) —
   os dois emissores identificados na reprodução da revisão. Uma biblioteca
   nova com um logger de nome desconhecido no futuro não seria coberta
   automaticamente; se isso for observado, adicionar o logger específico à
   função em vez de tentar uma solução totalmente genérica.

### 10.6 Registro do implementador — 2026-08-29 (fase 1, T06)

Interpretações declaradas antes de implementar T06 (chunking e indexação),
não bloqueantes; podem ser revistas pelo usuário.

1. **`rag index` reextrai o documento em vez de reconstruir a partir do
   banco.** T05 persiste apenas `Section` (path/level/páginas) e `Page`
   (texto completo da página) — o detalhe de bloco (parágrafo a parágrafo,
   com `section_path` e offset exatos) do `CanonicalDocument` não sobrevive
   à ingestão. Reconstruir esse detalhe a partir de `Section`+`Page` seria
   ambíguo (uma página pode conter vários parágrafos de seções diferentes).
   `rag index` busca o artefato correto (original para `pdf_text`/`epub`;
   derivado OCR para `pdf_scan`, mesma regra de `_extraction_type` de T05)
   no `ArtifactStore` por hash, reextrai via `DoclingExtractor` e casa o
   `section_path`/`page_index` de cada bloco recuperado com os `Section`/
   `Page` já persistidos (por igualdade de `path` e `physical_index`).
   Nenhuma coluna nova em `sections`/`pages` foi necessária.
2. **Hierarquia pai/filho: pai por seção-folha, sem embedding.** Um
   "parent" cobre uma seção-folha inteira (ou uma janela grande, se a seção
   ultrapassar `parent_target_tokens`); os "filhos" são sub-divisões
   menores dentro de cada pai, na ordem de leitura, cada um referenciando o
   pai via `parent_passage_id`. Só os filhos recebem embedding
   (`embedding_version_id`/`embedding` preenchidos); os pais ficam com
   ambos nulos — o schema de `Passage` já previa isso (`embedding_version_id`
   opcional). Filtrar pais fora da busca vetorial/lexical é responsabilidade
   de T08/T09, não deste registro.
3. **Passagem pode abranger mais de uma página física.** Um chunk não para
   numa quebra de página (o texto de um livro não para). Quando
   `page_start_id != page_end_id`, `char_start` refere-se ao texto de
   `page_start_id` e `char_end` ao texto de `page_end_id`; recompor o trecho
   exige concatenar `page_start.text[char_start:]` + o texto integral de
   páginas intermediárias + `page_end.text[:char_end]`. EPUB não tem
   páginas: `page_start_id`/`page_end_id`/`char_start`/`char_end` ficam
   `None` e o texto citável é o próprio `Passage.text` (T17 já prevê
   comportamento explícito para EPUB sem paginação estável).
4. **Contagem de tokens e sentenças são heurísticas, não um tokenizer
   real.** Nenhuma lib de tokenização (`tiktoken` etc.) está no conjunto de
   dependências aprovado (NOTES.md §10.1). `token_count` usa uma
   aproximação simples (caracteres/4); limites de sentença usam um
   separador por pontuação com guarda para abreviações comuns em
   português. Ambos já estão na lista de "pontos que devem ser calibrados"
   (NOTES.md §4) — o benchmark de T19 é o lugar certo para validar/ajustar,
   não este registro.
5. **`--force` apaga as passagens existentes da edição antes de reindexar.**
   Sem `--force`, uma edição já indexada (tem passagens) é idempotente (não
   faz nada, como `rag ingest`). Com `--force`, as passagens antigas da
   edição são removidas na mesma transação antes de criar as novas. Ainda
   não há `summaries`/`concepts` referenciando passagens (T11), então essa
   remoção é segura hoje; se uma passagem estiver referenciada no futuro, a
   FK (sem CASCADE) fará a remoção falhar fechado em vez de corromper
   silenciosamente — o que é o comportamento correto a preservar quando T11
   existir.
6. **Adapter HTTP de embeddings mínimo construído agora, não adiado
   inteiramente para T07.** "Geração em lote de embeddings" é entregável
   explícito de T06 e todos os testes de T06 (inclusive "dimensão
   inesperada falha antes de persistir") são satisfeitos com um adapter
   real, não só um double. Implementado um cliente HTTP compatível com
   OpenAI (`POST {base_url}/embeddings`) usando `httpx` — dependência já
   aprovada em NOTES.md §10.1 item 1, apenas ainda não consumida — com
   autenticação Bearer via variável de ambiente e mapeamento de erro para
   `ModelTimeoutError`/`ModelUnavailableError`/`ModelResponseError`/
   `EmbeddingDimensionError`. Deliberadamente SEM retries, circuit breaker
   ou limite de concorrência — esses são o valor agregado explícito de T07,
   que deve enriquecer este mesmo adapter, não substituí-lo. `respx`
   (dev, já aprovado) usado para o teste de contrato HTTP.
7. **Nenhum `ExtractionVersion` é criado por `rag index`.** Não existe hoje
   coluna em `sections`/`pages`/`passages` para associá-lo — adicionar uma
   agora seria alterar o schema de T03 (já revisado/aprovado) por um motivo
   fora do escopo declarado de T06. Meramente registrado aqui para
   referência futura (T18, rastreabilidade).

### 10.7 Incidentes de cadeia de suprimentos

Nenhum. Verificado em 2026-08-28: sem `plain-crypto-js`, sem axios 1.14.1/0.30.4 (axios
não é dependência), sem referência a `sfrclak.com`. `make security-scan` automatiza essa
verificação; `make audit` executa `pip-audit --strict` sobre o lockfile exportado e
`npm audit` (0 vulnerabilidades em T01).

### 10.8 Registro do implementador — 2026-08-29 (fase 1, T07)

Interpretações declaradas antes de implementar T07 (adapters HTTP de modelos),
não bloqueantes; podem ser revistas pelo usuário.

1. **Retries, circuit breaker e limite de concorrência: implementação própria,
   sem dependência nova.** Nenhuma lib de retry/circuit-breaker (`tenacity`,
   `pybreaker` etc.) está no conjunto aprovado (NOTES.md §10.1); adicionar uma
   exigiria aprovação fora do escopo. `rag.adapters.resilience` implementa os
   três sobre `asyncio`/`httpx` puros — mesmo espírito do rate limiting de
   token bucket (NOTES.md §10.2 item 6). Retries só cobrem falhas
   transitórias (`ModelTimeoutError`, `ModelUnavailableError`: timeout, conexão
   recusada, 5xx); `RateLimitError` (429) e `ModelResponseError` (payload/
   dimensão inválidos) nunca são retentados automaticamente — não são falhas
   transitórias do endpoint, e sim da requisição ou da resposta recebida.
   Circuit breaker: máquina de 3 estados (closed/open/half-open), abre após N
   falhas consecutivas (`circuit_breaker_failure_threshold`), permite uma
   tentativa de teste após `circuit_breaker_reset_seconds`. Concorrência:
   `asyncio.Semaphore` por instância de provider (`max_concurrency`).
2. **Adapter de embeddings de T06 enriquecido, não substituído.** Conforme
   anunciado em NOTES.md §10.6 item 6: `OpenAiCompatibleEmbeddingProvider`
   ganhou retry/circuit-breaker/concorrência via `call_with_resilience`; o
   contrato HTTP (`POST /embeddings`) e os testes de T06
   (`test_embedding_adapter.py`) não mudaram. Novos testes de resiliência em
   `test_embedding_adapter_resilience.py`.
3. **Autenticação por secret file: convenção `*_API_KEY_FILE`.** SPEC §11 só
   exige "variáveis de ambiente ou secret files", sem prescrever o mecanismo.
   Optou-se por um campo explícito `api_key_file: Path | None` (mixin
   `ModelAuthSettings`, compartilhado pelos três adapters) em vez do
   `secrets_dir` embutido do `pydantic-settings` (que exigiria nomear arquivos
   por convenção implícita e construir os Settings com `_secrets_dir=...` fora
   do padrão já usado em `config.py`). Definir `api_key` E `api_key_file` para
   o mesmo endpoint é erro de configuração (`ValidationError`), não
   precedência silenciosa — falha fechada, no espírito de RagError.
4. **Geração: JSON mode sobre `POST /chat/completions`, não streaming.**
   `response_format={"type":"json_object"}` é suportado pela maioria dos
   servidores compatíveis com OpenAI (vLLM, TGI, llama.cpp server) e permite
   validar `GeneratedAnswer` diretamente via Pydantic, sem parser de texto
   livre. Streaming de geração é responsabilidade de T14 (SSE da API), não
   deste adapter. Os seis blocos do prompt dissertativo (SPEC §9.3) viram uma
   mensagem `system` (política + contrato de saída) e uma mensagem `user` com
   seções delimitadas por cabeçalho markdown (pergunta+contexto, escopo,
   evidências numeradas por `passage_id`, instrução de profundidade) — o
   protocolo chat só tem os papéis `system`/`user`/`assistant`.
5. **Timeout maior para profundidade `deep`: por requisição, não por
   cliente.** `GenerationEndpointSettings.deep_timeout_seconds` é aplicado via
   `timeout=` na chamada `POST` quando `request.depth is Depth.DEEP`; o
   timeout do `httpx.AsyncClient` continua sendo o padrão (`timeout_seconds`)
   para as demais profundidades.
6. **Reranking: contrato HTTP explícito, não "compatível com OpenAI".** A API
   da OpenAI não tem endpoint de reranking. Adotado o contrato difundido entre
   servidores de reranking auto-hospedados (Cohere, Text Embeddings Inference,
   Infinity): `POST {base_url}/rerank` com `{model, query, documents}` e
   resposta `{"results": [{"index", "relevance_score"}, ...]}`. O adapter
   reordena a resposta por `index` antes de devolver, para respeitar o
   contrato do Protocol (`rerank(query, documents) -> list[float]`, pontuação
   por documento, na ordem de `documents`).
7. **Doubles locais vivem em `tests/fixtures/model_doubles.py`, não em
   `src/`.** São instrumentação de teste (para T08+ exercitarem recuperação,
   geração e planejador sem HTTP), não código de produção — mesmo critério já
   aplicado a `tests/fixtures/builders.py` em T05. Determinísticos por hash do
   texto (embedding) ou sobreposição de termos (reranker); aceitam uma fila de
   exceções para simular falha transitória seguida de sucesso.
8. **Confirmação de que chaves e payloads não aparecem em logs (SPEC §11,
   AC-16): garantida por construção, não por redação.** `call_with_resilience`
   só recebe um closure opaco (`operation`) e objetos de exceção — nunca o
   corpo da requisição/resposta dos adapters (textos, prompts, evidências,
   chave). Os únicos logs emitidos pelos adapters (retry/circuit-breaker)
   vêm dessa função e só têm acesso a `operation_name`/`error_type`/`attempt`.
   Testado em `test_resilience.py::test_failure_logs_are_free_of_operation_content`
   com `structlog.testing.capture_logs()`.

### 10.9 Registro do implementador — 2026-08-29 (fase 1, T08)

Interpretações declaradas antes de implementar T08 (busca lexical em
português), não bloqueantes; podem ser revistas pelo usuário.

1. **`LexicalQuery` é estruturada (campos explícitos), não uma mini-
   linguagem de busca em string única.** A especificação (§8.4) pede
   "suportar frase exata, termos obrigatórios e termos excluídos" como
   capacidades, não uma sintaxe (`+termo`/`-termo`/`"frase"`) para o usuário
   digitar. Interpretar a pergunta em linguagem natural e produzir essa
   estrutura é responsabilidade do planejador (T10, que também produz
   `QueryPlan.lexical_query: str` de T02) — fora do escopo declarado de
   T08 (dependências: T03, T06, não T02/T10). `LexicalQuery` (`domain/query.py`)
   tem `phrase: str | None`, `required_terms`/`excluded_terms: tuple[str, ...]`
   e `trigram_threshold: float`, ao lado de `EditionFilter` (já existente
   desde T02) na mesma responsabilidade de "Contratos de consulta".
2. **`required_terms`/`excluded_terms` são obrigatoriamente palavras
   isoladas** (`term.isalnum()`; validado em `LexicalQuery`) — sequências de
   várias palavras pertencem ao campo `phrase`. Não é só estilo: a
   tolerância trigram (item 3) compara cada termo contra cada PALAVRA da
   passagem; um termo multi-palavra compararia sua string inteira
   (incluindo espaços/pontuação) contra palavras isoladas do documento, o
   que quase sempre produz falsos positivos por coincidência de
   subsequência (ex.: o termo composto "ciume | bentinho !" tem
   similaridade trigram de 0.4 contra a palavra isolada "ciume" só por
   conter esse substring, vazando pela tolerância mesmo sem o termo
   "bentinho" estar presente). Restringir a uma palavra por termo elimina
   essa ambiguidade por construção.
3. **Tolerância trigram compara o termo contra cada palavra da passagem
   (`unnest(regexp_split_to_array(...))`), nunca contra o texto inteiro.**
   Tentativa inicial usou `similarity(rag_immutable_unaccent(text), termo)`
   direto (mesmo padrão do teste de plano de consulta de T03,
   `test_index_usage.py::test_trgm_similarity_query_uses_gin_index`) — mas
   esse teste só prova que o índice é utilizável, nunca que a correspondência
   é semanticamente correta. Na prática, a similaridade de Jaccard entre um
   termo curto e um texto de várias frases é diluída pelos trigramas de
   todas as outras palavras e fica sempre baixa, mesmo com o termo presente
   quase idêntico em uma única palavra — tolerância a erro de digitação não
   funcionava em nenhum limiar razoável contra passagens reais (só contra
   frases de teste artificialmente curtas). A comparação palavra-a-palavra
   corrige isso, mas **não usa `passages_text_trgm_gin`** (índice de T03,
   construído sobre o texto inteiro): é sempre um scan sequencial das linhas
   já filtradas pelas demais condições (FTS/filtros). Calibração de
   desempenho em corpora grandes é um ponto para o benchmark de T19
   (NOTES.md §4), não bloqueante para a correção demonstrada aqui.
4. **Passagens-pai nunca são candidatas** (`WHERE p.embedding_version_id IS
   NOT NULL`), conforme anunciado em NOTES.md §10.6 item 2 ("filtrar pais
   fora da busca vetorial/lexical é responsabilidade de T08/T09"). Só
   chunks-filho (unidades citáveis diretas) são recuperáveis.
5. **Nenhuma migration nova.** `text_search` (tsvector gerado) e o índice
   GIN sobre ele já existem desde a migration 0001 (T03); `LexicalSearchRepository`
   só compõe SQL parametrizado sobre schema existente.
6. **Retorno é `list[RankedCandidate]` (já existente em `domain/runs.py`,
   `stage=RankingStage.LEXICAL`), não um tipo novo.** T09 (RRF) consome
   exatamente essa forma para fundir com a lista vetorial — entregar o
   formato de consumo final agora evita um mapeamento redundante depois.
   `rank` é a posição na lista (0-based); `score` é `ts_rank_cd` do tsquery
   combinado (frase && termos obrigatórios) — 0.0 para acertos só por
   tolerância trigram (nenhum lexema exato contribui peso), o que já ordena
   corretamente acertos exatos antes de aproximados sem lógica extra
   (`ORDER BY score DESC, fuzzy_score DESC`, onde `fuzzy_score` desempata
   entre os aproximados pela soma das maiores similaridades por termo).
7. **Nenhuma interpolação de texto do usuário em SQL nem em sintaxe de
    tsquery.** Frase e cada termo são sempre parâmetros ligados
    (`%(nome)s`), processados por `phraseto_tsquery`/`plainto_tsquery` (que
    tratam a entrada como texto puro — nunca interpretam `&`/`|`/`!`/`<->`
    como operador) e por `similarity()`. Os únicos fragmentos de texto
    interpolados na string SQL são nomes de coluna/tabela fixos e chaves de
    parâmetro previsíveis geradas a partir do ÍNDICE da lista
    (`required_0`, `required_1`, ...) — nunca do conteúdo do termo — mesmo
    padrão de `VersionsRepository` (T03). `test_repository_never_interpolates_user_text_into_sql`
    (integração) contorna deliberadamente os validators de domínio
    (`LexicalQuery.model_construct`) para provar essa propriedade no nível
    do repository, não só por rejeição antecipada da validação.

### 10.10 Registro do implementador — 2026-08-30 (fase 1, T09)

Interpretações declaradas antes de implementar T09 (busca vetorial, RRF e
reranking), não bloqueantes; podem ser revistas pelo usuário.

1. **Busca vetorial: cosseno, `score = 1 - distância`.** A métrica padrão da
   especificação (§8.5). `VectorSearchRepository` ordena por
   `p.embedding <=> %(query)s::vector` (distância de cosseno) e expõe
   `score = 1 - distance` (similaridade de cosseno), reaproveitando o índice
   HNSW `passages_embedding_hnsw` (`vector_cosine_ops`, migration 0001).
   Filtros por obra/edição são aplicados no SQL ANTES da seleção (AC-07),
   mesmo padrão de `LexicalSearchRepository` (T08); passagens-pai
   (`embedding_version_id IS NULL`) nunca são candidatas (NOTES.md §10.6
   item 2). A dimensão do vetor de consulta é conferida contra
   `EMBEDDING_COLUMN_DIMENSIONS` antes de consultar (`EmbeddingDimensionError`
   tipado, falha fechada — nunca um `DataError` cru de psycopg).
2. **RRF é função pura do domínio (`fuse_rankings`).** Contribuição de cada
   lista: `1/(k + rank + 1)` com `rank` 0-based (posição na lista); a
   constante `k` é calibrable por profundidade (NOTES.md §4). Ordenação
   determinística: score RRF descendente, desempate por `passage_id`
   ascendente. Sem dependência de framework; testável com casos
   determinísticos isolados.
3. **Orçamento por profundidade (`RetrievalBudget`) versionado via
   `RetrievalPolicyVersion`.** Parâmetros calibráveis por profundidade:
   `lexical_top_k`, `vector_top_k`, `rrf_k` e `rerank_top_n` (SPEC §8.5:
   "top_k, constante RRF e rerank_top_n pertencem à política de
   profundidade"). Valores iniciais conservadores e monotonos por
   profundidade (brief < standard < deep); calibração no benchmark de T19
   (NOTES.md §4). `RetrievalPolicy` exige cobertura das três profundidades
   (sem política parcial silenciosa). `RetrievalService` registra a política
   (`RetrievalPolicyVersion.params` = dump JSON da política) via
   `VersionsRepository.get_or_create` — idempotente, nunca sobrescreve.
4. **`RetrievalService` (application): estágios lexical e vetorial
   independentes, fusão RRF e reranking; falha do reranker NUNCA é
   mascarada.** Os dois estágios são executados em separado (SPEC §8.5
   "recuperar listas lexical e semântica separadamente"), fundidos por RRF e
   re-rankados pelo provider de reranking. Se o reranking falhar (timeout,
   5xx, payload inválido, violação de contrato), o erro tipado do provider
   propagha ao chamador — nunca se devolve a lista fundida como se fora o
   resultado reranked (checklist §9: "Falha do reranker não é mascarada como
   sucesso"). Também nunca se devolve prosa sem evidências.
5. **`RetrievalResult` preserva scores e posições de TODOS os estágios
   (AC-06).** Modelo frozen com `lexical`, `vector`, `fused` e `reranked`;
   `answer_run_candidates()` produce o tuple a persistir em
   `AnswerRun.candidates` (append-only, transições de `runs.py`), mantendo
   os quatro estágios distintos e rastreáveis. A seleção final de evidências
   (número de evidências, montagem de contexto) é responsabilidade de T12,
   não desta tarefa — o orçamento aqui limita candidatos por estágio, não a
   montação de contexto.
6. **Paráfrase em fixture (AC-05) com embedding controlado.** Para provar
   recuperação semântica sem modelo real, o teste injeta embeddings
   determinísticos por conceito (função local do teste): dois textos são
   "paráfrases" quando compartam um conceito (palavras distintas mapeam à
   mesma dimensão). A consulta semântica recupera a passagem-paráfrase mesmo
   sem compartir termos principais, e a busca lexical para os termos da
   consulta não a encontra (evidencia da independência dos estágios). O
   contrato HTTP do provider de embeddings (T06/T07) já é testado em separado
   com respx; aqui interessa a integração vetorial, não o transporte.
7. **Reranking "altera a ordem em caso controlado" com o double
   `FakeRerankerProvider` de T07 (heuristic determinística por sobreposição
   de termos).** Não é um mock que valida contrato: é uma implementação
   determinística do Protocol `RerankerProvider` (NOTES.md §10.8 item 7),
   exercitada em pipeline real (PostgreSQL + RRF). O caso controlado monta
   uma passagem que domina a fusão RRF (alta em ambos os estágios) mas é
   superada no reranking por outra com mais termos da consulta — a inversão
   de ordem é observável e reproduzível.

### 10.11 Registro do implementador — 2026-08-30 (fase 1, T10)

Interpretações declaradas antes de implementar T10 (planejador de consulta),
não bloqueantes; podem ser revistas pelo usuário.

1. **`QueryPlan.lexical_query` passa de `str` para `LexicalQuery`.** O campo
   de T02 era um espaço reservado antes de T08 definir a consulta lexical
   estruturada (`LexicalQuery`); `RetrievalService.retrieve` (T09) já consome
   `LexicalQuery`, e NOTES.md §10.9 item 1 atribui ao planejador "interpretar a
   pergunta e produzir essa estrutura". Um `QueryPlan` com a consulta
   estruturada é directamente executável por `RetrievalService` sem
   reconstrução. Testes de T02 (`test_query.py::TestQueryPlan`) atualizados.
2. **`QueryPlan.justification` é substituida por
   `strategy_explanation: StrategyExplanation`.** T10 exige "explicação
   estruturada da estratégia selecionada" — um string livre não é uma
   explicação estruturada. `StrategyExplanation` (definida em `domain/query.py`)
   registra `requested` (o que o usuário pediu), `chosen` (resolvida),
   `intent_signals` e `rationale` (texto em português). `justification` não é
   usado em nenhum outro lugar além de `QueryPlan`/`test_query.py`.
3. **Classificação de intenção e resolução de estratégia são determinísticas
   no domínio (heurísticas léxicas em português), não chamadas ao modelo.** O
   planejador não depende de um modelo para decidir intenção/estratégia —
   comportamento reproduzível e testável sem rede. A chamada ao modelo (novo
   contrato `PlannerProvider` em `domain/providers.py`) fica reservada à
   "geração limitada de subperguntas/aliases" (estratégia `expanded`) e a uma
   sugestão opcional de `semantic_query`.
4. **`PlannerProvider` é um contrato novo (não reusa `GeneratorProvider`).**
   `GenerationRequest` exige `evidences` (min_length=1) e `scope_description`
   — não cabe a fase de planejamento, que não tem evidências. `PlanningRequest`/
   `PlannedQuery`/`PlannerProvider` definem o contrato; o adapter
   `OpenAiCompatiblePlannerProvider` (`adapters/planner_adapter.py`) usa o
   mesmo contrato HTTP compatível com OpenAI (JSON mode) e a mesma resiliência
   de T07 (`call_with_resilience`).
5. **`needs_diversity`/`needs_hierarchical` derivados da intenção.** factual →
   diversidade falsa (maximiza relevância, SPEC §8.6); comparative/conceituais
   amplas → diversidade verdadeira (limite flexível por edição — a execução de
   montagem de contexto em T12 aplica o limite flexível, nunca uma quota
   cega); conceptual/comparative → `needs_hierarchical` verdadeiro (índice
   hierárquico de T11); factual/navigational → falso.
6. **Filtros naturais: menções sem polaridade clara NÃO são inferidas
   (ambigüidade não é aplicada silenciosamente).** O planejador só infera
   `include_*`/`exclude_*` quando a polaridade é explícita por sinais léxicos
   ("só", "somente", "apenas", "no"(=em o), "na"(=em a), "em", "incluindo",
   "considerando" para inclusão; "exceto", "excepto", "salvo", "menos", "sem",
   "excluindo", "fora de" para exclusão). Uma menção sem sinais de polaridade
   (ex.: "quem escreveu Dom Casmurro?") não entra em `inferred_filters` — não
   se adivina um filtro. Se a mesma obra receber polaridades conflitantes, a
   menção é descartada inteira (ambigua, não aplicada).
7. **Prioridade de filtros explícitos é função pura `merge_filters(explicit,
   inferred)` no domínio.** SPEC §8.2: "entradas inferidas nunca substituem
   filtros explícitos; exclusões explícitas prevalecem sobre inclusões
   inferidas" (e simétrico). A função une os conjuntos e remove do lado oposto
   os IDs decididos explicitamente; o resultado respeita as invariantes de
   `EditionFilter` (nunca o mesmo ID em include e exclude). A chamada à
   `RetrievalService` (futura T12/T13) recebe o filtro efetivo fundido.
8. **Resolução de filtros naturais opera por título de obra (nível obra).** O
   catálogo mapeia título canônico normalizado → `CatalogEntry(work_id, título,
   edition_ids)`; menções com polaridade resolvem a `include_work_ids`/
   `exclude_work_ids`. Os `edition_ids` ficam disponíveis para uma resolução de
   nível edição futura (API/UI) — fora do escopo declarado de T10.
9. **`build_lexical_query` extrai palavras de conteúdo da pergunta
    (português), sem mini-linguagem e sem modelo.** Stopwords, palavras de
    pergunta e substantivos genéricos ("livro", "obra", "capítulo") são
    removidos; as palavras restantes viram `required_terms` (AND) com
    `trigram_threshold` padrão 0.3. É heurística calibrable (NOTES.md §4), não
    um analizador morfológico. Se não sobrar palavras de conteúdo, a consulta
    cai para `phrase` = pergunta inteira (raro corresponder; o estágio
    semântico continua funcionando).

### 10.12 Registro do implementador — 2026-08-30 (fase 1, T11)

Interpretações declaradas antes de implementar T11 (resumos hierárquicos e
conceitos), não bloqueantes; podem ser revistas pelo usuário.

1. **Contrato de enriquecimento próprio (`EnrichmentProvider`), não reuso de
   `GeneratorProvider`.** `GenerationRequest`/`GeneratedAnswer` produzem
   `Claim` (afirmações com evidências) — semântica da resposta dissertativa
   (T13), não da síntese/conceito. `EnrichmentProvider` define
   `summarize(SummaryRequest) -> SummaryResult` e
   `extract_concepts(ConceptExtractRequest) -> ExtractedConcepts`, usando
   `PassageRef` (id + texto + `section_path`) em vez de `EvidenceRef` (que
   carrega score/rank/work_id — conceitos da resposta, não da extração).
2. **`SummaryResult.supporting_passage_ids` PODE ser vazio no contrato do
   provedor.** O modelo pode julgar "sem suporte" (SPEC §7.4: "Se o suporte
   não puder ser identificado, o item abstrato não é publicado no índice") —
   é o SERVIÇO que rejeita (não publica) o item e registra warning. O modelo
   `Summary` do domínio continua exigindo `min_length=1`: um item publicado
   SEMPRE tem suporte. Suportes FORA do escopo (outra seção, outra edição)
   ou IDs de passagens desconhecidas são VIOLAÇÃO DE CONTRATO — falha
   fechada (`ModelResponseError`), nunca publicação silenciosa.
3. **Nenhuna migration nova.** O schema de T03 já tem `summaries`,
   `summary_supports`, `concepts`, `concept_aliases`, `concept_evidence` com
   as FKs compostas e o trigger `chapter = Section de topo` (R4-03).
   `generator_version_id`/`extractor_version_id` referenciam
   `model_endpoint_versions` com `endpoint_kind = 'generator'` — sínteses e
   conceitos são geração via `/chat/completions`.
4. **Versionamento de extração = `ModelEndpointVersion` + `PromptVersion`.**
   Cada execução registra (idempotente via `VersionsRepository.get_or_create`):
   `PromptVersion` por template (síntese e conceitos; identidade = hash do
   template) e `ModelEndpointVersion` por papel (`label='summarizer'` /
   `'concept-extractor'`, `kind='generator'`, `provider='openai-compatible'`,
   `model_name` configurable, `params` com o `prompt_version_id`).
   Reexecução com a MESMA versão é idempotente (no-op); com versão NOVA
   (modelo ou prompt distintos) cria NOVOS registros — histórico nunca é
   sobrescrito (test: "reexecução com nova versão não sobrescreve histórico").
   Não há `--force` em T11: apagar registros antigos violaría a garantia.
5. **Idempotência por (edição, versão), registrada como execução concluída —
   não pela existência de itens.** A identidade de uma execução é a versão de
   síntese (`summarizer_version_id`), registrada em `enrichment_runs` na MESMA
   transação dos itens publicados (correção T11-03). O registro existe MESMO
   quando nenhum item é publicado (todos os suportes rejeitados — suporte
   vazio é comportamento legítimo do provedor, item 2): "tem execução desta
   versão" implica a execução concluíu, e reexecutar a mesma versão é no-op
   reprodutível. Conceitos podem legitimamente ser zero em conteúdo. Falha
   rollbacka tudo (itens e execução), nunca publica estado parcial.
6. **Escopos de suporte validados fechados no serviço.** Resumo de seção:
   suportes ⊆ passagens-filho diretas da seção. Resumo de capítulo (Section
   de topo, level=0): suportes ⊆ passagens-filho das seções DESCENDENTES do
   capítulo ("resumos/trechos filhos", SPEC §7.4). Resumo de edição:
   suportes ⊆ todas as passagens-filho da edição. Conceito: suportes ⊆
   passagens-filho da edição.
7. **Resumos de seção cobrem TODA seção com passagens diretas; resumos de
   capítulo cobrem as seções de topo (level=0) com passagens descendentes.**
   Uma seção de topo com texto próprio pode ter os dois escopos (seção e
   capítulo) — granularidades distintas, redundância aceitável. Seções sem
   passagens não geram resumo (nada a sintetizar).
8. **Recuperação descendente = repositorios, não serviço de busca.**
   `SummariesRepository.supporting_passages(summary_id)` e
   `ConceptsRepository.supporting_passages(concept_id)` devolvem as
   `Passage` originais (texto citável) — nunca o texto da síntese/conceito.
   A resposta final nunca cita summary: `summary_supports` aponta SÓ a
   `passages` (FK composta) e o texto da síntese nunca é um `Passage`
   (AC-12, garantia estrutural + testada).
9. **Conceitos são globais (não por edição).** `concepts.normalized_label` é
   UNIQUE; `get_or_create` por rótulo normalizado. Aliases e evidências
   acumulam; a PK de `concept_evidence` inclui `extractor_version_id`,
   preservando histórico por versão. Estado padrão `proposed` (curatoria
   futura — SPEC §5.1). Confiança de aliases/evidencias: 1.0 (o provedor não
   devolve confiança numérica no contrato desta fase; é campo calibrable).
10. **Prompt templates vivem no serviço de enriquecimento**
    (`application/enrichment.py`) e são transmitidos no request (mesma
    convenção de `GenerationRequest`); o adapter monta as mensagens chat
    (`system` = política+contrato, `user` = escopo+passagens). Hash do
    template → `PromptVersion.template_sha256`. Sem profundidade em sínteses:
    as políticas de profundidade (§9.1) são para respostas dissertativas.
11. **Adapter HTTP único `OpenAiCompatibleEnrichmentProvider`** implementa os
    dois métodos do `EnrichmentProvider` sobre `POST /chat/completions` (JSON
    mode), reaproveitando `call_with_resilience` e
    `ModelAuthSettings`/`ResilienceSettings` (T07); configuração `ENRICHMENT_*`.
    Retries só cobrem falhas transitórias; 4xx/payload inválido falham
    fechados (mesma regra de T07, NOTES.md §10.8 item 1).
12. **Comando `rag enrich <edition-id>`** (correção T11-01). O enriquecimento
    é acionável e configurado pela operação (`EnrichmentService` +
    `OpenAiCompatibleEnrichmentProvider`, env `ENRICHMENT_*`), não só uma API
    interna: `rag ingest` → `rag index` → `rag enrich` produz as sínteses e
    conceitos em operação (SPEC §7.4 "Após a indexação das passagens"). A
    rota operacional é testada ponta a ponta, inclusive falha fechada sem
    publicação parcial. A integração no API fica para T14 se a operação o
    exigir.

## 11. Registro do implementador — 2026-09-01 (fase 1, correções de revisão T10)

Correções aplicadas ao corrigir `docs/rag/review_rounds/REVIEW_T10.md`
(T10-01 a T10-04). Elas complementam o registro §10.11.

1. **Filtro efetivo obrigatório no plano.** `QueryPlan` ganhou
   `effective_filters: EditionFilter`; `PlannerService.plan()` calcula
   `merge_filters(request.explicit_filter(), inferred)`, mantendo
   `inferred_filters` para os chips. A recuperação deve consumir somente o
   filtro efetivo.
2. **`expanded` exige provedor de planejamento.** Sem provedor, a estratégia
   `expanded`, automática ou explícita, falha fechada com
   `ModelUnavailableError`; o plano não declara expansão que não possa executar.
3. **Sinais posicionais `no`/`na`/`em`.** Essas preposições são sinais de
   inclusão apenas quando imediatamente antes da menção à obra; menções
   ambíguas continuam sem filtro inferido.
4. **Segurança do endpoint de planejamento.** `PlannerEndpointSettings` herda
   `HttpEndpointSettings`: Bearer sobre `http://` é recusado; sem credencial,
   `http://` continua permitido.
5. **AC-07 permanece parcial.** A cobertura de T10 vale para planejamento e
   recuperação; o critério global exige a prova ponta a ponta de T13.

## 12. Registro do implementador — 2026-09-01 (fase 1, correções de revisão T11)

Correções aplicadas ao corrigir `docs/rag/review_rounds/REVIEW_T11.md`
(T11-01 a T11-03). Elas complementam o registro §10.12.

1. **Enriquecimento integrado à operação via `rag enrich` (T11-01).** O
   desvio anterior ("integração fica para T14+") é revocado: a rota
   operacional `rag enrich <edition-id>` (SPEC §7.4 "Após a indexação das
   passagens") é acionável/configurada via `EnrichmentService` +
   `OpenAiCompatibleEnrichmentProvider` (env `ENRICHMENT_*`) e testada ponta
   a ponta no CLI, inclusive falha fechada sem publicação parcial.
2. **Enriquecimento opera sobre a execução ativa de indexação (T11-02).**
   `EnrichmentService.enrich()` resolve a `IndexRun` ativa da edição e usa
   `PassagesRepository.list_by_index_run()` — nunca `list_by_edition()` (que
   inclui histórico). Sínteses e conceitos representam o conjunto indexado
   corrente; passagens de execuções inativas nunca chegam ao provedor nem
   viram suporte. Integração com duas reindexações prova essa garantia.
3. **Idempotência por execução de enriquecimento, não por existência de
   itens (T11-03).** Nova migration `0005` cria `enrichment_runs`; a execução
   concluída (inclusive sem itens publicados) é registrada na MESMA transação
   dos itens. Reexecutar a mesma identidade é no-op reprodutível; a identidade
   NOVA acumula histórico sem sobrescrever (AC-15).
4. **`index_run_id` integra a identidade de `enrichment_runs` (R2-T11-01).** A
   chave de idempotência de uma execução de enriquecimento é
   `(edition_id, index_run_id, summarizer_version_id)`: `index_run_id` — o
   conjunto de passagens efetivamente enviado ao provedor — NÃO pode ficar de
   fora, ou reindexar a edição com o MESMO modelo de enriquecimento seria um
   no-op e as sínteses continuariam sustentadas por chunks inativos (viola
   T11-02, SPEC §7.4/§8.7, AC-15). Reindexar minta uma nova execução de
   enriquecimento sobre o conjunto corrente; execuções anteriores ficam
   preservadas como histórico. Migration `0005` inclui a coluna `index_run_id`
    (FK composta `(index_run_id, edition_id) → index_runs(id, edition_id)`) e a
    unicidade por `(edition_id, index_run_id, summarizer_version_id)`.

## 13. Registro do implementador — 2026-08-30 (fase 1, T12)

1. **Política de contexto versionada.** `ContextPolicy`/`ContextBudget`
   definem orçamento por profundidade, expansão parental e limite flexível por
   edição. A política é registrada como `ContextPolicyVersion` imutável.
2. **Seleção pura de evidências.** `select_evidences` preserva ranking,
   deduplica por passagem, respeita orçamento e aplica limite flexível por
   edição somente quando o plano requer diversidade. Expansão parental é
   contexto adicional, nunca evidência citável.
3. **Modo `quote` sem geração.** `ContextService.quote` projeta somente
   `EvidenceRef` do contexto montado e não recebe provider de geração.

## 14. Registro do implementador — 2026-09-01 (fase 1, correções de revisão T12)

Correções aplicadas ao corrigir `docs/rag/review_rounds/REVIEW_T12.md`
(T12-01 a T12-03). Elas complementam o registro §13.

1. **Citações multipágina transportan início e fim da localização (T12-01,
   AC-03).** `CitablePassage`/`EvidenceRef` passaram a expor `page_start_id`/
   `page_end_id`, `physical_page`/`page_end` (índices físicos) e
   `printed_label`/`printed_end_label` (rótulos impressos) de AMBAS as
   páginas, com `char_start` relativo à página de início e `char_end`
   relativo à página de fim — mesmo contrato do chunker (NOTES.md §10.6 item
   3). `PassagesRepository.get_citable` inclui o JOIN da página de fim
   (`pend`) e a projeção dos novos campos. Integração nova
   (`test_quote_multipage_passage_reproduces_text`) prova que a abertura da
   origem reproduz o trecho exato e que os offsets destacam dentro de cada
   página.
2. **Diversificação por conceito (T12-02, SPEC §8.6).** A associação
   rastreável passagem→conceito já existe no banco (`concept_evidence` +
   `concepts`, T11); `get_citable` a carrega (`CitablePassage.concepts`) e
   `select_evidences` a aplica quando `needs_diversity`: candidatos que
   traem um conceito ainda não seleccionado são preferidos sobre os que
   repetem conceitos já cobertos, numa segunda passada na ordem do ranking.
   NUNCA se impõe quota cega por conceito (um conceito com poucos candidatos
   não se preenche). Testes: unitários em `test_context.py` (a diversificação
   altera a seleção) e integração
   `test_concept_diversity_changes_selection_in_pipeline` (conceito
   "liberdade" em a0/a1, "destino" em b0 → a ordem passa de [a1,a0,b0] para
   [a1,b0,a0]).
3. **Teste de "generator não chamado" reformulado como verificação
   estrutural (T12-03).** `test_quote_never_calls_generator` instanciava
   `FakeGeneratorProvider` sem o injetar — a asserção não observava o sistema
   sob teste. `test_quote_has_no_generation_path` agora verifica
   estruturalmente que nem `ContextService.quote` nem `assemble` aceitam
   provedor de geração, e que a resposta contém só trechos literais do
   acervo. Quando a orquestração de consultas existir (T13), injetar um
   generator que falhe ao ser chamado e exercitar o fluxo `quote` completo.
4. **Bug preexistente do seed de integração corrigido.** O seed de
   `test_context_pipeline.py` criava uma `EmbeddingVersion` distinta da do
   provedor de recuperação (`ConceptEmbeddingProvider.embedding_version`); o
   estágio vetorial filtra por `embedding_version_id`, resultando em zero
   candidatos vetoriais e as asserções de ordem falhando. O seed passou a
   usar `provider.embedding_version` (mesmo padrão de
   `test_retrieval_pipeline.py`).
5. **Validator de offsets corregido para passagens multipágina (T12-R2-01,
   AC-03).** A comparação `char_end > char_start` só é válida quando ambos os
   offsets referem à MESMA página; para uma passagem multipágina, `char_start`
   é relativo à página de início e `char_end` à página de fim (NOTES.md
   §10.6 item 3) e podem ser invertidos entre páginas (ex.: início no offset
   100 da página A, fim no offset 3 da página B). `EvidenceRef`,
   `CitablePassage` e `Passage` (domínio) passaram a aplicar a comparação
   somente quando não há páginas distintas; a constraint CHECK
   `passages_check1` do banco recebe a mesma condição via migration nova
   `0007` (`char_end > char_start` só quando `page_start_id = page_end_id`
   ou sem páginas). Testes positivos e negativos adicionados nos três
   modelos e a integração multipágina passou a usar offsets invertidos
   (`char_start=30` na página 0, `char_end=13` na página 1).

## 15. Registro do implementador — 2026-08-30 (fase 1, T13)

Interpretações declaradas antes de implementar T13 (geração dissertativa e
verificação), não bloqueantes; podem ser revistas pelo usuário.

1. **`VerifierProvider` é contrato novo** (`domain/providers.py`), distinto de
   `GeneratorProvider`; julga cada par afirmação/evidência via
   `VerificationRequest`, `ClaimVerdict` e `VerificationVerdict`.
2. **IDs de evidência são verificados deterministicamente no serviço.**
   `invalid_evidence_ids` compara IDs citados com as evidências montadas. Uma
   citação inexistente nunca é liberada; após o limite de regenerações, há
   `VerificationError`.
3. **Suporte e contradição pertencem ao provedor; a agregação é pura.**
   `assess_claims` exige veredicto sustentado para cada par citado e trata par
   ausente como não sustentado, falhando fechado.
4. **Correção final usa marcação de inferência.** Após o limite, claims não
   sustentadas são marcadas por `mark_unsupported_as_inference`; cobertura
   abaixo do limiar força abstenção sem claims.
5. **Política de verificação é versionada.** `VerificationPolicy`/
   `VerificationBudget` são persistidos em `verification_policy_versions` pela
   migration `0008`, reencadeada após as migrations T12 para preservar uma
   única sequência Alembic.
6. **Falha ou timeout do verificador falha fechado.** O serviço retorna
   `VerificationError`; falha do gerador preserva o `ModelError` tipado.
7. **Comparativas de fonte única recebem limitação determinística.** Quando a
   intenção é comparativa e o contexto possui uma obra, o serviço acrescenta a
   limitação sem depender do gerador.
8. **A abstenção do gerador é um caminho separado.** Sem claims para julgar,
   retorna `VerificationResult` vazio; abstenção por cobertura insuficiente é
   forçada pelo serviço.
9. **Versões registradas:** prompts de geração/verificação, endpoints de
    generator/verifier e política de verificação. `DissertativeAnswer` expõe os
    IDs; a integração completa em `AnswerRun` pertence a T18.

## 16. Registro do implementador — 2026-09-02 (fase 1, correções de revisão T13)

Correções aplicadas ao corrigir `docs/rag/review_rounds/REVIEW_T13.md`
(T13-01 a T13-03), depois do usuário aprovar a via de correção de T13-03.
Elas complementam o registro §15.

1. **A abstenção NUNCA carrega prosa factual (T13-01, AC-10).** Em
   `GeneratedAnswer`, uma resposta abstida exige `answer_markdown` vazio,
   sem blocos e sem limitações — uma "abstenção" com Markdown não vazio é
   rejeitada por construção (falha fechada no adapter via
   `ModelResponseError`). Em `DissertativeService.answer`, quando o gerador
   declara abstenção, o serviço substitui a saída pela forma canônica
   (`_abstained_answer(_ABSTENTION_REASON)`): nem o `abstention_reason` do
   gerador atravessa o caminho de abstenção.
2. **Contradição sempre prevalece sobre `supported` (T13-02, AC-09).**
   `assess_claims` tornou um par não sustentado quando o veredicto tem
   `contradiction=true`, INDEPENDENTEMENTE de `supported`. Um veredicto
   `supported=true, contradiction=true` (combinação permitida pelo schema)
   já não mantém a afirmação factual: entra em `unsupported_claim_ids` e é
   marcada como inferência no caminho de correção.
3. **O Markdown entregue fica ligado às afirmações verificadas via blocos
   (T13-03, AC-09).** `GeneratedAnswer` ganhou `blocks: tuple[AnswerBlock]`,
   onde `AnswerBlock` é `text` + `claim_id` opcional. Invariantes:
   `answer_markdown == concat(block.text)` (nenhuna prosa factual fora dos
   blocos), cada bloco de afirmação corresponde verbatim a uma `Claim`, e
   cada `Claim` aparece como bloco. A escolha entre as duas vias propostas
   pela revisão (blocos/IDs vs. extração determinística) foi aprovada
   explicitamente pelo usuário (blocos/IDs). Sem migration: `GeneratedAnswer`
   é conteúdo JSONB de `answer_runs.response`, não coluna relacional.
4. **Contrato de geração atualizado para exigir blocos.** O
   `_GENERATION_OUTPUT_CONTRACT` do prompt dissertativo instrui o modelo a
   devolver `blocks` cuja concatenação reproduz exactamente `answer_markdown`,
   com `claim_id` para trechos que são afirmações. As respostas do gerador
   continuam validadas como `GeneratedAnswer` (falha fechada se violar o
   contrato).

## 17. Registro do implementador — 2026-09-02 (fase 1, rodada 2 de revisão T13)

Correção aplicada ao corrigir `docs/rag/review_rounds/REVIEW_T13_ROUND2.md`
(T13-R2-01, crítico — bypass de prosa factual em bloco sem claim_id).

1. **Bloco sem `claim_id` é restrito a tokens estruturais (T13-R2-01).** Em
   `answer.py`, `_is_structural_text(text)` exige que um bloco nulo NÃO
   contenga caracteres alfabéticos (`not any(ch.isalpha() for ch in text)`):
   só whitespace, pontuação, símbolos e dígitos — sintaxe de Markdown
   controlada pelo servidor. Todo texto natural do modelo deve ser um bloco
   de afirmação idêntico a uma `Claim`. O reprodutor da revisão
   ("Marte tem duas luas." em bloco nulo) é rejeitado por construção
   (AC-09; checklist §12). O contrato de geração foi atualizado em
   consequência.
2. **Defensa em profundidade no serviço (T13-R2-01).**
   `DissertativeService._revalidate_answer` revalida o contrato
   `GeneratedAnswer` do gerador antes de qualquer entrega
   (`ModelResponseError` em violação) — mesmo um provedor que contornasse os
   validators não pode entregar prosa factual fora das claims.
3. **Testes adversariais adicionados:** domínio (reprodutor da revisão,
   prosa conectiva, tokens estruturais permitidos), serviço (injeção via
   `model_construct` → `ModelResponseError`, nada entregue) e adapter de
   geração (payload com prosa factual em bloco nulo → `ModelResponseError`).

## 18. Registro do implementador — 2026-09-02 (fase 1, rodada 3 de revisão T13)

Correção aplicada ao corrigir `docs/rag/review_rounds/REVIEW_T13_ROUND3.md`
(T13-R3-01, crítico — conteúdo numérico em bloco sem claim_id).

1. **Bloco sem `claim_id` é restrito a whitespace puro (T13-R3-01).** A
   regra "ausência de letras" da rodada 2 permitia números/datas/quantidades
   (" 2024") como "estruturais". `_is_structural_text` foi substituída por
   `_is_whitespace_text(text) = text.isspace()`: um bloco nulo só pode
   contener whitespace (separadores/parágrafos inseridos pelo renderer).
   NINGÚN conteúdo semântico do modelo — texto, números, datas, quantidades,
   URLs, emoji ou símbolos — é permitido fora de uma `Claim` verificada
   (AC-09; checklist §12). O reprodutor da rodada 3 é rejeitado por
   construção. A defensa em profundidade do serviço
   (`_revalidate_answer`) usa o mesmo validator.
2. **Contrato de geração atualizado** para declarar `claim_id=null` SÓ para
   whitespace.
3. **Testes adversariais de números/datas/quantidades adicionados**:
   domínio (parametrizado: ano, quantidade, porcentagem, data, URL), serviço
   (injeção via `model_construct` com " 2024" → `ModelResponseError`) e
   adapter de geração (payload " 2024" → `ModelResponseError`).

## 19. Registro do implementador — 2026-09-02 (fase 1, revisão consolidada T13)

Correções aplicadas ao corrigir `docs/rag/review_rounds/REVIEW_T13_CONSOLIDATED.md`
(T13-FULL-01, T13-FULL-02, T13-FULL-03). As vias de correção foram aprovadas
explicitamente pelo usuário nesta sessão: limitações derivadas no serviço
(T13-FULL-01) e atualização de `transformers` (T13-FULL-03).

1. **`limitations` sai do contrato do gerador (T13-FULL-01, AC-09).**
   `GeneratedAnswer` deixou de ter o campo `limitations`: o modelo NÃO pode
   contribuir prosa factual por esse canal. As limitações são derivadas
   DETERMINISTICAMENTE pelo serviço (`DissertativeService._limitations`, a
   partir de condições — hoje: AC-11 fonte única) e entregues em
   `DissertativeAnswer.limitations`. O contrato de geração declara que não
   existe campo `limitations`. O campo extra do modelo é ignorado no adapter
   (nenhuna prosa entregue) — coberto no domínio, no adapter e no serviço.
2. **A saída do verificador fica reduzida a IDs, flags e códigos
   (T13-FULL-02, SPEC §9.4).** `ClaimVerdict.detail` foi removido: o provedor
   não pode introduzir prosa factual. `assess_claims` renderiza uma descrição
   FIXA e não factual (`_CONTRADICTION_DETAIL`) na `Contradiction.detail`.
   O contrato de verificação declara que não há campo `detail`; texto extra do
   verificador é ignorado (nenhuna prosa exposta nem persistida).
3. **`transformers` fixada em 5.10.4 (T13-FULL-03, aprovado).** A revisão
   achou `CVE-2026-9856` em `transformers 5.8.1` (transitiva via docling;
   versão-fixa 5.10.0). `5.10.0` está yanked em PyPI ("pushed from a week old
   main branch"); pinada `5.10.4` (patch mais recente não yanked que inclui a
   mesma correção) via `[tool.uv].constraint-dependencies`. Como
   `docling-core[chunking]` capa `transformers<5.9.0` em darwin, a resolução
   foi limitada ao ambiente-alvo da fase 1 (`environments =
   ["sys_platform == 'linux'"]`, SPEC §1: "Docker Compose em uma VM Linux");
   `make audit` passou sem vulnerabilidades. O lockfile removou pacotes de
   outros plataformas (colorama, pywin32, tzdata) por desenho da limitação.

## 20. Registro do implementador — 2026-08-30 (fase 1, T14)

1. **Consultas são tarefas asyncio no processo, com SSE e cancelamento
   cooperativo.** `POST /queries` retorna 202 e um `query_id`; o executor
   verifica o cancelamento entre planejamento, recuperação, contexto e
   geração/verificação. Fila distribuída permanece fora da fase 1.
2. **Sessões em T14 são somente CRUD e validação de identidade.** Reescrita de
   follow-up e histórico são responsabilidade de T15.
3. **Rate limit é token bucket por IP, CORS usa origem configurada sem
   credentials e os endpoints de health são isentos.** A implementação não
   introduz dependência adicional.
4. **A API usa envelope de erro tipado, headers de segurança, request IDs,
   readiness dependente de PostgreSQL e liveness sem dependências externas.**
5. **`rag serve` inicia uvicorn com `create_app()` e `/source` usa
   `asyncio.to_thread` para I/O de artefatos e ranges HTTP.**

## 21. Registro do implementador — 2026-09-02 (fase 1, correções de revisão T14)

1. **Ordem real da pilha de middlewares (T14-01).** Em Starlette,
   `add_middleware()` insere no início da lista e `build_middleware_stack()`
   a inverte ao empilhar: o ÚLTIMO registrado fica mais externo. A ordem
   desejada (externo→interno) `RequestId → SecurityHeaders → RateLimit → CORS`
   exige registar na ordem inversa. `install_security()` foi corrigida e
   documentada; a reprodução da revisão (429 sem `X-Request-ID`/headers e com
   `request_id: "desconhecido"`) agora entrega `X-Request-ID`, todos os
   headers de segurança e corpo com o MESMO ID do header.
2. **`request_id` persistido no `AnswerRun` (T14-02).** A columna `request_id`
   (migration `0010`) é setada na criação pela rota `POST /queries` (a partir
   do `request.state.request_id`), é imutável por transição, e o envelope de
   erro terminal (`GET /queries/{id}` e evento SSE `result`) o devolve — nunca
   vazio no caminho HTTP.
3. **Limite de crescimento dos buckets do rate limiter (T14-03).**
   `RateLimitMiddleware` expira buckets inativos (`bucket_ttl_seconds`, coleta
   periódica) e limita a cardinalidade (`max_buckets`, desalojo LRU); relógio
   inyectável.
4. **Cadeia de migrations linearizada.** A revisão `0005` era duplicada por
   conflito de merge (`0005_enrichment_runs` de T11 vs
   `0005_answer_runs_error_message` de T14). A migração do erro-message foi
   renumerada `0009` (down `0008`) e `request_id` fica `0010` (down `0009`).
5. **`AnswerRun` criado sincronamente na rota.** `POST /queries` cria o run
   (status `queued`) antes de devolver 202; o executor comeza da transição
   `queued → running`. Elimina a corrida POST→GET (404 transitório) assinalada
   como risco residual na revisão e alinha o contrato: o run existe logo após
   o 202.
6. **Harness de integração da API.** `build()` injeta `FakePlannerProvider`
   por padrão (o default de produção aponta para `localhost:8003`) e o seed usa
   a `embedding_version` do provedor — a busca vetorial filtra por
   `embedding_version_id` e o versão hardcoded do seed excluía a passagem.
7. **`RetrievalService.retrieve` exige `run` (fundido de T09/T13).** O
   executor injeta o run, que o serviço persiste (candidatos/versões); o
   executor recarrega o registro para continuar e registra a latência do
   estágio append-only. Sem esta correção, `make typecheck` falhava e o fluxo
   de consulta quebrava na integração.
8. **Teste de integração SSE com `httpx.ASGITransport`.** `client.stream()`
   com ASGI fica disponível só quando a aplicação conclúu o corpo; o teste
   agora lê até o evento terminal (`result`), que a rota encerra sempre
   entregue (subscrição ativa ou replay de terminal). Os eventos de estágio
   são cobertos a nível do broker (`test_api_events.py`).

9. **CORS externo ao rate limiter (T14-R2-01).** A pilha desejada é
   `RequestId → SecurityHeaders → CORS → RateLimit` (externo→interno): o
   `CORSMiddleware` fica ENTRE os headers de segurança e o rate limiter. A
   resposta 429 direta do limiter agora atravessa CORS e expõe
   `Access-Control-Allow-Origin` para a origem permitida — o SPA pode ler o
   corpo e `Retry-After` (SPEC §14, AC-18). O preflight OPTIONS é resolvido
   por CORS antes do limiter (não consome tokens). Registrados na ordem
   inversa (o último registrado é o mais externo em Starlette): RateLimit,
   CORS, SecurityHeaders, RequestId.
