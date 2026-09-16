"""@brief 动机档案 → 规范文本人格（模板化生成，零 LLM，数值↔文本一致）。

架构三段式的第三段：与数值档案同源生成——每句陈述都由档案字段直接推导，
不引入新的自由文本。输出是 canonical 附注，不替代用户原始画像。
"""

from __future__ import annotations

from worldmock.profile.models import (
    ATTENTION_KEYS,
    BIG_FIVE_DIMENSIONS,
    WEIGHT_KEYS,
    MotivationProfile,
)

#: 大五形容词表：分数带 1-2 低 / 3-5 中 / 6-7 高（anchored: 量表两端语义）。
_BIG_FIVE_TERMS = {
    "openness": {"low": "保守务实，偏好熟悉确定的事物", "mid": "对新事物态度适中",
                 "high": "好奇心强，乐于尝试新鲜体验"},
    "conscientiousness": {"low": "随性冲动，计划性弱", "mid": "做事有一定的条理",
                          "high": "自律有条理，目标导向强"},
    "extraversion": {"low": "安静内敛，偏好独处", "mid": "社交需求适中",
                     "high": "热情外向，主动社交"},
    "agreeableness": {"low": "直接竞争，不易妥协", "mid": "与人合作但不失立场",
                      "high": "随和体贴，重视合作"},
    "neuroticism": {"low": "情绪平稳，抗压", "mid": "情绪有波动但可控",
                    "high": "敏感易焦虑，情绪波动大"},
}
_DIMENSION_LABELS = {
    "openness": "开放性", "conscientiousness": "尽责性", "extraversion": "外向性",
    "agreeableness": "宜人性", "neuroticism": "神经质",
}
_WEIGHT_LABELS = {
    "money_fen": "金钱积累", "energy_kcal": "体能精力", "sleep_debt_hours": "睡眠恢复",
    "entertainment_need": "娱乐放松", "goal_task": "任务目标达成",
}
_ATTENTION_LABELS = {
    "feature_film": "电影", "news_feed": "新闻资讯", "joke_feed": "段子笑料",
    "webnovel_excerpt": "网文小说", "drama_series": "剧集",
}


def _band(score: int) -> str:
    """@brief 1-2 低带、3-5 中带、6-7 高带。"""
    if score <= 2:
        return "low"
    if score >= 6:
        return "high"
    return "mid"


def _ranked(weights: dict[str, float]) -> list[tuple[str, float]]:
    """@brief 按权重降序排序（平手按键名稳定排序），供文本排序一致。"""
    return sorted(weights.items(), key=lambda item: (-item[1], item[0]))


def render_persona_text(profile: MotivationProfile) -> str:
    """@brief 从数值档案生成规范文本人格（确定性：同档案同文本）。"""
    big_five = profile.big_five
    scores = big_five.model_dump()
    traits = "，".join(
        f"{_DIMENSION_LABELS[dimension]}{_band_score_text(scores[dimension])}"
        f"（{scores[dimension]}/7，{_BIG_FIVE_TERMS[dimension][_band(scores[dimension])]}）"
        for dimension in BIG_FIVE_DIMENSIONS)
    lines = [
        "# 规范文本人格（由动机档案同源生成）",
        "",
        f"## 大五特质：{traits}。",
        _weights_line(profile),
        _decision_line(profile),
        _loss_line(profile),
        _comfort_line(profile),
        _aspiration_line(profile),
        _emotion_line(profile),
        _attention_line(profile),
    ]
    return "\n".join(lines) + "\n"


def _band_score_text(score: int) -> str:
    """@brief 分数带文字：低/中等/高。"""
    return {"low": "低", "mid": "中等", "high": "高"}[_band(score)]


def _weights_line(profile: MotivationProfile) -> str:
    """@brief 动机权重排序描述（百分比由 0.01 格点值×100 精确推导）。"""
    ranked = _ranked(profile.weights.model_dump())
    top = "、".join(f"{_WEIGHT_LABELS[key]}（{value:.0%}）" for key, value in ranked[:3])
    weakest_key, weakest_value = ranked[-1]
    return (f"## 动机结构：最看重的三项依次是{top}；"
            f"最弱的是{_WEIGHT_LABELS[weakest_key]}（{weakest_value:.0%}）。")


def _decision_line(profile: MotivationProfile) -> str:
    """@brief tau（决策噪声）与现时偏好的行为语义。"""
    tau_text = {
        0.5: "行动与评分排序高度一致，极少被随机性带偏",
        1.0: "行为总体跟随评分排序，偶有偏离",
        2.0: "选择带有明显随机波动，排序只是倾向",
        3.0: "行为受随机扰动强烈，常偏离评分排序",
    }[profile.tau]
    if profile.beta_present_bias <= 0.65:
        bias_text = "显著偏好延迟的大回报"
    elif profile.beta_present_bias < 0.85:
        bias_text = "在即时与延迟回报间权衡"
    else:
        bias_text = "强烈偏好即时回报"
    return (f"## 决策特性：{tau_text}（tau={profile.tau:g}）；"
            f"现时偏好 beta={profile.beta_present_bias:g}，{bias_text}。")


def _loss_line(profile: MotivationProfile) -> str:
    """@brief 损失厌恶语义（1=中性，3=损失感受极强）。"""
    money = profile.loss_aversion.money_fen
    energy = profile.loss_aversion.energy_kcal
    def _strength(value: float) -> str:
        if value <= 1.5:
            return "较钝"
        if value <= 2.25:
            return "中等"
        return "强烈"
    return (f"## 损失厌恶：对金钱损失{_strength(money)}"
            f"（λ={money:g}），对精力耗损{_strength(energy)}（λ={energy:g}）。")


def _comfort_line(profile: MotivationProfile) -> str:
    """@brief 舒适缓冲：现金（分→元）与能量阈值。"""
    yuan = profile.comfort.balance_fen / 100
    return (f"## 舒适缓冲：现金余额低于 {yuan:g} 元、可用精力低于 "
            f"{profile.comfort.energy_kcal:g} kcal 时会明显感到不安并寻求恢复。")


def _aspiration_line(profile: MotivationProfile) -> str:
    """@brief 志向：每日有效工作秒数 → 小时（整除/半档对齐 600s 粒度）。"""
    seconds = profile.aspiration.daily_effective_work_seconds
    hours = seconds / 3600
    return f"## 志向：期望每天完成约 {hours:g} 小时有效工作（{seconds} 秒）。"


def _emotion_line(profile: MotivationProfile) -> str:
    """@brief 情绪敏感度语义。"""
    sensitivity = profile.emotion_sensitivity
    if sensitivity <= 0.3:
        text = "事件对情绪影响小，恢复快"
    elif sensitivity <= 0.7:
        text = "事件会明显影响情绪，但能恢复"
    else:
        text = "事件强烈牵动情绪，恢复慢"
    return f"## 情绪敏感度：{sensitivity:g}——{text}。"


def _attention_line(profile: MotivationProfile) -> str:
    """@brief 注意先验排序描述（top3 + 末位）。"""
    ranked = _ranked(profile.attention_prior.model_dump())
    top = "、".join(f"{_ATTENTION_LABELS[key]}（{value:.0%}）" for key, value in ranked[:3])
    weakest_key, weakest_value = ranked[-1]
    return (f"## 注意力偏好：空闲时最可能选择{top}；"
            f"最少选{_ATTENTION_LABELS[weakest_key]}（{weakest_value:.0%}）。")
