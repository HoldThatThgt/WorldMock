"""@brief 配置进程、冻结运行身份与准备人物输入的唯一职责。"""
from __future__ import annotations
from worldmock.profile import injection
from worldmock.profile.coverage import validate_coverage
from worldmock.catalog.loader import load_catalog
from worldmock.life.semantics import resolve_catalog_dir
from worldmock.profile.coverage_apps import verified_app_names
from worldmock.profile.coverage_targets import build_coverage_targets
from worldmock.profile.textgen import render_card
from worldmock.profile.compile import compile_persona
from worldmock.profile.taste import compute_taste_weights
from worldmock.catalog.admission import CONTENT_CORPORA
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from dotenv import dotenv_values
from worldmock.errors import IntegrityError
from worldmock.life.models import ModelIdentity
from worldmock.life.scenario import RuntimeScenario
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from worldmock.profile.injection import apply_scenario_rhythm
from worldmock.profile.models import PersonaCard, persona_card_struct
from worldmock.footprints.models import RETIRED_DECISION_STATEMENT

logger = logging.getLogger(__name__)


MAX_REACT_TURNS = 16


_WORK_REVIEW_THINKING = "disabled"


_WORK_REVIEW_TOOL_CHOICE = "required"


_AS2_PACKAGE = "agentsociety2"


AS2_LOCKED_SOURCE_SHA256 = "65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334"


_AS2_IDENTITY_ERROR = "AS2 runtime identity differs from the locked source"


def _configure_process(project_root: Path) -> None:
    """@brief 核对实际导入的 AS2 与锁定源码一致后，从唯一私密文件配置显式 DeepSeek 角色和 worker 导入路径。

    AS2 身份在读取凭据、改写进程环境与导入任何 AS2 模块之前核对，run、resume、replay 共用。
    """
    root = Path(project_root).resolve()
    if not (root / "custom" / "agents" / "life_agent.py").is_file():
        raise FileNotFoundError("WorldMock project root is incomplete")
    require_locked_as2()
    secret = _read_deepseek_key()
    variables = {
        "WORKSPACE_PATH": str(root),
        "AGENTSOCIETY_LLM_API_KEY": secret,
        "AGENTSOCIETY_LLM_API_BASE": DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_LLM_MODEL": DEEPSEEK_MODEL,
        "AGENTSOCIETY_CODER_LLM_API_KEY": secret,
        "AGENTSOCIETY_CODER_LLM_API_BASE": DEEPSEEK_BASE_URL,
        "AGENTSOCIETY_CODER_LLM_MODEL": DEEPSEEK_MODEL,
        "AGENTSOCIETY_EMBEDDING_MODEL": "",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "AGENTSOCIETY_LLM_REQUEST_TIMEOUT": "300",
        "MEM0_TELEMETRY": "False",
        "ANONYMIZED_TELEMETRY": "False",
    }
    os.environ.update(variables)
    _prepend_pythonpath(root)
    _require_loaded_config()


def _read_deepseek_key() -> str:
    """@brief 只读取固定私密配置中的多把 DeepSeek 节点密钥，不输出其内容。

    值为逗号分隔的至少一个非空密钥段；逐段 strip、按出现顺序去重后
    以逗号 join 返回（单把密钥也 join），由 Router 侧拆分为多 deployment。
    """
    path = Path.home() / ".config" / "worldmock" / "deepseek.env"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("DeepSeek credential file is missing or unsafe")
    values = dotenv_values(path, interpolate=False)
    unknown = set(values) - {"DEEPSEEK_NODE_KEYS"}
    raw = values.get("DEEPSEEK_NODE_KEYS")
    if unknown or not isinstance(raw, str) or not raw.strip():
        raise IntegrityError("DeepSeek credential file has an invalid schema")
    segments = [item.strip() for item in raw.split(",")]
    if not segments or any(not item for item in segments):
        raise IntegrityError("DeepSeek credential file has an invalid schema")
    return ",".join(dict.fromkeys(segments))


def _prepend_pythonpath(root: Path) -> None:
    """@brief 让 Ray worker 从当前精确 checkout 导入 custom 与 worldmock。"""
    current = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    required = [str(root), str(root / "src")]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([*required, *current]))


def _require_loaded_config() -> None:
    """@brief 拒绝进程早先导入的不同 AS2 模型配置。"""
    from agentsociety2.config.config import Config

    expected = (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL)
    default = (Config.LLM_API_BASE, Config.LLM_MODEL)
    coder = (Config.CODER_LLM_API_BASE, Config.CODER_LLM_MODEL)
    if default != expected or coder != expected or Config.EMBEDDING_MODEL:
        raise RuntimeError("AgentSociety2 was imported before the required model configuration")


def _model_identity() -> ModelIdentity:
    """@brief 形成不含凭据的显式 DeepSeek、提示和工具身份。

    顶层 thinking/tool_choice 取行动阶段参数并直接来自 LifeAgent 导出常量
    （单一事实源）；各阶段完整形状见 stage_model_identity()。提示指纹绑定七层系统层与层 Schema。
    """
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        life_tool_schema_sha256,
    )
    from custom.agents.life_prompt import life_prompt_sha256

    return ModelIdentity(
        provider="openai_compatible",
        base_url=DEEPSEEK_BASE_URL,
        requested_model=DEEPSEEK_MODEL,
        thinking=ACTION_THINKING["thinking"]["type"],
        tool_choice=ACTION_TOOL_CHOICE,
        max_retries=0,
        prompt_sha256=life_prompt_sha256(),
        tool_schema_sha256=life_tool_schema_sha256(),
    )


def stage_model_identity(same_second_continuation: bool = True) -> dict[str, Any]:
    """@brief 行动与工作验收两个阶段的完整冻结请求身份（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「单一行动阶段」）。

    行动阶段参数取自 LifeAgent 导出常量（单一事实源）；运行中不变更。不发送 reasoning_effort
    （以 None 表示该字段不进请求）。行动阶段另记运行开关 same_second_continuation（第二章「同秒续接」，缺省开）。
    """
    from agentsociety2.config.llm_dispatcher import TRANSPORT_MAX_RETRIES
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        ACTION_MAX_TOKENS,
    )

    return {
        "provider": "openai_compatible",
        "base_url": DEEPSEEK_BASE_URL,
        "requested_model": DEEPSEEK_MODEL,
        "max_retries": 0,
        "transport_max_retries": TRANSPORT_MAX_RETRIES,
        "max_react_turns": MAX_REACT_TURNS,
        "strict": True,
        "reasoning_effort": None,
        "stages": {
            "action": {
                "thinking": ACTION_THINKING["thinking"]["type"],
                "tool_choice": ACTION_TOOL_CHOICE,
                "max_tokens": ACTION_MAX_TOKENS,
                "same_second_continuation": same_second_continuation,
            },
            "work_review": {
                "thinking": _WORK_REVIEW_THINKING,
                "tool_choice": _WORK_REVIEW_TOOL_CHOICE,
            },
        },
    }


def runtime_code_sha256(project_root: Path) -> str:
    """@brief 哈希全部生产 Python 源码、根依赖锁定文件与实际导入的 AS2 包（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「运行身份」）。

    AS2 文件以 agentsociety2/<相对路径> 为标签，与安装位置无关；AS2 未通过锁定核对时拒绝给出代码身份。
    """
    root = Path(project_root).resolve()
    files = [(path.relative_to(root).as_posix(), path)
             for directory in (root / "src", root / "custom") for path in directory.rglob("*.py")]
    files.extend((name, root / name) for name in ("pyproject.toml", "uv.lock"))
    return _labeled_sha256([*files, *require_locked_as2()])


def require_locked_as2() -> list[tuple[str, Path]]:
    """@brief 核对实际导入的 AS2 包与锁定提交 8768e14 的源码摘要，返回其（标签, 路径）文件表。

    包目录为空、导入失败或摘要不符时以 IntegrityError 拒绝；读取或哈希 AS2 文件时的任何失败（条目缺失、
    不安全或不可读）同样按摘要不符拒绝，只用规格的唯一拒绝文案。run、resume、replay 与 validate 共用。
    """
    files = _as2_source_files()
    try:
        digest = _labeled_sha256(files)
    except (IntegrityError, OSError) as error:
        raise IntegrityError(_AS2_IDENTITY_ERROR) from error
    if digest != AS2_LOCKED_SOURCE_SHA256:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    return files


def _as2_source_files() -> list[tuple[str, Path]]:
    """@brief 列出实际导入的 AS2 包目录下除 __pycache__ 外的全部文件，标签为 agentsociety2/<相对路径>。

    包目录取 importlib.util.find_spec 解析出的唯一目录；解析失败、不是唯一包目录或目录为空即拒绝。
    """
    try:
        spec = importlib.util.find_spec(_AS2_PACKAGE)
    except (ImportError, ValueError) as error:
        raise IntegrityError(_AS2_IDENTITY_ERROR) from error
    locations = list(spec.submodule_search_locations or ()) if spec is not None else []
    if len(locations) != 1:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    package = Path(locations[0]).resolve()
    files = [(f"agentsociety2/{path.relative_to(package).as_posix()}", path)
             for path in package.rglob("*")
             if "__pycache__" not in path.relative_to(package).parts
             and (path.is_symlink() or not path.is_dir())]
    if not files:
        raise IntegrityError(_AS2_IDENTITY_ERROR)
    return files


def _labeled_sha256(files: list[tuple[str, Path]]) -> str:
    """@brief 按标签排序，逐文件写入标签长度、标签、内容长度与内容；缺失或不安全的来源即拒绝。"""
    digest = hashlib.sha256()
    for label, path in sorted(files, key=lambda item: item[0]):
        if not path.is_file() or path.is_symlink():
            raise IntegrityError("Runtime identity source is missing or unsafe")
        encoded = label.encode()
        content = path.read_bytes()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_state(project_root: Path) -> tuple[str | None, bool | None]:
    """@brief 返回项目根所在 Git 检出的（HEAD 提交, 是否有未提交改动）；非 Git 检出或 git 失败时二者为 None。

    未提交标志取 git status --porcelain 是否非空（显式含未跟踪文件，不含忽略文件）；二者只记录，不参与恢复比对。
    """
    head = _git_output(project_root, "rev-parse", "HEAD")
    if head is None:
        return None, None
    status = _git_output(project_root, "status", "--porcelain", "--untracked-files=normal")
    if status is None:
        return None, None
    return head.strip().decode("ascii"), bool(status.strip())


def _git_output(project_root: Path, *arguments: str) -> bytes | None:
    """@brief 在项目根执行一条只读 git 命令并返回标准输出；git 不可用或命令失败时返回 None。"""
    command = ["git", "-C", str(project_root), *arguments]
    try:
        result = subprocess.run(command, capture_output=True, check=False,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def creation_inputs(scenario_path: Path, scenario: RuntimeScenario, compiled: Mapping[int, Any],
                    same_second_continuation: bool = True) -> tuple[tuple[dict[str, Any], ...], list[dict]]:
    """@brief 新建运行与 validate 共用：由已编译的画像形成人物工作区输入并计算同一组运行输入警告。

    工作区输入经 _agent_specs（卡片先经场景作息覆盖 apply_scenario_rhythm，再渲染、存结构化卡片与算口味权重）；
    警告见 input_warnings。validate 用同一函数，所以两条路径对同一份编译结果给出相同的覆盖与警告。
    @param scenario_path 冻结场景路径（解析语料根与目录）
    @param scenario 原始加载的运行时场景（人物的 model_fields_set 可靠）
    @param compiled 人物编号 → 编译结果（CompiledPersona，测试桩可给 PersonaCard；没有背景故事的人物为 None 或缺席）
    @param same_second_continuation 同秒续接开关（冻结进人物配置）
    @return（工作区输入, 输入警告）
    """
    specs = _agent_specs(scenario, persona_compiler=lambda agent: compiled.get(agent.agent_id),
                         content_genres=_content_genre_catalog(scenario_path, scenario),
                         same_second_continuation=same_second_continuation)
    return specs, input_warnings(scenario_path, scenario, compiled, specs)


def input_warnings(scenario_path: Path, scenario: RuntimeScenario, compiled: Mapping[int, Any],
                   specs: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """@brief 运行输入警告（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 、2.1、8.1）：逐人物按编号，再场景级。

    人物：场景作息覆盖（CARD_FIELD_OVERRIDDEN / CARD_FIELD_CONFLICT）、口味与内容（按实际注入的口味权重）、游戏权重条目、
    世界可执行覆盖率（与 validate 同一目标集，低于 80% 只警告）与画像编译留下的警告；场景：配送路线与地点显示名。
    """

    genres = _content_genre_catalog(scenario_path, scenario)
    contents = tuple({"item_id": item.item_id, "genre": tuple(genres.get(item.item_id) or ())}
                     for item in scenario.contents)
    games = tuple(item.game_id for item in scenario.games)
    targets = _coverage_targets(scenario_path, scenario)
    weights = {spec["id"]: spec["profile"].get("taste_weights") for spec in specs}
    warnings: list[dict[str, Any]] = []
    for agent in sorted(scenario.agents, key=lambda item: item.agent_id):
        card, notes = _compiled_parts(compiled.get(agent.agent_id))
        taste = weights.get(agent.agent_id)
        warnings += injection.taste_warnings(agent.agent_id, card, contents, taste)
        warnings += injection.game_weight_warnings(agent.agent_id, games, taste)
        if card is not None:
            warnings += injection.coverage_warnings(agent.agent_id, validate_coverage(card, targets))
            warnings += apply_scenario_rhythm(card, agent).warnings
        warnings += injection.agreement_warnings(agent.agent_id, notes)
    return [*warnings, *injection.delivery_route_warnings(scenario), *injection.location_name_warnings(scenario)]


def _coverage_targets(scenario_path: Path, scenario: RuntimeScenario) -> Any:
    """@brief 与 validate 同一覆盖率目标集：场景兄弟目录 catalog/ 的目录与场景语料根。

    场景钉定的手机配置核验失败（身份、结构或执行证据，verified_app_names 抛 IntegrityError）时不使创建失败：
    按没有应用别名计算，覆盖率因此低于 80% 时照常记 COVERAGE_BELOW_THRESHOLD，另写一条英文 warning 日志
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 「运行路径永不因警告失败」）；validate 用自己的目标集，照旧因同一核验失败。
    """

    root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
    try:
        app_names = verified_app_names(scenario, root)
    except IntegrityError as error:
        logger.warning("Phone simulation config failed verification; run coverage counts no app names: %s", error)
        app_names = ()
    return build_coverage_targets(scenario, load_catalog(resolve_catalog_dir(scenario_path)), root, app_names)


def _agent_specs(scenario: RuntimeScenario, *, persona_compiler=None, content_genres=None,
                 same_second_continuation: bool = True) -> tuple[dict[str, Any], ...]:
    """@brief 从冻结生活人物形成原生 runner 工作区输入；同秒续接开关冻结进人物配置 profile["same_second_continuation"]。

    新建运行时为声明 background_story 的人物注入 PersonaCard 渲染文本
    （A9 compile_persona -> render_card，记忆巩固与反思读取 profile["persona_card"]）与卡片的结构化字段
    profile["persona_card_struct"]（去掉当下处境，宿主据此渲染人设层，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「人设层」），
    并由同一次编译产出确定性口味权重 profile["taste_weights"]（修订合同
    docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」：card.tastes.content_genres x 场景内容与游戏目录，经
    compute_taste_weights
    匹配；写入工作区档案随检查点原样携带）。卡片先经场景作息覆盖（apply_scenario_rhythm，
    docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值 ：场景显式声明的作息字段优先），渲染文本与结构化字段都取覆盖后的卡片，
    render_card 本身不变。恢复运行不重新编译也不重算——
    人物 profile 由检查点工作区原样恢复，分支人物身份与父运行保持一致。
    persona_compiler/content_genres 供测试注入确定性桩（persona_compiler 返回 CompiledPersona、PersonaCard 或 None，
    不触发真实模型调用）。
    """
    entries = _taste_entries(scenario, content_genres)
    specs = []
    for agent in sorted(scenario.agents, key=lambda item: item.agent_id):
        profile = agent.model_dump(mode="json")
        profile["id"] = agent.agent_id
        profile["same_second_continuation"] = same_second_continuation
        compiled = persona_compiler(agent) if persona_compiler is not None else _compile_persona(agent)
        card = _compiled_parts(compiled)[0]
        if card is not None:
            card = apply_scenario_rhythm(card, agent).card
            profile["persona_card"] = _render_card(card)
            profile["persona_card_struct"] = persona_card_struct(card)
            profile["taste_weights"] = _card_taste_weights(card, entries)
        config = {
            "enable_memory": True,
            "enable_todo_list": False,
            "max_react_turns": MAX_REACT_TURNS,
            "disabled_skill_ids": [],
            "default_activated_skill_ids": [],
        }
        specs.append({"id": agent.agent_id, "profile": profile, "config": config})
    return tuple(specs)


def _render_card(card: Any) -> str:
    """@brief 编译卡片的渲染文本（记忆巩固与反思的人物段）。"""

    return render_card(card)


def _compile_persona(agent: Any) -> Any:
    """@brief 场景 background_story -> CompiledPersona（卡片与编译警告；真实调用走三次一致性抽取）。

    编译警告（三次一致性低于 2/3 的字段、下班与到家锚点的降级说明）经输入警告 PERSONA_AGREEMENT_LOW 逐条透传，不再丢弃。
    """
    if agent.background_story is None:
        return None

    return compile_persona(agent.background_story)


def _compiled_parts(result: Any) -> tuple[PersonaCard | None, tuple[str, ...]]:
    """@brief 编译结果 → (卡片, 编译警告)：CompiledPersona 与带 card 的对象取其卡片与 warnings；
    测试桩可直接给 PersonaCard（没有编译警告）或 None（没有背景故事）。"""
    if result is None:
        return None, ()
    if isinstance(result, PersonaCard):
        return result, ()
    return result.card, tuple(getattr(result, "warnings", ()))


def _taste_entries(scenario: RuntimeScenario,
                   content_genres: dict[str, tuple[str, ...]] | None) -> tuple[dict, ...]:
    """@brief 口味匹配的条目表：场景内容按 item_id、游戏按 game_id 在同一张题材表里取 genre。

    游戏与内容同一算法（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ，裁决 S1）：题材表即 _content_genre_catalog
    （内容语料条目 item_id -> genre，游戏库 games/library 的条目同在其中）；没有题材声明的条目 genre 为空，
    权重为零，实际缓解走下限偏好。
    """
    genres = content_genres or {}
    identities = ([item.item_id for item in scenario.contents]
                  + [item.game_id for item in scenario.games])
    return tuple({"item_id": identity, "genre": tuple(genres.get(identity) or ())}
                 for identity in identities)


def _card_taste_weights(card: Any, entries: tuple[dict, ...]) -> dict[str, float]:
    """@brief 卡片口味 x 场景内容与游戏条目 -> 确定性权重（compute_taste_weights 生产接线）。

    游戏条目与内容条目同算（精确交集 / 条目题材数），键为 game_id；没有题材声明的游戏权重为 0.0
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ，裁决 S1）。
    """

    return compute_taste_weights(card.tastes.content_genres, entries)


def _content_genre_catalog(scenario_path: Path,
                           scenario: RuntimeScenario) -> dict[str, tuple[str, ...]]:
    """@brief 从内容语料条目收集 genre 表（item_id -> genre 元组，缺失即失败）。

    @param scenario_path 冻结场景路径（用于解析相对 corpus_root）
    @param scenario 组装后的运行时场景
    @return 场景内容目录的 genre 查找表（条目缺失 genre 视为空口味）
    """

    root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
    genres: dict[str, tuple[str, ...]] = {}
    for relative, mode in CONTENT_CORPORA:
        document = json.loads((root / relative).read_text(encoding="utf-8"))
        items = [document] if mode == "single" else (
            document if isinstance(document, list) else [])
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("item_id"), str):
                genres[item["item_id"]] = tuple(item.get("genre") or [])
    return genres



RETIRED_STAGE_IDENTITY_KEY = "two_stage_model_identity"


def validate_manifest_stage_identity(manifest: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """@brief 五个入口共用的清单阶段身份校验（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「清单」）。

    新建（写清单之前）、打开既有运行（含从工作区加载）、普通恢复与反事实重放（父清单）、环境校验（manifest 为 None，
    即新运行将冻结的清单）都只调用本函数。旧键 two_stage_model_identity 下的身份先经阶段校验，含退役的决策自述阶段即以
    英文错误拒绝；旧键本身不提供别名或迁移。缺少 stage_model_identity 拒绝；显式为 null 的引擎直驱身份返回 None。
    """
    if manifest is None:


        manifest = {"stage_model_identity": stage_model_identity()}
    if RETIRED_STAGE_IDENTITY_KEY in manifest:
        retired = manifest[RETIRED_STAGE_IDENTITY_KEY]
        if isinstance(retired, dict):
            validate_stage_model_identity(retired)
        raise IntegrityError(f"Run manifest key {RETIRED_STAGE_IDENTITY_KEY} is retired")
    if "stage_model_identity" not in manifest:
        raise IntegrityError("Run manifest decision stage identity is missing")
    identity = manifest["stage_model_identity"]
    return None if identity is None else validate_stage_model_identity(identity)


def validate_stage_model_identity(identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """@brief 阶段 model_identity 一致性校验：只有行动与工作验收两个阶段（LifeAgent 常量单一事实源）。

    五个入口经 validate_manifest_stage_identity 调用本函数；含退役的决策自述阶段的身份以英文错误拒绝
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「清单」）。行动阶段的运行开关 same_second_continuation 须为布尔值
    （第二章「同秒续接」），开与关都合法。
    """


    actual = identity if identity is not None else stage_model_identity()
    if not isinstance(actual, dict):
        raise IntegrityError("Stage model identity must be an object")
    problems = _identity_problems(actual)
    stages = actual.get("stages")
    if isinstance(stages, dict) and RETIRED_DECISION_STATEMENT in stages:
        raise IntegrityError(f"Model identity stage {RETIRED_DECISION_STATEMENT} is retired")
    if not isinstance(stages, dict) or set(stages) != {"action", "work_review"}:
        raise IntegrityError("Model identity stages must be exactly action, work_review")
    from custom.agents.life_agent import ACTION_MAX_TOKENS, ACTION_THINKING, ACTION_TOOL_CHOICE

    action = stages.get("action")
    switch = action.get("same_second_continuation") if isinstance(action, dict) else None
    if not isinstance(switch, bool):
        problems.append(f"stage action same_second_continuation is {switch!r}, expected a boolean")
    expected_action = {"thinking": ACTION_THINKING["thinking"]["type"],
                       "tool_choice": ACTION_TOOL_CHOICE,
                       "max_tokens": ACTION_MAX_TOKENS,
                       "same_second_continuation": switch}
    expected_review = {"thinking": "disabled", "tool_choice": "required"}
    for name, expected in (("action", expected_action), ("work_review", expected_review)):
        if stages.get(name) != expected:
            problems.append(f"stage {name} is {stages.get(name)!r}, expected {expected!r}")
    if problems:
        raise IntegrityError("Stage model identity is inconsistent: " + "; ".join(problems))
    return actual


def _identity_problems(actual: dict[str, Any]) -> list[str]:
    """@brief 核对阶段身份的端点（DeepSeek strict 端点，主站地址即拒绝）、重试、回合预算、严格声明与 reasoning_effort 顶层字段。"""
    from agentsociety2.config.llm_dispatcher import TRANSPORT_MAX_RETRIES

    problems: list[str] = []
    if actual.get("base_url") != DEEPSEEK_BASE_URL:
        problems.append(f"base_url={actual.get('base_url')!r} must be the DeepSeek strict endpoint "
                        f"{DEEPSEEK_BASE_URL!r}")
    if actual.get("max_retries") != 0:
        problems.append(f"max_retries={actual.get('max_retries')!r} must be 0")
    if type(actual.get("transport_max_retries")) is not int or actual["transport_max_retries"] != TRANSPORT_MAX_RETRIES:
        problems.append("transport_max_retries must match the dispatcher budget")
    if type(actual.get("max_react_turns")) is not int or actual["max_react_turns"] != MAX_REACT_TURNS:
        problems.append("max_react_turns must match the agent budget")
    if actual.get("strict") is not True:
        problems.append("every function declaration must be strict=true")
    if actual.get("reasoning_effort") is not None:
        problems.append("reasoning_effort must not be sent")
    return problems

