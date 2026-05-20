"""将主编 JSON 草稿渲染为自包含 HTML（卡片 + 关键词标签）。"""
from __future__ import annotations

import html
from typing import Any, Callable

ResolveTitle = Callable[[str], str]


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _tags_html(tags: list[str], *, variant: str = "default") -> str:
    if not tags:
        return ""
    cls = "tag"
    if variant != "default":
        cls += f" tag--{variant}"
    chips = "".join(f'<span class="{cls}">{_esc(t)}</span>' for t in tags)
    return f'<div class="tags">{chips}</div>'


def _item_card(it: dict[str, str], resolve_title: ResolveTitle) -> str:
    claim = it.get("claim") or ""
    ctx = (it.get("context") or "").strip()
    su = it.get("source_url") or ""
    lt = _esc(resolve_title(su))
    tags = it.get("tags") or []
    tags_block = _tags_html(tags, variant="item") if tags else ""
    ctx_block = f'<p class="card-context">{_esc(ctx)}</p>' if ctx else ""
    return f"""<article class="card item-card">
  {tags_block}
  <h3 class="card-claim">{_esc(claim)}</h3>
  {ctx_block}
  <footer class="card-footer"><a href="{_esc(su)}" rel="noopener noreferrer" target="_blank">{lt}</a></footer>
</article>"""


def render_digest_html(
    draft: dict[str, Any],
    *,
    week_label: str,
    resolve_title: ResolveTitle,
    generated_at: str = "",
    articles_count: int = 0,
    verification_note: str = "",
) -> str:
    headline = str(draft.get("headline") or "").strip()
    title = headline or f"本周技术快报 · {week_label}"
    keywords = draft.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []

    summary_parts: list[str] = []
    for p in draft.get("summary_paragraphs") or []:
        t = str(p).strip()
        if t:
            summary_parts.append(f"<p>{_esc(t)}</p>")

    sections_html: list[str] = []
    for sec in draft.get("sections") or []:
        stitle = str(sec.get("title") or "主题").strip()
        sec_tags = sec.get("tags") or []
        if not isinstance(sec_tags, list):
            sec_tags = []
        items_html = "".join(
            _item_card(it, resolve_title)
            for it in (sec.get("items") or [])
            if isinstance(it, dict)
        )
        if not items_html:
            continue
        sections_html.append(
            f"""<section class="section">
  <header class="section-header">
    <h2>{_esc(stitle)}</h2>
    {_tags_html([str(x).strip() for x in sec_tags if str(x).strip()], variant="section")}
  </header>
  <div class="card-grid">{items_html}</div>
</section>"""
        )

    trivia_html = ""
    trivia = draft.get("trivia") or []
    if isinstance(trivia, list) and trivia:
        cards = "".join(
            _item_card(it, resolve_title)
            for it in trivia
            if isinstance(it, dict)
        )
        if cards:
            trivia_html = f"""<section class="section section--trivia">
  <header class="section-header"><h2>边角短讯</h2></header>
  <div class="card-grid card-grid--compact">{cards}</div>
</section>"""

    meta_bits = [f"周次 {week_label}"]
    if generated_at:
        meta_bits.append(f"生成 {generated_at}")
    if articles_count:
        meta_bits.append(f"语料 {articles_count} 条")
    meta_line = " · ".join(meta_bits)

    warn = ""
    if verification_note.strip():
        warn = f'<aside class="warn">{_esc(verification_note)}</aside>'

    kw_block = _tags_html(
        [str(x).strip() for x in keywords if str(x).strip()],
        variant="keyword",
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_esc(title)}</title>
  <style>
    :root {{
      --bg: #f4f6f9;
      --surface: #ffffff;
      --text: #1a1d26;
      --muted: #5c6370;
      --accent: #2563eb;
      --accent-soft: #dbeafe;
      --border: #e5e9f0;
      --shadow: 0 1px 3px rgba(15, 23, 42, 0.06), 0 8px 24px rgba(15, 23, 42, 0.06);
      --radius: 12px;
      --font: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font);
      font-size: 16px;
      line-height: 1.65;
      color: var(--text);
      background: var(--bg);
    }}
    .page {{
      max-width: 920px;
      margin: 0 auto;
      padding: 2rem 1.25rem 3rem;
    }}
    .hero {{
      background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 55%, #3b82f6 100%);
      color: #fff;
      border-radius: calc(var(--radius) + 4px);
      padding: 1.75rem 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 0.5rem;
      font-size: 1.65rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .hero .meta {{
      margin: 0;
      font-size: 0.875rem;
      opacity: 0.88;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-top: 0.75rem;
    }}
    .tag {{
      display: inline-block;
      padding: 0.2rem 0.65rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 500;
      line-height: 1.4;
    }}
    .tag--keyword {{
      background: rgba(255,255,255,0.22);
      color: #fff;
      border: 1px solid rgba(255,255,255,0.35);
    }}
    .tag--section {{
      background: var(--accent-soft);
      color: #1d4ed8;
    }}
    .tag--item {{
      background: #f1f5f9;
      color: #475569;
    }}
    .summary-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1.25rem 1.35rem;
      margin-bottom: 1.75rem;
      box-shadow: var(--shadow);
    }}
    .summary-card h2 {{
      margin: 0 0 0.75rem;
      font-size: 1.1rem;
      color: var(--muted);
      font-weight: 600;
      text-transform: none;
    }}
    .summary-card p {{
      margin: 0 0 0.85rem;
    }}
    .summary-card p:last-child {{ margin-bottom: 0; }}
    .section {{ margin-bottom: 2rem; }}
    .section-header {{
      margin-bottom: 1rem;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--accent);
    }}
    .section-header h2 {{
      margin: 0 0 0.35rem;
      font-size: 1.25rem;
    }}
    .section--trivia .section-header {{
      border-bottom-color: #94a3b8;
    }}
    .card-grid {{
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    }}
    .card-grid--compact {{ grid-template-columns: 1fr; }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1.1rem 1.15rem;
      box-shadow: var(--shadow);
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    .card:hover {{
      border-color: #bfdbfe;
      box-shadow: 0 4px 16px rgba(37, 99, 235, 0.12);
    }}
    .card-claim {{
      margin: 0.35rem 0 0.5rem;
      font-size: 1rem;
      font-weight: 600;
      line-height: 1.5;
    }}
    .card-context {{
      margin: 0 0 0.65rem;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    .card-footer {{
      margin: 0;
      padding-top: 0.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.82rem;
    }}
    .card-footer a {{
      color: var(--accent);
      text-decoration: none;
      word-break: break-all;
    }}
    .card-footer a:hover {{ text-decoration: underline; }}
    .warn {{
      background: #fff7ed;
      border: 1px solid #fed7aa;
      color: #9a3412;
      padding: 0.85rem 1rem;
      border-radius: var(--radius);
      margin-bottom: 1.25rem;
      font-size: 0.9rem;
    }}
    footer.page-footer {{
      margin-top: 2.5rem;
      text-align: center;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    @media (max-width: 600px) {{
      .page {{ padding: 1rem 0.85rem 2rem; }}
      .hero h1 {{ font-size: 1.35rem; }}
      .card-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <h1>{_esc(title)}</h1>
      <p class="meta">{_esc(meta_line)}</p>
      {kw_block}
    </header>
    {warn}
    <div class="summary-card">
      <h2>本周综述</h2>
      {"".join(summary_parts) if summary_parts else "<p>（暂无综述）</p>"}
    </div>
    {"".join(sections_html)}
    {trivia_html}
    <footer class="page-footer">tect-news-agent · 结构化主编快报</footer>
  </main>
</body>
</html>
"""
