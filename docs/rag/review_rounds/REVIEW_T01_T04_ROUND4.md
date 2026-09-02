# Quarta revisão — T01 a T04

Data: 2026-08-29
Referência: resposta à seção ROUND3 em `REVIEW_RESPONSE_T01_T04.md`
Resultado: **reprovado; um bloqueador de concorrência permanece**

## 1. Gates executados

- `make lock`: passou;
- `make lint`: passou;
- `make format-check`: passou;
- `make typecheck`: passou;
- `make test`: 166 backend + 1 frontend passaram;
- `make test-integration`: 48 passaram;
- `make audit`: passou;
- `make security-scan`: passou.

RRR01–RRR03 foram confirmados nos cenários cobertos. A validação encontrou um problema de persistência concorrente que ainda permite regressão de uma execução terminal.

## 2. Bloqueador

### R4-01 — `AnswerRunsRepository.save()` não valida a versão persistida do run

Severidade: alta
Tarefas: T02/T03
Critério: AC-15

O domínio valida transições em relação ao estado do objeto em memória, mas o repository não compara esse estado com a versão atualmente persistida.

Cenário:

1. duas rotinas carregam o mesmo run em `RUNNING`;
2. a primeira transiciona e salva `SUCCEEDED`;
3. a segunda, baseada no objeto antigo, faz `RUNNING → RUNNING`;
4. `save()` atualiza a linha terminal de volta para `RUNNING`.

O mesmo problema permite:

- cancelamento e sucesso sobrescreverem um ao outro;
- uma atualização antiga apagar candidatos/latências adicionados por outra rotina;
- versões e evidências regredirem apesar das regras append-only do domínio.

O `SELECT` atual compara somente pergunta, filtros e criação. Ele não usa lock, revision, status esperado nem compare-and-swap. O `UPDATE` usa apenas `WHERE id = ...`.

Correção recomendada:

- adicionar controle otimista com `revision` monotônica em `answer_runs` e `AnswerRun`;
- cada transição carrega a revisão lida;
- `UPDATE ... WHERE id = :id AND revision = :expected_revision`, incrementando a revisão atomicamente;
- ausência de linha no `RETURNING` com ID existente deve virar erro de concorrência tipado, não `NotFoundError`;
- alternativa aceitável: `SELECT ... FOR UPDATE` e validação completa contra o registro atual, incluindo status e campos append-only, dentro da mesma transação;
- runs terminais nunca podem voltar a estados não terminais.

Testes obrigatórios:

1. save stale não reverte `SUCCEEDED`;
2. save stale não reverte `ABSTAINED`, `FAILED` ou `CANCELLED`;
3. duas conclusões concorrentes têm exatamente um vencedor;
4. atualização stale não remove candidatos, latências, versões ou evidências;
5. conflito de concorrência produz erro tipado distinto de recurso inexistente.

## 3. Correções importantes

### R4-02 — Proveniência de artefato derivado não é garantida no banco

Severidade: média
Tarefa: T03
Critérios: AC-02, AC-03

O domínio exige que `DerivedArtifactRef.derived_from` seja igual a `Edition.source_sha256`, mas `derived_artifacts.derived_from_sha256` possui apenas validação de formato. SQL direto ou outro adapter pode associar o OCR ao hash original errado.

Correção esperada:

- criar uma chave candidata em `editions(id, source_sha256)`;
- criar FK composta de `derived_artifacts(edition_id, derived_from_sha256)` para essa chave;
- testar hash original incorreto, edição incorreta e associação válida.

### R4-03 — Regra “chapter é Section de topo” está documentada, mas não imposta

Severidade: média
Tarefa: T03

O schema garante que o `section_id` pertence à edição, mas não diferencia section comum de chapter nem verifica nível. Qualquer seção aninhada pode ser usada com `scope_type='chapter'`.

Correção esperada:

- definir precisamente o que identifica um capítulo no modelo de `Section`;
- impor a regra no domínio e no banco, ou remover a alegação de “seção de topo” e adiar a distinção formal para o schema canônico de T05;
- adicionar teste para chapter apontando para seção incompatível.

### R4-04 — Evidência contém descrição obsoleta da dimensão

Severidade: baixa
Arquivo: `docs/rag/EVIDENCE.md`

A seção antiga de T03 ainda afirma que a dimensão é configurável por `RAG_EMBEDDING_DIMENSIONS` antes da primeira migration. Isso contradiz a migration determinística e as rodadas posteriores.

Remover ou corrigir a descrição antiga para declarar `vector(1024)` fixo na revisão 0001.

## 4. Pontos confirmados

- coleções de evidências, caminhos, respostas e summaries agora são imutáveis;
- `save()` rejeita alterações de pergunta/filtros/criação em execução existente;
- temporários de sidecar são removidos e classificados separadamente;
- sidecars são validados por hash, tamanho e media type;
- dimensão de embedding incompatível é rejeitada pelo repository e pelo banco;
- a constante da infraestrutura corresponde ao tipo físico;
- todos os gates atuais passam.

## 5. Próxima submissão

Corrigir R4-01 antes da aprovação. R4-02–R4-04 devem ser corrigidos ou receber decisão técnica explícita e aprovada. Não iniciar T05.
