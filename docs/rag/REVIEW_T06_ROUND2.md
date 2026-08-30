# Segunda revisão T06 — Chunking e indexação

Data: 2026-08-30  
Branch revisada: `T06`  
Commit revisado: `aff44b6` (`Fix T06 review findings T6-01 through T6-10`)  
Resultado: **correções obrigatórias**

## Resumo executivo

O commit atual corrigiu aspectos importantes apontados na primeira revisão:
reindexação passou a preservar o histórico por `IndexRun`, a identidade ativa
considera versões de extração/chunking/embedding/endpoint, chamadas de embedding
são feitas em lotes, respostas HTTP são validadas e reordenadas pelo campo
`index`, credenciais exigem HTTPS, e indexações concorrentes da mesma edição são
serializadas.

T06, entretanto, ainda não pode ser aprovada. O texto promovido pelo domínio a
`citable_text` pode ser maior que o chunk recuperado e diferente do intervalo
indicado pelos offsets. Além disso, o serviço não indexa conteúdo sem seção
(livros sem headings ou front matter anterior ao primeiro heading), e a
comparação da reextração com o estado ingerido não inclui a identidade dos
blocos canônicos que efetivamente controlam chunking, seção e proveniência.

O gate oficial de contract tests também falha por não coletar testes, e o
registro de evidências de T06 descreve o comportamento anterior às correções do
commit revisado.

## Bloqueador

### R2-T6-01 — `citable_text` não corresponde ao chunk recuperado nem aos offsets

Arquivos:

- `backend/src/rag/domain/chunking.py` (`_original_text_of`,
  `_node_from_sentences`);
- `backend/src/rag/domain/library.py` (`Passage.citable_text`);
- `backend/tests/unit/test_chunking.py`;
- `backend/tests/integration/test_index.py`.

Requisitos afetados:

- SPEC §7.3, especialmente associação exata aos trechos originais;
- SPEC §9.2, que exige trecho literal e offsets de destaque no modo `quote`;
- TASKS T06: “associação exata entre chunk e origem” e “offsets recompõem o
  trecho original”;
- AC-03.

`_original_text_of()` inclui o `original_text` inteiro de todo bloco que
contribuiu com pelo menos uma sentença. Isso ocorre mesmo quando uma janela
filha contém somente uma das sentenças do bloco. Em seguida,
`Passage.citable_text` prefere incondicionalmente esse `original_text` ao texto
do chunk.

Assim, filhos distintos podem possuir `text` e offsets diferentes, mas expor a
mesma citação integral. Em PDF, os offsets destacam apenas o intervalo do filho
enquanto `citable_text` pode conter todas as frases do bloco. Em EPUB, onde não
há offsets, perde-se a associação exata entre o resultado ranqueado e o trecho
literal apresentado.

Reprodução executada na raiz `backend/`:

```bash
.venv/bin/python - <<'PY'
from rag.domain.canonical import CanonicalBlock, CanonicalDocument
from rag.domain.chunking import ChunkingParams, chunk_document
from rag.domain.enums import SourceType

text = (
    "Primeira frase suficientemente longa. "
    "Segunda frase suficientemente longa. "
    "Terceira frase suficientemente longa."
)
doc = CanonicalDocument(
    source_type=SourceType.EPUB,
    blocks=(
        CanonicalBlock(
            ordinal=0,
            kind="paragraph",
            level=0,
            text=text,
            original_text=text,
            section_path=("Capítulo",),
        ),
    ),
)
nodes = chunk_document(
    doc,
    ChunkingParams(
        child_target_tokens=10,
        child_overlap_tokens=0,
        parent_target_tokens=1000,
    ),
)
for node in nodes:
    if node.parent_index is not None:
        print(repr(node.text), repr(node.original_text), node.text == node.original_text)
PY
```

Resultado observado:

```text
'Primeira frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
'Segunda frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
'Terceira frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
```

Os testes existentes confirmam apenas que o bloco original sobrevive e que não
é duplicado dentro de um mesmo nó. Eles não exigem que o texto citável tenha a
mesma extensão semântica/física do filho ou que corresponda aos offsets
persistidos.

Correção esperada:

- representar spans originais com granularidade suficiente para recortar o
  original exatamente nas fronteiras do chunk; ou
- quando não houver alinhamento confiável entre texto normalizado e original,
  não dividir internamente o bloco para fins citáveis, ou adotar outra política
  explícita que preserve a correspondência exata;
- garantir por invariante que o trecho retornado como citação é exatamente o
  trecho endereçado pelos offsets, quando offsets existirem;
- adicionar testes positivos e negativos com:
  - várias sentenças do mesmo bloco distribuídas entre filhos;
  - `text != original_text`;
  - PDF com offsets;
  - EPUB sem paginação;
  - overlap entre filhos.

## Correções importantes

### R2-T6-02 — Conteúdo sem seção não pode ser indexado

Arquivos:

- `backend/src/rag/application/index.py`;
- `backend/src/rag/domain/chunking.py`;
- `backend/src/rag/application/ingest.py`;
- testes de indexação.

Requisitos afetados:

- modelo de domínio: `Passage.section_id` é opcional;
- TASKS T06: chunker estrutural e associação à origem;
- AC-03.

O schema canônico aceita parágrafos com `section_path=()`. Isso ocorre em um
documento sem headings e também pode ocorrer no front matter anterior ao
primeiro heading. `derive_sections()` persiste somente headings, portanto não
há `Section` correspondente ao caminho vazio.

O chunker gera normalmente nós para esse conteúdo, mas `IndexingService`
executa `section_by_path.get(node.section_path)` e falha se o resultado for
`None`. A validação anterior, `_assert_matches_persisted()`, não rejeita o caso:
um documento inteiramente sem headings produz conjuntos de seções esperadas e
persistidas igualmente vazios. A falha acontece apenas durante a publicação
das passagens, apesar de `Passage.section_id` admitir `None`.

Impacto:

- livros sem headings não podem executar `rag index`;
- prefácios, dedicatórias ou introduções anteriores ao primeiro heading podem
  impedir a indexação da edição inteira;
- um documento aceito por `rag ingest` pode não ser indexável por T06.

Correção esperada:

- aceitar `section_id=None` para conteúdo realmente não seccionado, conforme o
  modelo; ou criar uma seção raiz canônica explícita e estável;
- preservar o cabeçalho contextual de obra/edição mesmo sem seção;
- testar PDF e EPUB sem heading;
- testar front matter antes do primeiro capítulo e uma edição que combine
  conteúdo sem seção com conteúdo seccionado.

### R2-T6-03 — A validação da reextração não cobre a representação canônica que controla o chunking

Arquivos:

- `backend/src/rag/application/index.py` (`_assert_matches_persisted`);
- `backend/src/rag/application/ingest.py`;
- schema/migrations de proveniência;
- testes de divergência de reextração.

Requisitos afetados:

- SPEC §6 (versionamento e reprodução);
- SPEC §7.2 e §7.3;
- AC-03 e AC-15;
- correção T6-02 da primeira revisão.

`_assert_matches_persisted()` compara headings derivados (path, level, title e
páginas) e o hash do texto completo de cada página. Ela não compara os blocos
canônicos que realmente alimentam o chunker:

- quantidade e ordem dos blocos;
- `kind` e `ordinal`;
- `section_path` de cada parágrafo;
- `char_start`/`char_end` por bloco;
- `original_text`;
- warnings ou uma identidade/fingerprint do documento canônico.

Logo, uma alteração do extrator pode manter exatamente os mesmos textos de
página e headings, mas mudar a segmentação dos parágrafos ou associar um
parágrafo a outra seção já existente. A validação passa e os novos blocos são
publicados sob uma `ExtractionVersion` da reextração atual, embora o banco não
permita demonstrar que essa foi a representação usada na ingestão.

Os testes adicionados alteram diretamente texto de página ou título de seção;
eles não exercitam divergência de bloco mantendo páginas e headings iguais.

Correção esperada:

- persistir o documento canônico versionado, ou ao menos um fingerprint
  determinístico que cubra integralmente blocos, proveniência e estrutura;
- associar à edição a identidade/versão da extração efetivamente usada em
  `rag ingest`;
- fazer `rag index` consumir essa representação ou provar equivalência completa
  antes do chunking;
- adicionar testes em que páginas/headings permanecem idênticos, mas mudam:
  - fronteiras de blocos;
  - `section_path` de um parágrafo;
  - offsets;
  - `original_text`.

### R2-T6-04 — O gate oficial de contract tests não executa os testes HTTP de T06

Arquivos:

- `Makefile`;
- `backend/tests/unit/test_embedding_adapter.py`;
- `backend/tests/contract/`;
- `docs/rag/EVIDENCE.md`.

Requisitos afetados:

- TASKS T06/T07: contratos HTTP com servidor simulado;
- REVIEW_CHECKLIST §2 e §4;
- definição de pronto baseada em comandos reproduzíveis.

Comando executado:

```text
make test-contract
```

Resultado:

```text
cd backend && uv run pytest tests/contract -q
no tests ran in 0.00s
make: *** [Makefile:50: test-contract] Error 5
```

Os testes com `respx` estão em `backend/tests/unit/test_embedding_adapter.py`,
enquanto o target oficial coleta somente `backend/tests/contract`. Embora os
testes sejam executados indiretamente por `make test`, o gate exigido pelo
checklist falha e não constitui evidência reproduzível de contract tests.

Correção esperada:

- mover ou classificar os testes HTTP para que `make test-contract` os colete;
- impedir duplicação acidental entre targets, se a separação da suíte for
  pretendida;
- registrar quantidade executada, skips e resultado do comando oficial.

### R2-T6-05 — `EVIDENCE.md` descreve a implementação anterior às correções

Arquivo:

- `docs/rag/EVIDENCE.md`, seção T06.

Requisitos afetados:

- método de trabalho do `AGENTS.md`: registrar comandos, resultados e
  limitações;
- TASKS: evidência e documentação fazem parte da definição de pronto;
- REVIEW_CHECKLIST §2 e §21.

A seção T06 ainda afirma que:

- `--force` apaga as passagens existentes;
- não há `ExtractionVersion` associada à indexação;
- a reindexação apenas reutiliza `ChunkingVersion`/`EmbeddingVersion`;
- o teste de force se chama `test_force_reindexes_and_replaces_passages`.

No commit revisado, passagens históricas são preservadas por `IndexRun`, há
`ExtractionVersion` e `ModelEndpointVersion`, e o teste existente se chama
`test_force_reindexes_preserves_passage_history`. As contagens registradas
(`263` testes unitários) também antecedem o estado atual (`278`).

Correção esperada:

- atualizar a seção T06 com o desenho vigente e os testes atuais;
- preservar o histórico da primeira implementação de forma explicitamente
  marcada como superada, se ele precisar permanecer no documento;
- registrar separadamente resultados da rodada de correção;
- não marcar T06 como concluída enquanto os bloqueadores desta revisão
  permanecerem.

## Evidências executadas nesta revisão

Ambiente:

- branch: `T06`;
- commit: `aff44b6`;
- Python: CPython 3.12.14 (ambiente criado por `uv`);
- integração PostgreSQL indisponível nesta máquina por ausência de daemon/socket
  Docker; Podman está instalado, mas o socket não estava acessível ao runner de
  `testcontainers`.

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK — 165 pacotes resolvidos |
| `make lint` | OK — Ruff e ESLint |
| `make format-check` | OK — 72 arquivos Python e frontend formatados |
| `make typecheck` | OK — mypy em 72 arquivos e `tsc --noEmit` |
| `make test` | OK — backend: 278 passed, 3 skipped; frontend: 1 passed |
| `uv run pytest tests/unit/test_chunking.py tests/unit/test_embedding_adapter.py -q` | OK — 45 passed |
| probe de `original_text`/filhos descrito em R2-T6-01 | reproduziu a divergência em três filhos |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `make test-contract` | **falhou** — nenhum teste coletado, exit 5 |
| `make test-integration` | **inconclusivo por ambiente** — 1 passed, 1 skipped, 110 erros de setup por ausência de Docker socket |

O resultado de integração acima não é classificado como defeito do código. Ele
também não pode ser contado como evidência de que migrations, concorrência e
persistência T06 passaram nesta revisão. A primeira revisão registrou execução
bem-sucedida da integração sobre `07e2b58`, mas isso não valida as mudanças de
persistência e concorrência introduzidas posteriormente em `aff44b6`.

Não foi executado `make audit` nesta rodada. `make security-scan` passou, e não
foi encontrado indicador proibido durante a inspeção do diff.

## Julgamento dos critérios relacionados

- **AC-03: falha.** O texto escolhido como citação pode não corresponder ao
  chunk/offset persistido, e conteúdo sem seção não é indexável.
- **AC-12: parcial conforme o cronograma.** A relação pai/filho existe e o
  cabeçalho contextual está separado de `Passage.text`; summaries e descida até
  passagens pertencem a T11. O defeito de `citable_text` ainda afeta a segurança
  de uso futuro dessas passagens como evidência.
- **AC-15: evidência insuficiente.** O histórico por `IndexRun` é uma correção
  adequada, mas a representação canônica que determinou os chunks não possui
  identidade persistida suficiente, a matriz está desatualizada e a integração
  do commit atual não pôde ser reexecutada neste ambiente.

## Riscos residuais observados

- `rag inspect` usa `list_by_edition()` e soma passagens de todas as execuções,
  sem distinguir conjunto ativo de histórico; após reindexações, a contagem
  pode ser interpretada como tamanho do índice corrente.
- O lock transacional por edição permanece mantido durante reextração e chamadas
  HTTP de embeddings. Isso serializa corretamente a edição, mas mantém conexão e
  transação abertas durante operações externas potencialmente longas; deve ser
  observado em T07/T18.
- `EMBEDDING_MODEL_REVISION` é opcional. Sem uma revisão imutável exposta pelo
  provedor, o mesmo endpoint/model name ainda pode mudar de pesos sem gerar nova
  identidade; a limitação está parcialmente documentada em `NOTES.md`.

## Conclusão

As correções T6-01 a T6-10 da primeira revisão não devem ser revertidas: o
histórico por execução, batching, validação do payload e serialização por edição
são avanços corretos. Contudo, R2-T6-01 viola diretamente a associação exata
entre passagem, citação e origem e bloqueia a aprovação de T06. R2-T6-02 e
R2-T6-03 precisam ser corrigidos para que documentos aceitos pela ingestão sejam
indexáveis e reproduzíveis. Depois disso, os gates oficiais e a matriz de
evidências devem ser atualizados e executados novamente.
