# Revisão T11 — rodada 3

Data: 2026-09-01
Resposta verificada: `REVIEW_RESPONSE_T11_ROUND2.md`
Resultado: **aprovado com ressalvas**

## R2-T11-01

**Corrigido.** A migration `0005_enrichment_runs` agora inclui
`index_run_id NOT NULL`, com FK composta `(index_run_id, edition_id)` para
`index_runs(id, edition_id)`, e a unicidade é
`(edition_id, index_run_id, summarizer_version_id)`. O serviço consulta e cria
`EnrichmentRun` com a `IndexRun` ativa. Portanto uma reindexação, mesmo usando
o mesmo modelo e prompt de enriquecimento, produz outra identidade e uma nova
execução; a reexecução sem reindexação continua idempotente.

O teste `test_two_reindexations_never_use_inactive_passages` foi corrigido
adequadamente: usa `model-a` nos dois passos, exige uma segunda execução,
verifica que os IDs das runs são distintos e que somente passagens da run ativa
entram no provider e nos suportes da segunda geração.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `uv run pytest -q tests/unit/test_enrichment.py tests/unit/test_enrichment_adapter.py tests/unit/test_model_doubles.py tests/unit/test_cli.py` | OK — 48 passed |
| `uv run ruff check src tests` | OK |
| `uv run ruff format --check src tests` | OK — 105 arquivos |
| `uv run mypy src tests` | OK — 105 arquivos, strict |
| `uv run pytest -q tests/integration/test_enrichment_pipeline.py tests/integration/test_cli.py` | Não executável localmente: 26 erros de setup por ausência do socket Docker/Podman |

## Ressalva

Não foi possível reproduzir localmente as integrações PostgreSQL declaradas na
resposta, pois o daemon de containers não está acessível neste ambiente. O diff
e os testes inspeccionados atendem aos achados T11-01, T11-02, T11-03 e
R2-T11-01; a aprovação plena depende de reexecutar essa suíte em ambiente com
Docker ou Podman configurado.

## Conclusão

Não restam achados funcionais na correção de T11. AC-12 está coberto para T11;
T11 contribui corretamente para AC-15, que permanece parcial até T13/T18.
