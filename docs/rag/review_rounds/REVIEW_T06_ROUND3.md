# Terceira revisão T06 — Validação da resposta à ROUND2

Data: 2026-08-30
Branch revisada: `T06`
Commit revisado: `f6cd3d3` (`Fix T06 round 2 review findings`)
Resposta avaliada: `docs/rag/REVIEW_RESPONSE_T06_ROUND2.md`
Resultado: **correções obrigatórias**

## Resumo executivo

A resposta declara R2-T6-01 a R2-T6-05 corrigidos, mas duas verificações
diretas contradizem essa conclusão:

1. a reprodução original de `citable_text` continua produzindo três filhos
   diferentes com o mesmo bloco inteiro como `original_text`;
2. o novo arquivo Alembic referencia uma revisão inexistente, de modo que o
   grafo de migrations não pode sequer ser carregado por `alembic heads`.

Além disso, a proteção por fingerprint é opcional e falha aberta para edições
existentes, a associação de seção passou a aceitar silenciosamente qualquer
caminho não encontrado (não somente o caminho vazio), e `EVIDENCE.md` teve
apenas o nome de um teste atualizado, mantendo as demais afirmações obsoletas
apontadas na ROUND2.

Os gates de lint, formatação, typecheck e testes unitários passam. Isso não é
evidência das correções acima: o commit não adicionou ou modificou nenhum teste
e os casos de regressão permanecem ausentes da suíte.

## Bloqueadores

### R3-T6-01 — R2-T6-01 não foi corrigido no caso alinhado reproduzido pela revisão

Arquivos:

- `backend/src/rag/domain/chunking.py`;
- `backend/src/rag/domain/library.py`;
- `backend/tests/unit/test_chunking.py`.

Requisitos afetados:

- associação exata entre chunk e origem (SPEC §7.3);
- offsets e texto literal de `quote` (SPEC §9.2);
- AC-03;
- bloqueador R2-T6-01.

A nova condição de `_original_text_of()` só usa o texto das sentenças quando
existe ao menos uma sentença com `original_is_aligned=False`:

```python
if any(not sentence.original_is_aligned for sentence in sentences) and (...):
    return " ".join(sentence.text for sentence in sentences)
```

Quando `original_text == text`, `original_is_aligned` é verdadeiro. Se o bloco
alinhado for dividido entre vários filhos, a condição não é executada e a
função continua retornando `block_original_text` inteiro para cada filho.
Esse é exatamente o caso usado na reprodução da ROUND2.

Reprodução executada em `backend/` sobre `f6cd3d3`:

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
for node in chunk_document(
    doc,
    ChunkingParams(
        child_target_tokens=10,
        child_overlap_tokens=0,
        parent_target_tokens=1000,
    ),
):
    if node.parent_index is not None:
        print(repr(node.text), repr(node.original_text), node.text == node.original_text)
PY
```

Resultado:

```text
'Primeira frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
'Segunda frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
'Terceira frase suficientemente longa.' 'Primeira frase suficientemente longa. Segunda frase suficientemente longa. Terceira frase suficientemente longa.' False
```

Portanto, a afirmação da resposta de que “`Passage.citable_text` não apresenta
mais o bloco inteiro para um filho que contém apenas parte dele” é falsa no
commit revisado.

Correção esperada:

- decidir pelo recorte no nível de cada bloco com base em o chunk conter ou não
  todas as sentenças daquele bloco, independentemente de `text` e
  `original_text` serem iguais;
- quando o bloco estiver alinhado, usar os offsets locais das sentenças para
  recortar exatamente o original;
- quando não estiver alinhado, aplicar a política fail-closed já proposta, sem
  promover o bloco inteiro para um filho parcial;
- adicionar o caso reproduzido acima como teste de regressão, incluindo a
  asserção sobre `Passage.citable_text` e, em PDF, a igualdade com a
  recomposição pelos offsets.

### R3-T6-02 — A nova migration quebra o grafo Alembic

Arquivos:

- `backend/alembic/versions/0002_edition_extraction_warnings.py`;
- `backend/alembic/versions/0003_index_runs.py`;
- `backend/alembic/versions/0003_canonical_fingerprint.py`;
- testes de migrations.

Requisitos afetados:

- T03/T06: migrations versionadas e ambiente reproduzível;
- AC-15;
- AC-20 futuramente;
- R2-T6-03.

`0003_canonical_fingerprint.py` declara:

```python
revision = "0003_canonical_fingerprint"
down_revision = "0002_edition_extraction_warnings"
```

O identificador real da migration anterior é `revision = "0002"`, não o nome
do arquivo. Já existe ainda `0003_index_runs.py`, com `revision = "0003"` e
`down_revision = "0002"`.

Comando executado:

```text
cd backend
.venv/bin/alembic -c alembic.ini heads
```

Resultado:

```text
UserWarning: Revision 0002_edition_extraction_warnings ... is not present
KeyError: '0002_edition_extraction_warnings'
```

Esse erro ocorre durante o carregamento estático do grafo e independe de Docker
ou PostgreSQL. Assim, a ausência de daemon não é a única razão pela qual a
integração do commit atual não foi validada: a migration falharia antes de
subir um banco vazio.

Correção esperada:

- colocar a migration de fingerprint depois de `0003_index_runs`, com uma nova
  revisão linear (por exemplo, `0004`) e `down_revision = "0003"`; ou criar uma
  merge revision válida caso haja motivo real para branches, o que não parece
  necessário aqui;
- executar `alembic heads`, upgrade de banco vazio, downgrade seguro e
  re-upgrade;
- manter um único head esperado;
- adicionar teste que valide o carregamento/topologia do grafo, além do teste
  de aplicação SQL em PostgreSQL.

## Correções importantes

### R3-T6-03 — Fingerprint ausente falha aberto e não possui caminho de backfill

Arquivos:

- `backend/alembic/versions/0003_canonical_fingerprint.py`;
- `backend/src/rag/application/index.py`;
- `backend/src/rag/application/ingest.py`;
- `backend/src/rag/domain/library.py`.

Requisitos afetados:

- SPEC §6;
- AC-03 e AC-15;
- R2-T6-03.

A coluna é nullable e o domínio usa `canonical_fingerprint: str | None = None`.
O indexador só compara quando o valor existe:

```python
if edition.canonical_fingerprint is not None and ...:
```

Logo, todas as edições criadas antes da migration continuam no comportamento
anterior: a comparação integral é simplesmente ignorada. Reexecutar `rag
ingest` não corrige isso porque o fluxo idempotente retorna a edição existente
antes de extrair o documento e antes de calcular/persistir o fingerprint.

Não há comando administrativo ou migration de dados para backfill, e o
indexador não falha fechado quando o campo está ausente. O texto da resposta,
portanto, só é verdadeiro para novas edições criadas depois da migration.

Correção esperada:

- definir uma estratégia explícita para edições existentes;
- preferencialmente fazer backfill controlado a partir do artefato imutável e
  da versão de extração conhecida, ou exigir reingestão/reextração explícita
  antes da indexação;
- se não houver fingerprint, `rag index` deve falhar fechado com erro tipado,
  nunca pular silenciosamente a verificação;
- após o backfill, impor `NOT NULL` se compatível com a estratégia aprovada;
- testar upgrade com edição preexistente, reingestão idempotente e tentativa de
  indexar uma edição sem fingerprint.

### R3-T6-04 — A correção de conteúdo sem seção também mascara caminhos não vazios inválidos

Arquivo:

- `backend/src/rag/application/index.py`.

Requisitos afetados:

- associação exata entre passagem e seção;
- AC-03;
- R2-T6-02;
- T6-02 da primeira revisão.

O código passou de “qualquer seção ausente falha” para “qualquer seção ausente
vira `section_id=None`”:

```python
section = section_by_path.get(node.section_path)
section_id = section.id if section is not None else None
```

A exceção necessária é somente `node.section_path == ()`. Um caminho não vazio
sem seção correspondente representa divergência estrutural e deve falhar
fechado. Aceitá-lo como conteúdo não seccionado recupera parte do comportamento
que motivou T6-02 na primeira revisão.

Correção esperada:

- permitir `section_id=None` exclusivamente para `section_path == ()`;
- levantar `IngestionError` sanitizado se um caminho não vazio não puder ser
  resolvido;
- testar documento sem headings, front matter com caminho vazio e caminho não
  vazio inválido.

### R3-T6-05 — Nenhum teste foi adicionado para as mudanças da ROUND2

Arquivos:

- `backend/tests/`;
- `Makefile`;
- `docs/rag/REVIEW_RESPONSE_T06_ROUND2.md`.

O diff `aff44b6..f6cd3d3` não modifica nenhum arquivo sob `backend/tests`. Os
“50 passed” citados são testes preexistentes de chunking/canonical/library; eles
não incluem:

- bloco alinhado dividido entre filhos;
- documento sem headings no serviço de indexação;
- front matter anterior ao primeiro heading;
- fingerprint mudando por bloco/path/offset/original enquanto páginas e
  headings permanecem iguais;
- persistência e leitura do fingerprint;
- upgrade/downgrade da nova migration;
- edição legada sem fingerprint.

Isso explica por que a suíte permanece verde apesar de R3-T6-01 e R3-T6-02
serem reproduzíveis.

O target `make test-contract` agora passa, mas inclui explicitamente um arquivo
que continua em `tests/unit`; consequentemente, os mesmos 26 testes são
executados tanto em `make test-unit` quanto em `make test-contract`. A resposta
afirma que “não houve duplicação de testes”, porém há duplicação entre os gates.
A organização preferível é mover os contract tests para `tests/contract` e
deixar cada target responsável por uma categoria disjunta.

Correção esperada:

- adicionar testes de regressão junto com cada correção;
- colocar os testes HTTP simulados na suíte de contrato;
- registrar contagens sem apresentar testes preexistentes como evidência de
  cenários que eles não exercitam.

### R3-T6-06 — R2-T6-05 foi corrigido apenas parcialmente

Arquivo:

- `docs/rag/EVIDENCE.md`, seção T06.

O commit altera somente o nome do teste de force. Permanecem as afirmações
obsoletas já listadas na ROUND2:

- linhas 475–483 ainda dizem que `--force` apaga passagens existentes;
- linhas 497–506 ainda descrevem o serviço destrutivo e não mencionam
  `IndexRun`, `ExtractionVersion` ou `ModelEndpointVersion`;
- linhas 557–562 ainda declaram como limitações que o histórico é removido e
  que nenhuma `ExtractionVersion` é registrada;
- as contagens continuam registrando 263 testes, enquanto o estado atual
  executa 278.

Portanto, R2-T6-05 não está resolvido e a evidência entregue continua
contradizendo a implementação.

Correção esperada:

- atualizar integralmente a seção T06, não apenas um nome de teste;
- marcar descrições históricas como superadas caso precisem permanecer;
- registrar a rodada atual com comandos e contagens reais somente após os
  novos testes e a integração passarem.

## Avaliação item a item da resposta ROUND2

- **R2-T6-01: não corrigido.** A reprodução original continua falhando.
- **R2-T6-02: parcialmente corrigido.** Caminho vazio pode virar
  `section_id=None`, mas qualquer caminho desconhecido também é aceito e não há
  testes.
- **R2-T6-03: não corrigido de forma utilizável.** O fingerprint é uma direção
  adequada para novas edições, porém a migration invalida o grafo e valores
  ausentes pulam a validação.
- **R2-T6-04: gate passa, organização incompleta.** `make test-contract` executa
  26 testes, mas os duplica com a suíte unitária.
- **R2-T6-05: não corrigido.** Apenas uma referência de teste foi atualizada.

## Evidências executadas nesta revisão

| Comando | Resultado |
|---------|-----------|
| `make lint` | OK — Ruff e ESLint |
| `make format-check` | OK — backend e frontend |
| `make typecheck` | OK — mypy em 72 arquivos e TypeScript |
| `make test-unit` | OK — 278 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `uv run pytest tests/unit/test_chunking.py tests/unit/test_canonical.py tests/unit/test_library.py -q` | OK — 50 passed |
| probe de `citable_text` de R3-T6-01 | **falha reproduzida** — três filhos diferentes retornam o mesmo bloco integral |
| `.venv/bin/alembic -c alembic.ini heads` | **falha** — revisão pai inexistente, `KeyError` |

`make test-integration` não foi repetido: o ambiente continua sem socket Docker
acessível, e o carregamento estático do grafo Alembic já demonstra que a suíte
falharia durante a preparação das migrations mesmo com PostgreSQL disponível.

Não foi executado `make audit` nesta rodada. Nenhuma dependência foi adicionada
pelo commit revisado.

## Critérios relacionados

- **AC-03: falha.** Citação e chunk ainda divergem; caminhos de seção não vazios
  podem falhar abertos.
- **AC-12: parcial conforme o cronograma.** Relações pai/filho permanecem, mas o
  texto que futuramente serviria de evidência primária continua incorreto.
- **AC-15: falha/evidência insuficiente.** O grafo de migrations está inválido,
  edições sem fingerprint pulam a proteção e a documentação contradiz o estado
  atual.

## Conclusão

O commit `f6cd3d3` não resolve o bloqueador de citação e introduz um novo
bloqueador de migrations. A ideia de fingerprint canônico e o suporte a
conteúdo com caminho de seção vazio são adequados, mas precisam falhar fechados,
ser migráveis e receber testes específicos. T06 permanece **reprovada** até
R3-T6-01 e R3-T6-02 serem corrigidos, seguidos pela resolução e evidência dos
demais itens.
