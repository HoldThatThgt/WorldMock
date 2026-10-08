"""@brief 预排事件子系统：封闭类型、确定性编译与到点效果（docs/design/04-simulation-engine.md「预排事件」）。

结构：events（类型与载荷模型）→ compile（场景材料到时间有序表）→ effects
（到点状态投影）。本包 __init__ 保持为空以维持导入方向（life.models 从
scheduling.events 再导出，故包体不得反向依赖 life）；使用方按子模块导入：

    from worldmock.scheduling.compile import compile_schedule
    from worldmock.scheduling.effects import apply_scheduled_effect
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind
"""
