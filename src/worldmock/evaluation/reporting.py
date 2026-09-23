"""@brief 生成中文评估图表与有证据边界的 Markdown 报告。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.font_manager import FontProperties

from worldmock.evaluation.errors import EvaluationError

LOGGER = logging.getLogger(__name__)
_FONT_PATHS = (
    Path("C:/Windows/Fonts/NotoSansSC-VF.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
)


@dataclass(frozen=True)
class ReportBundle:
    """@brief 聚合报告渲染所需的已核验评估工件。"""

    validation: Mapping[str, Any]
    mapping: Mapping[str, Any]
    causality: Mapping[str, Any]
    metrics: Mapping[str, Any]
    official: Mapping[str, Any]
    reviews: Mapping[str, Any]
    result: Mapping[str, Any]
    charts: Sequence[Path]


def write_charts(
    root: Path,
    metrics: Mapping[str, Any],
    official: Mapping[str, Any],
    reviews: Mapping[str, Any],
) -> tuple[Path, ...]:
    """@brief 写入节律、官方边际和盲评三个中文图表。"""
    chart_root = root / "charts"
    chart_root.mkdir(parents=True, exist_ok=True)
    font = _chinese_font()
    paths = (
        chart_root / "week-hour-rhythm.png",
        chart_root / "official-reference.png",
        chart_root / "blind-review.png",
    )
    _rhythm_chart(paths[0], metrics, font)
    _official_chart(paths[1], official, font)
    _review_chart(paths[2], reviews, font)
    return paths


def render_report(bundle: ReportBundle) -> str:
    """@brief 将最终状态和逐维度证据渲染为中文 Markdown。"""
    lines = _report_opening(bundle)
    lines.extend(_report_evidence(bundle))
    lines.extend(_report_references(bundle))
    lines.extend(_report_limits(bundle))
    return "\n".join(lines)


def _report_opening(bundle: ReportBundle) -> list[str]:
    """@brief 渲染标题、结论和候选身份。"""
    return [
        "# 数字足迹真实性比较",
        "",
        "## 结论",
        "",
        f"结果状态：`{bundle.result['status']}`；方向：`{bundle.result['outcome']}`。",
        "",
        _claim_text(bundle.result),
        "",
        "候选 A 是认证后的 LabelKit 修复数据，候选 B 是认证后的 WorldMock 导出。",
        "",
    ]


def _report_evidence(bundle: ReportBundle) -> list[str]:
    """@brief 渲染守恒、支持范围、因果和量化证据。"""
    return [
        "## 输入与逐事件守恒",
        "",
        *_mapping_table(bundle.mapping),
        "",
        *_support_table(bundle.metrics["support"]),
        "",
        "## 因果与资源连续性",
        "",
        *_causal_table(bundle.causality),
        "",
        "## 分维度量化结果",
        "",
        *_metric_table(bundle.metrics),
        "",
    ]


def _report_references(bundle: ReportBundle) -> list[str]:
    """@brief 渲染公开参照、盲评和图表。"""
    return [
        "## 国家统计局公开聚合参照",
        "",
        "公开数据只用于地域相近的描述性边际诊断，不是目标白领的个体真值。",
        "",
        *_official_table(bundle.official),
        "",
        "## 盲评",
        "",
        *_review_table(bundle.reviews),
        "",
        "## 图表",
        "",
        *[f"![{path.stem}](charts/{path.name})" for path in bundle.charts],
        "",
    ]


def _report_limits(bundle: ReportBundle) -> list[str]:
    """@brief 渲染不允许越过的证据声明边界。"""
    return [
        "## 证据边界",
        "",
        "- 没有匹配目标人群的真人微观轨迹时，结论只能描述相对合理性。",
        "",
        "- 缺失观察渠道进入支持分母；报告没有把它当成零活动或睡眠。",
        "",
        "- 模型评审是模型会话证据；同一模型的不同会话可能相关，不能称为独立真人。",
        "",
        "- 指标逐维度呈现，没有合成任意加权总分。",
        "",
    ]


def _chinese_font() -> FontProperties:
    """@brief 选择本机明确存在的中文字体，缺失时停止生成乱码图。"""
    for path in _FONT_PATHS:
        if path.is_file():
            return FontProperties(fname=path)
    LOGGER.error("Chinese chart font is unavailable")
    raise EvaluationError("chart_failed", "Chinese chart font is unavailable")


def _rhythm_chart(path: Path, metrics: Mapping[str, Any], font: FontProperties) -> None:
    """@brief 绘制双方星期小时活动起点分布。"""
    rhythm = metrics["metrics"]["week_hour_rhythm"]
    left = np.asarray(rhythm["left_distribution"], dtype=float).reshape(7, 24)
    right = np.asarray(rhythm["right_distribution"], dtype=float).reshape(7, 24)
    figure, axes = plt.subplots(2, 1, figsize=(12, 6), constrained_layout=True)
    maximum = max(float(left.max()), float(right.max()), 1e-12)
    for axis, data, title in zip(axes, (left, right), ("候选 A", "候选 B")):
        image = axis.imshow(data, aspect="auto", vmin=0, vmax=maximum, cmap="Blues")
        axis.set_title(f"{title}：星期小时活动起点分布", fontproperties=font)
        axis.set_xlabel("小时", fontproperties=font)
        axis.set_ylabel("星期（周一至周日）", fontproperties=font)
        axis.set_yticks(range(7), ["一", "二", "三", "四", "五", "六", "日"], fontproperties=font)
        figure.colorbar(image, ax=axis, fraction=0.02)
    _save_figure(figure, path)


def _official_chart(path: Path, official: Mapping[str, Any], font: FontProperties) -> None:
    """@brief 分别绘制参与率和参与者分钟数，不把边际合成总分。"""
    rows = [
        (name, value)
        for name, value in official["domains"].items()
        if value["left"]["status"] == "ok" and value["right"]["status"] == "ok"
    ]
    figure, axes = plt.subplots(2, 1, figsize=(12, 8), constrained_layout=True)
    if not rows:
        for axis in axes:
            axis.text(0.5, 0.5, "没有双方共同支持的官方活动领域", ha="center", fontproperties=font)
            axis.set_axis_off()
    else:
        _official_axis(axes[0], rows, "participation_rate", "参与率", font)
        _official_axis(axes[1], rows, "participant_minutes", "参与者分钟数", font)
    _save_figure(figure, path)


def _official_axis(
    axis: Any,
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    field: str,
    title: str,
    font: FontProperties,
) -> None:
    """@brief 绘制一个官方边际字段的候选与参照点。"""
    x = np.arange(len(rows), dtype=float)
    width = 0.18
    labels = [value["label_zh"] for _, value in rows]
    series = (
        ("候选 A", [value["left"]["observed"][field] for _, value in rows]),
        ("候选 B", [value["right"]["observed"][field] for _, value in rows]),
        ("城镇参照", [_reference_value(value, "urban", field) for _, value in rows]),
        ("劳动年龄参照", [_reference_value(value, "working_age", field) for _, value in rows]),
    )
    for index, (name, values) in enumerate(series):
        axis.bar(x + (index - 1.5) * width, values, width, label=name)
    axis.set_title(title, fontproperties=font)
    axis.set_xticks(x, labels, fontproperties=font)
    axis.legend(prop=font)


def _reference_value(value: Mapping[str, Any], cohort: str, field: str) -> float:
    """@brief 读取冻结官方来源中的原始参照值。"""
    return float(value["reference"][cohort][field])


def _review_chart(path: Path, reviews: Mapping[str, Any], font: FontProperties) -> None:
    """@brief 绘制每个盲评维度的候选偏好和无法判断比例。"""
    values = reviews["preference"]["by_dimension"]
    names = list(values)
    x = np.arange(len(names), dtype=float)
    left = [values[name]["left_preference_proportion"] or 0 for name in names]
    right = [values[name]["right_preference_proportion"] or 0 for name in names]
    ties = [values[name]["tie_proportion"] or 0 for name in names]
    missing = [values[name]["unjudgeable_proportion"] or 0 for name in names]
    figure, axis = plt.subplots(figsize=(13, 5), constrained_layout=True)
    axis.bar(x - 0.3, left, 0.2, label="候选 A 偏好")
    axis.bar(x - 0.1, right, 0.2, label="候选 B 偏好")
    axis.bar(x + 0.1, ties, 0.2, label="相当")
    axis.bar(x + 0.3, missing, 0.2, label="无法判断")
    axis.set_ylim(0, 1)
    axis.set_title("盲评分维度比例", fontproperties=font)
    axis.set_xticks(x, names, rotation=20, ha="right")
    axis.legend(prop=font)
    _save_figure(figure, path)


def _save_figure(figure: Any, path: Path) -> None:
    """@brief 使用固定元数据保存 PNG 并关闭绘图资源。"""
    try:
        figure.savefig(path, dpi=140, metadata={"Software": "WorldMock"})
    except Exception as exc:
        LOGGER.exception("Evaluation chart generation failed")
        raise EvaluationError("chart_failed", "evaluation chart generation failed") from exc
    finally:
        plt.close(figure)


def _claim_text(result: Mapping[str, Any]) -> str:
    """@brief 将英文机器边界转换为面向读者的中文说明。"""
    if result["status"] == "comparative_plausibility_only":
        return "本报告只比较相对合理性，不能声称已通过目标人群真人微观数据验证。"
    if result["status"] == "human_reference_comparison":
        return "真人参照结论只适用于输入中匹配的观察窗口和共同支持范围。"
    return "共同观察范围不足，不能比较两方。"


def _mapping_table(mapping: Mapping[str, Any]) -> list[str]:
    """@brief 渲染候选事件和活动守恒表。"""
    rows = ["| 候选 | 输入事件 | 映射事件 | 活动 | 自然日 | 未知活动 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for label, key in (("A", "left"), ("B", "right")):
        value = mapping[key]
        rows.append(
            f"| {label} | {value['input_events']} | {value['mapped_events']} | "
            f"{value['activities']} | {value['mapped_days']} | {value['unknown_activities']} |"
        )
    return rows


def _support_table(support: Mapping[str, Any]) -> list[str]:
    """@brief 渲染共同渠道、类别和覆盖分母。"""
    rows = [
        "| 范围 | 值 |",
        "| --- | --- |",
        f"| 共同渠道 | {', '.join(support['shared_channels']) or '无'} |",
        f"| 共同类别 | {', '.join(support['shared_categories']) or '无'} |",
    ]
    for label, key in (("A", "left"), ("B", "right")):
        value = support["coverage"][key]
        rows.append(f"| 候选 {label} 共同活动/全部活动 | {value['shared_activities']}/{value['total_activities']} |")
        rows.append(f"| 候选 {label} 私有事件排除 | {value['private_events_excluded']} |")
    return rows


def _causal_table(causality: Mapping[str, Any]) -> list[str]:
    """@brief 渲染生产因果检查器结果。"""
    rows = ["| 候选 | 是否通过 | 硬错误 | findings |", "| --- | --- | ---: | ---: |"]
    for label, key in (("A", "left"), ("B", "right")):
        value = causality[key]
        rows.append(f"| {label} | {value['passed']} | {value['hard_error_count']} | {value['finding_count']} |")
    return rows


def _metric_table(metrics: Mapping[str, Any]) -> list[str]:
    """@brief 渲染全部必需量化维度及适用状态。"""
    rows = ["| 维度 | 状态 | 置信区间状态 |", "| --- | --- | --- |"]
    for name, value in metrics["metrics"].items():
        interval = value.get("confidence_interval") or value.get("daily_count_confidence_interval") or {}
        rows.append(f"| {name} | {value.get('status')} | {interval.get('status', '分项报告')} |")
    human = metrics.get("human_reference")
    if human:
        rows.append(f"| human_reference | {human.get('status')} | 同日期共同支持 |")
    return rows


def _official_table(official: Mapping[str, Any]) -> list[str]:
    """@brief 渲染公开边际的逐领域比较方向。"""
    rows = ["| 活动领域 | 候选 A | 候选 B | 边际方向 |", "| --- | --- | --- | --- |"]
    for value in official["domains"].values():
        result = value["pair_alignment"]["result"]
        rows.append(f"| {value['label_zh']} | {value['left']['status']} | {value['right']['status']} | {result} |")
    return rows


def _review_table(reviews: Mapping[str, Any]) -> list[str]:
    """@brief 渲染盲评偏好、无法判断和一致性。"""
    rows = ["| 维度 | A 偏好 | B 偏好 | 无法判断 | alpha |", "| --- | ---: | ---: | ---: | ---: |"]
    reliability = reviews["reliability"]["by_dimension"]
    for name, value in reviews["preference"]["by_dimension"].items():
        alpha = reliability[name]["alpha"]
        rows.append(
            f"| {name} | {_percent(value['left_preference_proportion'])} | "
            f"{_percent(value['right_preference_proportion'])} | "
            f"{_percent(value['unjudgeable_proportion'])} | {_number(alpha)} |"
        )
    dependence = reviews["reviewer_dependence"]
    coverage = reviews["selection_coverage"]["missing_ordinary_samples"]
    missing = ", ".join(sorted(set(coverage["regular_workday"] + coverage["ordinary_weekend"])))
    rows.extend([
        "",
        f"模型评审 {dependence['model_review_count']} 个，真人评审 {dependence['human_review_count']} 个。",
        f"无可选普通日的周块：{missing or '无'}。",
    ])
    return rows


def _percent(value: Any) -> str:
    """@brief 格式化比例或缺失值。"""
    return "不适用" if value is None else f"{float(value):.1%}"


def _number(value: Any) -> str:
    """@brief 格式化统计量或缺失值。"""
    return "不适用" if value is None else f"{float(value):.3f}"
