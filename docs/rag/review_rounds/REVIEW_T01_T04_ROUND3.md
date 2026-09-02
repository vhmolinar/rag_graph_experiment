# Terceira revisão — T01 a T04

Data: 2026-08-29
Referência: resposta à seção ROUND2 em `REVIEW_RESPONSE_T01_T04.md`
Resultado: **reprovado; um bloqueador e duas correções importantes permanecem**

## 1. Gates executados

- `make lock`: passou;
- `make lint`: passou;
- `make format-check`: passou;
- `make typecheck`: passou;
- `make test`: 161 backend + 1 frontend passaram;
- `make test-integration`: 43 passaram;
- `make audit`: passou;
- `make security-scan`: passou;
- `make test` e `make audit` executados simultaneamente nesta revisão: ambos passaram.

RR01, RR03 e RR04 foram confirmados nos caminhos testados. RR02 permanece incompleto. RR05 funciona pelo repository, mas falta integridade equivalente no banco.

## 2. Bloqueador

### RRR01 — Imutabilidade profunda e proteção do repository ainda são incompletas

Origem: RR02
Severidade: alta
Critérios: AC-09, AC-15

Problemas reproduzidos:

```text
claim_evidence_count=2
section_path=['a', 'b']
repository_accepts_changed_question=alterada
```

#### Coleções mutáveis restantes

Apesar de os containers superiores terem sido convertidos para tuples:

- `Claim` é frozen, mas `Claim.evidence_ids` continua sendo `list[UUID]`;
- `EvidenceRef` é frozen, mas `EvidenceRef.section_path` continua sendo `list[str]`;
- uma resposta já construída pode ter suas evidências ou caminho de seção alterados sem validator;
- `Summary.supporting_passage_ids` também permanece uma lista e o modelo não é frozen, permitindo remover todos os suportes depois da validação.

Isso invalida a afirmação de imutabilidade profunda e pode mudar evidências depois da verificação.

#### Repository aceita alteração de campos declarados imutáveis

`AnswerRunsRepository._revalidate()` detecta um estado estruturalmente inválido, mas não sabe quais dados pertenciam ao registro original. Uma cópia com:

```python
run.model_copy(update={"question_original": "alterada"})
```

continua estruturalmente válida. `_revalidate()` a aceita, e `save()` atualiza `question_original`, `question_anonymized` e `explicit_filters`, embora `transition()` declare esses campos imutáveis.

Correção esperada:

- converter `Claim.evidence_ids`, `EvidenceRef.section_path` e demais coleções que compõem uma resposta/run para tuples;
- tornar `Summary` frozen e seus suportes imutáveis;
- não atualizar colunas imutáveis em `AnswerRunsRepository.save()`;
- comparar os campos imutáveis recebidos com o registro existente e levantar erro se divergirem, em vez de ignorar silenciosamente;
- manter updates limitados à mesma allowlist usada pelo domínio;
- testar cópias estruturalmente válidas que tentem mudar pergunta, filtros explícitos ou identidade lógica.

Testes obrigatórios:

1. append em `Claim.evidence_ids` é impossível;
2. append em `EvidenceRef.section_path` é impossível;
3. remoção dos suportes de `Summary` é impossível;
4. repository rejeita mudança de pergunta original/anônima;
5. repository rejeita mudança de filtros explícitos;
6. campos permitidos de progresso continuam persistindo.

## 3. Correções importantes

### RRR02 — Temporário interrompido de sidecar é tratado como objeto corrompido

Origem: RR03/R04
Severidade: média
Tarefa: T04

`_write_sidecar()` cria arquivos no formato:

```text
<sha>.meta.<uuid>.tmp
```

Se ocorrer falha antes do `os.replace`, o temporário não é removido por `finally`, não pertence a `root/tmp`, não é alcançado por `cleanup_stale_temps()` e é interpretado por `audit()` como objeto comum.

Foi reproduzido:

```text
objects=1
corrupted=['<sha>.meta.deadbeef.tmp']
```

Correção esperada:

- remover o temporário de sidecar em `finally`;
- fazer `audit()` reconhecer e remover/reportar separadamente temporários de sidecar;
- não contabilizá-los como objetos nem como hashes corrompidos;
- adicionar simulação de falha durante escrita/publicação do sidecar.

### RRR03 — Banco ainda aceita `EmbeddingVersion` incompatível

Origem: RR05
Severidade: média
Tarefa: T03
Critério: AC-15

O repository rejeita dimensão diferente de 1024, mas a tabela mantém:

```sql
dimensions int NOT NULL CHECK (dimensions > 0)
```

Uma inserção SQL direta ou outro adapter pode registrar dimensão 8/2560/4096 em um schema cuja coluna é `vector(1024)`. Além disso, a migration e `infrastructure/schema.py` duplicam a constante sem teste que prove igualdade.

Correção esperada:

- nesta revisão, usar constraint de banco compatível com a capacidade real (`dimensions = 1024`);
- manter o erro tipado no repository;
- adicionar teste que tenta dimensão incompatível diretamente no banco;
- adicionar teste que garante que a capacidade declarada pela infraestrutura corresponde ao tipo físico criado pela migration.

## 4. Pontos confirmados

- summaries agora têm escopo referencialmente íntegro por edição;
- sidecars têm SHA tipado e verificação completa de hash/tamanho/media type;
- deduplicação usa verificação completa;
- o audit de dependências usa arquivo temporário exclusivo e não apresentou a corrida anterior;
- o repository impede dimensões incompatíveis em seu caminho normal;
- todos os gates e testes existentes passam.

## 5. Próxima submissão

Corrigir RRR01–RRR03, atualizar a resposta/evidências e solicitar nova validação. Não iniciar T05 até aprovação.
