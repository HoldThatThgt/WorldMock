"""让「等待」成为例外规格的验收登记：分阶段完成集合、特性文件清单与源码变异。

@file     idle_fillers_acceptance.py
@brief    docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账 的机器登记。契约点到阶段、规格锚点、测试节点与实现符号的
          对应保存在 tests/fixtures/idle-fillers-acceptance.json；本模块保存已完成的阶段、特性文件清单与源码变异，
          判定由 tests/test_idle_fillers_acceptance.py 执行，变异经共用执行器 tests/mutation_runner.py 在隔离子进程里生效。
@note     分阶段形态：只有阶段属于 STAGES_DONE 的台账行必须列出测试与实现符号，任何行列出的节点都必须解析到存在、会被
          收集且未被跳过的测试函数。每个阶段的提交登记自己的行、变异与文件，并把阶段号加入 STAGES_DONE；阶段 6 是配对
          复跑，只报告，不登记节点。变异只登记在主进程内执行的生产代码上，`original` 在所登记的定义内恰好出现一次，
          杀手是已登记在台账某一行的行为用例。本文件不含任何延期标记字面量。
"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 规格「实施阶段与提交」的阶段号；阶段 0 是冻结提交，阶段 6 是只报告的配对复跑。
STAGE_IDS: frozenset[str] = frozenset({"0", "1", "2", "3", "4", "5", "6"})
#: 改代码、测试、基线与门禁的实施阶段；全部完成后由守卫判定。
IMPLEMENTATION_STAGES: frozenset[str] = frozenset({"1", "2", "3", "4", "5"})
#: 只报告的阶段：台账行不登记测试与实现符号。
REPORT_ONLY_STAGES: frozenset[str] = frozenset({"6"})
#: 已完成的阶段，随每个阶段的提交增长。
STAGES_DONE: frozenset[str] = frozenset()

#: 本特性新增或改动的生产文件，供延期标记与空壳函数扫描。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = ()

#: 本特性新增的测试文件与登记文件，供延期标记与空壳函数扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/idle_fillers_acceptance.py",
    "tests/test_idle_fillers_acceptance.py",
)

#: 规格「验收台账」变异表逐行登记：行首列原文 → 阶段与各分句（以「；」分隔）登记的变异名；已完成阶段的每个分句至少一个变异。
MUTATION_ROWS: dict[str, dict] = {
    '某一个成员进入睡眠允许集合（每个成员一个变异）': {"stage": '1', "clauses": ((),)},
    '足迹侧词表缺某一个名字（每个名字一个变异）': {"stage": '1', "clauses": ((),)},
    '下一边界候选不排除睡着的人': {"stage": '1', "clauses": ((),)},
    '下一次决策候选不在睡眠中剔除起止': {"stage": '1', "clauses": ((),)},
    '到点判定不置标志': {"stage": '1', "clauses": ((),)},
    '下一次决策候选漏掉起止': {"stage": '1', "clauses": ((),)},
    '时钟边界漏掉起止': {"stage": '1', "clauses": ((),)},
    '生成器不扫前一日；不扫次日': {"stage": '1', "clauses": ((), ())},
    '生成器「严格晚于」改成「不早于」': {"stage": '1', "clauses": ((),)},
    '生成器「恰在」改成「不晚于」': {"stage": '1', "clauses": ((),)},
    '生成器把班次开始与结束对调': {"stage": '1', "clauses": ((),)},
    '生成器不产出休息时段的起止': {"stage": '1', "clauses": ((),)},
    '到点判定输出的起止成员不按枚举次序（同秒重合时倒序）': {"stage": '1', "clauses": ((),)},
    '生成器把某一种起止记成另一成员': {"stage": '1', "clauses": ((),)},
    '取了别人的工作安排': {"stage": '1', "clauses": ((),)},
    '起止像闹钟一样结束睡眠': {"stage": '1', "clauses": ((),)},
    '起止到点时重排或结束清醒人物进行中的活动': {"stage": '1', "clauses": ((),)},
    '第 7 条「手上的活动照常进行」改成「手上的活动随之结束」': {"stage": '2', "clauses": ((),)},
    '第 14 条的插入句移到上班安排一句之后': {"stage": '2', "clauses": ((),)},
    '第 16 条恢复「不必因此提早上床」': {"stage": '2', "clauses": ((),)},
    '第 15 条恢复「等平常的饭点再吃正餐」': {"stage": '2', "clauses": ((),)},
    '模板第三条去掉「几点去睡也不设提醒」': {"stage": '3', "clauses": ((),)},
    '模板第五条恢复旧文': {"stage": '3', "clauses": ((),)},
    '校验器拒绝例行意图且提醒为空的目标': {"stage": '3', "clauses": ((),)},
}

#: C 的行为用例（规格「验收台账」行为用例表首列）→ 测试节点；引用某 T 的台账行所在阶段完成时该 T 必须有节点。
BEHAVIOUR_CASES: dict[str, tuple[str, ...]] = {
    'T1': (),
    'T2': (),
    'T3': (),
    'T4': (),
    'T5': (),
    'T6': (),
    'T7': (),
    'T8': (),
    'T9': (),
    'T10': (),
    'T11': (),
    'T12': (),
    'T13': (),
    'T14': (),
    'T15': (),
    'T16': (),
    'T17': (),
    'T18': (),
    'T19': (),
    'T20': (),
}

#: 规格「验收台账」变异表逐行落成的源码变异；变异名登记在 MUTATION_ROWS 的对应分句。
MUTATIONS: tuple[SourceMutation, ...] = ()

RECEIPTS = REPO_ROOT / ".work/idle-fillers/mutation-receipts"
BASELINE_TIMEOUT_FLOOR_SECONDS = 600
BASELINE_TIMEOUT_SECONDS_PER_NODE = 120
MUTATION_TIMEOUT_SECONDS = 600


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器：不带变异是基线，带变异是一次杀死尝试。"""
    timeout = MUTATION_TIMEOUT_SECONDS
    if mutation is None:
        timeout = max(BASELINE_TIMEOUT_FLOOR_SECONDS, BASELINE_TIMEOUT_SECONDS_PER_NODE * len(nodes))
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
