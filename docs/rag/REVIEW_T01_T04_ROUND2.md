# Segunda revisão — T01 a T04

Data: 2026-08-29  
Referências: `REVIEW_T01_T04.md` e `REVIEW_RESPONSE_T01_T04.md`  
Resultado: **reprovado; três correções bloqueadoras e duas importantes permanecem**

## 1. Verificações executadas

- `make lock`: passou;
- `make lint`: passou;
- `make format-check`: passou;
- `make typecheck`: passou;
- `make test`: 150 backend + 1 frontend passaram;
- `make test-integration`: 37 passaram;
- `make security-scan`: passou, sem IOC;
- `make audit`: passou isoladamente, mas falhou quando executado em paralelo com `make test`.

As correções R02, R03 e R08–R11 foram confirmadas. R01, R04, R05 e R06 ainda possuem lacunas descritas abaixo.

## 2. Bloqueadores

### RR01 — Escopo de summaries ainda pode apontar para outra edição ou para ID inexistente

Origem: R01  
Severidade: alta  
Critérios: AC-03, AC-12, AC-15

O novo `summaries.edition_id` protege os suportes via FK composta, mas não protege o recurso resumido:

- para `scope_type='edition'`, existe `CHECK scope_id = edition_id`;
- para `scope_type='section'` ou `scope_type='chapter'`, `scope_id` não possui FK;
- o banco aceita `scope_id` aleatório ou uma seção pertencente a outra edição;
- o validator de domínio repete apenas a regra do scope `edition`.

Correção esperada:

- representar escopos de forma referencialmente íntegra, sem FK polimórfica sem validação;
- opção recomendada: colunas nullable tipadas (`section_id`, `edition_scope_id`, ou equivalente) com CHECK que exige exatamente o campo compatível com `scope_type`;
- se `chapter` for representado por `Section`, documentar e impor isso;
- alternativa por trigger é aceitável se for testada e mantiver mensagens claras;
- garantir que o escopo pertença a `summary.edition_id`.

Testes:

1. summary de section com seção de outra edição falha;
2. summary de section com UUID inexistente falha;
3. summary de chapter com escopo incompatível falha;
4. escopos válidos da mesma edição passam.

### RR02 — `AnswerRun` não é imutável e `transition()` altera campos arbitrários

Origem: R05  
Severidade: alta  
Critério: AC-15

`ConfigDict(frozen=True)` é superficial:

- `run.candidates.append(...)` funciona;
- listas dentro de `VersionSet` também permanecem mutáveis;
- `GeneratedAnswer` e listas aninhadas podem ser alteradas;
- `transition(status, **changes)` aceita mudanças em qualquer campo, inclusive pergunta original, ID, criação e versões;
- `model_copy(update=...)` ainda produz `SUCCEEDED` sem resposta; os CHECKs barram esse caso específico no banco, mas não revalidam toda a estrutura JSON.

Foi reproduzido:

```text
mutated_candidates=1
changed_question=alterada
bypassed=succeeded, response=None
```

Correção esperada:

- trocar coleções internas que fazem parte do registro por estruturas imutáveis, como tuples;
- tornar também os modelos aninhados relevantes imutáveis;
- remover `**changes` irrestrito ou aplicar allowlist por transição;
- identidade, pergunta original, criação e versões já registradas não podem ser alteradas por transição de status;
- revalidar o dump completo no repository antes de persistir;
- testar mutações profundas e mudanças de campos proibidos.

Não é necessário impedir deliberadamente `model_construct`, mas o caminho normal de domínio/repository deve rejeitar cópias inválidas.

### RR03 — Sidecar pode declarar hash ou tipo incorreto e ainda ser aceito

Origem: R04  
Severidade: alta  
Critérios: AC-01, AC-03

`metadata()` compara apenas `size_bytes`. `verify_integrity()` recomputa o hash do objeto, mas não confirma:

- `metadata.sha256 == chave solicitada`;
- `metadata.media_type == tipo detectado do objeto`.

Assim, um sidecar válido em JSON, com o mesmo tamanho, mas hash ou media type adulterado é retornado como metadado válido. `_validated_existing()` também termina em `metadata()` e herda a lacuna.

Correção esperada:

- tipar `ArtifactMetadata.sha256` como `Sha256`;
- comparar explicitamente o hash do sidecar com a chave;
- validar o media type do sidecar contra o objeto em `verify_integrity()` e no caminho de deduplicação;
- decidir se `metadata()` fará verificação completa ou se documentará níveis distintos de confiança sem chamar metadado não verificado de íntegro.

Testes:

1. sidecar com SHA diferente e mesmo tamanho falha;
2. sidecar com media type diferente falha na verificação/deduplicação;
3. `audit()` reporta ou repara esses casos conforme política documentada.

## 3. Correções importantes

### RR04 — `make audit` não é seguro para execuções concorrentes

Origem: R06  
Severidade: média

O script usa o caminho global fixo `backend/.audit-reqs.txt`. Os testes de tooling também criam e removem esse mesmo arquivo. Ao executar `make test` e `make audit` em paralelo, o teste removeu o arquivo enquanto `pip-audit` o usava:

```text
ERROR: Could not open requirements file:
[Errno 2] No such file or directory: '.audit-reqs.txt'
make: *** [audit] Error 1
```

Isoladamente, `make audit` passa.

Correção esperada:

- usar `mktemp` para um arquivo exclusivo por execução;
- passar caminho absoluto ao `pip-audit`;
- fazer o `trap` remover somente o arquivo criado pela própria execução;
- testes não devem manipular um arquivo global do repositório.

Adicionar teste com duas execuções concorrentes do script.

### RR05 — Versões de embedding incompatíveis podem ser registradas

Origem: R02  
Severidade: média  
Critério: AC-15

A coluna da revisão é `vector(1024)`, mas `embedding_versions.dimensions` aceita qualquer inteiro positivo. O próprio teste registra dimensão 8. O repository valida o vetor contra a versão, mas não valida a versão contra a capacidade do schema; a falha ocorre apenas ao inserir a passagem.

Correção esperada:

- nesta revisão, impedir registro de `EmbeddingVersion` incompatível com `vector(1024)`; ou
- modelar explicitamente uma estratégia que permita múltiplas dimensões sem criar versões inutilizáveis;
- falhar no cadastro da versão, antes do processamento de documentos.

Teste:

- registrar dimensão diferente de 1024 nesta revisão deve falhar com erro tipado e claro.

## 4. Pontos confirmados

- referências de passagem, página, seção e passagem-pai agora respeitam a edição;
- suportes de summary respeitam a edição do summary;
- migration não depende mais de variável de ambiente;
- identidade de `PromptVersion` inclui o hash;
- replay divergente de seção/página falha;
- rankings e versões fazem round-trip no banco;
- FTS, trigram e HNSW têm provas de plano;
- lookup por source hash está testado;
- scanner estrutural detecta as versões e pacotes bloqueados;
- ArtifactStore agora trata o objeto como autoritativo, revalida hash na deduplicação e possui auditoria;
- todos os testes atuais passam quando os gates são executados sem a corrida do arquivo temporário.

## 5. Nova submissão

Responder RR01–RR05 individualmente e atualizar:

- `REVIEW_RESPONSE_T01_T04.md`;
- `EVIDENCE.md`;
- testes negativos correspondentes.

Executar novamente todos os gates, incluindo um teste de concorrência do script de auditoria. Não iniciar T05 antes da aprovação desta rodada.
