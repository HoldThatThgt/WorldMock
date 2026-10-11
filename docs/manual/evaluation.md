<a name="evaluation-自定义评估与盲评输入"></a>
# 自定义评估与盲评输入

本页命令以 WorldMock 为当前工作目录，评估配置使用 `.work/my-evaluation.json`。

评估配置决定“比较同一时间段内哪些活动、使用哪些公开参照、请评审判断哪些维度”。
从现有 `tests/evaluation/fixtures/scenario.json` 复制完整评估输入修改。
它和[运行配置](runtime.md#runtime-编写-config-toml)是两种不同输入，评估配置用于 `worldmock evaluate`。
所有命令均在仓库根目录执行。

[返回配置手册](README.md)

```mermaid
flowchart LR
    A[评估配置与公开参照] --> D[认证输入与计算统计]
    B[旧 LabelKit 完整交付] --> D
    C[已完成的 WorldMock 足迹导出] --> D
    H[可选的真人参照] --> D
    D --> E[三个隔离盲评包]
    E --> F[外部评审填写三个收据]
    F --> G[校验与解盲]
    G --> I[报告和成功 manifest]
```

<a name="evaluation-环境与规则的归属"></a>
## 环境与规则的归属

按[运行与框架集成](../design/10-runtime.md)修订；验收结果独立记录。

报告只将非布尔整数秒且起点小于终点的区间计为有效结算，缺少实际区间时明确报告缺证。
日期按运行时区切分，反思快照使用主观来源校验，不能当作活动经历。

<a name="evaluation-先分清要编辑的文件"></a>
## 先分清要编辑的文件

| 文件或目录 | 谁填写 | 用途 |
|---|---|---|
| `.work/my-evaluation.json` | 用户 | 时间窗口、类别映射、参照来源、评审设计和旧交付身份 |
| `--human-reference` 指向目录的 `manifest.json` | 真人数据提供方 | 可选；声明规范化真人轨迹的文件、时间范围和身份 |
| 三份 `review-record-1` JSON | 评审组织者根据真实评审结果填写 | 绑定实际评审包、会话、原始回答和每项判断 |
| `metrics.json`、`mapping-report.json`、`official-reference.json` | 程序生成 | 统计与适配结果，不是用户配置 |
| `review-packets/`、`blind-review-pack.json`、`unblinding-key.json` | 程序生成 | 隔离评审材料与解盲信息，准备后不可编辑 |
| `prepare-receipt.json`、最终 `manifest.json` | 程序生成 | 冻结输入与产物身份，不要手工修改哈希或成功状态 |

目前没有通用 `adapter.toml` 或任意字段重命名配置。可配置的适配入口是
`category_mappings` 与 `official_domain_map`；输入文件结构仍须符合现有交付合同。

<a name="evaluation-从完整示例开始"></a>
## 从完整示例开始

下面命令从真实评估夹具创建完整配置，只修改评估名称。该夹具用于离线结构演示，
`expected_labelkit.run_id="artificial-labelkit"`，全部摘要为零占位值；它没有绑定可信的真实候选交付。
目标文件已经存在时会拒绝覆盖：

```bash
python - <<'PY'
import json
from pathlib import Path

source = Path("tests/evaluation/fixtures/scenario.json")
target = Path(".work/my-evaluation.json")
config = json.loads(source.read_text(encoding="utf-8"))
config["scenario_id"] = "my-white-collar-evaluation"
target.parent.mkdir(parents=True, exist_ok=True)
with target.open("x", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
```

离线加载通过只证明结构合规。执行候选比较前，先确定双方真实覆盖的日期，再修改窗口、特殊日期和映射，
并从已认证的真实旧交付填写全部 `expected_labelkit` 身份、行数与文件哈希；保留占位值不能进入真实评估。

| 必填字段 | 单位与边界 | 与其他配置的关系 |
|---|---|---|
| `scenario_id`、`schema_version` | 非空字符串 | 名称可以自定义；通常保留当前 `schema_version` |
| `window.start`、`window.end` | 带时区偏移的 ISO 8601 时间，起点小于终点 | 在 `window.timezone` 下均须为午夜；包含起点，不包含终点 |
| `window.timezone` | 有效 IANA 时区名，例如 `Asia/Shanghai` | 决定自然日边界；须与共享人物和输入清单一致 |
| `bootstrap.repetitions` | 正整数，次 | 默认示例使用一万次；降低会影响区间稳定性 |
| `bootstrap.block_days` | 固定为 `7` 天 | 当前采用周块重采样，不能任意调短 |
| `review.reviewer_count` | 固定为 `3` | 必须提交三个不同槽、不同会话的评审收据 |
| `review.dimensions` | 不重复的非空字符串数组 | 使用下表已有维度；新名称没有对应可判定性规则 |
| `shared_profile` | 固定 `person_count: 1`、`occupation_class: "white_collar"`，必填 `timezone` | 当前评估支持单人白领；不能通过此处配置其他群体 |
| `special_dates` | 日期字符串到日历类别名称的对象，可为空 | 日期必须在窗口内；其余日期自动区分工作日、周末 |
| `category_mappings` | 包含两组外部 LabelKit 映射 | 保留外部交付的真实类别来源，WorldMock 直接使用实际动作与对象 |
| `official_domain_map`、`official_reference` | 必填对象 | 活动类别映射的官方领域必须实际存在 |
| `expected_labelkit` | 必填对象 | 冻结旧候选交付身份，见后文 |

结束时间写成 `2026-03-30T00:00:00+08:00` 表示最后一个被评估的自然日是三月二十九日，
不是三月三十日。双方和可选真人参照必须使用完全一致的窗口，不会自动取交集或裁剪数据。
统计重抽样、日期抽样、左右交换和匿名引用都读取已认证 WorldMock 检查点的唯一随机根。
随机根不公开给评审，评估配置不另设种子。

| 已支持的评审维度 | 判断什么 |
|---|---|
| `temporal_plausibility` | 活动时点与时长是否合理 |
| `causal_continuity` | 可观察对象的状态变化是否连续 |
| `persona_consistency` | 是否符合双方都已证实的粗画像 |
| `long_term_consequences` | 跨日状态变化是否连贯 |
| `routine_and_variation` | 多日规律及变化是否合理 |
| `missing_expected_activity` | 在完整日期与日历类型中是否缺少预期活动 |

保留示例的完整维度集合最便于比较。加载器只检查字符串结构，不会替你检查新维度的语义实现；
填写未实现名称会在生成盲评包时失败。评审一致性的 `alpha >= 0.80` 门槛也不是配置字段。

<a name="evaluation-修改类别映射和公开参照"></a>
## 修改类别映射和公开参照

WorldMock 直接使用真实请求和活动中的动作：出行、购买、睡眠和 interact 都取请求所写的动作，出行一律是 travel；
公开证据保留实际内容、应用、路线和订单对象。
盲评里的「出行方式」只在人物步行时标成 walking。数据集定义的出行方式都不带类别，
即使作者把它的身份起成 driving、bus、taxi 也一样，评估不按名字猜。
所以只开车或坐车出行的人物，盲评里这一栏不会显示；写盲评结论时要注明它不反映数据集定义的出行方式。
内容没有评估类别，报告不把工作、娱乐或社交标签套到 WorldMock 活动。
`category_mappings` 仅保留 `labelkit_sequence_class` 与 `labelkit_pattern` 两组外部 LabelKit 类别映射。

商品效果在实际收货时结算，食品多样性读取本人食品 goods_received。
数字交互只来自真实 app 绑定活动；目录浏览、内容消费和系统应用触点分别依据实际证据呈现。
共同渠道或时长语义不足时明确报告不可比较，缺失事实不补造。

官方参照把共同活动含义映射到统计领域，例如：

```json
{
  "official_domain_map": {
    "sleep": ["personal_essential"],
    "work_study": ["paid_work"],
    "travel": ["transportation"],
    "daily_care": ["personal_essential"]
  }
}
```

每个数组必须非空且不重复，名称必须存在于 `official_reference.domains`。更新参照时保留发布机构、调查名称、发布时间、采集时间、粒度和原始来源地址。每个领域填写 `label_zh`、`supported_duration_kinds`、`urban` 和 `working_age`；参与分钟为非负数，参与率在零到一之间。`world_activity` 与 `observed_digital_session` 是不同的时长语义，不能用数字交互时长代替全部现实生活时长。映射和证据口径见[行为评估设计](../design/07-evaluation.md#evaluation-适配器职责)。

<a name="evaluation-绑定真实候选输入"></a>
## 绑定真实候选输入

`--worldmock` 指向 `worldmock export` 生成的**已完成运行的足迹导出目录**，其中包含
`manifest.json`、认证检查点及 `events.jsonl`、`activities.jsonl`、`verified-experiences.jsonl`、
`purchases.jsonl`、`app-usage.jsonl`、`location-stays.jsonl` 六类正文。当前版本与字段以
[状态与序列化](../design/09-io-formats.md)及[记录引擎](../design/05-recording-engine.md)为准，
冻结清单的 environment_identity 必须与实际环境和规则身份一致。
评估要求 `experiment_completed: true`，检查点位于窗口终点，并验证实际导出内容。
LabelKit-DataSet 的训练记录不能替代这里的完整足迹导出。

`--legacy` 指向旧 LabelKit 交付根目录。当前适配器固定读取以下文件族：

```text
legacy-root/
  out/white-collar-12-weeks.manifest.json
  repaired/white-collar-12-weeks.jsonl
  repaired/white-collar-12-weeks.stream.jsonl
  repaired/white-collar-12-weeks.repair-report.json
  repaired/white-collar-12-weeks.final-audit.json
```

原始 main、stream、report 文件由旧 manifest 的描述引用，也必须完整保留在该根目录内。
最终审计要求 `status: "pass"`、`mode: "full"`，且没有审计问题；修复报告必须绑定原始与修复后文件。
这不是任意 JSONL 导入接口。

`expected_labelkit` 必须且只包含以下字段：

| 字段 | 怎样填写 |
|---|---|
| `run_id`、`delivery_digest` | 从可信旧交付的成功 manifest 核对；digest 是六十四位小写 SHA-256 |
| `main_rows`、`stream_rows` | 修复后文件的正整数行数 |
| `raw_main_sha256`、`raw_stream_sha256` | 原始 main、stream 的实际 SHA-256 |
| `repaired_main_sha256`、`repaired_stream_sha256` | 修复后文件的实际 SHA-256 |
| `repair_report_sha256`、`final_audit_sha256` | 对应报告完整文件的实际 SHA-256 |

只有更换了已经认证的基准交付才更新这些值。改哈希不能替代修复链和完整审计。

<a name="evaluation-可选真人参照"></a>
## 可选真人参照

`--human-reference` 接受规范化的真实事件与活动目录；当前没有把手机日志或 CSV 自动转成该格式的命令。
它复用 WorldMock 的事件和活动结构，只是不要求世界运行的检查点与完成事件。
字段结构见[足迹数据合同](../design/09-io-formats.md)；缺失的真实经历不能通过填造事件补齐。

参照目录必须有 `manifest.json` 和两个 JSONL 文件。清单的必需内容如下，
其中哈希由文件计算，不能照抄占位值；版本和字段定义见[状态与序列化](../design/09-io-formats.md)：

| 清单字段 | 约束 |
|---|---|
| `schema_version`、`committed` | 使用当前参照格式并确认文件已提交 |
| `run_id`、`source_run_ids` | 非空身份；来源数组不重复且包含当前 `run_id`；事件只能引用声明的来源 |
| `window`、`daily_coverage` | 窗口与评估配置完全相同；按顺序列出窗口内所有本地自然日 |
| `artifacts.events`、`artifacts.activities` | 只声明这两个工件；每项含相对 `path`、实际 `sha256`、`rows` |

事件的 `occurred_at`、活动起止时间和区间时间使用 **UTC 整数秒**，不是毫秒；
`elapsed_seconds` 使用秒，并须等于实际活动区间的时长和。
事件 `event_seq` 从一开始连续，事件身份不重复；活动与来源事件的引用必须能互相核对。
所有时间都须符合评估窗口，不能通过清单声明掩盖越界或缺失。

已由数据提供方规范化好的目录，可以先离线核验：

```bash
python - <<'PY'
from pathlib import Path
from worldmock.evaluation.adapters import adapt_human_reference
from worldmock.evaluation.scenario import load_scenario

scenario = load_scenario(Path(".work/my-evaluation.json"))
view = adapt_human_reference(Path("references/my-human"), scenario)
print({"valid": True, "events": len(view.events), "activities": len(view.activities)})
PY
```

<a name="evaluation-离线验证并准备评审"></a>
## 离线验证并准备评审

先运行配置验证；它不会读取密钥、请求模型或启动仿真：

```bash
python - <<'PY'
from pathlib import Path
from worldmock.evaluation.scenario import load_scenario

scenario = load_scenario(Path(".work/my-evaluation.json"))
print({"valid": True, "scenario_id": scenario.scenario_id, "days": len(scenario.window.days)})
PY
```

准备命令会认证完整候选输入、计算统计并写盲评包。把输入路径换成实际目录，输出目录须尚不存在：

```bash
worldmock evaluate \
  --legacy deliveries/legacy-white-collar \
  --worldmock exports/my-world \
  --scenario .work/my-evaluation.json \
  --output evaluations/my-comparison
```

有匹配真人轨迹时增加 `--human-reference references/my-human`。
这条命令本身不调用评审模型；它生成 `review-packets/reviewer-01.json`、
`reviewer-02.json`、`reviewer-03.json` 和准备收据。
只有 `prepare_status: "awaiting_review"` 才能进入评审后发布；`not_comparable` 表示共同观察范围不足。

每位评审只读取分配给自己的包和统一评审指令。不要向评审提供 `unblinding-key.json` 或其他槽的材料。
包中的 `judgeability` 已声明可判断性：`judgeable: false` 的维度必须填写 `unjudgeable`。

<a name="evaluation-怎样填写评审收据"></a>
## 怎样填写评审收据

每个槽单独一个完整 JSON 文件，放在评估输出目录**之外**，例如 `reviews/my-comparison/`。
准备目录的文件清单已经冻结，往里面放评审回答也会使发布校验失败。

| 必填字段 | 写法 |
|---|---|
| `schema_version` | 固定为 `review-record-1` |
| `pack_id`、`reviewer_slot` | 从该评审实际读取的包原样复制 |
| `reviewer_kind` | `human` 或 `model` |
| `reviewer_session_id` | 真实评审会话的非空标识，三个槽不能复用同一个标识 |
| `model` | 真人评审填 `null`；模型评审填 `{"provider": "实际提供方", "name": "实际模型名"}` |
| `prompt_sha256` | 实际统一评审指令文本文件的六十四位小写 SHA-256 |
| `packet_sha256` | 实际评审包文件的六十四位小写 SHA-256 |
| `raw_response` | 保留真实原始回答的非空文本 |
| `ratings` | 覆盖该包全部 `item_id`，每项都包含全部评审维度 |

每个维度必须且只包含 `choice` 和 `rationale`；`choice` 只能为 `left`、`right`、`tie`、`unjudgeable`，
`rationale` 必须是非空依据。`left` 和 `right` 指各自包的左右侧，不能自行换成系统名称。
同模型的不同会话会在报告中披露，不能写成不同真人评审。

`ratings` 数组中的每个条目必须且只包含 `item_id` 与 `dimensions`。
下面是**单条 rating 的完整结构模板**，对应默认六个维度；它还不是可提交的评审结果。
把 `COPY_ITEM_ID_FROM_PACKET` 换成实际包内的 `item_id`，逐项依据证据填写选择与理由。
这里故意用 `null` 和空字符串留空，复制后不填写会被校验拒绝；不要把结构示例当作评审结论。
如果评估配置选择了已有维度的子集，`dimensions` 的键必须与实际包的维度集合完全一致。

```json
{
  "item_id": "COPY_ITEM_ID_FROM_PACKET",
  "dimensions": {
    "temporal_plausibility": {"choice": null, "rationale": ""},
    "causal_continuity": {"choice": null, "rationale": ""},
    "persona_consistency": {"choice": null, "rationale": ""},
    "long_term_consequences": {"choice": null, "rationale": ""},
    "routine_and_variation": {"choice": null, "rationale": ""},
    "missing_expected_activity": {"choice": null, "rationale": ""}
  }
}
```

为了避免手填包身份与哈希，下面的**完整封装脚本**把每位评审的真实回答包装成收据。
事先保存统一指令为 `reviews/my-comparison/prompt.txt`；每位评审的原始回答保存为
`reviewer-01.response.json` 等文件，正文结构为 `{"ratings": [...]}`，其中判断由评审填写。
示例用于三个真人评审；会话名称应替换成自己的真实会话标识：

```bash
python - <<'PY'
import hashlib
import json
from pathlib import Path

prepared = Path("evaluations/my-comparison")
reviews = Path("reviews/my-comparison")
prompt_hash = hashlib.sha256((reviews / "prompt.txt").read_bytes()).hexdigest()
sessions = {
    "reviewer-01": "my-review-session-a",
    "reviewer-02": "my-review-session-b",
    "reviewer-03": "my-review-session-c",
}
for slot, session in sessions.items():
    packet_bytes = (prepared / "review-packets" / f"{slot}.json").read_bytes()
    packet = json.loads(packet_bytes)
    response = (reviews / f"{slot}.response.json").read_text(encoding="utf-8")
    record = {
        "schema_version": "review-record-1", "pack_id": packet["pack_id"], "reviewer_slot": slot,
        "reviewer_kind": "human", "reviewer_session_id": session, "model": None,
        "prompt_sha256": prompt_hash, "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "raw_response": response, "ratings": json.loads(response)["ratings"],
    }
    with (reviews / f"{slot}.json").open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
PY
```

只生成收据不会提交结果。可以在发布前离线校验三份收据；此步骤会校验并解盲，应由评审组织者在全部评审完成后执行：

```bash
python - <<'PY'
from pathlib import Path
from worldmock.evaluation.review import ingest_reviews
from worldmock.evaluation.scenario import load_scenario

scenario = load_scenario(Path(".work/my-evaluation.json"))
paths = sorted(Path("reviews/my-comparison").glob("reviewer-??.json"))
result = ingest_reviews(paths, Path("evaluations/my-comparison"), scenario)
print({"valid": True, "records": len(result["records"])})
PY
```

<a name="evaluation-评审后发布"></a>
## 评审后发布

完成所有真实评审后，仍使用同一输出目录，只传 `--output` 和 `--reviews`：

```bash
worldmock evaluate --output evaluations/my-comparison --reviews \
  reviews/my-comparison/reviewer-01.json \
  reviews/my-comparison/reviewer-02.json \
  reviews/my-comparison/reviewer-03.json
```

准备后不要改评估场景、统计文件或评审包；最终发布会重验哈希。若需要修改设计，重新准备新的评估目录。
成功会生成 `review-results.json`、`result.json`、图表、`report.md` 和 `committed: true` 的 `manifest.json`。

没有真人轨迹时，结果状态为 `comparative_plausibility_only`：报告比较合理性与评审偏好。
有匹配真人轨迹时可进入 `human_reference_comparison`；各维度方向、因果检查和评审一致性仍决定结论，
提供参照不保证某候选获胜。

| 常见错误 | 检查什么 |
|---|---|
| 窗口不匹配或时间越界 | 双方导出、真人参照、共享人物时区和评估窗口是否一致 |
| 缺少完成检查点 | `--worldmock` 是否为完整运行的足迹导出，而非中途导出或训练交付 |
| 未知维度或缺少共同观察范围 | 维度使用已实现名称；双方是否提供相同渠道与时长语义的真实证据 |
| 评审条目不全或槽重复 | 三个包分别评审，所有条目、所有维度都有判断，会话标识互异 |
| 评审材料不可判断却填了偏好 | 根据包中 `judgeability` 改为真实的 `unjudgeable` 判断 |
| 准备目录出现意外文件或哈希变化 | 收据放到外部目录；不要编辑已冻结的准备文件 |

<a name="evaluation-读行为报告里的睡眠统计"></a>
## 读行为报告里的睡眠统计

行为报告的标识是 `behavior-metrics-4`。它的 `sleep_schedule` 把每段睡眠分成三类，写在 `sessions[].category`：

| 类别 | 含义 | 怎样统计 |
|---|---|---|
| `bedtime` | 实际睡满 180 分钟的睡眠，多睡的那段算在内 | 进 `summary`：就寝与起床时刻的中位数、平均时长、段数 |
| `nap` | 已结束而不满 180 分钟的睡眠 | 进 `naps`：次数与平均时长 |
| `unfinished` | 运行结束时还没睡醒的那一段 | 两边都不计 |

不到 3 小时的夜间睡眠算小睡，那一夜没有就寝时刻；下午补觉睡满 3 小时算一次就寝。
没有任何就寝睡眠时 `summary` 为 `null`，报告说明里写「没有睡满 180 分钟的睡眠，就寝与起床时刻无法计算」。
就寝时刻的中位数以早上 6 点为一天的分界来算，零点前后就寝的夜晚不再被算成中午；
代价是在 6 点前后入睡的人会算出偏离很远的时刻，例如 05:50 和 06:10 各一晚算成 18:00。起床时刻的中位数照常按零点起算。

评估依据和完整结果边界见[评估设计](../design/07-evaluation.md)。
