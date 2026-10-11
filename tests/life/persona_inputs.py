"""在冻结环境副本的 persona.toml 里声明人物作息，保持原文的 LF 与表结构。"""

from pathlib import Path

#: persona.toml 里本来就有、声明时只能替换的两个作息键。
PRESENT_RHYTHM_KEYS = ("chronotype_midsleep", "sleep_need_hours")


def declare_rhythm(root: Path, **values) -> Path:
    """给环境副本的人设换上声明的作息值，返回 persona.toml 的路径。

    时段写成两个小时数的数组，放在 [body] 表之前（TOML 的顶层键必须先于表）；睡眠中点与睡眠需要替换原有的两行。
    """
    path = Path(root) / "persona.toml"
    text = path.read_text(encoding="utf-8")
    for name in PRESENT_RHYTHM_KEYS:
        if name in values:
            current = next(line for line in text.splitlines() if line.startswith(f"{name} = "))
            text = text.replace(current, f"{name} = {values.pop(name)!r}")
    lines = "".join(f"{name} = {list(value)!r}\n" for name, value in values.items())
    assert text.count("\n[body]\n") == 1
    path.write_bytes(text.replace("\n[body]\n", "\n" + lines + "\n[body]\n").encode("utf-8"))
    return path
