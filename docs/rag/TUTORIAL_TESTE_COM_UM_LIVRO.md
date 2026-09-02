# Tutorial — testar o RAG com um livro, do arquivo à resposta

Este tutorial descreve como exercitar o estado atual das tarefas T01–T14 com
um livro em português. O objetivo não é apenas “fazer funcionar”, mas observar
cada transformação:

```text
arquivo
  → validação
  → extração canônica
  → persistência
  → chunking
  → embeddings
  → enriquecimento
  → recuperação
  → contexto
  → quote ou dissertative
  → verificação
  → resposta HTTP
```

Use uma obra em domínio público, licenciada ou para a qual você tenha
autorização. O sistema permanece privado/local e não deve ser exposto
publicamente.

## 1. Limites do estado atual

Antes de começar:

1. O backend está restrito a Linux no lockfile atual. Em macOS, `uv sync` e
   `uv run` falham. Use uma VM Linux até existir o fluxo de desenvolvimento
   em container planejado na revisão consolidada.
2. T20 ainda não foi implementada. Não há um `docker compose up` completo para
   API, banco, frontend e modelos.
3. É necessário fornecer:
   - PostgreSQL 17 com pgvector;
   - endpoint OpenAI-compatible para embeddings;
   - endpoint HTTP de reranking;
   - endpoint OpenAI-compatible para geração/verificação/planejamento e,
     opcionalmente, enriquecimento.
4. Sem endpoints de modelos, ainda é possível testar ingestão e inspeção, mas
   não indexação nem consultas completas.
5. O parecer vigente está em
   `docs/rag/reviews/REVIEW_T01_T14_CONSOLIDATED.md`. Alguns comportamentos
   observáveis são falhas conhecidas:
   - `literal` ainda percorre o pipeline vetorial;
   - aliases e subperguntas de `expanded` não alteram a recuperação;
   - summaries/conceitos ainda não participam das consultas;
   - limitações comparativas são perdidas na API;
   - inferências sem evidência podem escapar do julgamento semântico;
   - chunks parciais de EPUB podem perder fidelidade ao texto original.

Esses pontos devem virar casos de teste, não expectativas aceitas.

## 2. Escolher o primeiro livro

Para o primeiro ciclo, prefira:

- idioma português;
- PDF com camada de texto;
- poucas páginas;
- estrutura simples de capítulos;
- texto que você possa conferir manualmente;
- ao menos uma frase distintiva para busca literal;
- ao menos um conceito que possa ser perguntado por paráfrase.

Deixe EPUB e PDF escaneado para a segunda rodada. Eles acrescentam,
respectivamente, incerteza de fidelidade textual e OCR.

### 2.1 Checklist manual do arquivo

Abra o PDF e confirme:

- o texto pode ser selecionado e copiado;
- a ordem de leitura é correta;
- as páginas não são apenas imagens;
- títulos e parágrafos aparecem como texto;
- você conhece a numeração física e a numeração impressa;
- existe uma frase curta que possa ser conferida literalmente.

Se o texto não puder ser selecionado, trate o arquivo como `pdf_scan` e siga a
seção de OCR mais adiante.

## 3. Preparar um ambiente Linux

Versões esperadas:

- Linux;
- Python `>=3.12,<3.13`;
- uv `0.12.7`;
- Docker;
- Git.

Clone o repositório e entre nele:

```bash
git clone <URL-DO-REPOSITORIO>
cd studies
git switch main
git pull --ff-only
```

Confira a revisão:

```bash
git rev-parse --short HEAD
git status --short
```

O status deve estar limpo. Instale de forma reproduzível:

```bash
make setup
make lock
```

Antes de usar dados reais, execute os gates que não dependem do banco:

```bash
make lint
make format-check
make typecheck
make test
make test-contract
make security-scan
make audit
```

Observe:

- todos os comandos terminam com código zero;
- nenhum teste crítico é ignorado;
- os skips de OCR/Docling real são contabilizados;
- o lockfile não muda;
- não surge nenhum arquivo `.env`, PDF ou segredo no `git status`.

## 4. Iniciar PostgreSQL + pgvector

O exemplo usa a imagem aprovada para a fase 1. A senha é gerada na sessão e
não é escrita no repositório.

```bash
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=ragbooks
export POSTGRES_USER=ragbooks
export POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Crie o volume e o container:

```bash
docker volume create ragbooks_pgdata
docker run --name ragbooks-postgres \
  --detach \
  --publish 127.0.0.1:5432:5432 \
  --env POSTGRES_DB="$POSTGRES_DB" \
  --env POSTGRES_USER="$POSTGRES_USER" \
  --env POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  --volume ragbooks_pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:0.8.6-pg17-bookworm
```

Espere o banco ficar pronto:

```bash
until docker exec ragbooks-postgres \
  pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
do
  sleep 1
done
```

### 4.1 O que observar

- a porta está vinculada a `127.0.0.1`, não a todas as interfaces;
- o health do PostgreSQL converge;
- os dados ficam no volume `ragbooks_pgdata`;
- nenhuma senha foi colocada em arquivo versionado;
- o container usa PostgreSQL com a extensão pgvector disponível.

## 5. Aplicar as migrations

Alembic usa `RAG_DATABASE_URL`. Exporte-a apenas na sessão:

```bash
export RAG_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
```

Execute:

```bash
cd backend
uv run alembic -c alembic.ini upgrade head
uv run alembic -c alembic.ini current
cd ..
```

O revision atual deve ser o head único.

Verifique extensões sem imprimir credenciais:

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT extname FROM pg_extension WHERE extname IN ('vector','unaccent','pg_trgm') ORDER BY extname;"
```

Esperado:

```text
pg_trgm
unaccent
vector
```

### 5.1 O que observar

- migration sobe um banco vazio sem intervenção manual;
- há um único head Alembic;
- as três extensões existem;
- erros não exibem senha;
- nenhuma tabela precisa ser criada manualmente.

## 6. Configurar armazenamento local

Use um diretório fora da árvore Git:

```bash
export ARTIFACT_ROOT="$HOME/.local/share/ragbooks/artifacts"
mkdir -p "$ARTIFACT_ROOT"
```

Guarde o livro em outro diretório privado:

```bash
mkdir -p "$HOME/rag-test-input"
```

Não copie livros para dentro do repositório. Mesmo com `.gitignore`, manter o
acervo separado reduz o risco de commit acidental.

### 6.1 O que observar

- o store cria objetos endereçados por SHA-256;
- repetir o mesmo conteúdo não cria outra cópia;
- temporários não permanecem após sucesso;
- logs mostram IDs/hashes e nomes sanitizados, não o texto integral.

## 7. Criar os metadados

Crie `"$HOME/rag-test-input/livro.yaml"`:

```yaml
title: "Título da edição testada"
authors:
  - "Nome do autor"
original_title: null
publisher: null
publication_year: null
isbn: null
edition_label: "teste local"
license_status: unknown
language: pt
source_type: pdf_text
```

Valores de `license_status` aceitos:

- `unknown`;
- `public_domain`;
- `licensed`;
- `restricted`.

Use `public_domain` ou `licensed` apenas quando isso for verdadeiro.

### 7.1 O que observar

- `title` identifica esta edição;
- autores estão corretos;
- `language` é `pt`;
- `source_type` corresponde ao arquivo;
- metadados de edições diferentes não são artificialmente igualados;
- não há caminho, token ou credencial no YAML.

## 8. Executar o dry-run

Defina os caminhos:

```bash
export BOOK_FILE="$HOME/rag-test-input/livro.pdf"
export BOOK_METADATA="$HOME/rag-test-input/livro.yaml"
```

No diretório `backend`:

```bash
cd backend
uv run rag ingest "$BOOK_FILE" --metadata "$BOOK_METADATA" --dry-run
cd ..
```

O comando deve:

- validar extensão e tipo;
- validar metadados;
- calcular hash;
- extrair a representação canônica;
- emitir contagens e warnings;
- não persistir obra, edição, seções ou páginas.

### 8.1 Confirmar que o dry-run não persistiu

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) AS editions_after_dry_run FROM editions;"
```

Em um banco novo, o valor esperado é zero.

### 8.2 O que observar

- quantidade de páginas aproxima-se do visualizador;
- títulos foram reconhecidos como estrutura;
- blocos não estão zerados;
- warnings sobre tabelas, figuras ou labels desconhecidos são compreensíveis;
- PDF sem texto falha e orienta usar OCR;
- código de saída é diferente de zero em falha;
- nenhum conteúdo integral aparece no console.

Não prossiga se:

- páginas estiverem ausentes;
- a ordem de leitura estiver claramente errada;
- o arquivo for scan, mas estiver declarado como `pdf_text`;
- warnings mostrarem perda estrutural incompatível com o teste.

## 9. Persistir a ingestão

```bash
cd backend
uv run rag ingest "$BOOK_FILE" --metadata "$BOOK_METADATA"
cd ..
```

A saída contém:

- `edição=<UUID>`;
- `obra=<UUID>`;
- tipo;
- quantidade de seções;
- páginas;
- blocos.

Copie o UUID retornado:

```bash
export EDITION_ID="<UUID-DA-EDICAO>"
```

O UUID não é segredo, mas deve corresponder exatamente à saída.

### 9.1 Inspecionar imediatamente

```bash
cd backend
uv run rag inspect "$EDITION_ID"
cd ..
```

Antes da indexação, é normal haver zero passagens.

### 9.2 Testar idempotência

Execute novamente:

```bash
cd backend
uv run rag ingest "$BOOK_FILE" --metadata "$BOOK_METADATA"
cd ..
```

Esperado:

- saída indica edição existente;
- o mesmo `edition_id` é retornado;
- não surgem páginas/seções duplicadas.

Confira:

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v edition_id="$EDITION_ID" \
  -c "SELECT id, source_type, ingestion_status, source_sha256 FROM editions WHERE id = :'edition_id'::uuid;"
```

### 9.3 O que observar

- hash do original permanece estável;
- warnings do dry-run reaparecem persistidos no `inspect`;
- página física começa em índice zero internamente;
- duas edições do mesmo livro devem receber IDs distintos;
- alterar metadados imutáveis e repetir a mesma fonte deve gerar conflito,
  não sobrescrever silenciosamente.

## 10. Configurar o endpoint de embeddings

O endpoint deve aceitar:

```text
POST {EMBEDDING_BASE_URL}/embeddings
```

com contrato OpenAI-compatible e devolver vetores de 1024 dimensões.

Para um endpoint local sem autenticação:

```bash
export EMBEDDING_BASE_URL="http://127.0.0.1:8001/v1"
export EMBEDDING_MODEL="qwen3-embedding"
export EMBEDDING_MODEL_REVISION="<REVISAO-IMUTAVEL-SE-DISPONIVEL>"
unset EMBEDDING_API_KEY
unset EMBEDDING_API_KEY_FILE
```

Para endpoint remoto autenticado:

```bash
export EMBEDDING_BASE_URL="https://<HOST-SEGURO>/v1"
export EMBEDDING_API_KEY_FILE="/caminho/privado/embedding_api_key"
unset EMBEDDING_API_KEY
```

Não defina chave Bearer em endpoint `http://`; a configuração rejeita isso.

### 10.1 Smoke test do contrato

Se `jq` estiver disponível:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Content-Type: application/json" \
  --data "{\"model\":\"${EMBEDDING_MODEL}\",\"input\":[\"teste em português\"]}" \
  "${EMBEDDING_BASE_URL}/embeddings" \
  | jq '.data[0].embedding | length'
```

Esperado: `1024`.

Se houver autenticação, prefira que o endpoint esteja atrás de um secret file
consumido pela aplicação; evite colocar a chave diretamente no comando, no
histórico do shell ou em logs.

## 11. Indexar a edição

Use inicialmente os defaults versionados:

```bash
cd backend
uv run rag index "$EDITION_ID"
cd ..
```

A saída deve informar:

- `execução=<UUID>`;
- quantidade de pais;
- quantidade de filhos;
- `chunking_version`;
- `embedding_version`.

Inspecione:

```bash
cd backend
uv run rag inspect "$EDITION_ID"
cd ..
```

Agora deve haver passagens pai e filho.

### 11.1 Consultar as contagens

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v edition_id="$EDITION_ID" \
  -c "
    SELECT
      count(*) FILTER (WHERE parent_passage_id IS NULL) AS parents,
      count(*) FILTER (WHERE parent_passage_id IS NOT NULL) AS children,
      count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded
    FROM passages
    WHERE edition_id = :'edition_id'::uuid;
  "
```

### 11.2 O que observar

- pais não possuem embedding;
- filhos possuem embedding;
- chunks não misturam capítulos;
- contexto do cabeçalho não foi incorporado ao texto citável;
- passagens multipágina carregam início e fim;
- a dimensão incompatível falha antes de publicar uma execução ativa;
- warnings e erros não contêm texto integral.

### 11.3 Testar idempotência da indexação

Repita sem `--force`:

```bash
cd backend
uv run rag index "$EDITION_ID"
cd ..
```

Esperado: execução existente/no-op para a mesma identidade.

Não use `--force` no primeiro teste. Ele cria uma nova execução e é útil para
testar histórico somente depois que a linha-base estiver registrada.

## 12. Configurar e executar enriquecimento

O endpoint de enriquecimento usa `/chat/completions` e JSON mode.

Para um endpoint local sem autenticação:

```bash
export ENRICHMENT_BASE_URL="http://127.0.0.1:8003/v1"
export ENRICHMENT_MODEL="qwen3-instruct"
unset ENRICHMENT_API_KEY
unset ENRICHMENT_API_KEY_FILE
```

Execute:

```bash
cd backend
uv run rag enrich "$EDITION_ID"
cd ..
```

A saída informa:

- summaries de seção;
- summaries de capítulo;
- summary da edição;
- conceitos;
- aliases;
- evidências;
- versões de summarizer e extractor.

### 12.1 Inspeção SQL sem imprimir o texto do livro

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v edition_id="$EDITION_ID" \
  -c "
    SELECT scope_type, count(*)
    FROM summaries
    WHERE edition_id = :'edition_id'::uuid
    GROUP BY scope_type
    ORDER BY scope_type;
  "
```

```bash
docker exec ragbooks-postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v edition_id="$EDITION_ID" \
  -c "
    SELECT count(DISTINCT ce.concept_id) AS concepts,
           count(*) AS evidence_links
    FROM concept_evidence ce
    JOIN passages p ON p.id = ce.passage_id
    WHERE p.edition_id = :'edition_id'::uuid;
  "
```

### 12.2 O que observar

- todo summary publicado possui suporte;
- suporte aponta a passagens da edição e indexação ativas;
- conceito sem suporte não é publicado;
- reexecução com a mesma identidade é idempotente;
- texto de summary nunca deve virar passagem citável.

Limite conhecido: no estado atual, esses summaries e conceitos ainda não
participam da recuperação da API. O teste desta etapa comprova geração,
persistência e proveniência do enriquecimento, não sua utilização em consulta.

## 13. Configurar os modelos usados pela API

### 13.1 Reranker

Contrato:

```text
POST {RERANKER_BASE_URL}/rerank
```

Body:

```json
{
  "model": "qwen3-reranker",
  "query": "pergunta",
  "documents": ["passagem 1", "passagem 2"]
}
```

Resposta:

```json
{
  "results": [
    { "index": 0, "relevance_score": 0.9 },
    { "index": 1, "relevance_score": 0.5 }
  ]
}
```

Configuração local:

```bash
export RERANKER_BASE_URL="http://127.0.0.1:8002"
export RERANKER_MODEL="qwen3-reranker"
unset RERANKER_API_KEY
unset RERANKER_API_KEY_FILE
```

### 13.2 Generator

```bash
export GENERATOR_BASE_URL="http://127.0.0.1:8003/v1"
export GENERATOR_MODEL="qwen3-instruct"
unset GENERATOR_API_KEY
unset GENERATOR_API_KEY_FILE
```

### 13.3 Verifier

O verifier pode usar o mesmo servidor, mas possui configuração independente:

```bash
export VERIFIER_BASE_URL="http://127.0.0.1:8003/v1"
export VERIFIER_MODEL="qwen3-instruct"
unset VERIFIER_API_KEY
unset VERIFIER_API_KEY_FILE
```

### 13.4 Planner

```bash
export PLANNER_BASE_URL="http://127.0.0.1:8003/v1"
export PLANNER_MODEL="qwen3-instruct"
unset PLANNER_API_KEY
unset PLANNER_API_KEY_FILE
```

### 13.5 Observação sobre `.env.example`

O projeto não carrega `.env` automaticamente. Variáveis precisam estar
exportadas no processo ou injetadas pelo supervisor.

Além disso, não copie chaves placeholder para URLs locais `http://`: uma chave
não vazia exige `https://`. Para endpoints locais sem autenticação, deixe
`*_API_KEY` e `*_API_KEY_FILE` indefinidos.

## 14. Iniciar a API

Restrinja a origem e o rate limit:

```bash
export CORS_ALLOWED_ORIGINS="http://localhost:5173"
export RATE_LIMIT_PER_MINUTE=60
```

No terminal da API:

```bash
cd backend
uv run rag serve --host 127.0.0.1 --port 8000
```

Não use `0.0.0.0` neste tutorial.

Em outro terminal, com as mesmas variáveis de banco:

```bash
curl --fail --silent http://127.0.0.1:8000/api/v1/health/live
curl --fail --silent http://127.0.0.1:8000/api/v1/health/ready
```

Esperado:

```json
{"status":"ok"}
{"status":"ready"}
```

### 14.1 O que observar

- API escuta apenas localhost;
- readiness depende do PostgreSQL;
- liveness não depende de modelos;
- respostas possuem `X-Request-ID`;
- headers de segurança estão presentes;
- CORS só permite a origem configurada.

Confira headers:

```bash
curl --silent --dump-header - --output /dev/null \
  http://127.0.0.1:8000/api/v1/health/live
```

## 15. Conferir o catálogo

```bash
curl --fail --silent \
  http://127.0.0.1:8000/api/v1/works
```

```bash
curl --fail --silent \
  "http://127.0.0.1:8000/api/v1/editions/${EDITION_ID}"
```

Observe:

- obra correta;
- edição correta;
- `source_type`;
- editora/ano/rótulo;
- distinção entre duas edições, se houver.

## 16. Primeira consulta: `quote`

Escolha uma frase curta e literal do livro. Use inicialmente estratégia
`hybrid`, pois o comportamento `literal` possui falha conhecida.

```bash
export QUESTION_LITERAL="substitua por uma frase distintiva do livro"
```

Crie a consulta:

```bash
CREATE_RESPONSE="$(
  curl --fail --silent --show-error \
    --request POST \
    --header "Content-Type: application/json" \
    --data "{
      \"question\": \"${QUESTION_LITERAL}\",
      \"answer_mode\": \"quote\",
      \"depth\": \"standard\",
      \"search_strategy\": \"hybrid\",
      \"include_edition_ids\": [\"${EDITION_ID}\"],
      \"exclude_edition_ids\": []
    }" \
    http://127.0.0.1:8000/api/v1/queries
)"
```

Extraia o ID:

```bash
export QUERY_ID="$(
  python3 -c 'import json,sys; print(json.load(sys.stdin)["query_id"])' \
  <<<"$CREATE_RESPONSE"
)"
```

Acompanhe por SSE:

```bash
curl --no-buffer --silent \
  "http://127.0.0.1:8000/api/v1/queries/${QUERY_ID}/events"
```

Ou consulte o estado:

```bash
curl --fail --silent \
  "http://127.0.0.1:8000/api/v1/queries/${QUERY_ID}"
```

### 16.1 O que observar no resultado

Em `result.evidences`:

- o texto é literal;
- não há introdução, tradução ou paráfrase;
- `edition_id` é o solicitado;
- `passage_id` é estável;
- `score` e `rank` existem;
- seção está correta;
- página física e rótulo impresso fazem sentido;
- offsets apontam para o trecho.

Em `strategy`:

- a API deve mostrar a estratégia resolvida;
- os estágios executados deveriam corresponder a ela.

Falha conhecida: atualmente `literal` ainda chama embedding/busca
vetorial/RRF/reranking. Registre isso como reprodução, não como sucesso.

## 17. Abrir a origem e conferir a citação

Baixe somente uma faixa inicial:

```bash
curl --fail --silent \
  --header "Range: bytes=0-1023" \
  --output /tmp/ragbook-prefix.bin \
  "http://127.0.0.1:8000/api/v1/editions/${EDITION_ID}/source"
```

Observe os headers:

```bash
curl --silent --dump-header - --output /dev/null \
  --header "Range: bytes=0-1023" \
  "http://127.0.0.1:8000/api/v1/editions/${EDITION_ID}/source"
```

Esperado:

- status `206`;
- `Accept-Ranges: bytes`;
- `Content-Range`;
- `Content-Length`;
- tipo `application/pdf`.

Com o `passage_id` retornado:

```bash
export PASSAGE_ID="<UUID-DA-PASSAGEM>"
curl --fail --silent \
  "http://127.0.0.1:8000/api/v1/editions/${EDITION_ID}/passages/${PASSAGE_ID}"
```

Compare manualmente com o PDF original.

Limite conhecido: o endpoint de detalhe da passagem ainda perde metadados da
página final em passagens multipágina. A resposta `quote` carrega mais campos
que esse endpoint.

## 18. Segunda consulta: paráfrase sem os termos principais

Formule uma pergunta semanticamente equivalente que evite as palavras da
frase original:

```bash
export QUESTION_SEMANTIC="escreva uma paráfrase conceitual sem repetir os termos principais"
```

Repita o POST com:

```json
{
  "answer_mode": "quote",
  "depth": "standard",
  "search_strategy": "hybrid"
}
```

Observe:

- lexical pode não recuperar a passagem;
- vetorial deve recuperá-la;
- reranker pode alterar a ordem;
- a versão de embedding usada pela consulta deve ser compatível com a
  indexação;
- resultado não deve incluir outra edição quando `include_edition_ids` limita
  o escopo.

Não conclua qualidade sem mais de um caso. Um embedding determinístico de
teste prova contrato, não qualidade real em português.

## 19. Terceira consulta: resposta dissertativa

Use uma pergunta sustentada claramente pelo trecho:

```bash
export QUESTION_DISSERTATIVE="faça uma pergunta respondível apenas com o conteúdo do livro"
```

```bash
CREATE_RESPONSE="$(
  curl --fail --silent --show-error \
    --request POST \
    --header "Content-Type: application/json" \
    --data "{
      \"question\": \"${QUESTION_DISSERTATIVE}\",
      \"answer_mode\": \"dissertative\",
      \"depth\": \"standard\",
      \"search_strategy\": \"hybrid\",
      \"include_edition_ids\": [\"${EDITION_ID}\"],
      \"exclude_edition_ids\": []
    }" \
    http://127.0.0.1:8000/api/v1/queries
)"
```

Extraia `query_id` e aguarde o evento terminal como na consulta anterior.

### 19.1 O que observar

- `status` é `succeeded`, `abstained` ou `failed`, nunca sucesso parcial;
- `verification` está presente em resposta bem-sucedida;
- toda claim factual tem IDs de evidência;
- IDs existem no contexto retornado/persistido;
- contradição não é aceita como suporte;
- `citation_coverage` é coerente;
- falha/timeout do verifier produz erro, não resposta;
- prosa da resposta corresponde às claims validadas;
- nenhuma limitação livre do generator aparece.

Falha conhecida: claims com `inference=true` e lista vazia de evidências podem
ser aceitas sem julgamento semântico. Inclua um caso adversarial ao avaliar o
provider.

## 20. Testar abstenção

Faça uma pergunta deliberadamente ausente do livro e restrinja à edição:

```bash
export QUESTION_UNSUPPORTED="qual é o número de série da espaçonave descrita nesta obra?"
```

Envie em modo `dissertative`.

Esperado:

- `status=abstained`; ou
- resposta canônica com `abstained=true`;
- `answer_markdown` vazio;
- nenhuma claim;
- razão segura de abstenção;
- nenhuma resposta baseada em conhecimento geral.

Falha crítica:

- generator responde usando conhecimento externo;
- sistema marca `succeeded`;
- citação não sustenta a afirmação;
- abstenção contém prosa factual.

## 21. Testar filtros negativos

Envie a pergunta excluindo a própria edição:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Content-Type: application/json" \
  --data "{
    \"question\": \"${QUESTION_LITERAL}\",
    \"answer_mode\": \"quote\",
    \"depth\": \"standard\",
    \"search_strategy\": \"hybrid\",
    \"include_edition_ids\": [],
    \"exclude_edition_ids\": [\"${EDITION_ID}\"]
  }" \
  http://127.0.0.1:8000/api/v1/queries
```

Esperado:

- nenhuma passagem da edição excluída em lexical;
- nenhuma em vetor;
- nenhuma em RRF;
- nenhuma enviada ao reranker;
- nenhuma no contexto;
- nenhuma na resposta.

Se o acervo contém apenas essa edição, `quote` deve ficar vazio e
`dissertative` deve abster-se.

## 22. Testar pergunta comparativa

Este teste exige duas obras ou duas perspectivas reais. Não simule divergência
quando só houver uma fonte.

Com apenas uma obra:

- a resposta pode explicar o que a fonte diz;
- deve declarar que a comparação ficou limitada a uma obra.

Falha conhecida: o serviço calcula essa limitação, mas a API atual a descarta.
Este é um reprodutor direto do achado B04 do parecer consolidado.

Registre:

- pergunta;
- IDs das obras disponíveis;
- IDs das evidências;
- JSON do estado sem conteúdo integral desnecessário;
- ausência da limitação no contrato HTTP.

## 23. Testar estratégia `expanded`

Use uma pergunta conceitual ou comparativa com `search_strategy=expanded`.

Observe o plano:

- subperguntas;
- aliases;
- rótulos de conceitos;
- `needs_hierarchical`.

Depois compare os candidatos com `hybrid`.

Falhas conhecidas:

- subperguntas não são executadas;
- aliases não geram buscas adicionais;
- conceitos não selecionam regiões;
- summaries não entram no pipeline;
- o plano pode declarar expansão sem efeito observável.

O resultado correto futuro deverá recuperar pelo menos um caso acessível
somente por expansão e registrar a origem desse candidato.

## 24. Testar cancelamento

Crie uma consulta e cancele:

```bash
curl --fail --silent --show-error \
  --request POST \
  "http://127.0.0.1:8000/api/v1/queries/${QUERY_ID}/cancel"
```

Observe:

- resposta inicial `cancelling`;
- estado final `cancelled`;
- evento SSE terminal único;
- nenhum sucesso posterior sobrescreve o cancelamento.

Falha conhecida: o flag não é verificado em todas as fronteiras e não
interrompe providers em andamento. Teste especialmente durante geração e
verificação, não apenas durante embedding.

## 25. Fluxo para EPUB

Metadados:

```yaml
title: "Título da edição EPUB"
authors:
  - "Nome do autor"
license_status: unknown
language: pt
source_type: epub
```

Execute dry-run, ingestão, indexação e `quote` como no PDF.

Observe:

- EPUB não possui paginação estável;
- seções e ordem de leitura são preservadas;
- citação usa o texto da passagem;
- diferenças entre texto original e normalizado não são ocultadas.

Caso adversarial obrigatório:

1. bloco com várias sentenças;
2. texto original contendo hífen de quebra, whitespace ou Unicode diferente;
3. chunk pequeno que inclua só parte do bloco;
4. `quote` deve reproduzir o original.

Esse caso atualmente pode falhar e corresponde ao achado B06.

## 26. Fluxo para PDF escaneado

Primeiro gere o derivado:

```bash
cd backend
uv run rag ocr "$HOME/rag-test-input/livro-scan.pdf" \
  --output "$HOME/rag-test-input/livro-scan-ocr.pdf" \
  --engine rapidocr
cd ..
```

Metadados:

```yaml
title: "Título da edição escaneada"
authors:
  - "Nome do autor"
license_status: unknown
language: pt
source_type: pdf_scan
ocr_artifact: "/caminho/absoluto/livro-scan-ocr.pdf"
```

Depois execute ingestão sobre o scan original:

```bash
cd backend
uv run rag ingest "$HOME/rag-test-input/livro-scan.pdf" \
  --metadata "$HOME/rag-test-input/livro-scan.yaml"
cd ..
```

Observe:

- o original continua sendo a identidade da edição;
- o derivado OCR tem hash próprio;
- proveniência está embutida no PDF derivado;
- derivado de outro original é rejeitado;
- contagem de páginas coincide;
- imagem original permanece visível;
- texto reconhecido é pesquisável;
- logs de OCR não expõem caminhos internos de modelos.

## 27. Evidências que vale registrar

Crie um diretório privado fora do repositório. Não grave texto integral do
livro nem segredos.

Para cada etapa, registre:

- data;
- commit;
- sistema operacional;
- Python, uv, PostgreSQL e modelos;
- comando;
- código de saída;
- IDs de obra, edição, index run e query;
- contagens;
- versões;
- warnings;
- latências;
- resultado do teste manual de citação;
- skips.

Exemplo de cabeçalho:

```text
commit:
ambiente:
livro/edição:
source_sha256:
embedding model/revision:
reranker:
generator:
verifier:
```

Não registre:

- API keys;
- senha do banco;
- prompt completo com livro;
- resposta contendo grandes trechos;
- conteúdo integral do arquivo.

## 28. Critério de parada

Pare o teste e abra um achado se ocorrer:

- citação não corresponde ao original;
- edição ou página incorreta;
- obra excluída aparece;
- `quote` contém síntese;
- dissertativa não foi verificada;
- citação aponta para ID inexistente;
- resposta usa conhecimento externo;
- summary aparece como evidência primária;
- falha de modelo vira resposta bem-sucedida;
- segredo, caminho local ou texto integral aparece em logs;
- migration ou indexação publica estado parcial.

## 29. Encerrar preservando os dados

Pare API e modelos com `Ctrl-C`.

Pare o banco:

```bash
docker stop ragbooks-postgres
```

Os dados permanecem no volume. Para retomar:

```bash
docker start ragbooks-postgres
```

Não remova o volume até concluir a análise. Excluir o volume apaga banco,
versões, rankings e evidências.

## 30. Checklist final do primeiro livro

- [ ] gates executados em Linux;
- [ ] banco e extensions prontos;
- [ ] dry-run não persistiu;
- [ ] ingestão criou uma única edição;
- [ ] reingestão foi idempotente;
- [ ] `inspect` mostrou estrutura plausível;
- [ ] indexação criou pais e filhos;
- [ ] embeddings possuem dimensão compatível;
- [ ] reindexação sem force foi idempotente;
- [ ] enriquecimento possui suportes;
- [ ] API ficou restrita a localhost;
- [ ] health live/ready passou;
- [ ] `quote` literal foi conferido no original;
- [ ] busca semântica recuperou uma paráfrase real;
- [ ] dissertativa passou por verificação;
- [ ] pergunta sem resposta causou abstenção;
- [ ] filtro negativo removeu a edição em todos os estágios;
- [ ] source range retornou 206 correto;
- [ ] limitações e bugs conhecidos foram registrados;
- [ ] nenhum segredo ou texto integral foi logado;
- [ ] evidências incluem commit, versões, comandos e resultados.

Concluir esse checklist produz uma linha-base útil. Ele não substitui o
benchmark T19, o leitor T17, observabilidade T18 nem o ambiente T20.
