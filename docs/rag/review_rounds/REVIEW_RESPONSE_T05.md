# Resposta à revisão T05

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T05.md`
Resultado: **T5-01–T5-10 corrigidos**. Sem discordâncias técnicas — todos os
achados foram aceitos e endereçados.

## T5-01 — O PDF derivado por OCR não contém a varredura original (bloqueador) ✅

Arquivos: `backend/src/rag/adapters/pdf_writer.py`, `backend/tests/unit/test_ocr.py`,
`backend/tests/fixtures/builders.py`.

`write_text_layer_pdf` foi reescrito: em vez de construir um PDF novo com
páginas vazias, abre o PRÓPRIO `source_pdf` via `pypdfium2` e insere objetos
de texto invisíveis (`FPDFPageObj_NewTextObj` + `FPDFTextObj_SetTextRenderMode`
com `FPDF_TEXTRENDERMODE_INVISIBLE`) diretamente nas páginas existentes, via
`FPDFPage_InsertObject` + `FPDFPage_GenerateContent`. O documento salvo é o
original com a camada sobreposta — imagem, contagem de páginas, dimensões e
rotação vêm do próprio PDF, não são reconstruídas.

Nova fixture `make_scanned_pdf` (usa `pypdfium2` para desenhar um retângulo —
conteúdo visual real, sem texto) substitui a antiga página vazia como
"varredura" nos testes.

Testes: `test_text_layer_preserves_visual_content_and_is_searchable` renderiza
original e derivado e compara os pixels (`_render_extrema` idêntico e não
branco) além de provar o texto pesquisável; `test_page_count_mismatch_rejected`
cobre contagem de páginas divergente do original.

## T5-02 — O writer de OCR falha com Unicode comum em livros (bloqueador) ✅

Arquivo: `backend/src/rag/adapters/pdf_writer.py`.

Resolvido pela mesma reescrita do T5-01: `FPDFText_SetText` recebe a string em
UTF-16LE nativamente — não há mais escape/encoding manual em Latin-1. Testado
com travessão, aspas curvas e acentuação portuguesa.

Teste: `test_unicode_text_round_trips` (`"Acentuação: ção, ã, é — travessão,
"aspas curvas", café."` round-trip completo via extração real do PDF).

## T5-03 — Reingestão aceita metadados divergentes como idênticos (bloqueador) ✅

Arquivo: `backend/src/rag/application/ingest.py`.

`_assert_coherent` agora recebe também o `Work` existente e o `source_type`
resolvido, e compara toda a identidade imutável da ingestão: `title`,
`source_type`, `publisher`, `publication_year`, `edition_label`, `isbn`,
`license_status` e, via `Work`, `original_title`, `language` e `authors`
(ordenados por `ordinal`). Divergência em qualquer campo continua sendo
`ConflictError`, agora com o nome do campo divergente em `context["fields"]`.

Testes: `test_divergent_metadata_field_conflicts` (parametrizado: authors,
publisher, publication_year, license_status, original_title — cada um
verificado via `exc_info.value.context["fields"]`) e
`test_source_type_divergence_rejected` (mesmo arquivo, `source_type` diferente
na segunda tentativa).

## T5-04 — Proveniência do OCR declarada mas não validada (bloqueador) ✅

Arquivos: `backend/src/rag/adapters/ocr_adapter.py`,
`backend/src/rag/application/ingest.py`.

Novo contrato `OcrProvenance` (pydantic, frozen): `input_sha256`,
`output_sha256`, `engine`, `engine_version`, `pages`, `created_at`. `rag ocr`
grava esse sidecar (`<output>.provenance.json`) ao lado do derivado.
`_store_ocr_derivative` em `ingest.py` agora:

1. carrega o sidecar (`load_provenance`) — falha fechado se ausente ou
   inválido;
2. confere `output_sha256` contra o hash do arquivo informado (detecta
   adulteração pós-`rag ocr`);
3. confere `input_sha256` contra o hash do arquivo original sendo ingerido
   (impede associar derivado de outro livro);
4. confere `pages` contra a contagem extraída do derivado;
5. usa `engine`/`engine_version` reais do sidecar como `generator` do
   `DerivedArtifactRef` (antes: string fixa `"docling-ocr"`).

Testes: `test_ocr_artifact_without_provenance_sidecar_rejected`,
`test_ocr_artifact_from_different_original_rejected`,
`test_ocr_artifact_page_count_mismatch_rejected`;
`test_scan_ingest_preserves_original_identity` verifica
`derived.generator == "stub:stub-0"`.

## T5-05 — Não existe o e2e de OCR real declarado (correção importante) ✅

Arquivos: `backend/tests/unit/test_ocr.py`, `backend/tests/fixtures/builders.py`,
`backend/tests/integration/test_ingest.py`.

- adicionado `test_real_engine_scan_to_ingest_pipeline`, gate opcional
  `RAG_OCR_E2E=1`, usando `DoclingOcrEngine(engine="rapidocr")` (motor real,
  já disponível localmente, sem `StubEngine`) sobre `make_scanned_pdf`: valida
  imagem preservada, hashes e alinhamento de páginas do sidecar;
- a fixture de "scan" deixou de ser uma página PDF vazia — `make_scanned_pdf`
  desenha conteúdo visual real;
- os testes de integração de `pdf_scan` (`TestPdfScan`) agora chamam
  `ocr_pdf()` de verdade em vez de fabricar o "derivado OCR" com
  `make_text_pdf` diretamente — exercitam o pipeline real ponta a ponta
  (com `StubEngine` para determinismo; o e2e opcional cobre o motor real).

## T5-06 — Proveniência com múltiplos spans é truncada (correção importante) ✅

Arquivo: `backend/src/rag/adapters/docling_adapter.py`.

Novo helper `_spans_of(item, label)`: item sem `prov` → uma posição sem
página (EPUB); um único `prov` → comportamento anterior preservado; mais de
um `prov` → cada entrada é dividida por página usando seu `charspan`
(`item.text[start:end]`); um `charspan` ausente/inválido falha fechado com
`IngestionError` em vez de atribuir o texto inteiro à primeira página.

Testes: `test_multi_page_provenance_splits_into_per_page_blocks` (texto
"PARTE UM PARTE DOIS" com prov em 2 páginas → dois blocos, um por página, com
o texto exato de cada `charspan`); `test_multi_page_provenance_without_charspan_fails_closed`.

## T5-07 — Combinações inválidas extensão×source_type aceitas (correção importante) ✅

Arquivo: `backend/src/rag/application/ingest.py`.

`_resolve_source` agora valida contra uma matriz fechada
(`_VALID_SOURCE_TYPES_BY_SUFFIX`: `.pdf` → `{pdf_text, pdf_scan}`, `.epub` →
`{epub}`) — cobre as duas direções (antes só EPUB declarado `pdf_text`/
`pdf_scan` era verificado; `.pdf` declarado `epub` não).

Teste: `TestExtensionSourceTypeMatrix` (as duas direções, cada uma com seu
próprio teste).

## T5-08 — Warnings não sobrevivem à ingestão (correção importante) ✅

Arquivos: `backend/alembic/versions/0002_edition_extraction_warnings.py`,
`backend/src/rag/domain/library.py`,
`backend/src/rag/infrastructure/repositories/editions.py`,
`backend/src/rag/application/ingest.py`, `backend/src/rag/cli/main.py`.

Nova migration `0002` adiciona `editions.extraction_warnings jsonb NOT NULL
DEFAULT '[]'`. `Edition.extraction_warnings: tuple[str, ...]` é persistido na
criação da edição (`canonical.warnings`) e lido de volta por
`EditionsRepository.get`/`get_by_source_hash`. `rag inspect` agora imprime os
warnings da edição.

Testes: `test_extraction_warnings_roundtrip`,
`test_extraction_warnings_default_empty` (contra PostgreSQL real, valida o
round-trip via jsonb).

## T5-09 — Conteúdo textual descartado sem política completa (correção importante) ✅

Arquivo: `backend/src/rag/adapters/docling_adapter.py`.

`footnote` e `caption` saíram de `_SKIPPED_WITH_WARNING` e entraram em
`_PARAGRAPH_LABELS` — são texto citável de livro, não mobília. Qualquer
outro rótulo de `TextItem` que não seja heading/parágrafo/mobília/skip
conhecido (ex.: `code`) passa a gerar um warning nomeado
(`"N item(ns) de rótulo '<label>' ignorado(s): rótulo não mapeado na fase
1"`) em vez de desaparecer silenciosamente — cobre também itens não-`TextItem`
sem rótulo mapeado.

Testes: `test_footnote_and_caption_are_preserved_as_text`,
`test_unmapped_label_is_never_silently_dropped` (usa `doc.add_code(...)`).

## T5-10 — Logs podem vazar stack traces e caminhos (correção importante) ✅

Arquivo: `backend/src/rag/cli/main.py`.

Renderer trocado de `KeyValueRenderer` para `JSONRenderer` (alinhado ao
contrato geral do sistema — SPEC/T18: "logs JSON redigidos"). Novo processor
`_redact_exception`: remove `exc_info` do evento e expõe só `error_type`; o
traceback completo só é gravado se a variável `RAG_DEBUG_LOG` apontar para um
arquivo (destino interno explícito) — nunca vai ao console.

Testes: `TestRedactException` (remove caminho/segredo do evento e mantém
`error_type`; grava o traceback em `RAG_DEBUG_LOG` quando configurado; é
no-op sem `exc_info`).

## Ajustes de evidência

`docs/rag/EVIDENCE.md` atualizado: nova seção "T05 — Correções da revisão"
com o resumo acima, tabela de gates reexecutados (`make lock/lint/format-check/
typecheck/test/test-integration/audit/security-scan`, todos OK — 223
unitários + 2 skipped opcionais, 84 integração), e uma correção explícita
sobre "persistência em transação única" (vale só para o banco; blobs do
artifact store são gravados antes da transação e detectáveis por `audit()`
se órfãos).

## Evidência executada

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | OK — 223 unitários passed, 2 skipped (e2e opcionais); 1 frontend |
| `make test-integration` | OK — 84 passed |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |

Nenhuma dependência nova foi adicionada — `pypdfium2` já era transitiva via
Docling (usada previamente em testes; `pyproject.toml` já tinha um override
de mypy para o módulo).

# Resposta à segunda revisão (ROUND2)

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T05_ROUND2.md`
Resultado: **R5-01–R5-11 corrigidos**. Sem discordâncias técnicas.

## R5-01 — O e2e de OCR real falha quando habilitado (bloqueador) ✅

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`.

`RapidOcrOptions()` (sem argumentos) tem `backend="onnxruntime"` como padrão
da própria lib — pacote que nunca foi aprovado/declarado. `_ocr_options`
agora fixa explicitamente `RapidOcrOptions(backend="torch")`: o único backend
do RapidOCR já disponível no conjunto de dependências aprovado (`torch` e
`rapidocr` já eram transitivos via Docling). Nenhuma dependência nova.

Reexecutei o comando exato do relatório:

```text
RAG_OCR_E2E=1 uv run pytest \
  tests/unit/test_ocr.py::TestOcrPdf::test_real_engine_recognizes_text_and_preserves_image -q
```

Resultado: **1 passed**.

## R5-02 — `--dry-run` não valida a proveniência OCR (bloqueador) ✅

Arquivo: `backend/src/rag/application/ingest.py`.

A verificação de hashes do sidecar (`_verify_ocr_hashes`, extraída de
`_store_ocr_derivative`) agora roda incondicionalmente logo após
`_resolve_source`, ANTES de qualquer branch de retorno — inclusive antes do
lookup de deduplicação e antes de `--dry-run`. A checagem de contagem de
páginas (que depende da extração) roda logo após `self._extractor.extract`,
também antes do branch de dry-run. Nenhum retorno antecipado do método
consegue mais pular essas validações.

Testes (`TestPdfScanDryRun`): `test_dry_run_rejects_missing_provenance`,
`test_dry_run_rejects_different_original`,
`test_dry_run_rejects_page_count_mismatch` — os três confirmam também que
nada é persistido (`_assert_nothing_persisted`); e
`test_dry_run_accepts_valid_provenance_without_persisting` prova o caminho
positivo.

## R5-03 — Reingestão existente contorna a proveniência OCR (bloqueador) ✅

Arquivo: `backend/src/rag/application/ingest.py`.

`_assert_coherent` agora recebe a `OcrProvenance` já validada (hashes) e,
para `pdf_scan`, exige que seu `output_sha256` seja EXATAMENTE um dos
`derived_artifacts` já registrados na edição — reingestão não pode trocar
silenciosamente o derivado por outro (mesmo que este também aponte,
corretamente, para o mesmo original).

Testes: `test_reingest_with_same_derivative_is_idempotent` (caminho legítimo
continua idempotente — mesmo sha256, sucesso sem duplicar) e
`test_reingest_with_different_derivative_rejected` (outro derivado, mesma
proveniência íntegra, ainda assim conflita — `"ocr_artifact" in
context["fields"]`).

## R5-04 — PDF e sidecar não publicados como operação segura (bloqueador) ✅

Arquivos: `backend/src/rag/adapters/pdf_writer.py`,
`backend/src/rag/adapters/ocr_adapter.py`.

Nova função `publish_derivative_pair`: constrói e grava AMBOS os conteúdos
(PDF e sidecar) em arquivos temporários com `fsync` antes de qualquer
`os.replace`; só então publica os dois, um após o outro. Qualquer falha
durante a preparação (build do PDF, serialização do sidecar, escrita ou
fsync de qualquer um dos dois temporários) não toca nenhum caminho final —
um par publicado anteriormente permanece válido e íntegro. `ocr_pdf` foi
reescrito para construir os bytes do PDF (`build_text_layer_pdf`, extraído
de `write_text_layer_pdf`) e do sidecar ANTES de chamar essa função.

Testes: `TestPublishDerivativePair` (4 testes: publica os dois; falha na
escrita do PDF não toca nada; falha na escrita do sidecar — mesmo já com o
PDF em temporário — não toca nada; falha não destrói um par anterior válido)
e `test_publish_failure_leaves_previous_pair_intact` (no nível de `ocr_pdf`,
falha ao serializar a proveniência preserva a execução anterior).

## R5-05 — Teste "e2e" pode passar sem reconhecer texto (correção importante) ✅

Arquivos: `backend/tests/fixtures/builders.py`, `backend/tests/unit/test_ocr.py`,
`backend/tests/integration/test_ingest.py`.

Nova fixture `make_scanned_pdf_with_text`: rasteriza uma frase em português
como pixels (Pillow — `ImageFont.load_default(size=...)`, fonte TrueType
embutida desde Pillow ≥10.1, **nenhuma dependência nova**) e insere como
`PdfImage` — sem nenhuma camada de texto. O e2e em `test_ocr.py` agora
verifica: (1) o original não tem texto extraível; (2) `report.lines > 0`;
(3) as palavras esperadas aparecem no texto reconhecido do derivado
(comparação por conjunto de palavras — a ordem dos boxes do RapidOCR não é
estritamente da esquerda para a direita); (4) imagem pixel-idêntica (R5-06).

Novo e2e completo em `tests/integration/test_ingest.py`
(`TestOcrRealEngineToIngestE2E`), gate `RAG_OCR_E2E=1`: `rag ocr` (motor
real) → `service.ingest()` real → confirma texto reconhecido persistido em
`pages[0].text` e `DerivedArtifactRef` correto. Este SIM executa `rag
ingest`, o que a versão anterior do teste não fazia.

## R5-06 — Evidência de preservação visual compara só extremos (correção importante) ✅

Arquivo: `backend/tests/unit/test_ocr.py`.

`_render_extrema` foi substituída por `_renders_are_pixel_identical`
(`PIL.ImageChops.difference(...).getbbox() is None` — verdadeiramente pixel
a pixel, não só mínimo/máximo). Todos os testes de preservação visual agora
usam essa comparação. Novo teste
`test_rotated_page_preserves_rotation_and_pixels`: gera uma página com
`/Rotate 90` (`make_scanned_pdf(..., rotation=90)`), confirma
`get_rotation() == 90` no derivado E pixels idênticos ao original.

## R5-07 — Camada de texto sem informação suficiente para alinhamento (correção importante) ✅ (parcial, com escopo documentado)

Arquivo: `backend/src/rag/adapters/pdf_writer.py`,
`backend/src/rag/adapters/ocr_adapter.py`.

- `OcrPage.physical_index` (novo campo obrigatório): `build_text_layer_pdf`
  valida `ocr_page.physical_index == index` para cada página — associação
  deixa de depender apenas da posição na lista; um índice fora de ordem
  falha fechado (`test_physical_index_out_of_order_rejected`).
  `DoclingOcrEngine.recognize` popula com `page_no - 1`.
- `OcrLine.width` (novo campo opcional): quando informado, o texto invisível
  é escalado horizontalmente (âncora na borda esquerda) para ocupar
  exatamente a largura detectada pelo motor —
  `test_line_width_fits_bounding_box` confirma `right - left ≈ width`
  (tolerância 0.5pt) reabrindo o PDF gravado. `DoclingOcrEngine.recognize`
  agora calcula `width = bbox.r - bbox.l`.
- Rotação: como o derivado reutiliza a MESMA página física do original
  (nunca reconstrói geometria), `/Rotate` já era preservado sem código
  adicional — o que faltava era o teste (`test_rotated_page_preserves_rotation_and_pixels`,
  ver R5-06), agora presente.

Escopo não coberto, com justificativa: altura por linha continua vindo só de
`bbox.b`/`bbox.t` (sem ajuste fino adicional) — largura era a lacuna
explicitamente citada no relatório ("não ajusta a largura"); altura já era
usada para o tamanho da fonte antes desta correção. Alinhamento
pixel-perfeito de seleção seguirá sendo validado quando o leitor (T17)
implementar destaque real sobre o PDF.

## R5-08 — Versão registrada não é a versão real do motor (correção importante) ✅ (parcial, com escopo documentado)

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`.

Nova função `_engine_version(engine, ocr_options)`: sempre inclui a versão do
docling; quando a opção do motor expõe um parâmetro que determina a
implementação concreta (hoje: `backend` do RapidOCR), esse detalhe é
anexado — ex.: `docling-2.123.1+rapidocr-backend=torch` em vez de
`docling-2.123.1` genérico para qualquer motor.

Escopo não coberto, com justificativa verificável: (a) `auto` não é resolvido
a um motor concreto — Docling não expõe publicamente qual motor o modo
automático escolheu (confirmado lendo `docling.datamodel.pipeline_options` e
`docling.models.stages.ocr.*`: a seleção acontece internamente no pipeline,
sem um atributo público pós-conversão); introspectar isso exigiria depender
de detalhes internos não estáveis entre versões do Docling. (b) Versão do
binário do Tesseract e hash de modelos do RapidOCR/ocrmac não são
capturados — GetVersion por engine exigiria spawnar processos/ler arquivos de
modelo especificamente por motor, desproporcional ao objetivo do contrato de
proveniência (impedir associar artefato de outro livro — já garantido pelos
hashes de entrada/saída, independente de granularidade de versão do motor).
Recomendação registrada: usuários que precisem de atribuição exata de motor
devem evitar `--engine auto` em produção.

## R5-09 — `pypdfium2` como dependência direta não declarada (correção importante) ✅

Arquivo: `backend/pyproject.toml`.

Aprovado explicitamente pelo usuário nesta sessão. `pypdfium2==5.13.0`
(mesma versão já resolvida no lockfile) declarado em `dependencies`;
`uv lock` executado — lockfile inalterado em conteúdo (mesma resolução, 164
pacotes), agora com a dependência direta refletida no manifesto.

## R5-10 — Contrato de logs não corresponde ao comportamento (correção importante) ✅

Arquivo: `backend/src/rag/cli/main.py`.

- `structlog.configure(..., logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))`
  — fábrica explícita; confirmado por probe independente (`stdout=''`,
  `stderr='{"event": "probe", ...}'`).
- `RAG_DEBUG_LOG`: arquivo aberto com `os.open(..., 0o600)` (não depende do
  umask do processo); conteúdo passa por `_redact_secrets` (regex para
  `password|passwd|secret|token|api[_-]?key` e cabeçalhos `Authorization`)
  antes de gravar — confirmado com uma exceção contendo
  `password=hunter2 secreto`: gravado como
  `password=***REDACTED*** secreto`.
- Toda a escrita do debug log está em um único `try/except OSError: pass` —
  uma falha ao abrir/escrever o arquivo de debug (caminho inválido,
  permissão negada) nunca propaga nem interrompe o processor de log;
  confirmado chamando `_write_debug_log` com um caminho inexistente.

Testes: `TestRedactException` (unit, `tests/unit/test_cli.py`) cobre remoção
de traceback do console, gravação em `RAG_DEBUG_LOG` e no-op sem `exc_info`.

## R5-11 — `original_text` perdido ao dividir múltiplos spans (correção importante) ✅

Arquivo: `backend/src/rag/adapters/docling_adapter.py`.

`_spans_of` agora recorta `item.orig[start:end]` (mesmos índices do
`charspan`) SOMENTE quando `len(item.orig) == len(item.text)` — alinhamento
1:1 garantido. Quando os comprimentos divergem, cai para o texto normalizado
do próprio span: nunca alega uma equivalência falsa com o original, apenas
não preserva fidelidade extra além do já normalizado nesse caso específico.

Testes: `test_multi_page_provenance_preserves_original_when_aligned` (orig
minúsculo, mesmo comprimento — original_text preserva o case original) e
`test_multi_page_provenance_falls_back_when_orig_misaligned` (orig de
comprimento diferente — cai para o texto normalizado, sem erro).

## Gates finais (2026-08-29, após R5-01–R5-11)

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | OK — 233 unitários passed, 2 skipped (e2e opcionais); 1 frontend |
| `make test-integration` | OK — 90 passed, 1 skipped (e2e opcional) |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `RAG_OCR_E2E=1` (unit, motor real rapidocr/torch) | OK — 1 passed |
| `RAG_OCR_E2E=1` (integration, e2e completo até `rag ingest`) | OK — 1 passed |

Dependência declarada nesta rodada: `pypdfium2==5.13.0` (direta, aprovada
pelo usuário — antes só transitiva via Docling). Nenhuma outra dependência
nova.

# Resposta à terceira revisão (ROUND3)

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T05_ROUND3.md`
Resultado: **R6-01–R6-06 corrigidos**. Sem discordâncias técnicas.

## R6-01 — Falha no segundo rename publica PDF e sidecar de versões diferentes (bloqueador) ✅

Arquivos: `backend/src/rag/adapters/pdf_writer.py`,
`backend/src/rag/adapters/ocr_adapter.py`, `backend/src/rag/application/ingest.py`.

Concordo com o diagnóstico: preparar os dois temporários antes dos renames
reduz a janela, mas dois `os.replace()` consecutivos nunca são atômicos em
conjunto — uma falha entre os dois é exatamente o cenário reproduzido pela
revisão. Em vez de inventar um protocolo de commit de dois arquivos (que
teria a mesma classe de problema em outra forma — manifesto + diretório
versionado ainda são dois objetos a coordenar), escolhi a opção mais simples
das sugeridas pela própria revisão: **um único artefato**.

A proveniência agora é embutida como um ANEXO dentro do próprio PDF derivado
(`pdf_writer.embed_attachment`/`read_attachment`, via
`PdfDocument.new_attachment`/`get_attachment` do pypdfium2) antes da
gravação. `ocr_pdf()` constrói o PDF completo (camada de texto + anexo de
proveniência) em memória, computa o sha256 do resultado final, e publica com
uma ÚNICA `publish_file()` (temporário + fsync + rename + fsync de
diretório) — a mesma primitiva atômica de arquivo único já usada e testada
desde T04. Não há mais um segundo arquivo para dessincronizar, então a
classe de bug relatada deixa de existir por construção, não por uma janela
mais estreita.

Consequência no contrato: `OcrProvenance.output_sha256` foi removido — ele
só existia para detectar "o sidecar e o PDF divergiram", uma pergunta que não
faz mais sentido quando os dois são o mesmo arquivo. `_verify_ocr_hashes` e
`_assert_coherent` (`ingest.py`) passaram a usar o sha256 computado na hora a
partir do próprio arquivo (`sha256_of_file`) onde antes comparavam contra o
campo removido.

Testes: `test_rename_failure_does_not_destroy_previous_valid_file`
(`pdf_writer`, mocka `os.replace` para falhar e confirma que o arquivo
anterior não foi tocado nem ficou incoerente) e
`test_publish_failure_leaves_previous_file_intact` (nível `ocr_pdf`, mesma
verificação incluindo a proveniência lida de volta). Os testes antigos de
`TestPublishDerivativePair` foram removidos (a função não existe mais).

## R6-02 — O motor real emite caminhos absolutos fora do structlog (correção importante) ✅

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`.

RapidOCR usa `logging.getLogger("RapidOCR")` com seu próprio
`StreamHandler` (stderr), e o Torch emite `UserWarning` via
`warnings.warn` — ambos por fora do pipeline structlog do CLI.
`_harden_third_party_logging()` (chamada uma vez, em
`DoclingOcrEngine.__init__`, idempotente):

- adiciona um `logging.Filter` ao logger `RapidOCR` e a cada um de seus
  handlers, e a `logging.lastResort` (usado por qualquer logger sem handler
  configurado ao longo da hierarquia) — o filtro reescreve a mensagem
  reduzindo qualquer caminho absoluto ao nome do arquivo;
- substitui `warnings.showwarning` por uma versão que aplica a mesma
  redação antes de escrever.

Não haverá garantia de cobrir toda e qualquer biblioteca de terceiros no
futuro (novo logger com nome desconhecido escaparia), mas os dois emissores
identificados na reprodução do relatório (RapidOCR e Torch) estão cobertos.

Teste: `test_real_engine_does_not_leak_absolute_paths_to_console`
(`RAG_OCR_E2E=1`) — reproduz o cenário do relatório em subprocesso real,
captura stdout/stderr separadamente e confirma ausência de caminho absoluto
em ambos.

## R6-03 — `original_text` continua semanticamente incorreto no fallback (correção importante) ✅

Arquivo: `backend/src/rag/adapters/docling_adapter.py`.

Escolhi a terceira opção listada pela revisão: representar a perda no
schema existente (via warning), sem inventar um campo novo nem falhar
fechado (o texto normalizado ainda é útil para recuperação/citação, só não é
o "original" literal). `_spans_of` agora retorna também um booleano
`original_is_fiel`; `_to_canonical` conta quantos blocos caíram no fallback e
emite `"N bloco(s) sem texto original preservável: ... original_text contém
o texto normalizado, não o original de fato (R6-03)"` no
`CanonicalDocument.warnings` — o mesmo canal já usado (e persistido desde
T5-08) para toda perda de fidelidade da extração.

Testes: `test_multi_page_provenance_falls_back_when_orig_misaligned`
(atualizado para confirmar o warning) e
`test_multi_page_provenance_aligned_emits_no_fidelity_warning` (garante que
o warning NÃO aparece quando o alinhamento é possível).

## R6-04 — Geometria declarada não é validada contra a página (correção importante) ✅

Arquivo: `backend/src/rag/adapters/pdf_writer.py`.

- `_check_geometry` (nova): compara `OcrPage.width`/`height` contra
  `page.get_width()`/`get_height()` com tolerância de 1pt; divergência real
  falha fechado antes de qualquer inserção de texto na página;
- `FPDFPage_GenerateContent()` agora tem seu retorno verificado — falha
  fechado em vez de publicar uma página cuja geração de conteúdo pode ter
  falhado silenciosamente;
- `_fit_width` passou a falhar fechado (`StorageError`) quando
  `FPDFPageObj_GetBounds` não confirma os limites do objeto, em vez de
  retornar em silêncio como antes.

Descoberta ao testar: uma página rotacionada 90°/270° tem
`get_width()`/`get_height()` (e o `page.size` que o Docling reporta)
EFETIVOS — já com a rotação aplicada (612×792 vira 792×612) — não o
mediabox bruto; o teste de rotação foi ajustado para declarar a geometria
nessa convenção, que é a mesma que um motor real reporta.

Testes: `test_geometry_mismatch_rejected`,
`test_geometry_within_tolerance_accepted`. Alinhamento pixel-perfeito de
seleção (bounding box exato por linha em todas as condições) permanece
fora do escopo de T05 — é o alvo de T17 (leitor); o que esta correção
garante é a coerência geométrica básica do artefato, que pertence a T05.

## R6-05 — `engine_version` não identifica engine/modelo reproduzível (correção importante) ✅

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`.

**Decisão aprovada explicitamente pelo usuário nesta sessão** (pergunta
direta: investir mais para fechar a lacuna, ou renomear e documentar o
escopo parcial): renomear. O campo passou a se chamar `adapter_version`
(era `engine_version`); a docstring de `OcrProvenance` deixa explícito que
NÃO resolve `auto` a um motor concreto (Docling não expõe isso
publicamente — confirmado lendo `docling.datamodel.pipeline_options` e os
módulos de OCR: a escolha acontece internamente, sem atributo público
pós-conversão) e NÃO captura versão de binário do Tesseract nem hash de
modelo do RapidOCR/ocrmac. A limitação está registrada em NOTES.md §10.5
como decisão aceita, não como lacuna silenciosa.

## R6-06 — Documentação mantém afirmações históricas contraditórias (correção importante) ✅

Arquivo: `docs/rag/EVIDENCE.md`.

Marquei explicitamente as seções da primeira e segunda rodadas com uma nota
apontando o que foi substituído por rodadas posteriores (sidecar,
`output_sha256`, `_render_extrema`, `publish_derivative_pair`,
`engine_version`), e corrigi inline os trechos mais enganosos (T5-01/T5-02 e
T5-04) para não afirmarem, sem qualificação, um comportamento que já não é o
atual. A matriz de evidências (`## Matriz AC`) não precisou de mudança — ela
já referenciava nomes de teste, não implementação.

## Gates finais (2026-08-29, após R6-01–R6-06)

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | OK — 233 unitários passed, 3 skipped (e2e opcionais); 1 frontend |
| `make test-integration` | OK — 90 passed, 1 skipped (e2e opcional) |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `RAG_OCR_E2E=1` (unit: e2e real + subprocesso R6-02) | OK — 2 passed |
| `RAG_OCR_E2E=1` (integration: e2e completo até `rag ingest`) | OK — 1 passed |

Nenhuma dependência nova. Nenhum campo novo em tabela do banco (a mudança de
proveniência é inteiramente dentro do arquivo PDF e do contrato Python).
