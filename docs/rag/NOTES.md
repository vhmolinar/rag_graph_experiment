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
5. **Idempotência por (edição, versão).** Se a edição já tem resumos da
   versão desta execução, a execução é no-op (mesmo espírito de `rag index`
   sem `--force`). A identidade de uma execução é a versão de síntese; toda
   a execução corre numa única transação, então "tem sínteses desta versão"
   implica a execução concluíu — incluindo conceitos (que podem
   legitimamente ser zero em conteúdo). Falha rollbacka tudo, nunca publica
   estado parcial.
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
12. **Sem comando CLI em T11** (não consta nos entregáveis; os comandos de
    fase 1 continuam sendo ingest/ocr/index/inspect). O enriquecimento é
    invocado via `EnrichmentService`; integração no CLI/API fica para T14+
    se a operação o exigir.

### 10.13 Registro do implementador — 2026-08-30 (fase 1, T12)

Interpretações declaradas antes de implementar T12 (montagem de contexto e modo
quote), não bloqueantes; podem ser revistas pelo usuário.

1. **Nova tabela de versão `context_policy_versions` (migration 0003).** A
   política de montagem de contexto (número de evidências, orçamento de
   contexto, expansão parental, limite flexível por edição) afeta a resposta
   (SPEC §2: "toda configuração que afeta uma resposta deve ser versionada";
   AC-15). O schema aprovado (T03) lista seis tipos de versão, mas §6 define
   um MÍNIMO ("devem existir registros para..."), não um teto: adicionar um
   registro de versão novo é aditivo (nova tabela, trigger de imutabilidade
   mesmo padrão), não altera tabelas existentes nem critérios. A tabela é
   gemelha da `retrieval_policy_versions` (T09) e registrada na allowlist do
   `VersionsRepository`; `PackedContext.policy_version_id` expõe o registro
   para `AnswerRun`/T13.
2. **`ContextPolicy`/`ContextBudget` no domínio, mesma convenção de
   `RetrievalPolicy` (T09).** Orçamento por profundidade com valores iniciais
   conservadores e monotonos (brief < standard < deep); calibração no
   benchmark de T19 (NOTES.md §4). Parâmetros: `max_evidences` (número de
   evidências citáveis, SPEC §9.1), `max_context_chars` (orçamento total de
   contexto = evidências + expansão parental), `parent_expansion_chars`
   (máximo de texto parental por evidência, contexto NUNCA citável) e
   `per_edition_limit` (limite flexível por edição; `None` = sem limite).
3. **Seleção de evidências é função pura do domínio (`select_evidences`), não
   chamada ao provedor de geração/reranking.** Ordem preservada do ranking
   reranked (T09); deduplicação por `passage_id`; o orçamento de contexto é
   respeitado durante a seleção (uma evidência que não couber no orçamento
   restante é descartada, nunca estoura) e `PackedContext` impõe
   estruturalmente `total_chars <= context_budget_chars` (falha fechada se
   violado — nunca se devolve contexto acima do orçamento). A expansão
   parental é limitada por evidência (`parent_expansion_chars`), truncada
   como contexto adicional; nunca é texto citável.
4. **Diversidade adaptativa executada na seleção: limite flexível por edição,
   nunca quota cega.** `select_evidences` aplica a capa por edição
   (`per_edition_limit`) SÓ quando `needs_diversity` (comparativa/conceitual
   ampla, SPEC §8.6). "Flexível" = a capa não é uma meta a preencher: se uma
   edição tem MENOS candidatos que o limite, as posições sobrentes NÃO são
   preenchidas com outra obra menos relevante; se todas as edições atingirão
   a capa e o orçamento ainda couber, a seleção PARA (pode ficar abaixo de
   `max_evidences`) — nunca se inclui uma obra menos relevante para atingir
   uma quantidade fixa. Factual/navegacional maximizam relevância sem capa
   por edição.
5. **Modo quote = `QuoteResponse` (domínio T02) construido a partir da
   montagem de contexto; o serviço NÃO tem provedor de geração.** `ContextService.quote`
   delega para `ContextService.assemble` (que nunca chama o provedor de
   geração) e projeta os `EvidenceRef` seleccionados. Ausência de geração é
   estrutural (nenhum `GeneratorProvider` na assinatura) E testada
   ("nenhuma chamada ao generator em quote", T12). O `QuoteResponse` não
   adiciona campos (o teste `test_has_no_prose_fields` de T02 fixa o
   contrato `{"evidences"}`).
6. **Metadados citáveis resolvidos numa única query por passagem
   (`PassagesRepository.get_citable`).** Joins `passages` → `sections`
   (path), `pages` (página física/rótulo impresso da `page_start_id`),
   `editions` → `works` (`work_id`) e a passagem-pai (`parent_passage_id`/
   `parent_text` via self-join). EPUB sem páginas: `physical_page`/
   `printed_label`/offsets ficam nulos (NOTES.md §10.6 item 3). O trecho
   recomposto a partir de páginas e offsets deve reproduzir `text` da
   evidência (AC-03; testado via `PagesRepository.list_by_edition`).
7. **`ContextService` expõe `assemble(...) -> PackedContext` e
   `quote(...) -> QuoteResponse`; o provedor de geração fica FORA do serviço.**
    `PackedContext.evidences` (com `parent_text`) é a entrada natural de T13
    (`GenerationRequest.evidences` = `EvidenceRef` de cada evidência; o texto
    parental, não citável, pode entrar como contexto adicional). Sem
    migration nova além de `context_policy_versions`; sem alteração em
    `runs.py`/`AnswerRun` nesta tarefa (a integração completa de versões na
    `AnswerRun` é T13/T18).

### 10.14 Registro do implementador — 2026-08-30 (fase 1, T13)

Interpretações declaradas antes de implementar T13 (geração dissertativa e
verificação), não bloqueantes; podem ser revistas pelo usuário.

1. **`VerifierProvider` é contrato novo** (`domain/providers.py`), não reuso
   de `GeneratorProvider`. A verificação julga cada par (afirmação,
   evidência) — semântica distinta da geração. `VerificationRequest`/
   `ClaimVerdict`/`VerificationVerdict` definem o contrato; o adapter
   `OpenAiCompatibleVerifierProvider` usa o endpoint compatível com OpenAI
   (`/chat/completions`, JSON mode) e a mesma resiliência de T07.
2. **Existência de IDs é determinística no serviço (função pura do domínio),
   NUNCA do provedor.** `invalid_evidence_ids` compara os IDs citados pelas
   afirmações contra o conjunto de evidências montado (T12). Uma citação
   inexistente é CITAÇÃO FABRICADA (SPEC §9.4 "rejeita IDs inexistentes";
   checklist §20 bloqueador): após o limite de regenerações,
   `VerificationError` — a resposta nunca é liberada com ID inexistente.
3. **Suporte/contradição são semânticas do provedor; a agregação é função
   pura do domínio (`assess_claims`).** Uma afirmação é sustentada SÓ quando
   TODAS as suas evidências citadas receberam veredicto `supported` sem
   contradição; um par ausente do veredicto é tratado como não sustentado
   (conservador, falha fechada — o provedor não pode omitir silenciosamente
   um juízo). Veredictos para pares não informados (afirmação ou evidência
   inexistentes) são ignorados defensivamente.
4. **Correção final após o limite: afirmações não sustentadas/contraditorias
   são MARCadas como inferências (`mark_unsupported_as_inference`).** SPEC
   §9.4: "remove, corrige ou marca inferências sem suporte direto". O serviço
   usa a opção "marca" — AC-09 exige "marcação explícita de inferência"; é
   transformação determinística do `Claim` (`inference=True`), nunca introduz
   conteúdo novo. Se a cobertura ficar abaixo do limiar da política, há
   ABSTENÇÃO forzada (`FORCED_ABSTENTION`) — a resposta abstida não carrega
   afirmações (AC-10).
5. **`VerificationPolicy`/`VerificationBudget` versionados via nova tabela
   `verification_policy_versions` (migration 0004).** Mesmo padrão de T12
   (NOTES.md §10.13 item 1): a política de verificação (iterações e limiar de
   cobertura) afeta a resposta e deve ser versionada (SPEC §2, AC-15).
   Valores iniciais conservadores e monotonos (brief < standard < deep);
   calibração no benchmark de T19 (NOTES.md §4).
6. **Timeout/falha do provedor de verificação falha fechado: nenhuna resposta
   não verificada é liberada.** O serviço envolve a chamada ao provedor e
   devolve `VerificationError` (cause preservado). Falha de GERAÇÃO, pela
   contrário, propagha como `ModelError` tipado (AC-14) — o serviço não a
   mascara.
7. **AC-11: limitação de fonte única é garantia determinística do serviço.**
   Se a intenção for comparativa e as evidências montadas virem de UMA única
   obra, o serviço anexa uma limitação em `GeneratedAnswer.limitations` (se
   ainda não estiver presente). Não depende de o gerador lembrar-se — AC-11 é
   garantido por construção, não por prompt.
8. **Abstenção do gerador é aceita sem verificação (nenhumas afirmações a
   verificar); a verificação forzada é caminho separado.** Se
   `GeneratedAnswer.abstained`, o serviço devolve a resposta abstida com
   `VerificationResult` vazio (`total_claims=0`, `coverage=1.0` vacua, action
   ACCEPTED) e sem chamar o provedor de verificação. A forzada (cobertura <
   limiar após o limite) é caminho distinto (item 4). Ambas produzem abstenção
   (AC-10).
9. **Versões registradas pelo serviço (`PromptVersion` + `ModelEndpointVersion`
    + `VerificationPolicyVersion`).** Prompt de geração e de verificação são
    templates hasheados (mesma convenção de T11); `ModelEndpointVersion` por
    papel (`generator`/`verifier`, kind `generator`). As instruções de
    profundidade continuam vivendo no adapter (conteúdo de prompt do adapter,
    mesmo critério de T11 para o template). `DissertativeAnswer` devolve os
    IDs das versões registradas; a integração completa em `AnswerRun` fica para
    T18 (NOTES.md §10.13 item 7).

### 10.15 Registro do implementador — 2026-08-30 (fase 1, T14)

Interpretações declaradas antes de implementar T14 (API FastAPI e segurança),
não bloqueantes; podem ser revistas pelo usuário.

1. **Execução de consulta: tarefa asyncio em processo, cancelamento
   cooperativo, SSE por cola em memória — conforme NOTES.md §10.2 item 1.**
   `POST /queries` valida, cria o `AnswerRun` (`queued`) e agenda uma tarefa
   asyncio; retorna 202 com `query_id`. O executor verifica o flag de
   cancelamento (`asyncio.Event` por query) ENTRE os estágios (planejamento →
   recuperação → montagem de contexto → geração/verificação). Uma fila
   distribuída foi recusada na fase 1 (NOTES.md §8). A tarefa é encerrada
   (cancelada) no shutdown da aplicação para não vazar execuções órfãns.
2. **`mode` não é columna nova: é derivado do tipo da resposta no `GET
   /queries`.** `AnswerRun.response` é `QuoteResponse` (modo quote) ou
   `GeneratedAnswer` (dissertativo). A API expõe `mode` inferido desse tipo —
   sem migration nova e sem alterar o schema de T03/T13. A integração completa
   do `AnswerRun` (todos os campos/versões de T18) fica para T18.
3. **Sessões mínimas em T14; contexto/reescrita de follow-up é T15.** As
   tabelas `sessions`/`session_entries` já existem (migration 0001, T03).
   `POST/GET/DELETE /sessions` são implementados com um `SessionsRepository`
   mínimo e um modelo `Session` de domínio minimal; `QueryRequest.session_id`
   é validado (404 se a sessão não existir) e persistido no `AnswerRun`
   (columna já existente). A reescrita de follow-up para pergunta autônoma e
   o histórico de sessão são T15 — AC-13 permanece ⬜ até T15, quando também
   os `session_entries` serão alimentados.
4. **Rate limiting: token bucket por IP, implementação própria sem dependência
   nova (NOTES.md §10.2 item 6).** `RATE_LIMIT_PER_MINUTE` (padrão 60). O
   bucket é por endereço IP do cliente; relógio monotónico inyectável para
   testes. Resposta 429 com `Retry-After` e corpo `{"error": {code:
   RATE_LIMITED, ...}}`. Endpoints de health (`/health/live`, `/health/ready`)
   ficam isentos — liveness/readiness não podem ser estrangulados por limiar
   (probes de orquestração).
5. **CORS restrito à origem configurada, sem wildcard e sem credentials.**
   `CORS_ALLOWED_ORIGINS` (lista separada por vírgula; padrão
   `http://localhost:5173`). `allow_credentials=False` (sem cookies/autenticação
   na fase 1). Métodos e headers explícitos.
6. **SSE: implementação própria sobre `StreamingResponse`
   (`text/event-stream`), sem `sse-starlette`** (não está no conjunto aprovado
   de dependências). Um `EventBroker` por query (cola em memória + último
   evento terminal) permite que o stream encerre corretamente em sucesso, erro
   e cancelamento, e que um cliente conectado DEPOIS do fim receba o estado
   terminal. Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
7. **Health endpoints em `/api/v1/health/live` e `/api/v1/health/ready`.**
   Liveness responde 200 sem consultar dependências (nunca causa restart por
   falha transitória externa — checklist §14). Readiness consulta PostgreSQL
   (`SELECT 1` com timeout curto, via `asyncio.wait_for`) e responde 503 se o
   banco estiver indisponível.
8. **Erros: mapa `ErrorCode` → HTTP status; corpo sempre
   `{"error": {code, message, request_id}}` (SPEC §10.1).** `RagError` tipado é
   mapeado (VALIDATION_ERROR→400, NOT_FOUND→404, CONFLICT→409,
   RATE_LIMITED→429, MODEL_TIMEOUT→504, MODEL_UNAVAILABLE→503,
   MODEL_INVALID_RESPONSE→502, EMBEDDING_DIMENSION_MISMATCH→502,
   VERIFICATION_FAILED→502, STORAGE_ERROR/DATABASE_ERROR/INTERNAL_ERROR→500,
   CANCELLED→409). `RequestValidationError` (Pydantic) → 422 com o mesmo
   envelope; exceções inesperadas → 500 `INTERNAL_ERROR` sanitizado. Nenhum
   stack trace, SQL, caminho local ou credencial chega ao cliente; o detalhe
   interno fica nos logs sanitizados com `request_id`.
9. **`rag serve` no CLI** inicia uvicorn com `create_app()` da camada API;
   configuração por ambiente (`POSTGRES_*`, `ARTIFACT_*`, `EMBEDDING_*`,
   `RERANKER_*`, `GENERATOR_*`, `PLANNER_*`, `VERIFIER_*`, `CORS_ALLOWED_ORIGINS`,
   `RATE_LIMIT_PER_MINUTE`). O provedor de planejamento (estratégia `expanded`)
   é criado de `PLANNER_*` como os demais adapters — só é chamado na estratégia
   `expanded`; se o endereço estiver inalcanzável, a falha é fechada (nunca
   se responde sem o enriquecimento pedido). `PLANNER_BASE_URL` vazio → `None`
   (o planejador determinístico continua funcionando sem expansão).
10. **Artifact store I/O síncrono → `asyncio.to_thread` no endpoint `/source`.**
     Confirmado na EVIDENCE.md T04 ("endpoints HTTP usarão asyncio.to_thread se
     necessário (T14/T17)"). Range requests (SPEC §10.2) implementados por
     `ArtifactStore.read_range`/`open_stream`: 206 com `Content-Range` para
     ranges válidos, 416 para ranges inválidos, 200 com stream completo se não
     houver header `Range`.

### 10.16 Registro do implementador — 2026-08-31 (fase 1, T15)

Interpretações declaradas e decisões do usuário antes/duante de implementar T15
(contexto de sessão, AC-13), não bloqueantes; podem ser revistas pelo usuário.

1. **Reescrita determinística no domínio, sem provedor de modelo — decisão do
   usuário (Opção A).** A reescrita de follow-up para pergunta autônoma é
   função pura em `rag.domain.sessions` (`rewrite_follow_up`): referências
   anafóricas (ordinal + substantivo "o segundo autor"; demonstrativo +
   substantivo "essa obra"; demonstrativo puro "isso"/"isto"; pronomes
   "ele"/"ela") são resolvidas contra o histórico da sessão e o catálogo de
   obras/autores. Nunca adivina: uma referência não resolvida fica como estan
   e a pergunta autônoma coincide com a pergunta original. Limitação
   conhecida: a reescrita é por substituição a nível de string — a
   concatenação pode produzir artículos/preposiciones redundantes ("da O
   Ensaio da Memória"); a resolução é correta, a gramática não é refinada.
2. **Contexto = perguntas + projeção truncada das respostas — decisão do
   usuário.** `session_entries` só persiste perguntas; a projeção da resposta
   (`answer_text`, truncada a `MAX_ANSWER_CONTEXT_CHARS`) é montada no
   serviço a partir de `answer_run_id` → `AnswerRun.response` e serve para
   localizar obras/autores mencionados nas respostas (ex.: "o autor de X é
   Nome" introduz o autor no contexto). Nunca é o texto integral.
3. **Histórico limitado = janela de rodadas USADAS na reescrita, não poda de
   armazenamento.** `list_entries(limit=N)` devolve as N rodadas MAIS RECENTES
   (ordinal DESC limit + reversa) em ordem cronológica; `SESSION_HISTORY_LIMIT`
   (padrão 20, calibrable, NOTAS.md §4) configura essa janela. As rodadas
   antigas permanecem no banco até a exclusão da sessão (efêmera).
4. **`AnswerRun.rewritten_query` deixa de ser `plan.semantic_query` (T14) e
   passa a ser a pergunta AUTÔNOMA (AC-13).** O planejador e a geração usan a
   pergunta autônoma (`request.model_copy(update={"question": autonomous})` no
   executor); `rewritten_query` fica `None` quando não há reescrita (a pergunta
   original coincide com a autônoma). `session_entries.rewritten_query`
   registra a mesma pergunta autônoma (ou `None`). A pergunta autônoma é
   inspeccionável via `GET /queries/{id}` (`QueryState.rewritten_query`, campo
   novo).
5. **Contexto de sessão no prompt do gerador (SPEC §9.3).** Além da pergunta
   autônoma, o modo dissertativo recebe `session_context` (projeção truncada
   das rodadas mais recentes dentro de `MAX_SESSION_PROMPT_CONTEXT_CHARS`) —
   bloco "pergunta e contexto da sessão" do SPEC §9.3. O modo `quote` não chama
   o gerador (nenhum contexto de sessão necessário).
6. **Exclusão da sessão remove o histórico (CASCADE).** A tabela `session_entries`
   já tinha `ON DELETE CASCADE` desde T03; `DELETE /sessions/{id}` remove as
   rodadas. `answer_runs.session_id` fica `NULL` (`ON DELETE SET NULL`) — a
   execução permanece rastreável sem a sessão.

### 10.17 Incidente: corregíons de bugs pré-existentes de T14 em `_retrieve`
      (aprovadas pelo usuário em 2026-08-31)

Durante a verificação de T15 (integração contra PostgreSQL real via podman),
o pipeline de consulta de T14 foi exercitado pela primeira vez em ambientes
reais (as testes de integração de T14 nunca rodaram no ambiente do T14 —
EVIDENCE.md T14: "NÃO EXECUTADO neste ambiente — requer Docker"). Dous bugs
latentes foram encontrados e **aprovados pelo usuário para corregir em T15**:

1. **Latências append-only em `_retrieve`.** `AnswerRun.transition` exige que
   os campos append-only (`candidates`, `latencies`) sejam passados
   ACUMULADOS (`incoming[:len(current)] == current`, provado em
   `test_runs.py::test_candidates_are_append_only`). `_retrieve` passava só a
   latência de recuperação quando o run já carregava a latência de
   planejamento → `InvalidTransitionError: Campo 'latencies' é append-only` em
   TODA consulta que chega a recuperação. Corregido: `latencies=(*run.latencies,
   StageLatency(retrieval))`. Impacto: o contrato de SPEC §13.2 (latências por
   estágio) fica respeitado — todos os estágios persistem.
2. **Run obsoleto propagado de `_retrieve`.** `_retrieve` persistia as
   candidatas/latências (incrementando `revision` do CAS) mas devolvia só
   `RetrievalResult`; o executor continuava com o `run` de `_plan` (revisão
   anterior) → `ConcurrencyError: Execução modificada concorrentemente` no
   `_quote`/`_dissertate` (CAS de `save`). Corregido: `_retrieve` devolve
   `(run, retrieval)` e o executor usa a execução atualizada. Sem esse corregir,
   nenhuna consulta quote/dissertativa podía concluir depois da recuperação.

Ambas são corregir de comportamento de T14 (fora do escopo declarado de T15),
mas bloqueaban a verificação da T15 (e toda consulta real). Registradas aqui
como incidente, com evidência reproduzível (`git diff` + integração real).


