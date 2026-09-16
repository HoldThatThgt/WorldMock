# 社媒流语料（预排新鲜感）

三个社交流平台语料，为 12 周（84 天）场景提供"每天解锁新内容"的刷流体验。
准备阶段一次性拉取并按天分批冻结；运行时不产生任何新内容，模拟日期只决定
数字人当天刷到的是哪一段——时间是世界唯一的变化推动者。

## 目录结构

```
social/
  weibo/          微博流：3000 条，条目 30-60 秒（刷微博）
    day-001.json … day-084.json   每天 35-36 条
    weibo.srt                      84 天批次按序拼接的时间轴
  zhihu/          知乎流：1000 条，条目 60-120 秒（看知乎）
    day-001.json … day-084.json   每天 11-12 条
    zhihu.srt
  movie_reviews/  影评流：1000 条，条目 30-60 秒（看完电影刷影评）
    day-001.json … day-084.json   每天 11-12 条
    movie-reviews.srt
```

每个 day 文件：`{platform, day, date, segment_start_seconds, items}`，
其中 `items` 元素字段为
`{item_id, platform, author_hint, body, estimated_read_seconds}`。
`date` 从 2026-01-05（12 周场景 start_at）起逐日 +1；
`segment_start_seconds` 是该天批次在拼接时间轴中的起点（前缀和）。

## 来源与抽样（全部固定种子、去重、按正文长度过滤）

| 流 | 来源（Hugging Face） | 许可 | 抽取方式 | 种子 |
| --- | --- | --- | --- | --- |
| 微博 | `dirtycomputer/weibo_senti_100k`（weibo_senti_100k.csv，119,988 行，19,699,818 字节，全量下载） | 数据集未声明许可（研究用灰色） | 保留 150≤字符≤300 的正文（对应 30-60 秒），按正文精确去重得 6,876 条，shuffle 后取前 3000 | Random(20260921) |
| 知乎 | `wangrui6/Zhihu-KOL`（train-00003-of-00005 parquet 分片，291,841,874 字节，仅下载该分片；sha256 与 HF etag 比对一致） | 数据集未声明许可（研究用灰色） | body=问题(INSTRUCTION)+换行+回答(RESPONSE)；保留 300≤字符≤600（60-120 秒），去重得 32,416 条，shuffle 取前 1000 | Random(20260922) |
| 影评 | `dirtycomputer/douban_movie_review`（all_movies_with_id.csv 全量 2,295,613,764 字节，超单源 300MB 预算，HTTP Range 仅取前 134,217,728 字节即 587,405 行前缀） | 数据集未声明许可（研究用灰色） | 前缀内保留 150≤字符≤300 的 Comment（30-60 秒），去重得 7,479 条，shuffle 取前 1000 | Random(20260923) |

`estimated_read_seconds = round(len(body)/5)`，沿用项目 5 字符/秒的正文阅读
约定。正文内多余空白/换行压缩为单个换行（防止 SRT 块被空行拆断）。

`author_hint`：微博取正文首个 @handle（转发链头），无则 `weibo-user-<sha1 前 6 位>`；
知乎为 `zhihu-kol-<answer_id>`（数据集 METADATA 不含作者名，取 answer_id 作稳定
伪句柄，可回溯来源 URL）；影评取真实 Username 并附所评影片名
（`用户名·评《影片》`，缺失则 `douban-user-<sha1 前 6 位>`）。合成句柄是稳定伪身份，
不是真实用户。

## 运行时机制（选型：内容进度系统，不新增预排事件）

每个流是场景 `contents[]` 里的一个条目（timeline 指向拼接 SRT），沿用现有
内容进度系统：WATCH 活动推进 media_position，`reveal_watch` 只揭示本次新跨过的
cue。84 天批次按序拼接保证消费顺序 = 天序——数字人每天刷到的都是没刷过的
"新内容"，语料整体哈希冻结、可复现。不引入 FEED_BATCH_RELEASE 之类的
新调度事件：进度系统已提供同样的新鲜感语义，无需改动 src/。

## 重建说明

本目录为一次性冻结产物（manifest 逐文件登记 sha256）。重建脚本为一次性
工具（/tmp/social_download/build_social_corpus.py，未入库），上表 selector
完整记录了可复核的抽样过程。
