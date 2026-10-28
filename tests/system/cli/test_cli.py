"""@brief 实际命令仅接受完整环境，纯入口和验证不加载运行引擎。"""

import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shutil
import subprocess
import sys
import threading

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.cli import build_parser

ROOT = Path(__file__).resolve().parents[3]
CLI_ENTRY = "from worldmock.cli import main; raise SystemExit(main())"


def _command(*arguments):
    environment = {**os.environ, "WORLDMOCK_TEST_API_KEY": "test-only-key"}
    return subprocess.run([sys.executable, "-c", CLI_ENTRY, *map(str, arguments)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60, env=environment)


@pytest.fixture
def published_environment(tmp_path):
    """@brief 真正 HTTP 服务只返回当前模型的权威元数据，CLI 不替换生产加载器。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/v1/models" or self.headers.get("Authorization") != "Bearer test-only-key":
                self.send_error(401)
                return
            body = json.dumps({"data": [{"id": "test-model", "context_window": 524288,
                                         "max_output_tokens": 32768}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "config.toml"
    replacement = f"http://127.0.0.1:{server.server_port}/v1"
    path.write_bytes(path.read_bytes().replace(b"https://model.example.invalid/v1", replacement.encode()))
    try:
        yield root
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pure_cli_and_runtime_imports_do_not_load_engine_or_as2():
    script = (
        "import sys; from worldmock.cli import build_parser; import worldmock.configuration.inputs.validation; import worldmock.runtime.lifecycle.runner; "
        "build_parser(); forbidden=('worldmock.simulation_engine.engine','worldmock.simulation_engine.world.context',"
        "'worldmock.runtime.framework.read_query'); assert not set(forbidden)&set(sys.modules); "
        "assert not any(name.startswith('agentsociety2') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_pytest_collection_of_pure_commands_does_not_load_engine_or_as2(tmp_path):
    arguments = ["--collect-only", "tests/system/cli/test_cli.py", "tests/system/environment/test_validation.py",
                 "tests/unit/cli/test_parser.py", "-q", "-p", "no:cacheprovider",
                 "--basetemp", str(tmp_path / "collection")]
    script = (
        "import sys,pytest; code=pytest.main(" + repr(arguments) + "); assert code==0; "
        "forbidden=('worldmock.simulation_engine.engine','worldmock.simulation_engine.world.context','worldmock.runtime.framework.read_query'); "
        "assert not set(forbidden)&set(sys.modules); "
        "assert not any(name.startswith('agentsociety2') for name in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validate_real_command_reads_current_inputs_without_writing_assets(published_environment):
    before = {path.relative_to(published_environment): path.read_bytes()
              for path in published_environment.rglob("*") if path.is_file()}
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"valid": True, "environment_id": "artificial-test",
                                        "environment_version": "4"}
    assert before == {path.relative_to(published_environment): path.read_bytes()
                      for path in published_environment.rglob("*") if path.is_file()}


def test_rejected_run_does_not_create_an_output_directory(published_environment, tmp_path):
    (published_environment / "persona.toml").write_bytes(b"schema_version = 1\n")
    output = tmp_path / "run"
    result = _command("run", "--environment", published_environment, "--output", output)
    assert result.returncode == 1
    assert "PERSONA_INVALID" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
def test_main_validation_exit_code_matches_the_actual_input_report(
    published_environment, monkeypatch, capsys, valid
):
    from worldmock.cli import main

    monkeypatch.setenv("WORLDMOCK_TEST_API_KEY", "test-only-key")
    if not valid:
        (published_environment / "persona.toml").write_bytes(b"schema_version = 1\n")
    before = {path.relative_to(published_environment): path.read_bytes()
              for path in published_environment.rglob("*") if path.is_file()}
    assert main(["validate", "--environment", str(published_environment)]) == (0 if valid else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is valid
    if not valid:
        assert any(item["code"] == "PERSONA_INVALID" for item in report["errors"])
    assert before == {path.relative_to(published_environment): path.read_bytes()
                      for path in published_environment.rglob("*") if path.is_file()}


def test_main_logs_an_actual_missing_run_failure_without_creating_files(tmp_path, capsys, caplog):
    from worldmock.cli import main

    missing = tmp_path / "missing-run"
    assert main(["status", "--run-dir", str(missing)]) == 1
    assert capsys.readouterr().out == ""
    assert "Run database does not exist" in caplog.text
    assert "WorldMock command failed" in caplog.text
    assert not missing.exists()


def test_main_cancellation_logs_interrupt_and_returns_shell_interrupt_status(
    tmp_path, monkeypatch, capsys, caplog
):
    from worldmock import cli

    requested = tmp_path / "cancelled-run"
    received = []

    def interrupted_read(run_dir):
        received.append(run_dir)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "read_status", interrupted_read)
    assert cli.main(["status", "--run-dir", str(requested)]) == 130
    assert received == [requested]
    assert capsys.readouterr().out == ""
    assert "WorldMock command interrupted" in caplog.text
    assert not requested.exists()
