#!/usr/bin/env python3
"""Varredura estrutural de IOCs bloqueados na cadeia de dependências (R07).

Bloqueados (ataque de supply chain npm 2026-03-31):
- axios 1.14.1 e axios 0.30.4 (qualquer posição no grafo, direta ou transitiva);
- plain-crypto-js em qualquer versão;
- domínio sfrclak.com em qualquer arquivo rastreado relevante.

Analisa manifests e lockfiles de forma estrutural (JSON), não por regex sobre texto
solto. Sai com código 1 e relatório BLOCKED por achado; 0 se limpo.
"""

import json
import re
import sys
from pathlib import Path

BLOCKED_AXIOS = {"1.14.1", "0.30.4"}
BLOCKED_PACKAGES = {"plain-crypto-js"}
BLOCKED_DOMAIN = re.compile(r"sfrclak\.com", re.IGNORECASE)

# axios deve estar pinado de forma exata (sem ^, ~, *, >=, ranges ou tags)
LOOSE_RANGE = re.compile(r"^[\^~*>=<]|\s|\||latest")


class Finding:
    def __init__(self, file: Path, detail: str) -> None:
        self.file = file
        self.detail = detail

    def __str__(self) -> str:
        return f"BLOCKED: {self.file}: {self.detail}"


def _check_package(name: str, version: str, file: Path, where: str) -> list[Finding]:
    findings: list[Finding] = []
    if name in BLOCKED_PACKAGES:
        findings.append(
            Finding(file, f"{where}: pacote malicioso {name}@{version} (RAT dropper)")
        )
    if name == "axios":
        clean = version.lstrip("^~")
        if clean in BLOCKED_AXIOS:
            findings.append(
                Finding(
                    file,
                    f"{where}: axios@{version} é versão comprometida "
                    "(supply chain 2026-03-31)",
                )
            )
        elif LOOSE_RANGE.search(version):
            findings.append(
                Finding(
                    file,
                    f"{where}: axios@{version} não está pinado de forma exata; "
                    "ranges podem resolver para versão comprometida",
                )
            )
    return findings


def scan_package_json(path: Path) -> list[Finding]:
    data = json.loads(path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, version in (data.get(section) or {}).items():
            findings.extend(_check_package(name, str(version), path, section))
    return findings


def _scan_lock_packages(packages: dict, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for key, meta in packages.items():
        if not isinstance(meta, dict):
            continue
        # chaves no formato node_modules/<nome> (inclusive aninhadas/transitivas)
        name = key.rsplit("node_modules/", 1)[-1] if "node_modules/" in key else None
        if not name:
            continue
        version = str(meta.get("version", ""))
        findings.extend(_check_package(name, version, path, f"lockfile entry {key}"))
    return findings


def _scan_lock_dependencies_v1(deps: dict, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name, meta in deps.items():
        if not isinstance(meta, dict):
            continue
        version = str(meta.get("version", ""))
        findings.extend(_check_package(name, version, path, "lockfile dependency"))
        nested = meta.get("dependencies")
        if isinstance(nested, dict):
            findings.extend(_scan_lock_dependencies_v1(nested, path))
    return findings


def scan_package_lock(path: Path) -> list[Finding]:
    data = json.loads(path.read_text(encoding="utf-8"))
    findings = _scan_lock_packages(data.get("packages") or {}, path)
    deps = data.get("dependencies")
    if isinstance(deps, dict):  # formato lockfile v1
        findings.extend(_scan_lock_dependencies_v1(deps, path))
    return findings


def scan_text_for_domain(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return []
    if BLOCKED_DOMAIN.search(text):
        return [Finding(path, "referência ao domínio C2 sfrclak.com")]
    return []


def scan_repo(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    manifests = sorted(root.glob("**/package.json")) + sorted(
        root.glob("**/package-lock.json")
    )
    for manifest in manifests:
        if "node_modules" in manifest.parts:
            continue
        findings.extend(scan_text_for_domain(manifest))
        try:
            if manifest.name == "package.json":
                findings.extend(scan_package_json(manifest))
            else:
                findings.extend(scan_package_lock(manifest))
        except (json.JSONDecodeError, OSError) as exc:
            findings.append(Finding(manifest, f"manifesto ilegível: {exc}"))
    for extra in ("backend/pyproject.toml", "backend/uv.lock"):
        candidate = root / extra
        if candidate.exists():
            findings.extend(scan_text_for_domain(candidate))
            text = candidate.read_text(encoding="utf-8")
            for blocked in BLOCKED_PACKAGES:
                if blocked in text:
                    findings.append(
                        Finding(candidate, f"pacote malicioso {blocked} presente")
                    )
    return findings


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    findings = scan_repo(root)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"security-scan: {len(findings)} achado(s) bloqueador(es)", file=sys.stderr)
        return 1
    print("security-scan: nenhum IOC bloqueado encontrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
