# Quarta revisão T06 — Validação da resposta à ROUND3

Data: 2026-08-30  
Branch revisada: `T06`  
Estado revisado: alterações não commitadas sobre `f6cd3d3`  
Resposta avaliada: `docs/rag/REVIEW_RESPONSE_T06_ROUND3.md`  
Resultado: **correções obrigatórias**

## Resumo executivo

A resposta ROUND3 corrigiu dois pontos de forma verificável:

- o grafo Alembic volta a ter um único head (`0004` após `0003`);
- o caso EPUB alinhado, de um único bloco dividido entre três filhos, agora
  retorna o texto exato de cada filho.

Também foi restaurada a falha fechada para `section_path` não vazio sem seção
persistida, e uma edição sem fingerprint agora é rejeitada explicitamente.

T06 ainda não pode ser aprovada. A correção de texto citável continua
incompleta para PDF: quando um filho atravessa dois blocos e contém apenas parte
de um deles, `_original_text_of()` troca a quebra de bloco (`\n`) por espaço.
Os offsets recompõem o texto com a quebra de linha, enquanto `citable_text`
retorna uma string diferente. Isso mantém a falha de AC-03.

Além disso, edições existentes ficam sem fingerprint e não há operação de
backfill ou reingestão capaz de corrigir o estado. A resposta cita testes de
fingerprint existentes, mas nenhum teste desse tipo existe no repositório. A
matriz `EVIDENCE.md` permanece no estado obsoleto já apontado nas duas rodadas
anteriores.

## Bloqueador

### R4-T6-01 — `citable_text` de PDF ainda diverge da recomposição pelos offsets

Arquivos:

- `backend/src/rag/domain/chunking.py`;
- `backend/src/rag/domain/library.py`;
- `backend/tests/unit/test_chunking.py`.

Requisitos afetados:

- SPEC §7.3: associação exata entre chunk e origem;
- SPEC §9.2: trecho literal e offsets de destaque;
- TASKS T06: offsets recompõem o trecho original;
- AC-03;
- R2-T6-01 e R3-T6-01.

A nova primeira condição de `_original_text_of()` corrige o caso em que um
bloco alinhado é dividido:

```python
if any(by_block[ordinal] != totals[ordinal] for ordinal in by_block):
    return " ".join(sentence.text for sentence in sentences)
```

Porém, `" ".join(...)` não preserva os separadores físicos entre blocos. Para
PDF, `_node_from_sentences()` constrói `node.text` por `_slice_text()`, isto é,
pela fatia exata de `CanonicalPage.text`; blocos na página são separados por
`\n`. Assim, um filho que cobre o fim de um bloco e parte do seguinte recebe:

- `text`/offsets: `"bloco 1\nfrase do bloco 2"`;
- `original_text`/`citable_text`: `"bloco 1 frase do bloco 2"`.

Reprodução executada em `backend/`:

```bash
.venv/bin/python - <<'PY'
from rag.domain.canonical import (
    BlockKind,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalPage,
)
from rag.domain.chunking import ChunkingParams, chunk_document
from rag.domain.enums import SourceType

b1 = "Primeira frase completa."
b2 = "Segunda frase longa completa. Terceira frase longa completa."
page = b1 + "\n" + b2
doc = CanonicalDocument(
    source_type=SourceType.PDF_TEXT,
    pages=(CanonicalPage(physical_index=0, text=page),),
    blocks=(
        CanonicalBlock(
            ordinal=0,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text=b1,
            original_text=b1,
            section_path=("C",),
            page_index=0,
            char_start=0,
            char_end=len(b1),
        ),
        CanonicalBlock(
            ordinal=1,
            kind=BlockKind.PARAGRAPH,
            level=0,
            text=b2,
            original_text=b2,
            section_path=("C",),
            page_index=0,
            char_start=len(b1) + 1,
            char_end=len(page),
        ),
    ),
)
for node in chunk_document(
    doc,
    ChunkingParams(
        child_target_tokens=14,
        child_overlap_tokens=0,
        parent_target_tokens=1000,
    ),
):
    if node.parent_index is not None:
        rebuilt = page[node.char_start : node.char_end]
        print(repr(node.text), repr(node.original_text), node.original_text == rebuilt)
PY
```

Resultado relevante:

```text
'Primeira frase completa.\nSegunda frase longa completa.'
'Primeira frase completa. Segunda frase longa completa.'
False
```

O teste novo cobre apenas EPUB, um único bloco e ausência de offsets; por isso
passa sem detectar a divergência acima.

Correção esperada:

- para PDF, garantir por construção que `citable_text` seja exatamente a fatia
  recomposta pelos offsets;
- não reconstruir separadores com `" ".join(...)` quando existe uma fonte
  endereçável (`CanonicalPage.text`);
- uma política simples e segura é usar `node.text` como texto citável para PDF,
  pois ele já vem de `_slice_text()`; se o original bruto precisar ser citado,
  o schema deverá fornecer offsets alinhados nesse original;
- adicionar regressão PDF com múltiplos blocos, bloco parcial, overlap e
  `original_text != text`;
- testar a propriedade diretamente:
  `passage.citable_text == recompor(page_start, char_start, page_end, char_end)`.

## Correções importantes

### R4-T6-02 — Edições existentes ficam permanentemente não indexáveis

Arquivos:

- `backend/alembic/versions/0004_canonical_fingerprint.py`;
- `backend/src/rag/application/ingest.py`;
- `backend/src/rag/application/index.py`;
- repositories de edição;
- CLI administrativa.

Requisitos afetados:

- SPEC §6;
- AC-15;
- R3-T6-03.

A mudança para falhar fechado quando `canonical_fingerprint is None` é correta.
Entretanto, a migration adiciona a coluna como nullable e não preenche edições
existentes. Não há comando de backfill ou reingestão administrativa
implementado.

A mensagem orienta “execute o backfill ou reingestão administrativa”, mas:

- não existe comando de backfill;
- `rag ingest` de um arquivo já conhecido encontra a edição por
  `source_sha256` e retorna nas linhas 180–186 de `application/ingest.py`;
- esse retorno ocorre antes da extração, do cálculo de fingerprint e de
  qualquer atualização da edição.

Portanto, a operação sugerida pelo erro não existe no produto atual. Depois do
upgrade, toda edição criada antes da coluna fica não indexável, inclusive por
`--force`.

Correção esperada:

- implementar e documentar uma operação explícita, transacional e auditável de
  backfill/reextração; ou
- fazer uma reingestão explicitamente autorizada calcular e registrar o
  fingerprint sem alterar silenciosamente a identidade da edição;
- registrar qual versão/algoritmo realizou o backfill e falhar se a extração
  não corresponder às páginas/seções persistidas;
- testar upgrade com edição existente, tentativa antes do backfill, backfill,
  indexação posterior e divergência durante o backfill;
- considerar `NOT NULL` após a transição, se a estratégia de dados permitir.

### R4-T6-03 — A resposta alega testes de fingerprint que não existem

Arquivos:

- `backend/tests/`;
- `docs/rag/REVIEW_RESPONSE_T06_ROUND3.md`.

A resposta afirma:

> “As demais proteções são exercitadas pelos testes existentes de fingerprint,
> offsets e chunking.”

Busca executada:

```text
rg -n 'canonical_fingerprint|fingerprint\(' backend/tests
```

Resultado: nenhuma ocorrência.

O diff da ROUND3 adiciona somente
`test_split_aligned_block_has_exact_text_per_child`. Não há testes para:

- determinismo do fingerprint;
- alteração do fingerprint ao mudar bloco, path, offset, original ou warning;
- persistência e leitura na edição;
- rejeição de fingerprint divergente;
- rejeição de fingerprint ausente;
- conteúdo sem seção e caminho não vazio inválido;
- aplicação/rollback da migration `0004`;
- upgrade de edição preexistente.

Correção esperada:

- adicionar testes positivos, negativos e de falha para todas as mudanças;
- não citar cobertura inexistente como evidência;
- reexecutar integração PostgreSQL antes de declarar persistência corrigida.

### R4-T6-04 — A matriz de evidências continua obsoleta

Arquivo:

- `docs/rag/EVIDENCE.md`, linhas 468–562.

Requisito afetado:

- entrega e definição de pronto do `AGENTS.md`/TASKS;
- AC-15 enquanto evidência de reprodução;
- R2-T6-05, R3-T6-06.

Nenhuma alteração em `EVIDENCE.md` foi feita nesta rodada. Permanecem:

- `--force` descrito como exclusão de passagens nas linhas 480 e 504–506;
- ausência de descrição de `IndexRun`, `ExtractionVersion` e
  `ModelEndpointVersion` no serviço atual;
- contagem antiga de 263 testes, embora esta revisão tenha executado 279;
- limitação falsa de que histórico é removido e nenhuma `ExtractionVersion` é
  registrada, linhas 559–562;
- ausência completa do fingerprint canônico e da migration `0004`.

Correção esperada:

- substituir a seção T06 pelo comportamento atual e evidências atuais;
- se o histórico da implementação inicial for mantido, marcá-lo claramente
  como superado;
- registrar resultados somente depois de migrations e integração passarem.

### R4-T6-05 — Gate de contrato ainda duplica a suíte unitária

Arquivos:

- `Makefile`;
- `backend/tests/unit/test_embedding_adapter.py`;
- `backend/tests/contract/`.

`make test-contract` passa com 26 testes, mas coleta explicitamente um arquivo
sob `tests/unit`; `make test-unit` coleta o mesmo arquivo novamente. O item não
é bloqueador de comportamento, mas contradiz a afirmação da ROUND2 de que não
há duplicação e mantém categorias de evidência sobrepostas.

Correção esperada:

- mover os contract tests HTTP para `tests/contract`;
- manter `test-unit` e `test-contract` disjuntos;
- registrar as contagens corretas de cada categoria.

## Avaliação item a item da resposta ROUND3

- **R3-T6-01: parcialmente corrigido.** O caso EPUB de um bloco foi corrigido,
  mas PDF com múltiplos blocos ainda viola a igualdade entre citação e offsets.
- **R3-T6-02: corrigido no grafo estático.** `alembic heads` retorna somente
  `0004`. Aplicação e rollback em PostgreSQL continuam sem evidência nesta
  rodada.
- **R3-T6-03: parcialmente corrigido.** Falha fechada foi adicionada, mas não há
  operação capaz de tornar edições legadas válidas.
- **R3-T6-04: corrigido no código.** Somente caminho vazio pode resultar em
  `section_id=None`; faltam testes.
- **R3-T6-05: não corrigido.** Foi adicionado apenas um teste; as demais
  proteções citadas não possuem cobertura.
- **R3-T6-06: não abordado.** `EVIDENCE.md` permanece obsoleto.

## Evidências executadas nesta revisão

| Comando | Resultado |
|---------|-----------|
| `make lint` | OK — Ruff e ESLint |
| `make format-check` | OK — backend e frontend |
| `make typecheck` | OK — mypy em 72 arquivos e TypeScript |
| `make test-unit` | OK — 279 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `uv run pytest tests/unit/test_chunking.py -q` | OK — 20 passed |
| `.venv/bin/alembic -c alembic.ini heads` | OK — `0004 (head)` |
| probe EPUB original da ROUND3 | OK — três filhos com `original_text == text` |
| probe PDF multi-bloco de R4-T6-01 | **falha reproduzida** — citação usa espaço e offsets recompõem `\n` |

`make test-integration` não foi executado porque o ambiente continua sem daemon
Docker/socket acessível. Diferentemente da ROUND3 anterior, o grafo estático
Alembic agora carrega corretamente; ainda falta evidência real da migration,
persistência e fluxos de indexação.

Não foi executado `make audit`. Nenhuma dependência foi adicionada nesta
rodada.

## Critérios relacionados

- **AC-03: falha.** `citable_text` de PDF ainda pode divergir do intervalo
  indicado pelos offsets.
- **AC-12: parcial conforme o cronograma.** A hierarquia pai/filho permanece,
  mas a passagem ainda não é uma evidência primária confiável em todos os
  casos.
- **AC-15: evidência insuficiente.** O grafo de migrations foi corrigido, mas
  não há transição para dados existentes, testes de fingerprint ou integração
  PostgreSQL, e a matriz permanece incorreta.

## Conclusão

As correções da ROUND3 avançam o estado da implementação, especialmente ao
restaurar a topologia Alembic e a falha fechada. Contudo, R4-T6-01 mantém o
bloqueador central de associação entre citação e origem. T06 continua
**reprovada** até que a propriedade de igualdade entre `citable_text` e offsets
seja garantida e testada para PDF, seguida pela implementação/evidência da
transição de fingerprint e atualização completa da matriz.
