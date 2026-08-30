# Revisão T06 — Chunking e indexação

Data: 2026-08-29  
Commit revisado: `07e2b58`  
Resultado: **correções obrigatórias**

T06 ainda não está aprovada. O commit entrega uma boa base: chunker puro,
hierarquia pai/filho, offsets no fluxo feliz, validação prévia da dimensão,
CLI e testes contra PostgreSQL real. Porém, a estratégia atual de reindexação
destrói o histórico que deveria tornar execuções reproduzíveis, a reextração
não valida sua correspondência com páginas e seções persistidas e o adapter
de embeddings pode associar silenciosamente um vetor ao trecho errado.

## Bloqueadores

### T6-01 — Reindexação ignora configuração nova ou apaga o histórico

Arquivos:

- `backend/src/rag/application/index.py`
- `backend/src/rag/infrastructure/repositories/passages.py`
- `backend/tests/integration/test_index.py`

Se já existir qualquer passagem e `force=False`, `index_edition()` retorna
antes de comparar `chunking_params`, modelo ou versão de embedding. Assim, uma
solicitação com parâmetros novos é aceita como sucesso, mas não é executada.

Com `force=True`, todas as passagens da edição são apagadas e recriadas com
novos IDs. Isso contradiz SPEC §6: reindexação deve criar uma versão nova sem
alterar silenciosamente o significado de execuções anteriores. Um
`AnswerRun` histórico que registre IDs de evidências deixa de ser
reproduzível, mesmo que as linhas imutáveis de `ChunkingVersion` e
`EmbeddingVersion` continuem no banco.

O teste citado como evidência,
`test_different_chunking_params_create_new_version`, indexa **duas edições
diferentes**. Ele prova apenas que parâmetros diferentes geram IDs diferentes
na tabela de versões; não prova reindexação da mesma edição.

Correção esperada:

- versionar conjuntos de passagens por execução/índice, preservando os
  conjuntos antigos;
- tornar a seleção do conjunto ativo explícita, sem inferi-la apenas pela
  existência de qualquer passagem;
- fazer a idempotência considerar edição, versão de extração, chunking,
  embedding e endpoint/modelo;
- testar na mesma edição: repetição equivalente, parâmetros diferentes,
  modelo diferente e concorrência;
- não usar exclusão física como implementação de `--force`.

### T6-02 — Reextração pode publicar offsets e relações estruturalmente errados

Arquivo: `backend/src/rag/application/index.py`

`rag index` executa Docling novamente, mas não comprova que o resultado é o
mesmo usado por `rag ingest`. Em seguida, os lookups de `section_path` e
`physical_index` usam `.get()` e, quando não encontram correspondência,
persistem `section_id`, `page_start_id` ou `page_end_id` como `NULL`. A edição
ainda recebe status `indexed`.

Logo, uma mudança de saída do extrator pode associar offsets calculados sobre
um texto novo às páginas antigas do banco, ou remover por completo a âncora da
passagem. Os testes usam o mesmo extrator determinístico nas duas etapas e não
exercitam divergência. Isso viola a associação exata de SPEC §7.3.6 e AC-03.

Correção esperada:

- persistir o documento canônico versionado na ingestão e indexar exatamente
  essa representação; ou registrar e verificar uma identidade canônica
  equivalente antes de indexar;
- registrar a `ExtractionVersion` efetivamente usada;
- comparar conteúdo/identidade de páginas e caminhos de seção, não apenas
  tentar casar chaves;
- falhar fechado se qualquer seção ou página esperada não for encontrada;
- adicionar testes negativos com página, texto e caminho de seção divergentes.

O argumento em NOTES §10.6.7 de que isso exigiria alterar o schema já aprovado
não é suficiente: migrações incrementais são o mecanismo normal para cumprir
requisitos posteriores.

### T6-03 — A ordem retornada pelo endpoint pode trocar embeddings entre chunks

Arquivos:

- `backend/src/rag/adapters/embedding_adapter.py`
- `backend/src/rag/application/index.py`

O adapter ignora o campo `index` dos itens da resposta OpenAI-compatible e usa
a ordem do array `data`. Depois, `IndexingService` associa essa ordem à ordem
dos chunks com `zip`. Uma resposta válida contendo os itens fora de ordem
atribui vetores aos textos errados sem produzir erro.

Correção esperada:

- validar cada item por contrato Pydantic tipado;
- exigir índices inteiros, únicos e no intervalo esperado;
- reordenar pelos índices antes de devolver os vetores;
- rejeitar índices ausentes, repetidos ou fora do intervalo;
- testar resposta permutada e confirmar no PostgreSQL que cada vetor ficou
  ligado ao chunk correspondente.

### T6-04 — Credencial pode ser enviada por HTTP sem TLS

Arquivo: `backend/src/rag/adapters/embedding_adapter.py`

`EmbeddingEndpointSettings` aceita qualquer `base_url`; quando `api_key` está
presente, o adapter sempre envia `Authorization: Bearer ...`, inclusive para
uma URL `http://` remota. Isso viola a regra obrigatória de transporte de
credenciais.

Correção esperada:

- validar a URL com tipo apropriado;
- exigir HTTPS sempre que houver credencial;
- adicionar testes de rejeição da combinação insegura;
- manter segredo fora de mensagens, contexto de erros e logs.

## Correções importantes

### T6-05 — O texto original citável não sobrevive ao chunking

`domain/chunking.py` usa apenas `CanonicalBlock.text`; `original_text` nunca é
transportado para `ChunkNode` ou `Passage`. Para EPUB, `" ".join(...)` também
substitui as separações originais e não guarda offsets ou IDs dos blocos.
Assim, a associação demonstrada é com o texto normalizado, não com o trecho
original preservado pelo schema canônico.

Preservar uma referência exata aos blocos/spans originais e definir
explicitamente qual campo alimenta a citação literal. Adicionar casos em que
`text != original_text` e múltiplos parágrafos EPUB participam do chunk.

### T6-06 — “Lote” é uma única requisição sem limite

Todos os filhos de um livro são enviados em uma chamada a
`embed_documents()`. Um livro grande pode exceder o limite de entradas,
tokens ou payload do endpoint. Implementar tamanho de lote configurável e
versionado, validar cada lote e provar que falha intermediária não publica
índice parcial.

### T6-07 — O chunker não é configurável pelo CLI

`rag index` sempre instancia `ChunkingParams()` com defaults. Não há opções ou
configuração de ambiente para tamanho de pai, tamanho de filho e overlap.
Expor parâmetros validados no comando/configuração e registrar exatamente os
valores resolvidos.

### T6-08 — Identidade registrada é insuficiente para reprodução

`ChunkingVersion.label="structural-chunker"` não distingue revisões do
algoritmo, da heurística de sentenças ou da contagem de tokens.
`EmbeddingVersion` registra nome e dimensão, mas não identifica o endpoint ou
uma revisão imutável dos pesos. Mudanças sob os mesmos nomes reutilizam IDs
antigos.

Registrar versões de implementação e endpoint/modelo sem incluir credenciais.
O `ModelEndpointVersion` já existente deve ser integrado quando aplicável.

### T6-09 — Validação do contrato de embeddings é incompleta

Além de não usar responses Pydantic, o adapter aceita `NaN`/infinito e não
valida timeout positivo. Vetores não finitos devem falhar antes do banco.
Cobrir payloads malformados, índices e valores numéricos não finitos.

### T6-10 — Concorrência de indexação não é controlada

Duas indexações simultâneas podem observar zero passagens, ambas chamar o
modelo e competir pela restrição `(edition_id, ordinal)`. A segunda termina
com erro de integridade não tipado, em vez de idempotência ou conflito
controlado. Adicionar lock por edição ou controle otimista e teste com duas
conexões.

## Evidências verificadas

Executados independentemente sobre `07e2b58`:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK — 165 pacotes |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | OK — 263 passed, 3 skipped; frontend 1 passed |
| `make test-integration` | OK — 103 passed, 1 skipped |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |

Os gates verdes demonstram estabilidade da suíte existente, mas não cobrem os
cenários bloqueadores acima.

## Julgamento

- AC-03: **parcial** — offsets funcionam no fluxo feliz, mas divergência entre
  ingestão e reextração falha aberta e o texto original não é preservado.
- AC-12: **parcial conforme o cronograma** — cabeçalho contextual está separado;
  resumos continuam destinados a T11.
- AC-15: **não atendido por T06** — passagens históricas são apagadas,
  mudanças podem ser ignoradas e extração/algoritmo/endpoint não possuem
  identidade reproduzível completa.

T06 permanece bloqueada até T6-01 a T6-04 serem corrigidos e as evidências de
reindexação serem refeitas na mesma edição.
