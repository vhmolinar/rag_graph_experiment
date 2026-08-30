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

### 10.7 Registro do implementador — 2026-08-29 (fase 1, correções de revisão T06)

Correções aplicadas ao corrigir `docs/rag/REVIEW_T06.md` (T6-01 a T6-10).
Superam as interpretações declaradas em §10.6 itens 5 e 7, que se mostraram
insuficientes na revisão.

1. **Reindexação passou a ser versionada por execução, nunca destrutiva
   (T6-01).** Nova tabela `index_runs` (migration `0003`) registra cada
   execução de `rag index` — edição + versões de extração/chunking/embedding/
   endpoint — com no máximo uma execução `is_active` por edição. Passagens de
   execuções antigas nunca são apagadas; `--force` minta uma execução nova
   mesmo que a identidade de versões não tenha mudado, em vez de fazer
   `DELETE` físico. A idempotência agora compara a identidade completa (as
   quatro versões), não a mera existência de qualquer passagem — parâmetros
   novos são executados automaticamente, sem exigir `--force`.
2. **Reextração é validada contra o que `rag ingest` persistiu, sempre
   (T6-02).** Antes de decidir qualquer coisa (inclusive no caminho
   idempotente), `rag index` recompara a reextração com as `Section`/`Page`
   já persistidas — por conteúdo (`text_sha256` recalculado), não apenas por
   chave — e falha fechado (`IngestionError`) em qualquer divergência. Isso
   NÃO exigiu alterar o schema de T03: o campo `text_sha256` já existente em
   `pages` foi suficiente para a comparação de conteúdo. `ExtractionVersion`
   passou a ser efetivamente registrada por `rag index` (a lacuna do item 7
   de §10.6 está corrigida).
3. **`original_text` sobrevive ao chunking (T6-05).** `ChunkNode`/`Passage`
   ganharam `original_text`, reconstituído por concatenação dos blocos
   canônicos de origem (granularidade de bloco — a mesma que o schema de T05
   preserva); `citable_text` passou a priorizar esse campo. `text`
   (normalizado) continua alimentando busca lexical/embeddings.
4. **Adapter de embeddings validado por contrato tipado (T6-03/T6-09).**
   Resposta agora é parseada por modelos Pydantic e reordenada pelo campo
   `index` de cada item (nunca pela ordem bruta de `data`); índices ausentes,
   repetidos ou fora do intervalo, e valores não finitos (NaN/Inf), são
   rejeitados antes de qualquer persistência.
5. **HTTPS exigido com credencial (T6-04).** `EmbeddingEndpointSettings`
   rejeita `api_key` não vazio combinado com `base_url` que não seja
   `https://`.
6. **Lote de embeddings configurável e versionado (T6-06).** Novo
   `EMBEDDING_BATCH_SIZE` (default 64); cada lote é gerado e validado antes
   de qualquer INSERT — uma falha no meio não publica índice parcial. O
   tamanho do lote usado é registrado em `EmbeddingVersion.params` (AC-15).
7. **Chunking configurável via `CHUNKING_*` e opções de CLI (T6-07).**
   `rag index` não fica mais preso aos defaults de `ChunkingParams`.
8. **Identidade de versão mais explícita (T6-08).** Rótulos de
   `ChunkingVersion`/`ExtractionVersion` passaram a incluir um sufixo de
   revisão do algoritmo (`structural-chunker-v1`/`docling-structural-v1`) —
   mudar a heurística exige bumpar o sufixo. `ModelEndpointVersion` passou a
   ser registrada por `rag index`, com o endpoint e uma revisão de modelo
   opcional (`EMBEDDING_MODEL_REVISION`) em `params`.
9. **Concorrência serializada por lock de edição (T6-10).** Um
   `pg_advisory_xact_lock` por `edition_id`, tomado no início da transação de
   `index_edition`, serializa duas indexações concorrentes da mesma edição —
   a segunda observa a execução já ativa em vez de competir por uma
   restrição de unicidade (que deixou de existir sobre o histórico de
   `index_runs` — só a execução ativa é única por edição).

### 10.8 Incidentes de cadeia de suprimentos

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
9. **Correções da rodada de revisão (REVIEW_T07, 2026-08-30).** Sem mudança de
   arquitetura; refinamentos para honrar SPEC §11 e AC-14/AC-16 que a
   implementação anterior não atendia integralmente:

   - **T7-01** — `GenerationEndpointSettings.max_retries` agora é `Literal[0]`
     (não apenas default 0): a geração NUNCA retenta. `POST /chat/completions`
     não é idempotente (retentar pode reexecutar uma geração já processada,
     consumir recursos e produzir resposta diferente). `Literal[0]` rejeita
     `max_retries != 0` na construção (inclusive via `GENERATOR_MAX_RETRIES`),
     garantido por `test_non_zero_max_retries_is_rejected` e
     `test_env_non_zero_max_retries_is_rejected`. Retries continuam permitidos
     para embedding e reranking (idempotentes).
   - **T7-02** — A regra T6-04 (credencial só sobre `https://`) foi centralizada
     em `HttpEndpointSettings` e vale para os três endpoints, incluindo
     generator e reranker. Mensagens de erro não citam chave, URL nem caminho.
   - **T7-03** — A resposta do reranker é validada por Pydantic
     (`_RerankItem`/`_RerankResponse`): finitude do score e cardinalidade
     exata por documento — duplicatas (mesmo para um único documento), lacunas
     e índices fora do intervalo são rejeitados com `ModelResponseError`.
   - **T7-04** — `CircuitBreaker` agora limita o estado half-open a UMA probe
     simultânea (`_probe_in_progress`); chamadas concorrentes durante a probe
     são rejeitadas até o sucesso/falha da tentativa de teste.
   - **T7-05** — Validações de limite (timeout `>0`, `max_retries >=0`,
     `max_concurrency >0`, backoff `>0`, multiplicador `>=1`,
     `circuit_breaker_failure_threshold >=1`, reset `>0`) e de URL/credencial
     passaram a falhar na construção dos settings (`ValidationError`
     previsível), e não depois com erro genérico de `httpx`/`asyncio`.
     `hide_input_in_errors` evita que o `str()` do `ValidationError` ecoe o
     caminho do secret file.
