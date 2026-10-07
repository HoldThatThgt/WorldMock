"""@brief pytest 自动分类与三层选择的唯一规则；耗时数据不参与用例选择。"""

from __future__ import annotations

import ast
import inspect
import shlex
from dataclasses import dataclass, field, replace
from importlib.util import resolve_name
from types import ModuleType
from functools import lru_cache
from pathlib import Path

TIERS = ("fast", "standard", "full")
MARKERS = ("engine", "mutation", "day_run", "external")


@lru_cache(maxsize=None)
def source_dependencies(path):
    """@brief 提取全部静态导入与调用名，包含函数内导入和别名。"""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    imports, names, literals = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
    return imports, names, literals


@dataclass(eq=False)
class _Reference:
    """静态调用对象；名称只保存实际定义身份，不用于词语猜测。"""

    name: str
    obj: object = None
    path: Path | None = None
    node: ast.AST | None = None
    namespace: dict | None = None
    receiver: object = None


@dataclass(eq=False)
class _Instance:
    """已到达的构造调用产生的对象及显式赋值字段。"""

    definition: _Reference
    fields: dict = field(default_factory=dict)


_UNKNOWN = object()
_CALL_RESULTS = {}


def _value_identity(value, seen=None):
    """可达静态调用的复用身份，包含真实对象、参数与闭包绑定。"""
    seen = set() if seen is None else seen
    if value is _UNKNOWN:
        return ("unknown",)
    if isinstance(value, (type(None), str, int, float, bool)):
        return (type(value).__name__, value)
    if id(value) in seen:
        return ("recursive", type(value).__name__)
    seen.add(id(value))
    if isinstance(value, _Reference):
        receiver = value.receiver.get("values", {}) if isinstance(value.receiver, dict) else value.receiver
        return (value.name, id(value.obj), str(value.path), getattr(value.node, "lineno", None),
                _value_identity(receiver, seen))
    if isinstance(value, _Instance):
        return ("instance", value.definition.name, _value_identity(value.fields, seen))
    if isinstance(value, dict):
        return tuple(sorted((str(key), _value_identity(item, seen)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_value_identity(item, seen) for item in value)
    return (type(value).__name__, id(value))


@lru_cache(maxsize=None)
def _tree(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _definitions(path):
    found = {}

    def visit(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = ".".join(filter(None, (prefix, node.name)))
                found[name] = node
                visit(node.body, name)
    visit(_tree(path).body)
    return found


class _Calls:
    """仅沿一个用例及真实夹具调用闭包解析对象、别名和参数。"""

    def __init__(self, path):
        self.path = Path(path).resolve()
        projects = [folder for folder in self.path.parents if (folder / "pyproject.toml").is_file()]
        self.root = projects[-1] if projects else self.path.parent
        self.markers = set()
        self.day_loop = False
        self.short_window = False
        self.active = set()
        self.objects = {}
        self.locations = {}
        self.resolving = set()

    def reference(self, obj):
        receiver = None
        if isinstance(obj, (staticmethod, classmethod)):
            obj = obj.__func__
        if inspect.ismethod(obj):
            owner = obj.__self__
            receiver = self.reference(owner) if inspect.isclass(owner) else _Instance(self.reference(type(owner)))
            obj = obj.__func__
        obj = inspect.unwrap(obj) if callable(obj) else obj
        if isinstance(obj, ModuleType) or inspect.isfunction(obj) or inspect.isclass(obj) or inspect.isbuiltin(obj):
            if id(obj) in self.objects:
                reference = self.objects[id(obj)]
                return replace(reference, receiver=receiver) if receiver is not None else reference
            name = obj.__name__ if isinstance(obj, ModuleType) else f"{obj.__module__}.{obj.__qualname__}"
            reference = _Reference(name, obj)
            self.objects[id(obj)] = reference
            if inspect.isfunction(obj) or inspect.isclass(obj):
                try:
                    source = inspect.getsourcefile(obj)
                except TypeError:
                    source = None
                if source and Path(source).is_relative_to(self.root):
                    reference.path = Path(source)
                    qualified = obj.__qualname__.replace(".<locals>.", ".")
                    reference.node = _definitions(str(reference.path)).get(qualified)
                    reference.namespace = getattr(obj, "__globals__", None)
                    if inspect.isfunction(obj):
                        reference.namespace = {**obj.__globals__, **inspect.getclosurevars(obj).nonlocals}
            return replace(reference, receiver=receiver) if receiver is not None else reference
        return obj

    def locate(self, name):
        if name in self.locations:
            return self.locations[name]
        if name in self.resolving:
            return _Reference(name)
        self.resolving.add(name)
        result = self._locate(name)
        self.resolving.remove(name)
        self.locations[name] = result
        return result

    def _locate(self, name):
        pieces = name.split(".")
        for split in range(len(pieces), 0, -1):
            module, member = ".".join(pieces[:split]), ".".join(pieces[split:])
            for prefix in (self.root / "src", self.root):
                base = prefix.joinpath(*pieces[:split])
                path = base.with_suffix(".py") if base.with_suffix(".py").is_file() else base / "__init__.py"
                if not path.is_file():
                    continue
                if not member:
                    return _Reference(module, path=path)
                node = _definitions(str(path)).get(member)
                if node is not None:
                    return _Reference(name, path=path, node=node)
                scope = {"path": path, "module": module, "values": {}, "namespace": None}
                value = self.global_value(member.split(".")[0], scope)
                for part in member.split(".")[1:]:
                    value = self.member(value, part)
                if value is not _UNKNOWN:
                    return value
        return _Reference(name)

    def imported(self, node, scope):
        if isinstance(node, ast.Import):
            for item in node.names:
                scope["values"][item.asname or item.name.split(".")[0]] = self.locate(
                    item.name if item.asname else item.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = scope["module"] if scope["path"].name == "__init__.py" else scope["module"].rpartition(".")[0]
                module = resolve_name("." * node.level + module, package)
            for item in node.names:
                scope["values"][item.asname or item.name] = self.locate(module + "." + item.name)

    def global_value(self, name, scope):
        if name in scope["values"]:
            return scope["values"][name]
        namespace = scope["namespace"] or {}
        if name in namespace:
            value = self.reference(namespace[name])
            scope["values"][name] = value
            return value
        for node in _tree(str(scope["path"])).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imported(node, scope)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope["values"].setdefault(node.name, _Reference(scope["module"] + "." + node.name,
                                                                path=scope["path"], node=node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    scope["values"][name] = _UNKNOWN
                    scope["values"][name] = self.value(node.value, scope) if node.value else _UNKNOWN
        if name in {"str", "list", "tuple", "getattr"}:
            scope["values"].setdefault(name, _Reference("builtins." + name))
        return scope["values"].get(name, _UNKNOWN)

    def member(self, owner, name):
        if isinstance(owner, _Instance):
            if name in owner.fields:
                return owner.fields[name]
            member = self.member(owner.definition, name)
            if isinstance(member, _Reference):
                member = replace(member, receiver=owner)
            return member
        if not isinstance(owner, _Reference):
            return _UNKNOWN
        if owner.obj is not None:
            try:
                value = inspect.getattr_static(owner.obj, name)
                reference = self.reference(value)
                return replace(reference, receiver=owner) if isinstance(value, classmethod) else reference
            except AttributeError:
                pass
        if owner.node is not None and isinstance(owner.node, ast.ClassDef):
            for node in owner.node.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return _Reference(owner.name + "." + name, path=owner.path, node=node, namespace=owner.namespace)
        return self.locate(owner.name + "." + name)

    def value(self, node, scope):
        if node is None:
            return _UNKNOWN
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.global_value(node.id, scope)
        if isinstance(node, ast.Attribute):
            return self.member(self.value(node.value, scope), node.attr)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [self.value(item, scope) for item in node.elts]
        if isinstance(node, ast.Dict):
            return {self.value(key, scope): self.value(value, scope) for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant)}
        if isinstance(node, ast.Subscript):
            owner, key = self.value(node.value, scope), self.value(node.slice, scope)
            if (isinstance(owner, dict) and isinstance(key, (str, int))
                    or isinstance(owner, (list, tuple)) and isinstance(key, int)):
                try:
                    return owner[key]
                except (KeyError, IndexError):
                    pass
            return _UNKNOWN
        if isinstance(node, ast.IfExp):
            values = [self.value(node.body, scope), self.value(node.orelse, scope)]
            return next((value for value in values if value is not _UNKNOWN), _UNKNOWN)
        if isinstance(node, ast.BinOp):
            left, right = self.value(node.left, scope), self.value(node.right, scope)
            if isinstance(node.op, ast.Div) and isinstance(right, str):
                return str(Path(left) / right) if isinstance(left, str) else right
            if isinstance(node.op, ast.Add) and type(left) is type(right) and isinstance(left, (str, list)):
                return left + right
            return _UNKNOWN
        if isinstance(node, ast.Call):
            args = [self.value(item, scope) for item in node.args]
            keywords = {item.arg: self.value(item.value, scope) for item in node.keywords if item.arg}
            target = self.value(node.func, scope)
            if isinstance(target, _Reference) and target.name in {"builtins.str", "pathlib.Path", "shutil.which"}:
                return args[0] if args else _UNKNOWN
            return self.call(target, args, keywords, node)
        if isinstance(node, ast.Await):
            return self.value(node.value, scope)
        if isinstance(node, ast.Lambda):
            return _Reference("<lambda>", path=scope["path"], node=node, namespace=scope["namespace"], receiver=scope)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.value(child, scope)
        return _UNKNOWN

    def call(self, reference, args, keywords, call):
        if not isinstance(reference, _Reference):
            return _UNKNOWN
        name = reference.name
        if name == "tests.tier_support.classify":
            return _UNKNOWN
        if name in {"worldmock.life.engine.LifeEngine", "worldmock.life.engine.LifeEngine.create",
                    "worldmock.life.engine.LifeEngine.open"}:
            self.markers.add("engine")
            return _Instance(self.locate("worldmock.life.engine.LifeEngine"))
        if name == "custom.envs.life_env.LifeEnv._require_engine":
            self.markers.add("engine")
            return _Instance(self.locate("worldmock.life.engine.LifeEngine"))
        if name == "tests.mutation_runner.run_behavior":
            mutation = args[2] if len(args) > 2 else keywords.get("mutation", None)
            if mutation is not None:
                self.markers.add("mutation")
            return _UNKNOWN
        if name in {"subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_call",
                    "subprocess.check_output", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}:
            command = args[0] if args else keywords.get("args", _UNKNOWN)
            words = command if isinstance(command, list) else shlex.split(command) if isinstance(command, str) else []
            executable = Path(words[0]).name.lower() if words and isinstance(words[0], str) else ""
            if executable in {"node", "node.exe"}:
                self.markers.add("external")
            return _UNKNOWN
        if name == "worldmock.life.engine.LifeEngine.run_until" and any(
                isinstance(item, ast.Attribute) and item.attr == "end_time" for item in ast.walk(call)):
            self.day_loop = True
        if reference.node is not None and isinstance(reference.node, ast.ClassDef):
            instance = _Instance(reference)
            constructor = self.member(instance, "__init__")
            self.call(constructor, args, keywords, call)
            return instance
        return self.function(reference, args, keywords)

    def assign(self, target, value, scope):
        if isinstance(target, ast.Name):
            scope["values"][target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = value if isinstance(value, list) else [_UNKNOWN] * len(target.elts)
            for member, item in zip(target.elts, values):
                self.assign(member, item, scope)
        elif isinstance(target, ast.Attribute):
            owner = self.value(target.value, scope)
            if isinstance(owner, _Instance):
                owner.fields[target.attr] = value

    def statements(self, nodes, scope):
        returned = _UNKNOWN
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope["values"][node.name] = _Reference(scope["module"] + "." + node.name, path=scope["path"],
                                                        node=node, namespace=scope["namespace"], receiver=scope)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imported(node, scope)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and scope["path"].is_relative_to(self.root / "tests") and any(
                        isinstance(target, ast.Attribute) and target.attr == "current_time" for target in targets):
                    self.short_window = self.short_window or any(
                        isinstance(item, ast.Attribute) and item.attr == "end_time" for item in ast.walk(node.value))
                value = self.value(node.value, scope)
                for target in targets:
                    self.assign(target, value, scope)
            elif isinstance(node, (ast.Return, ast.Expr)):
                result = self.value(node.value.value if isinstance(node.value, ast.Yield) else node.value, scope)
                if isinstance(node, ast.Return) or isinstance(node.value, ast.Yield):
                    returned = result
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                self.value(node.iter, scope)
                self.assign(node.target, _UNKNOWN, scope)
                self.statements(node.body + node.orelse, scope)
            elif isinstance(node, (ast.While, ast.If)):
                self.value(node.test, scope)
                if isinstance(node, ast.While) and any(isinstance(item, ast.Attribute) and item.attr == "end_time"
                                                     for item in ast.walk(node.test)):
                    self.day_loop = True
                values = [self.statements(node.body, scope), self.statements(node.orelse, scope)]
                returned = next((item for item in values if item is not _UNKNOWN), returned)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    value = self.value(item.context_expr, scope)
                    if item.optional_vars:
                        self.assign(item.optional_vars, value, scope)
                returned = self.statements(node.body, scope)
            elif isinstance(node, ast.Try):
                values = [self.statements(body, scope) for body in
                          [node.body, node.orelse, node.finalbody, *(handler.body for handler in node.handlers)]]
                returned = next((item for item in values if item is not _UNKNOWN), returned)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.expr):
                        self.value(child, scope)
        return returned

    def function(self, reference, args=(), keywords=None):
        node = reference.node
        if node is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return _UNKNOWN
        active_key = (str(reference.path), node.lineno)
        key = (_value_identity(reference), _value_identity(args), _value_identity(keywords or {}))
        if key in _CALL_RESULTS:
            value, markers, day_loop, short_window = _CALL_RESULTS[key]
            self.markers.update(markers)
            self.day_loop = self.day_loop or day_loop
            self.short_window = self.short_window or short_window
            return value
        if active_key in self.active:
            return _UNKNOWN
        self.active.add(active_key)
        parent_markers, parent_day_loop, parent_short_window = self.markers, self.day_loop, self.short_window
        self.markers, self.day_loop, self.short_window = set(), False, False
        module = self.module_name(reference.path)
        scope = {"path": reference.path, "module": module, "values": {}, "namespace": reference.namespace}
        if isinstance(reference.receiver, dict):
            scope["values"].update(reference.receiver["values"])
        elif isinstance(reference.receiver, (_Instance, _Reference)):
            args = [reference.receiver, *args]
        parameters = [*node.args.posonlyargs, *node.args.args]
        defaults = [_UNKNOWN] * (len(parameters) - len(node.args.defaults)) + list(node.args.defaults)
        for index, (parameter, default) in enumerate(zip(parameters, defaults)):
            value = self.value(default, scope) if isinstance(default, ast.AST) else default
            scope["values"][parameter.arg] = args[index] if index < len(args) else (keywords or {}).get(parameter.arg, value)
        for parameter, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            scope["values"][parameter.arg] = (keywords or {}).get(parameter.arg, self.value(default, scope))
        result = self.value(node.body, scope) if isinstance(node, ast.Lambda) else self.statements(node.body, scope)
        _CALL_RESULTS[key] = (result, frozenset(self.markers), self.day_loop, self.short_window)
        parent_markers.update(self.markers)
        self.markers = parent_markers
        self.day_loop = self.day_loop or parent_day_loop
        self.short_window = self.short_window or parent_short_window
        self.active.remove(active_key)
        return result

    def module_name(self, path):
        for prefix in (self.root / "src", self.root):
            if Path(path).is_relative_to(prefix):
                return module_for_path(Path(path).relative_to(prefix))
        return Path(path).stem


def classify(path, fixtures, function_name="", *, fixture_functions=(), test_function=None, parameter_values=None):
    """@brief 按真实用例、实际 FixtureDef 和可达静态调用分类；无关导入、未调用函数与名字均不打标。"""
    calls = _Calls(path)
    provided = dict(fixture_functions) if isinstance(fixture_functions, dict) else {
        inspect.unwrap(function).__name__: function for function in fixture_functions}
    parameters = {name: calls.reference(value) for name, value in (parameter_values or {}).items()}
    fixture_values = {}
    pending = set()

    def fixture(name):
        if name in fixture_values:
            return fixture_values[name]
        if name not in provided:
            return parameters.get(name, _UNKNOWN)
        if name in pending:
            return _UNKNOWN
        pending.add(name)
        reference = calls.reference(provided[name])
        request = _Instance(_Reference("pytest.FixtureRequest"), {"param": parameters.get(name, _UNKNOWN)})
        arguments = {parameter: request if parameter == "request" else fixture(parameter)
                     for parameter in inspect.signature(provided[name]).parameters}
        fixture_values[name] = (request.fields["param"]
                               if reference.name == "_pytest.python.get_direct_param_fixture_func"
                               else calls.function(reference, keywords=arguments))
        pending.remove(name)
        return fixture_values[name]

    for name in fixtures:
        fixture(name)
    if test_function is not None:
        reference = calls.reference(test_function)
    else:
        definitions = _definitions(str(calls.path))
        name = function_name or next((name for name in definitions if name.startswith("test_")), "")
        node = definitions.get(name) or next((node for qualified, node in definitions.items()
                                             if qualified.rsplit(".", 1)[-1] == name), None)
        reference = _Reference(calls.module_name(calls.path) + "." + name, path=calls.path, node=node)
    calls.function(reference, keywords={**parameters, **fixture_values})
    if calls.day_loop and not calls.short_window and "engine" in calls.markers:
        calls.markers.add("day_run")
    return calls.markers


def module_for_path(path):
    """@brief 仓库相对生产路径转为其真实 Python 模块名。"""
    parts = Path(path).with_suffix("").parts
    if parts[:1] == ("src",):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def selected(record, tier, changed=()):
    """@brief 快速层增加直接导入被改模块的引擎用例；两类长审查只进入全量层。"""
    if tier not in TIERS:
        raise ValueError(f"Unknown test tier: {tier}")
    markers = set(record["markers"])
    if tier == "full":
        return True
    if markers & {"mutation", "day_run"}:
        return False
    if tier == "standard":
        return True
    if not markers & set(MARKERS):
        return True
    if "external" in markers:
        return False
    changed_modules = {module_for_path(path) for path in changed
                       if Path(path).suffix == ".py" and Path(path).parts[:1] in (("src",), ("custom",))}
    return "engine" in markers and bool(changed_modules & set(record["imports"]))
