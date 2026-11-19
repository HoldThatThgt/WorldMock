"""@brief 模型客户端的模块边界：src 里只有它导入 litellm；导入 litellm 之前设好本地价目表变量，干净进程里不访问网络；
顶层只依赖标准库与 litellm、openai、httpx，可以按文件路径加载。"""

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
#: 唯一允许导入 litellm 的生产模块。
MODEL_CLIENT = ROOT / "src" / "worldmock" / "configuration" / "model" / "model_client.py"
#: 干净子进程里执行的脚本：先让名字解析与连接都记一次并失败，再依次导入两个模块。
_FRESH_PROCESS = """
import os
import socket
import sys

attempts = []


def refuse(*arguments, **keywords):
    attempts.append(arguments)
    raise OSError("network access is not allowed")


socket.getaddrinfo = refuse
socket.socket.connect = refuse
socket.socket.connect_ex = refuse
import worldmock.cli
import worldmock.agents.prompt.prompt_capacity
print(os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP"), len(attempts), "litellm" in sys.modules)
"""


def _imported_modules(tree: ast.AST) -> list[str]:
    """@brief 一个模块里全部导入语句（含函数体内）导入的绝对模块名。"""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_only_the_model_client_imports_litellm():
    """src 下全部 Python 源码里，只有模型客户端模块导入 litellm（函数内导入也算）。"""
    importers = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        if any(name.split(".")[0] == "litellm" for name in names):
            importers.append(path.relative_to(ROOT).as_posix())
    assert importers == ["src/worldmock/configuration/model/model_client.py"]


def test_fresh_process_sets_the_local_cost_map_before_litellm_and_opens_no_connection():
    """环境里没有价目表变量、禁止一切网络访问的新进程依次导入命令行与容量预检模块之后：litellm 已加载，变量为 True，
    没有任何名字解析或连接尝试。"""
    environment = {key: value for key, value in os.environ.items() if key != "LITELLM_LOCAL_MODEL_COST_MAP"}
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run([sys.executable, "-c", _FRESH_PROCESS], cwd=ROOT, env=environment,
                               capture_output=True, text=True, timeout=300, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "True 0 True"


def test_model_client_imports_only_the_standard_library_litellm_openai_and_httpx():
    """模型客户端顶层导入的模块只有标准库与 litellm、openai、httpx。"""
    tree = ast.parse(MODEL_CLIENT.read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    roots = {name.split(".")[0] for name in _imported_modules(ast.Module(body=top, type_ignores=[]))}
    assert roots and roots <= set(sys.stdlib_module_names) | {"litellm", "openai", "httpx"}


async def test_model_client_loads_by_file_path(monkeypatch):
    """按文件路径以别的模块名加载模型客户端，构造并关闭一个客户端。"""
    spec = importlib.util.spec_from_file_location("model_client_by_path", MODEL_CLIENT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "model_client_by_path", module)
    spec.loader.exec_module(module)
    client = module.ModelClient(base_url="https://example.invalid", api_key="test-key", max_retries=0,
                                transport_max_retries=0)
    await client.close()
    assert client._client.is_closed()
