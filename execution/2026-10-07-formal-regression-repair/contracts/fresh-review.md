# 正式回归输入责任新鲜审查

**结论：APPROVED。** 对 `c0265684bb2015f20353b3f3b55ab70123b9bd1c..36603196cb6495d0b0854e249564ccd7f418e457`
进行有界审查后，没有发现会改变系统行为、削弱验证、覆盖全局环境选择或制造正式环境假阳性的实质阻断。

## 审查边界

| 来源责任 | 文件 | 数量 |
| --- | --- | ---: |
| 共享夹具基础 | `tests/engine_fixture.py`、`tests/life/scenario_fixtures.py` | 2 |
| 固定人工行为、纯食品与身体常量、显式反例 | `tests/life/test_activity_location_kinds.py`、`tests/life/test_body.py`、`tests/life/test_candidate_constants.py`、`tests/life/test_lifestyle.py`、`tests/life/test_load_checks.py`、`tests/life/test_rrs_food_body.py`、`tests/life/test_scenario.py`、`tests/life/test_semantics.py`、`tests/scheduling/test_branch_coverage.py`、`tests/test_validation.py` | 10 |
| 保留正式输入证人 | `tests/life/test_frozen_inputs_runtime.py`、`tests/life/test_world_query.py` | 2 |
| 局部修复 | `tests/footprints/test_validation.py`、`tests/integration/test_input_warnings.py`、`tests/integration/test_presentation_habits.py`、`tests/integration/test_strict_endpoint.py` | 4 |
| 新增所选环境生命周期 | `tests/integration/test_selected_environment_lifecycle.py` | 1 |

恢复相关的十三个文件、两项已定位的正式/人工行为差异、bootstrap 和生产实现不在本轮源码审查范围内。

## 实质证据

- 根 `conftest.py` 与 `tests/data_paths.py` 在审查区间没有变化。`--environment-root` 仍在
  `pytest_configure` 调用 `select_environment`，随后测试模块在收集阶段取得更新后的
  `ENVIRONMENT_ROOT`。本轮没有新增全局覆盖、fallback、migration、skip、xfail 或选择收窄。
- `engine_factory` 的字符串参数现在明确解析到人工环境；显式 `Path` 原样使用。缓存只在未 mutate
  且路径正好位于人工场景根或当前所选场景根时启用。其他显式路径仍走真实加载，mutate 仍跳过缓存。
  `FrozenInputs` 的键继续覆盖场景字节、完整 catalog 树与完整 corpus 树，并在会话结束核对文件和缓存对象摘要。
- 现有十八个文件中的 160 个测试函数在前后提交间保持同一测试身份、参数化装饰器和
  `pytest.raises` 表达式。断言在归一化输入根名称后逐抽象语法树相等。唯一原始断言文本差异是
  `test_candidate_constants.py` 在读取声明值的同一断言里把 `ENVIRONMENT_ROOT` 改为
  `ARTIFICIAL_ENVIRONMENT_ROOT`；期望值 `3600`、阈值和比较关系均未改变。
- `test_frozen_inputs_runtime.py` 的每个正式节点都显式传入
  `ENVIRONMENT_ROOT / "scenarios/single-person.json"`。`test_world_query.py` 的名称查询使用相同正式
  `Path`，查询名称从实际观察的公开名称派生；其余固定供给变体使用人工默认工厂。正式路径没有被默认人工根遮蔽。
- `tests/test_validation.py` 的反例副本改为人工根，但原有环境准入节点仍对动态
  `ENVIRONMENT_ROOT` 执行完整 validate。新增生命周期节点也只导入动态 `ENVIRONMENT_ROOT`，没有导入人工常量。
- 局部四文件的最终 SHA-256 与修复回执完全一致。input warnings 只把固定画像副本改为人工环境；
  presentation habits 在人工单人场景上写回原有背景故事和全部五个作息字段，行为与旧
  `_scenario(..., **_WC_RHYTHM)` 等价；strict endpoint 只把闭集外媒体桩 `电影` 改成现有闭集值
  `长视频`；footprints 只把固定覆盖率目标测试的场景、目录和语料改为人工环境。四文件的断言、错误码、
  参数化、阈值和正式通用 helper 均保持原样。
- 新生命周期先复制当前所选环境，仅在副本中把单人场景缩成一小时，并按生产校验规则过滤所有三类定时输入：
  messages 保留 `sent_at < end_at`，bills 和 payrolls 保留
  `start_at < due_at < end_at`。生产 `LifeScenario` 没有第四类定时输入。当前正式源的
  190 条消息、3 条账单和 3 条工资在该一小时窗口中均保留 0 条；原人物、catalog、corpus 和源字节不变，
  来源 SHA、原窗口、派生窗口及保留的定时输入都写入 provenance。
- 生命周期实际执行 create、start activity、advance、checkpoint、close、目录搬迁、open、完成、
  export 和 `validate_export`，并核对恢复前后状态、catalog、人物、完成标记、活动、action result 和源 SHA。
  它是“所选正式输入的一小时派生副本生命周期”证据，不是原始正式 dayrun、原生 24 小时运行或在线模型运行证明。
- 冻结规范 SHA-256 仍是
  `f61367ea90b6bdd188c8b4638f85e91b880f2cc5f482e14a4af5a3d7764a9784`。正式环境 97 个文件与
  `.work/formal-regression-repair/preservation-before.json` 逐文件比较，结果为 `changed: []`。
- 收集回执记录原有 5701 个节点全部保留；当前 5708 个节点中的新增节点包含本轮生命周期节点，没有原节点缺失。

## 验证

| 检查 | 结果 |
| --- | --- |
| 审查范围 `git diff --check c0265684..36603196` | exit 0 |
| 十八个现有文件的测试身份、装饰器、断言和异常抽象语法树比较 | 160 对 160；输入根归一化后全部相等 |
| 显式正式根运行新增生命周期节点 | `1 passed in 3.94s` |
| 显式正式根运行 frozen-input runtime 与 world-query 参数组 | `12 passed in 3.46s` |
| 局部四文件既有回执 | `62 passed in 12.40s`，日志 SHA-256 `5a1dddecf72ed82c4c7dccec1b0eb501103284b891f71acaac4cc2b8b6053ca6` |
| 集成方 targeted 回执 | `271 passed in 16.66s`，日志 SHA-256 `df31021ac182b269577fe3c27c6362e1fba0c44fcbcea2c314fa6b69e293438d` |
| 正式文件保护 | 97 checked，0 changed |
| 审查范围 patch SHA-256 | `bafeeba2522a855b0d859f4794b71bc088d3387efa924aac81bbfc1bffb12df6` |
| 审查结束工作树 | `HEAD=36603196cb6495d0b0854e249564ccd7f418e457`，tracked clean |

## 一次性外观项

这些项不影响行为或验证结论，可在不改测试节点身份的一次整理中处理：

- 本轮改动后新增四条超过 120 字符的测试行：
  `tests/life/test_frozen_inputs_runtime.py:34`、`:70`，
  `tests/life/test_scenario.py:198`、`:204`。
- 若干人工输入已改用 `ARTIFICIAL_ENVIRONMENT_ROOT`，但内部变量 `_FORMAL` 或注释仍写“正式”；
  `tests/test_validation.py` 的既有节点名称也写 artificial，却继续承担动态所选环境准入。测试节点名称应保留，
  内部变量与注释可统一澄清为“人工”或“所选环境”。

## 剩余证据责任

`root-targeted.log` 只记录 pytest 输出，没有内嵌命令、提交或环境根身份。独立审查已用显式正式根补跑最关键的
生命周期、缓存和查询证人；完整正式门禁、命令回执、平台门禁和最终发布结论仍由集成方负责。该证据缺口不构成当前
源码阻断。
