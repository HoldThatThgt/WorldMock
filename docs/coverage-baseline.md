<!-- worldmock-doc status=CURRENT authority=coverage_baseline -->
# 覆盖率门禁口径与基线

## 门禁口径

本项目验收采用三项覆盖率口径，定义如下：

| 覆盖率 | 目标 | 定义 | 判定方式 |
| --- | --- | --- | --- |
| 功能 spec 测试用例覆盖率 | 100% | `docs/agentsociety2-design.md`（含 2026-09-18 修订合同）每条规格条款映射到至少一个具体测试用例 | 映射义务，随测试命名与映射评审维护；不由脚本机器计算 |
| 接口覆盖率 | 100% | `src/worldmock` 与 `custom` 全部 `.py` 生产文件中，模块顶层与类体定义的公共 `def`/`async def`（非下划线开头，含 dunder 之外的类方法），其函数体所有行均被测试执行 | `scripts/coverage_gate.py` 机器判定：接口函数体任一行出现在 coverage `missing_lines` 即为未覆盖 |
| 分支覆盖率 | ≥ 85% | coverage branch 模式下，`covered_branches / (covered_branches + missing_branches)` 按全部文件汇总，保留两位小数后与阈值比较 | `scripts/coverage_gate.py` 机器判定 |

### 失败关闭约定

- 文件出现在生产根目录但未被 coverage 测量（如从未被导入）：其全部公共接口计为未覆盖。
- 汇总分支总数为 0（测量管道异常）：整体分支覆盖率按 0% 判定，门禁不通过。
- 测试套件本身失败（pytest 非零退出）：门禁不通过，无论覆盖率数值。

## 范围与排除

- 测量范围：`src/worldmock`、`custom/agents`、`custom/envs`（`pyproject.toml` 的
  `[tool.coverage.run] source`；`custom` 子目录无 `__init__.py`，coverage 枚举未执行
  文件时会剪枝非包子目录，故须显式列到叶子目录）。
- 排除项：`tests/`、`scripts/`、`vendor/`（`omit` 安全网；三者也均不在 `source` 目录边界内）。
- 分支模式：`branch = true`，测量与导出均启用。
- 行排除表（`[tool.coverage.report] exclude_lines`）显式替换默认值：仅保留
  `# pragma: no cover` 与 `if TYPE_CHECKING:`；**不含**默认对纯 `...` stub 行的豁免，
  即未调用的占位实现（`...` 体）会计入 `missing_lines`，被接口覆盖率门禁捕获——
  这与项目"禁止占位实现/空实现"方针一致。

## 使用命令

```bash
# 一键门禁：跑全量 pytest（当前基线约 95 秒）→ 导出 coverage JSON →
# 判定两项机器口径 → 写 docs/coverage-report.json；退出码 0=通过 / 1=不通过
uv run python scripts/coverage_gate.py

# 分项查看（需先有 .coverage 数据，可直接由门禁脚本产生）
uv run coverage report          # 终端汇总表（show_missing，两位小数）
uv run coverage html            # HTML 明细
```

- 门禁报告：`docs/coverage-report.json`（含生成时间、执行命令、整体与分文件分支覆盖、未覆盖接口清单 `文件:行:函数名`、最终判定位），每次运行覆盖重写。
- 阈值：`scripts/coverage_gate.py` 中 `BRANCH_THRESHOLD = 85.0`。

## 当前基线（C10 终收口实测，2026-09-18）

> 由覆盖率达标终收口单元 C10 在六路覆盖单元合并后的稳定树上实测：
> `uv run python scripts/coverage_gate.py` exit 0；全量 pytest 2525 passed / 0 failed。

| 指标 | 数值 |
| --- | --- |
| 整体分支覆盖率 | 99.03%（covered=5526，missing=54，total=5580，阈值 85.00） |
| 未覆盖公共接口数 | 0 |
| 门禁判定 | PASSED（`scripts/coverage_gate.py` 退出码 0） |
