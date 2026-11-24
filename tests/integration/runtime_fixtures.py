"""@brief 运行测试共用的夹具：首次决策之前的空人物工作区、最小工程根、可恢复身份、恢复分支、不发模型请求的客户端与
按新建顺序启动的运行宿主。"""

from __future__ import annotations

from pathlib import Path

#: 重放分支里人物的余额改成这个数；它不改变检查点之前已经冻结的任何抽取。
BRANCH_BALANCE = 123456


def write_agent_workspace(workspace: Path) -> Path:
    """@brief 建出首次决策之前的空人物工作区并返回该目录；快照它得到的就是新建运行在第一次决策之前的人物快照。"""
    workspace.mkdir(parents=True)
    return workspace


def write_project(root: Path) -> Path:
    """@brief 写一个最小工程根：runtime_code_sha256 需要的两个依赖文件与一份生产源码。"""
    for relative, text in (("pyproject.toml", "[project]\nname = 'fixture'\n"), ("uv.lock", "version = 1\n"),
                           ("src/worldmock/probe.py", "VALUE = 1\n")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def current_run_identity(inputs, run_id: str):
    """@brief 使用当前运行代码、多档案模型身份、多人物人设摘要和真实场景建立可恢复身份。"""
    from worldmock.runtime.framework.setup import model_identity, runtime_code_sha256
    from worldmock.state.identity import RunIdentity

    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(persona.agent_id for persona in inputs.personas),
        persona_sha256s=dict(inputs.persona_sha256s),
        start_at=inputs.config.start_time.isoformat(),
        end_at=inputs.config.stop_time.isoformat(),
        timezone=inputs.config.timezone,
        model_identity=model_identity(inputs.config, inputs.model_metadata),
        code_sha256=runtime_code_sha256(),
    )


def branch_of(parent, tmp_path: Path, kind: str, source: str):
    """@brief 在父运行此刻写共同检查点，从它开出恢复分支或带一项余额干预的重放分支，返回打开的分支引擎。

    :param kind: resume 是普通恢复；replay 是反事实重放，分支打开后把人物余额改成 BRANCH_BALANCE。
    :param source: 重放干预引用的来源事件身份。
    """
    from worldmock.recording_engine.storage.checkpoint import restore_branch, snapshot_directory
    from worldmock.simulation_engine.engine import LifeEngine
    from worldmock.simulation_engine.world.world_updates import _request_sha
    from worldmock.state.decisions.results import CounterfactualIntervention

    snapshots = {
        f"agent:{agent_id}": snapshot_directory(
            write_agent_workspace(tmp_path / f"agent-{agent_id}"),
            parent.context.store.assets,
        )
        for agent_id in sorted(parent.context.state.agents)
    }
    checkpoint = parent.checkpoint(snapshots)
    target_agent = sorted(parent.context.state.agents)[0]
    intervention = CounterfactualIntervention(
        kind="set_balance_fen",
        agent_id=target_agent,
        balance_fen=BRANCH_BALANCE,
        hypothesis="余额改变不影响已经冻结的抽取。",
        source_event_ids=(source,),
    )
    metadata = None if kind == "resume" else {
        "branch_kind": "counterfactual",
        "intervention_sha256": _request_sha("counterfactual_intervention", intervention),
    }
    restore_branch(
        parent.context.store,
        checkpoint.checkpoint_id,
        tmp_path / "branch",
        *(() if metadata is None else (metadata,)),
    ).close()
    child = LifeEngine.open(parent.context.inputs, tmp_path / "branch")
    if kind == "replay":
        child.apply_counterfactual(intervention)
    return child


class OfflineClient:
    """@brief 整次运行模型客户端的离线替身：这些用例不发模型请求，收到请求就失败；记下关闭次数。"""

    def __init__(self) -> None:
        """@brief 关闭次数从零起。"""
        self.closed = 0

    async def call(self, request):
        """@brief 任何模型请求都是用例之外的调用，当场失败。"""
        raise AssertionError("Offline client received a model request")

    async def close(self) -> None:
        """@brief 记一次关闭。"""
        self.closed += 1


async def new_run(prepared, client):
    """@brief 按运行宿主的新建顺序启动一次准备好的新运行（建工作区、建引擎、初始检查点与零点任务），返回尚未关闭的宿主。"""
    from worldmock.configuration.model.model_registry import ModelRegistry
    from worldmock.runtime.lifecycle.run_host import RunHost
    from worldmock.runtime.lifecycle.runner import _creation_options

    registry = client if isinstance(client, ModelRegistry) else ModelRegistry(
        prepared.inputs.config,
        prepared.inputs.model_metadata,
        client_factory=lambda _profile: client,
    )
    host = RunHost(prepared.run_dir, prepared.inputs, registry, prepared.agents, prepared.memory_records)
    await host.start_new(prepared.run_identity, _creation_options(prepared))
    return host
