# Feedback de revisão — T01 a T04

Data: 2026-08-29  
Escopo: T01, T02, T03 e T04  
Resultado: **correções necessárias antes da aprovação**

## 1. Resumo

A estrutura geral está consistente com a especificação e os testes atuais passam. Entretanto, há falhas de integridade e reprodutibilidade que os testes não cobrem. T03 e T04 não devem ser considerados concluídos até a correção dos itens bloqueadores abaixo.

Não implemente tarefas T05+ como resposta a esta revisão. Corrija apenas os contratos e fundações pertencentes a T01–T04.

## 2. Evidências executadas pelo revisor

Com `~/.local/rag-tools` adicionado ao `PATH`:

- lint backend e frontend: passou;
- format check backend e frontend: passou;
- mypy strict e TypeScript: passaram;
- testes unitários backend: **122 passaram**;
- teste frontend: **1 passou**;
- testes de integração PostgreSQL: **18 passaram**;
- `pip-audit`: nenhuma vulnerabilidade conhecida;
- `npm audit`: nenhuma vulnerabilidade conhecida;
- busca direta por IOCs bloqueados: nenhum resultado.

Uma execução histórica de integração havia terminado com 18 erros enquanto o pipeline que usava `| tail` retornou status zero. A execução atual passou, mas isso reforça que scripts não podem mascarar exit codes.

## 3. Correções bloqueadoras

### R01 — Impedir referências entre edições diferentes

Severidade: alta  
Tarefa: T03  
Critérios afetados: AC-02, AC-03, AC-15

Problema:

`passages.edition_id` é independente das FKs de `section_id`, `page_start_id`, `page_end_id` e `parent_passage_id`. O banco aceita, por exemplo, uma passagem da edição A ligada a uma página da edição B. Isso torna citações e destaques incorretos mesmo que todos os IDs existam.

Correção esperada:

- criar constraints que garantam a mesma `edition_id` para passagem, seção, páginas e passagem-pai;
- preferir `UNIQUE(id, edition_id)` nas tabelas referenciadas e FKs compostas;
- aplicar a mesma regra ao parentesco de seções;
- avaliar relações de suporte de summaries/concepts para impedir proveniência incoerente quando o escopo exigir uma edição.

Testes obrigatórios:

- inserir passagem com página de outra edição deve falhar;
- inserir passagem com seção de outra edição deve falhar;
- inserir passagem-filho com pai de outra edição deve falhar;
- inserir seção com pai de outra edição deve falhar;
- o caso válido dentro da mesma edição continua funcionando.

### R02 — Tornar a migration determinística

Severidade: alta  
Tarefa: T03  
Critério afetado: AC-15

Problema:

`0001_initial_schema.py` lê `RAG_EMBEDDING_DIMENSIONS`. Assim, a mesma revisão Alembic cria schemas diferentes conforme o ambiente. Isso impede afirmar que a revisão `0001` identifica um schema específico e prejudica restauração, comparação e reprodução.

Correção esperada:

- remover decisões de schema baseadas em variável de ambiente de uma migration versionada;
- definir uma dimensão explícita e imutável em cada revisão; ou propor, documentar e testar outra estratégia determinística;
- se dimensões diferentes exigirem schemas diferentes, cada mudança deve ser uma nova migration;
- documentar como Qwen embeddings com dimensões diferentes serão avaliados sem reutilizar indevidamente a mesma revisão.

Testes obrigatórios:

- executar a migration com valores ambientais diferentes deve produzir o mesmo schema;
- validar antecipadamente dimensões não suportadas pelo tipo/índice pgvector escolhido;
- comprovar compatibilidade entre a dimensão registrada e a coluna/index.

### R03 — Corrigir a identidade de `PromptVersion`

Severidade: alta  
Tarefa: T03  
Critério afetado: AC-15

Problema:

A chave única e o `VersionsRepository` identificam `PromptVersion` por `(label, params)`, ignorando `template_sha256`. Dois templates diferentes com o mesmo label e parâmetros colidem; o segundo `get_or_create` retorna silenciosamente o primeiro prompt.

Correção esperada:

- incluir `template_sha256` na constraint única da tabela;
- incluir `template_sha256` no conflict target/lookup do repository;
- revisar as outras tabelas de versão para garantir que todos os campos que alteram comportamento participem da identidade;
- não alterar registros antigos para representar conteúdo novo.

Testes obrigatórios:

- mesmo label/parâmetros/hash retorna o mesmo ID;
- mesmo label/parâmetros com hash diferente cria ID diferente;
- os dois registros são carregados com o hash correto.

### R04 — Fechar a janela de inconsistência do ArtifactStore

Severidade: alta  
Tarefa: T04  
Critérios afetados: AC-01, AC-03

Problema:

O sidecar é publicado antes do objeto. Um crash entre a escrita do sidecar e `os.replace(tmp, existing)` deixa metadados de um objeto inexistente. Além disso:

- `metadata()` pode devolver um sidecar válido sem confirmar a existência do objeto;
- `put()` considera qualquer objeto existente como deduplicado sem validar novamente tamanho/hash;
- o sidecar não é suficiente para garantir integridade do conteúdo.

Correção esperada:

- definir claramente qual artefato é autoritativo;
- garantir que leitores nunca observem metadados válidos para objeto ausente;
- validar objeto existente antes de devolvê-lo como deduplicado;
- verificar ao menos existência e tamanho; para a garantia de content addressing, validar hash em operação apropriada;
- tornar sidecars órfãos detectáveis e recuperáveis/removíveis;
- documentar e testar comportamento sob interrupção entre etapas de publicação;
- fazer fsync dos arquivos e diretórios necessários, ou documentar limite explícito de durabilidade.

Testes obrigatórios:

- sidecar sem objeto falha fechado;
- objeto sem sidecar falha fechado ou é reparado por regra documentada;
- objeto existente com conteúdo corrompido não é aceito como deduplicado;
- simulação de falha antes/depois de cada etapa não deixa estado apresentado como válido;
- concorrência de dois writers para o mesmo hash preserva resultado íntegro.

### R05 — Impedir bypass de invariantes por `model_copy`

Severidade: alta  
Tarefa: T02/T03  
Critérios afetados: AC-09, AC-15

Problema:

Pydantic `model_copy(update=...)` não executa novamente os validators. Foi confirmado que o código permite produzir:

```text
AnswerRun(status=SUCCEEDED, response=None)
```

Esse objeto inválido pode ser aceito por `AnswerRunsRepository.save`, pois o banco também não impõe a coerência do estado terminal.

Correção esperada:

- não usar `model_copy(update=...)` como transição de estado sem revalidação;
- oferecer métodos/funções de transição que reconstruam e validem o modelo;
- validar transições permitidas e coerência dos estados terminais;
- `save()` deve detectar ID inexistente e rejeitar objetos inválidos;
- adicionar proteção de banco para invariantes terminais que sejam expressáveis com constraints.

Testes obrigatórios:

- `SUCCEEDED` sem resposta é rejeitado;
- `FAILED` sem código de erro é rejeitado;
- `ABSTAINED` sem resposta abstida é rejeitado;
- transição regressiva ou inválida é rejeitada;
- `save()` de ID inexistente não retorna sucesso.

## 4. Correções importantes

### R06 — Não mascarar falhas em `make audit`

Severidade: média  
Tarefa: T01

Problema:

O uso de `; rm ...` permite que a falha de `uv export` ou `pip-audit` seja ignorada. O comando subsequente `npm audit` pode fazer o target inteiro retornar zero.

Correção esperada:

- executar o recipe em modo fail-fast;
- preservar o exit code do audit;
- usar cleanup via `trap` ou equivalente que não transforme falha em sucesso;
- adicionar um teste/smoke check que simule falha do audit.

### R07 — Tornar o scan de IOCs estrutural

Severidade: média  
Tarefa: T01

Problema:

O regex atual para Axios procura uma declaração `"axios": "versão"`. Ele pode não detectar o formato transitivo do `package-lock.json`, no qual o pacote aparece como `node_modules/axios` e a versão em outro campo.

Correção esperada:

- analisar manifests e lockfiles de forma estrutural;
- reprovar Axios 1.14.1, Axios 0.30.4, qualquer `plain-crypto-js` e o domínio bloqueado;
- incluir fixtures de lockfile com dependência direta e transitiva maliciosa;
- manter o scan independente do fato de Axios existir atualmente.

Não há IOC ativo no lockfile atual.

### R08 — Não ignorar conflitos de seção/página silenciosamente

Severidade: média  
Tarefa: T03

Problema:

`SectionsRepository.create_many` e `PagesRepository.create_many` usam `ON CONFLICT ... DO NOTHING`. Um ordinal já existente com conteúdo diferente é tratado como sucesso.

Correção esperada:

- retornar o registro existente somente se os dados relevantes forem equivalentes; ou
- levantar `ConflictError` quando o mesmo identificador lógico representar conteúdo diferente.

Testes obrigatórios:

- repetição idêntica é idempotente;
- repetição divergente falha explicitamente.

### R09 — Completar evidência de persistência de runs

Severidade: média  
Tarefa: T03  
Critérios afetados: AC-06, AC-15

Problema:

O teste de integração de `AnswerRun` não persiste/recarrega candidatos dos quatro estágios, evidências selecionadas nem um `VersionSet` preenchido.

Correção esperada:

- criar teste de round-trip com `LEXICAL`, `VECTOR`, `FUSED` e `RERANKED`;
- incluir IDs de evidência e todas as versões relevantes;
- comparar o objeto recarregado integralmente.

Isso não exige implementar a recuperação de T08/T09.

### R10 — Validar os índices realmente declarados

Severidade: média  
Tarefa: T03

Problema:

Há prova de plano para FTS e HNSW, mas não para o índice trigram.

Correção esperada:

- adicionar `EXPLAIN` para a expressão e o operador que serão usados na busca aproximada;
- confirmar que o plano utiliza `passages_text_trgm_gin`.

### R11 — Testar lookup por hash da edição

Severidade: média  
Tarefa: T03

`EditionsRepository.get_by_source_hash()` é parte do caminho de idempotência, mas não possui teste.

Adicionar teste create → lookup → mesmo ID, além do caso inexistente.

## 5. Observações de escopo

Os seguintes critérios permanecem apenas parciais nesta etapa e isso é esperado:

- AC-01 completo depende do serviço/CLI de ingestão em T05;
- AC-03 completo depende da API e do leitor em T14/T17;
- AC-06 completo depende da recuperação em T08/T09;
- AC-08 comportamental depende do fluxo em T12;
- AC-09 comportamental depende do gerador/verificador em T13;
- AC-15 ponta a ponta depende dos componentes posteriores.

Não marque esses critérios como integralmente cobertos agora. Nesta revisão, exige-se apenas que as fundações de T01–T04 não tornem os critérios impossíveis ou inconsistentes.

## 6. Pontos aprovados

- separação backend/frontend;
- domínio sem imports de frameworks, ORM, Docling ou SDKs;
- enums e contratos básicos;
- dependências diretas pinadas e lockfiles presentes;
- migrations sobem, descem e voltam a subir em banco descartável;
- extensões PostgreSQL e índices FTS/HNSW existem;
- repositories usam parâmetros para valores SQL;
- hash original da edição e artefato OCR derivado foram modelados separadamente;
- validação básica de tipo, tamanho, hash e path traversal no armazenamento;
- leitura de byte ranges;
- limpeza de temporários;
- testes atuais, lint e tipos passam;
- nenhuma dependência bloqueada foi encontrada.

## 7. Critério para nova revisão

Solicite nova revisão quando:

1. R01–R05 estiverem corrigidos;
2. R06–R11 estiverem corrigidos ou houver justificativa aprovada;
3. novos testes negativos passarem;
4. `docs/rag/EVIDENCE.md` tiver sido atualizado sem promover prematuramente critérios de tarefas futuras;
5. os comandos abaixo terminarem com status confiável:

```text
make lock
make lint
make format-check
make typecheck
make test
make test-integration
make audit
make security-scan
```

O agente deve responder ao feedback item por item, indicando arquivos alterados, testes adicionados e eventual discordância técnica fundamentada.
