"""Agent skills：目录约定加载，注入主编 system prompt（轻量，无新依赖）。

约定：目录内含 `SKILL.md`；`SKILLS_DIR` 指向含若干 skill 子目录的根目录。
内置 skill 放在本包 `skills/` 下，随 `include_bundled=True` 默认注入。
"""
from __future__ import annotations

from pathlib import Path

_BUNDLED = Path(__file__).parent / "skills"


def _skill_text(skill_dir: Path) -> str:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return ""
    txt = md.read_text(encoding="utf-8").strip()
    name = skill_dir.name
    if not txt:
        return ""
    # 确保注入内容里带 skill 名称，方便 LLM 理解上下文来源
    if txt.startswith("#"):
        return txt
    return f"### Skill: {name}\n\n{txt}"


def _collect(roots: list[Path]) -> str:
    parts: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*/SKILL.md")):
            txt = _skill_text(p.parent)
            if txt:
                parts.append(txt)
    return "\n\n".join(parts)


def load_skills_text(skills_dir: str | None = None, *, include_bundled: bool = True) -> str:
    roots: list[Path] = []
    if include_bundled:
        roots.append(_BUNDLED)
    if skills_dir:
        d = Path(skills_dir).expanduser()
        if not d.is_absolute():
            d = Path.cwd() / d
        roots.append(d)
    return _collect(roots)
