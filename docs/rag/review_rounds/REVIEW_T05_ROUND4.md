# Quarta revisão T05

Data: 2026-08-29
Commit revisado: `97309a32880c3a0583aecd66d91fd463e8391346`
Referências: `docs/rag/REVIEW_T05.md`,
`docs/rag/REVIEW_T05_ROUND2.md`,
`docs/rag/REVIEW_T05_ROUND3.md`
Resultado: **APROVADA**

## Veredito

T05 está aprovada. O bloqueador R6-01 foi eliminado por construção: PDF,
camada OCR e proveniência agora formam um único artefato, publicado por uma
única troca atômica. Não existe mais o segundo rename que permitia combinar
PDF e sidecar de gerações diferentes.

R6-02 a R6-06 também foram tratados:

- logs dos motores reais têm caminhos locais redigidos;
- perda de fidelidade de `original_text` gera warning persistido;
- índice, dimensões e geração do conteúdo PDF são validados;
- o campo foi corretamente reduzido para `adapter_version`, com limitação
  aprovada e documentada;
- evidências históricas foram marcadas como substituídas.

## Evidência independente

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | 233 backend passed, 3 skipped; 1 frontend passed |
| `make test-integration` | 90 passed, 1 skipped |
| `make audit` | nenhuma vulnerabilidade conhecida |
| `make security-scan` | nenhum IOC bloqueado |
| três testes com `RAG_OCR_E2E=1` | 3 passed |

Os testes opcionais confirmaram:

- reconhecimento por RapidOCR com backend Torch;
- preservação visual e texto pesquisável;
- fluxo real até a persistência por `rag ingest`;
- ausência dos caminhos absolutos anteriormente observados.

## Limitações aceitas

- `adapter_version` não identifica integralmente binários e pesos do motor;
- alinhamento visual pixel-perfect será validado no leitor em T17;
- rótulos impressos de página ainda usam o índice físico;
- blobs podem ficar órfãos após rollback relacional, sendo detectáveis pelo
  audit do artifact store.

Essas limitações estão documentadas e não bloqueiam a definição de pronto de
T05.

## Próximo passo

T06 está liberada.
