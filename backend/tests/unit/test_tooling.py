"""Testes dos scripts de tooling: audit fail-fast (R06) e scan estrutural de IOCs (R07)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SH = REPO_ROOT / "scripts" / "audit.sh"
SCAN_PY = REPO_ROOT / "scripts" / "security_scan.py"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Stub de `uv`: honra o caminho passado em `-o` (RR04: arquivo exclusivo por
    # execução via mktemp); UV_EXPORT_SLEEP alonga a janela para o teste de
    # concorrência.
    _write_executable(
        bin_dir / "uv",
        """#!/usr/bin/env bash
case "$1" in
  export)
    [ "${UV_FAIL_EXPORT:-0}" = "1" ] && exit 1
    out=""
    prev=""
    for arg in "$@"; do
      if [ "$prev" = "-o" ]; then out="$arg"; fi
      prev="$arg"
    done
    [ -n "$out" ] || exit 1
    sleep "${UV_EXPORT_SLEEP:-0}"
    echo "stub-reqs" > "$out"
    exit 0
    ;;
  run)
    [ "${UV_FAIL_AUDIT:-0}" = "1" ] && exit 1
    exit 0
    ;;
esac
exit 0
""",
    )
    _write_executable(
        bin_dir / "npm",
        """#!/usr/bin/env bash
touch "${NPM_MARKER:?}"
exit "${NPM_FAIL:-0}"
""",
    )
    return bin_dir


def _run_audit(tmp_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bin_dir = _stub_bin(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    # `uv run` exporta UV=<caminho real do uv>; o script honra UV, então o stub
    # precisa ser injetado pela variável, não apenas pelo PATH.
    env["UV"] = str(bin_dir / "uv")
    env["NPM_MARKER"] = str(tmp_path / "npm-ran")
    env.update(env_extra)
    # Comandos fixos do harness de teste; nenhuma entrada externa é executada.
    return subprocess.run(  # noqa: S603
        ["bash", str(AUDIT_SH)],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        check=False,
    )


class TestAuditFailFast:
    def test_export_failure_aborts_before_npm(self, tmp_path: Path) -> None:
        result = _run_audit(tmp_path, {"UV_FAIL_EXPORT": "1"})
        assert result.returncode != 0
        assert not (tmp_path / "npm-ran").exists()

    def test_pip_audit_failure_aborts_before_npm(self, tmp_path: Path) -> None:
        result = _run_audit(tmp_path, {"UV_FAIL_AUDIT": "1"})
        assert result.returncode != 0
        assert not (tmp_path / "npm-ran").exists()

    def test_npm_failure_propagates(self, tmp_path: Path) -> None:
        result = _run_audit(tmp_path, {"NPM_FAIL": "7"})
        assert result.returncode == 7

    def test_success_runs_all_steps_and_leaves_no_global_temp(self, tmp_path: Path) -> None:
        result = _run_audit(tmp_path, {})
        assert result.returncode == 0
        assert (tmp_path / "npm-ran").exists()
        assert not (REPO_ROOT / "backend" / ".audit-reqs.txt").exists()

    def test_concurrent_runs_do_not_interfere(self, tmp_path: Path) -> None:
        """RR04: duas execuções paralelas usam temporários exclusivos."""
        import concurrent.futures

        def run_once(tag: str) -> subprocess.CompletedProcess[str]:
            return _run_audit(
                tmp_path / tag,
                {"UV_EXPORT_SLEEP": "0.3", "NPM_MARKER": str(tmp_path / tag / "npm-ran")},
            )

        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run_once, ["a", "b"]))
        assert all(r.returncode == 0 for r in results)
        assert (tmp_path / "a" / "npm-ran").exists()
        assert (tmp_path / "b" / "npm-ran").exists()
        assert not (REPO_ROOT / "backend" / ".audit-reqs.txt").exists()


def _run_scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - comando fixo do harness de teste
        [sys.executable, str(SCAN_PY), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_manifests(
    root: Path,
    *,
    package_json: dict[str, object] | None = None,
    lock: dict[str, object] | None = None,
) -> None:
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    if package_json is not None:
        (frontend / "package.json").write_text(json.dumps(package_json), encoding="utf-8")
    if lock is not None:
        (frontend / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")


class TestSecurityScan:
    def test_clean_project_passes(self, tmp_path: Path) -> None:
        _write_manifests(
            tmp_path,
            package_json={"dependencies": {"react": "19.2.8"}},
            lock={"packages": {"node_modules/react": {"version": "19.2.8"}}},
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 0, result.stderr

    def test_real_repo_is_clean(self) -> None:
        result = _run_scan(REPO_ROOT)
        assert result.returncode == 0, result.stderr

    def test_direct_blocked_axios_in_manifest(self, tmp_path: Path) -> None:
        _write_manifests(tmp_path, package_json={"dependencies": {"axios": "1.14.1"}})
        result = _run_scan(tmp_path)
        assert result.returncode == 1
        assert "axios" in result.stderr

    def test_transitive_blocked_axios_in_lockfile(self, tmp_path: Path) -> None:
        _write_manifests(
            tmp_path,
            lock={
                "packages": {
                    "node_modules/wrapper": {"version": "1.0.0"},
                    "node_modules/wrapper/node_modules/axios": {"version": "0.30.4"},
                }
            },
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 1
        assert "0.30.4" in result.stderr

    def test_lockfile_v1_nested_dependency(self, tmp_path: Path) -> None:
        _write_manifests(
            tmp_path,
            lock={
                "dependencies": {
                    "wrapper": {
                        "version": "1.0.0",
                        "dependencies": {"axios": {"version": "1.14.1"}},
                    }
                }
            },
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 1

    def test_plain_crypto_js_anywhere_is_blocked(self, tmp_path: Path) -> None:
        _write_manifests(
            tmp_path,
            lock={"packages": {"node_modules/plain-crypto-js": {"version": "4.2.1"}}},
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 1
        assert "plain-crypto-js" in result.stderr

    def test_loose_axios_range_rejected(self, tmp_path: Path) -> None:
        _write_manifests(tmp_path, package_json={"dependencies": {"axios": "^1.13.0"}})
        result = _run_scan(tmp_path)
        assert result.returncode == 1
        assert "pinado" in result.stderr

    def test_safe_pinned_axios_passes(self, tmp_path: Path) -> None:
        _write_manifests(
            tmp_path,
            package_json={"dependencies": {"axios": "1.14.0"}},
            lock={"packages": {"node_modules/axios": {"version": "1.14.0"}}},
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 0, result.stderr

    def test_c2_domain_blocked_in_python_manifests(self, tmp_path: Path) -> None:
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text(
            '# endpoint = "https://sfrclak.com:8000"\n', encoding="utf-8"
        )
        result = _run_scan(tmp_path)
        assert result.returncode == 1
        assert "sfrclak.com" in result.stderr
