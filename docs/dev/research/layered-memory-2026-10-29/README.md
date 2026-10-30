# 分层记忆提案的证据快照（2026-10-29）

本目录保存 [分层记忆提案](../../PROPOSAL_2026-10-29_LAYERED_MEMORY.md) 背后的分析材料，是写作当时的快照，不是现行规范。代码基线为 `f67c25e51db8a9e10e8b2b1f4fdff1f26cd5ca30`；证据运行为 WorldMock-Dataset/execution/ 下的 `20261029T023604Z-white-collar-food-variety`（V）和 `20261029T023604Z-white-collar-food-baseline`（B）。

| 路径 | 内容 |
| --- | --- |
| brief.md | 事实简报 F1–F22，已经对抗核对；提案里的 F 编号即指这里 |
| out/map-*.md | 现状测绘：决策循环、提示各层、AS2 记忆仓库、合同与门禁、运行证据、类别推导 |
| out/gaps.json、out/verify.json | 心智模型与现状的差距表，以及逐条核对和修正 |
| out2/design-*.md | 三份内部设计稿：照字面（literal）、确定性优先（deterministic）、成本优先（economy） |
| out2/judgments.json | 合同、行为、成本三个视角对三份设计稿的评审 |
| out2/critiques.json、out2/final-check.json | 对综合稿的事实、完整性和需求覆盖核对；意见已按代码复核后改进提案 |
| out2/review-after-rulings.json、out2/final-delta-check.json | 2026-10-29 两条裁定（压缩一律由模型写成段落；search_memory 是唯一的记忆工具）之后的四视角评审和最终复核；意见均已改进提案 |
| owner_literal/、econ/、synth/、critic/ | 对 V、B 的 footprints.sqlite 和导出做只读查询的脚本，提案里的数字由它们算出。其中 critic/longterm.py、longterm_1012.json、viewrate.py、fill.py 服务于已废弃的「长期」块（第 17 条裁定为只经检索），只作历史证据 |
| out3/reports/ | 2026-10-29 第二批裁定（第 6、14、17、18–20 条和各级字数上限）之后的五份测量报告：tokens.md（token/字换算与 14,000 字预算）、entries.md（按决策的今天条目模拟）、econ.md（缓存与费用、回顾成本、检索上限）、views.md（看法移出提示的影响与 M12）、code.md（代码与测试改动面）；sheet.md 是据此写出的逐节变更规格 |
| out3/tokens/、entries/、econ/、views/、code/ | 上述五份测量的脚本和小体量输出；逐决策的大中间文件（*_rows.jsonl、windows_*.json、rows.json、*.npy）没有保留，重跑脚本即可生成 |
| out3/spec/example1150.py | 从 entries/v_1012_entries.json 取出 V 10-12 11:50 那次决策可见的 30 条今天条目，写出 example1150.json，提案的 11:50 示例即来自这里 |

脚本里写的是本机的绝对路径，以只读方式打开运行目录。第一批脚本中，除了 `critic/longterm.py` 会把算出的「长期」示例写到当前目录下的 `critic/longterm_1012.json`，其余都不写文件；out3/ 的脚本把输出写在各自的子目录里。
