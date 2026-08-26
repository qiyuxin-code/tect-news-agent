"""agent_editor 冒烟测试：用 FunctionModel 模拟 LLM，验证工具调用循环与输出校验（不触网）。"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tect_news.agent_editor import DigestDeps, DigestDraft, build_editor_agent
from tect_news.config import load_settings
from tect_news.harness.skills import load_skills_text
from tect_news.models import Article

UTC = timezone.utc

ROOT = Path(__file__).resolve().parent.parent


def _settings():
    s = load_settings()
    return replace(
        s,
        openai_api_key="sk-test",
        digest_agent_score=False,
        digest_cs_filter=False,
        digest_fetch_body=False,
        digest_strict_urls=True,
        digest_output_html=False,
        digest_items_per_source=3,
    )


def _articles() -> list[Article]:
    now = datetime.now(UTC)
    return [
        Article(
            title="量子位：某大模型发布",
            url="https://example.com/a",
            source="rss",
            summary="发布了新的推理模型。",
            published_at=now - timedelta(days=1),
        ),
        Article(
            title="GitHub：某仓库开源",
            url="https://github.com/foo/bar",
            source="github",
            summary="开源了一个新框架。",
            published_at=now - timedelta(days=2),
        ),
    ]


def _deps(settings) -> DigestDeps:
    now = datetime.now(UTC)
    return DigestDeps(
        settings=settings,
        week_label="2026-W34",
        collect_since=now - timedelta(days=7),
        until=now,
        seed_path=ROOT / "data" / "xiaohongshu_seed.json",
        output_dir=ROOT / "output",
        generated_at=now.isoformat(),
        pre_collected=_articles(),
        log=print,
    )


def _mock_model(steps: list[str]):
    """steps: 期望的依次 tool_name；最后一步必须是结构化输出工具（自动取 output_tools[0].name）。"""
    state = {"i": 0}

    def handler(messages, info):
        step = state["i"]
        state["i"] += 1
        if step < len(steps):
            tool = steps[step]
            if tool == "__final__":
                out_name = info.output_tools[0].name if info.output_tools else "final_result"
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=out_name,
                            args={
                                "headline": "本周技术主线",
                                "keywords": ["大模型", "开源"],
                                "summary_paragraphs": ["本周值得关注的两条技术脉络。"],
                                "sections": [
                                    {
                                        "title": "rss",
                                        "tags": [],
                                        "items": [
                                            {
                                                "claim": "某大模型发布",
                                                "context": "做了推理优化",
                                                "tags": ["大模型"],
                                                "source_url": "https://example.com/a",
                                            }
                                        ],
                                    }
                                ],
                                "trivia": [],
                            },
                            tool_call_id=f"t{step}",
                        )
                    ]
                )
            return ModelResponse(
                parts=[ToolCallPart(tool_name=tool, args={}, tool_call_id=f"t{step}")]
            )
        raise AssertionError(f"mock 调用超出预期步数：{state['i']}")

    return FunctionModel(handler)


def test_agent_loop_calls_tool_then_output():
    settings = _settings()
    deps = _deps(settings)
    model = _mock_model(["collect_news", "__final__"])
    agent: Agent[DigestDeps, DigestDraft] = build_editor_agent(settings, model=model)

    result = agent.run_sync("本周技术快报", deps=deps)
    draft = result.output

    assert isinstance(draft, DigestDraft)
    assert draft.headline == "本周技术主线"
    assert len(draft.sections) == 1
    # collect_news 必须真的把语料放进工作台
    assert len(deps.collected) == 2
    assert draft.sections[0].items[0].source_url == "https://example.com/a"


def test_output_validator_rejects_unknown_url():
    """输出里出现白名单外 URL → ModelRetry 重试，最终走重试步骤后通过。"""
    settings = _settings()
    deps = _deps(settings)
    state = {"n": 0}

    def handler(messages, info):
        out_name = info.output_tools[0].name if info.output_tools else "final_result"
        n = state["n"]
        state["n"] += 1
        if n == 0:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="collect_news", args={}, tool_call_id="c0")]
            )
        url = "https://evil.example.com" if n < 3 else "https://example.com/a"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=out_name,
                    args={
                        "headline": "标题",
                        "keywords": ["大模型"],
                        "summary_paragraphs": ["综述。"],
                        "sections": [
                            {
                                "title": "rss",
                                "tags": [],
                                "items": [
                                    {
                                        "claim": "c",
                                        "context": "k",
                                        "tags": [],
                                        "source_url": url,
                                    }
                                ],
                            }
                        ],
                        "trivia": [],
                    },
                    tool_call_id=f"t{n}",
                )
            ]
        )

    agent: Agent[DigestDeps, DigestDraft] = build_editor_agent(
        settings, model=FunctionModel(handler)
    )
    result = agent.run_sync("生成", deps=deps)
    assert result.output.sections[0].items[0].source_url == "https://example.com/a"


def test_skills_load_bundled():
    text = load_skills_text(include_bundled=True)
    assert "editor-workflow" in text
    assert "compliance" in text
    assert "collect_news" in text


def test_skills_load_custom_dir(tmp_path):
    (tmp_path / "custom-skill").mkdir()
    (tmp_path / "custom-skill" / "SKILL.md").write_text("# Skill: custom\n- 自定义约束", encoding="utf-8")
    text = load_skills_text(str(tmp_path), include_bundled=False)
    assert "custom" in text and "自定义约束" in text


def test_mcp_config_build_ok():
    """mcp_config 指向无法连接的 URL 时，构建 agent 不应报错（连接延迟到 run）。"""
    settings = replace(_settings(), mcp_config=[{"url": "https://127.0.0.1:9/mcp"}])
    agent = build_editor_agent(settings)
    assert agent is not None


def test_run_agent_digest_writes_file(tmp_path, monkeypatch):
    """run_agent_digest 全链路：mock model → 文件落盘 + 白名单校验通过。"""
    from tect_news import agent_editor as ae

    settings = replace(_settings(), digest_output_html=False)
    model = _mock_model(["collect_news", "__final__"])

    def fake_build(s, **kw):
        return build_editor_agent(s, model=model, **kw)

    monkeypatch.setattr(ae, "build_editor_agent", fake_build)

    out = ae.run_agent_digest(
        settings=settings,
        output_dir=tmp_path,
        pre_collected=_articles(),
    )
    assert out.name.endswith(".md")
    content = out.read_text(encoding="utf-8")
    assert "本周技术主线" in content
    assert "https://example.com/a" in content
    assert "verification_ok=True" in content
