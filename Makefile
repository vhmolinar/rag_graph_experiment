UV ?= uv
NPM ?= npm --prefix frontend

.PHONY: setup lock lint format format-check typecheck test test-unit test-integration test-contract test-e2e audit security-scan clean

## setup: instala dependências de backend e frontend
setup:
	cd backend && $(UV) sync --frozen
	$(NPM) ci

## lock: valida que os lockfiles correspondem aos manifests
lock:
	cd backend && $(UV) lock --check
	$(NPM) ls --package-lock-only >/dev/null

## lint: ruff (backend) + eslint (frontend)
lint:
	cd backend && $(UV) run ruff check src tests
	$(NPM) run lint

## format: aplica formatação (ruff format + prettier)
format:
	cd backend && $(UV) run ruff format src tests
	$(NPM) run format

## format-check: verifica formatação sem alterar arquivos
format-check:
	cd backend && $(UV) run ruff format --check src tests
	$(NPM) run format-check

## typecheck: mypy (backend) + tsc (frontend)
typecheck:
	cd backend && $(UV) run mypy src tests
	$(NPM) run typecheck

## test: todos os testes unitários (backend + frontend)
test: test-unit
	$(NPM) run test

## test-unit: testes unitários do backend
test-unit:
	cd backend && $(UV) run pytest tests/unit -q

## test-integration: testes de integração (requer Docker para PostgreSQL)
test-integration:
	cd backend && $(UV) run pytest tests/integration -q

## test-contract: testes de contrato HTTP (servidores simulados)
test-contract:
	cd backend && $(UV) run pytest tests/contract tests/unit/test_embedding_adapter.py -q

## test-e2e: testes end-to-end (Playwright; requer ambiente de pé)
test-e2e:
	$(NPM) run e2e

## audit: auditoria de dependências sobre os lockfiles (pip-audit + npm audit)
## fail-fast via script: qualquer falha aborta com status != 0 (R06)
audit:
	UV="$(UV)" bash scripts/audit.sh

## security-scan: análise estrutural de IOCs bloqueados (diretos e transitivos) (R07)
security-scan:
	python3 scripts/security_scan.py .

## clean: remove caches e artefatos de build
clean:
	rm -rf backend/.venv frontend/node_modules frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
