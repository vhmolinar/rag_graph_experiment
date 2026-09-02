# Quinta revisão T06 — Validação da resposta à ROUND4

Data: 2026-08-30
Branch revisada: `T06`
Commit revisado: `8d4e676` (`Complete T06 round 4 corrections`)
Resposta avaliada: `docs/rag/REVIEW_RESPONSE_T06_ROUND4.md`
Resultado: **correções obrigatórias**

## Resumo executivo

A resposta ROUND4 resolveu e comprovou o bloqueador de AC-03: para PDF, o
`original_text` persistido passa a ser exatamente `node.text`, que já é a fatia
de `CanonicalPage.text` delimitada pelos offsets. O probe multi-bloco da ROUND4
agora passa, e foi adicionado um teste de regressão correspondente.

Também foram confirmados:

- único head Alembic `0004`;
- migration, persistência e indexação contra PostgreSQL/pgvector reais;
- testes unitários e contract tests em diretórios/gates disjuntos;
- fingerprints determinísticos sensíveis aos campos canônicos testados;
- falha fechada para fingerprint ausente e section path inválido.

T06 ainda não pode ser aprovada porque o novo comando administrativo de
backfill possui duas falhas de proveniência:

1. para `pdf_scan`, ele reextrai o scan original em vez do derivado OCR usado
   por `rag ingest` e `rag index`;
2. ele sobrescreve incondicionalmente um fingerprint já existente, permitindo
   redefinir silenciosamente a identidade canônica que deveria detectar uma
   mudança do extrator.

Nenhum teste exercita o serviço ou o CLI de backfill. Os `111` testes de
integração reproduzidos nesta revisão são os mesmos da rodada anterior e não
incluem qualquer ocorrência de `backfill_fingerprint`.

## Bloqueador

### R5-T6-01 — Backfill pode sobrescrever a identidade canônica já estabelecida

Arquivos:

- `backend/src/rag/application/ingest.py` (`backfill_fingerprint`);
- `backend/src/rag/infrastructure/repositories/editions.py`
  (`update_canonical_fingerprint`);
- migration `0004`;
- testes de integração/CLI.

Requisitos afetados:

- SPEC §6: registros e significado histórico não podem ser alterados
  silenciosamente;
- SPEC §7.2/§7.3: proveniência da representação canônica;
- AC-03 e AC-15;
- R3-T6-03/R4-T6-02.

O comando é apresentado como operação para uma edição legada sem fingerprint,
mas não verifica essa pré-condição. Depois de reextrair, executa:

```python
UPDATE editions SET canonical_fingerprint = %s WHERE id = %s
```

Isso substitui tanto `NULL` quanto um fingerprint válido já existente.

Cenário de falha:

1. uma edição é ingerida e recebe fingerprint `A`;
2. o extrator muda a segmentação dos blocos sem mudar o texto das páginas e
   headings persistidos;
3. `rag index` rejeitaria corretamente a nova representação `B` porque
   `B != A`;
4. executar `rag backfill-fingerprint` sobrescreve `A` por `B`;
5. a próxima indexação aceita `B`.

Assim, o comando administrativo contorna exatamente a verificação fail-closed
introduzida para AC-15. Duas execuções concorrentes também podem gravar
resultados diferentes por last-writer-wins.

Correção esperada:

- backfill comum deve operar somente quando
  `canonical_fingerprint IS NULL`;
- fazer compare-and-set parametrizado no banco (`UPDATE ... WHERE id = %s AND
  canonical_fingerprint IS NULL`) e verificar `rowcount`/`RETURNING`;
- se o fingerprint já existir:
  - mesma representação deve ser no máximo um no-op idempotente;
  - representação diferente deve gerar conflito tipado, nunca overwrite;
- qualquer operação excepcional de rebaseline deve seguir o protocolo de
  alteração arquitetural, ter opção/nome distintos, auditoria, versão anterior,
  motivo e aprovação explícita; não deve ser o comportamento do backfill;
- serializar ou controlar otimisticamente duas execuções concorrentes;
- adicionar testes de edição sem fingerprint, repetição idempotente,
  fingerprint existente divergente e concorrência.

## Correções importantes

### R5-T6-02 — Backfill de `pdf_scan` usa o artefato errado

Arquivos:

- `backend/src/rag/application/ingest.py` (`backfill_fingerprint`);
- `backend/src/rag/application/index.py` (`_reextract`, como referência da
  regra correta);
- entidades de `DerivedArtifactRef`;
- testes de scan/OCR/backfill.

Requisitos afetados:

- decisão aprovada em NOTES §10.1.5;
- proveniência OCR de T05;
- AC-03 e AC-15.

Para todos os source types, o backfill abre:

```python
self._store.open_stream(edition.source_sha256)
```

Em `pdf_scan`, `source_sha256` identifica deliberadamente a varredura original
imutável. A representação canônica da ingestão foi produzida a partir do
artefato derivado `OCR_TEXT_LAYER`, não do original. Essa regra já está
implementada corretamente em `IndexingService._reextract()`, que procura o
derivado e usa seu hash.

O backfill atual passa o scan original ao `DoclingExtractor` como
`SourceType.PDF_TEXT`. Dependendo do documento/configuração, isso falha por não
haver texto ou executa uma extração/OCR diferente da derivação registrada. Em
nenhum caso comprova a identidade canônica usada por T05.

Correção esperada:

- centralizar a resolução do artefato extraível para evitar regras divergentes
  entre ingestão, indexação e backfill;
- para `pdf_scan`, exigir exatamente um derivado OCR registrado e abrir o hash
  desse derivado;
- preservar a identidade da edição/original e verificar a proveniência do
  derivado, conforme T05;
- falhar fechado se o derivado estiver ausente ou ambíguo;
- testar backfill real/configurável de PDF scan, incluindo derivado ausente,
  derivado correto e tentativa de usar o original.

### R5-T6-03 — A cobertura alegada para o backfill não existe

Arquivos:

- `backend/tests/`;
- `docs/rag/REVIEW_RESPONSE_T06_ROUND4.md`.

A resposta declara:

> “O backfill possui cobertura de código e contrato.”

Busca executada:

```text
rg -n 'backfill-fingerprint|backfill_fingerprint' backend/tests
```

Resultado: nenhuma ocorrência.

Os novos testes cobrem somente o método puro `CanonicalDocument.fingerprint()`
e o recorte citável do chunker. Não há teste do serviço, repository update,
CLI, transação, falha parcial, edição desconhecida ou source types do backfill.

Embora `make test-integration` tenha passado com `111 passed, 1 skipped`, a
contagem é igual à da rodada anterior e a suíte não chama o backfill. Portanto,
ela valida a migration e os fluxos preexistentes, mas não a operação
administrativa nova.

Correção esperada:

- adicionar testes unitários de resolução/falha e testes de integração reais
  para persistência;
- cobrir EPUB, PDF-texto e PDF-scan;
- testar que falha não publica fingerprint;
- testar o contrato CLI, códigos de saída e mensagens sanitizadas;
- não declarar cobertura inexistente.

### R5-T6-04 — Target de integração ficou específico ao UID 1000 e ao Podman

Arquivo:

- `Makefile`.

Requisitos afetados:

- comandos uniformes/reproduzíveis de T01;
- ambiente Docker Compose previsto na especificação;
- evidência portátil de integração.

O Makefile define por padrão:

```make
CONTAINER_HOST ?= unix:///run/user/1000/podman/podman.sock
```

e sempre exporta esse valor como `DOCKER_HOST`. Isso funcionou nesta máquina,
cujo UID é de fato `1000`, e permitiu reproduzir a integração. Entretanto:

- falha para usuários Podman com outro UID;
- ignora o Docker padrão em `/var/run/docker.sock`;
- muda o target genérico do projeto para uma configuração local específica;
- exige override manual mesmo quando `DOCKER_HOST` já seria resolvido
  corretamente pelo client.

Correção esperada:

- manter `make test-integration` neutro por padrão, respeitando `DOCKER_HOST`
  existente ou a resolução padrão do Docker client;
- oferecer configuração/target Podman explícito, derivando o socket de
  `XDG_RUNTIME_DIR` ou `id -u`, sem UID hardcoded;
- aplicar `TESTCONTAINERS_RYUK_DISABLED=true` somente quando o backend escolhido
  exigir;
- documentar ambos os caminhos e testar ao menos a expansão/configuração do
  comando.

### R5-T6-05 — Matriz AC ainda não aponta para as novas evidências

Arquivo:

- `docs/rag/EVIDENCE.md`.

A subseção T06 agora marca corretamente as descrições antigas como históricas e
superadas, o que resolve a contradição textual central das rodadas anteriores.
Contudo, as linhas da matriz AC permanecem sem as novas evidências:

- AC-03 não menciona
  `test_pdf_citable_text_recomposes_offsets_across_blocks`;
- AC-15 não menciona `IndexRun`, fingerprint, migration `0004` ou testes de
  reindexação da mesma edição;
- o registro vigente não lista separadamente unit (`257`), contract (`26`) e
  integration (`111/1 skipped`).

Correção esperada:

- atualizar as linhas AC-03 e AC-15 com links/testes atuais;
- registrar os comandos e contagens por categoria;
- incluir os testes de backfill somente depois que eles existirem e passarem.

## Avaliação item a item da resposta ROUND4

- **R4-T6-01: corrigido.** O texto citável PDF é exatamente o texto
  endereçado pelos offsets e há regressão multi-bloco.
- **R4-T6-02: parcialmente corrigido.** Existe um comando de backfill, mas ele
  usa o artefato errado para scans e pode sobrescrever fingerprints válidos.
- **R4-T6-03: parcialmente corrigido.** Há três testes puros de fingerprint,
  mas nenhuma cobertura do serviço/CLI/persistência de backfill alegada na
  resposta.
- **R4-T6-04: parcialmente corrigido.** A subseção histórica foi marcada como
  superada; a matriz AC ainda não contém a evidência vigente completa.
- **R4-T6-05: corrigido funcionalmente, com ressalva de portabilidade.** Unit e
  contract tests estão disjuntos, mas o target de integração ficou preso ao
  ambiente Podman/UID local.

## Evidências executadas nesta revisão

| Comando | Resultado |
|---------|-----------|
| `make lint` | OK — Ruff e ESLint |
| `make format-check` | OK — 73 arquivos Python e frontend |
| `make typecheck` | OK — mypy em 73 arquivos e TypeScript |
| `make test-unit` | OK — 257 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `make test-integration` | OK — 111 passed, 1 skipped contra PostgreSQL/pgvector via Podman |
| `.venv/bin/alembic -c alembic.ini heads` | OK — único head `0004` |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `git diff --check f6cd3d3..HEAD` | falha apenas por trailing spaces usados como hard breaks em dois arquivos Markdown de resposta; nenhum erro em código |

O teste de integração levou aproximadamente 104 segundos e foi executado
independentemente nesta revisão. `make audit` não foi executado. Nenhuma
dependência foi adicionada pelo commit.

## Critérios relacionados

- **AC-03: passa no incremento T06 para novas edições PDF/EPUB.** O caminho
  completo até o leitor permanece para T17. Backfill de `pdf_scan` legado ainda
  não preserva a regra de proveniência aprovada.
- **AC-12: parcial conforme o cronograma.** Hierarquia pai/filho e separação do
  cabeçalho permanecem corretas; summaries pertencem a T11.
- **AC-15: falha.** O backfill pode alterar a identidade já estabelecida e não
  usa o artefato canônico correto para scans; a operação não possui testes.

## Conclusão

O bloqueador original de citação/offsets está resolvido, e os principais gates
da implementação foram reproduzidos com sucesso, inclusive PostgreSQL real.
As correções restantes estão concentradas na operação administrativa criada
para suportar edições legadas. T06 permanece **reprovada** porque o backfill
atual pode sobrescrever a identidade canônica e contornar a proteção de AC-15.
Depois de tornar a operação write-once/idempotente, usar o derivado OCR correto
e adicionar os testes correspondentes, a tarefa poderá ser reavaliada com
escopo bem menor.
