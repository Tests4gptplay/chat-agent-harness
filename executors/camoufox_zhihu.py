#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.headings: list[str] = []
        self.images: list[str] = []
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "img":
            d = dict(attrs)
            for key in ("data-original", "data-actualsrc", "src"):
                value = d.get(key)
                if value and value.startswith(("http://", "https://")):
                    self.images.append(value)
                    break
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag == tag:
            text = re.sub(r"\s+", " ", "".join(self._heading_parts)).strip()
            if text:
                self.headings.append(text)
            self._heading_tag = None
            self._heading_parts = []
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._heading_tag:
            self._heading_parts.append(data)
        self.text_parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.text_parts))
        lines = [re.sub(r"\s+", " ", x).strip() for x in raw.splitlines()]
        return "\n".join(x for x in lines if x)


def ensure_camoufox() -> dict[str, Any]:
    setup: dict[str, Any] = {"installed_package": False, "fetched_browser": False}
    try:
        import camoufox  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "camoufox"],
            check=True, timeout=300
        )
        setup["installed_package"] = True

    probe = subprocess.run(
        [sys.executable, "-m", "camoufox", "version"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=60
    )
    setup["version_before"] = probe.stdout[-4000:]
    if probe.returncode != 0 or "Installed" not in probe.stdout or re.search(r"Installed\s+No", probe.stdout, re.I):
        subprocess.run([sys.executable, "-m", "camoufox", "fetch"], check=True, timeout=900)
        setup["fetched_browser"] = True
    return setup


def find_article(initial: dict[str, Any], article_id: str) -> dict[str, Any] | None:
    cur: Any = initial
    try:
        articles = cur["initialState"]["entities"]["articles"]
        if isinstance(articles, dict):
            value = articles.get(article_id)
            if isinstance(value, dict):
                return value
            for v in articles.values():
                if isinstance(v, dict) and str(v.get("id") or "") == article_id:
                    return v
    except Exception:
        pass
    return None


def fact_scan(text: str) -> dict[str, Any]:
    # Derived factual signals only; do not persist the full article body.
    model_patterns = [
        r"DeepSeek[^\n，。；]{0,40}",
        r"GLM[^\n，。；]{0,40}",
        r"Qwen[^\n，。；]{0,40}",
        r"MiniMax[^\n，。；]{0,40}",
        r"Kimi[^\n，。；]{0,40}",
        r"Doubao[^\n，。；]{0,40}",
        r"豆包[^\n，。；]{0,40}",
        r"混元[^\n，。；]{0,40}",
        r"文心[^\n，。；]{0,40}",
    ]
    models: list[str] = []
    for pat in model_patterns:
        for m in re.finditer(pat, text, flags=re.I):
            v = re.sub(r"\s+", " ", m.group(0)).strip(" ：:，,。；;")
            if 2 <= len(v) <= 80 and v not in models:
                models.append(v)
    timings = []
    for m in re.finditer(r"([^\n，。；]{0,60}?)(\d{2,5})\s*秒", text):
        context = re.sub(r"\s+", " ", m.group(1)).strip()
        timings.append({"context": context[-60:], "seconds": int(m.group(2))})
    costs = []
    for m in re.finditer(r"([^\n，。；]{0,60}?)(\d+(?:\.\d+)?)\s*(?:元|美元|倍)", text):
        context = re.sub(r"\s+", " ", m.group(1)).strip()
        costs.append({"context": context[-60:], "value": m.group(2), "unit": m.group(0)[-1]})
    return {
        "model_mentions": models[:40],
        "timing_mentions": timings[:40],
        "cost_or_ratio_mentions": costs[:40],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--action", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()

    action_path = Path(args.action)
    result_path = Path(args.result)
    action = json.loads(action_path.read_text(encoding="utf-8"))
    payload = action.get("payload") or {}
    url = str(payload.get("url") or "")
    if not url.startswith(("https://zhuanlan.zhihu.com/", "https://www.zhihu.com/")):
        raise SystemExit("camoufox_zhihu only accepts Zhihu URLs")

    article_match = re.search(r"/p/(\d+)", url)
    article_id = article_match.group(1) if article_match else ""
    artifact_dir = ROOT / str(payload.get("artifact_dir") or f"evidence/{action['task_id']}")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    setup = ensure_camoufox()
    from camoufox.sync_api import Camoufox

    final_url = ""
    page_title = ""
    initial_raw = ""
    body_html = ""
    extraction_source = ""
    blocked = False
    with Camoufox(headless=True, os="windows", locale="zh-CN", humanize=True) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(6000)
        final_url = page.url
        page_title = page.title()
        blocked = "/account/unhuman" in final_url or "unhuman" in page.content().lower()

        script = page.locator("script#js-initialData")
        if script.count() > 0:
            initial_raw = script.first.text_content() or ""

        if initial_raw:
            try:
                initial = json.loads(initial_raw)
                article = find_article(initial, article_id)
                if article:
                    page_title = str(article.get("title") or page_title)
                    body_html = str(article.get("content") or "")
                    extraction_source = "js-initialData"
            except Exception:
                pass

        if not body_html:
            for selector in ("article", ".Post-RichTextContainer", ".RichText"):
                loc = page.locator(selector)
                if loc.count() > 0:
                    try:
                        body_html = loc.first.inner_html()
                        extraction_source = f"dom:{selector}"
                        break
                    except Exception:
                        continue

    parser = ArticleParser()
    parser.feed(body_html)
    body_text = parser.text()
    text_sha = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    facts = fact_scan(body_text)

    meta = {
        "source_url": url,
        "final_url": final_url,
        "article_id": article_id,
        "title": page_title,
        "blocked": blocked,
        "extraction_source": extraction_source,
        "body_text_chars": len(body_text),
        "body_text_sha256": text_sha,
        "heading_count": len(parser.headings),
        "headings": parser.headings[:80],
        "image_count": len(dict.fromkeys(parser.images)),
        "image_urls": list(dict.fromkeys(parser.images))[:100],
        "facts": facts,
        "camoufox_setup": setup,
        "copyright_note": "Full article body was processed transiently for structured extraction but is not persisted verbatim.",
    }
    meta_path = artifact_dir / "article_structure.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status = "PASS" if (not blocked and len(body_text) >= 1000 and extraction_source) else "FAIL"
    summary = (
        f"Camoufox accessed Zhihu article: {page_title}; "
        f"source={extraction_source}; chars={len(body_text)}; headings={len(parser.headings)}; images={len(dict.fromkeys(parser.images))}"
        if status == "PASS"
        else f"Zhihu capture insufficient: blocked={blocked}, final_url={final_url}, chars={len(body_text)}, source={extraction_source}"
    )
    rel_meta = meta_path.relative_to(ROOT).as_posix()
    result = {
        "v": 1,
        "result_id": f"result-{action['action_id']}",
        "action_id": action["action_id"],
        "task_id": action["task_id"],
        "round": action["round"],
        "status": status,
        "summary": summary,
        "evidence": [
            {"type": "final_url", "value": final_url},
            {"type": "title", "value": page_title},
            {"type": "extraction_source", "value": extraction_source},
            {"type": "body_text_chars", "value": len(body_text)},
            {"type": "body_text_sha256", "value": text_sha},
            {"type": "heading_count", "value": len(parser.headings)},
            {"type": "image_count", "value": len(dict.fromkeys(parser.images))},
            {"type": "structured_artifact", "value": rel_meta, "ref": rel_meta},
        ],
        "artifacts": [rel_meta],
        "fault_boundary": "none" if status == "PASS" else "zhihu_capture",
        "log_excerpt": "Camoufox used as anti-detect browser; full body not persisted verbatim.",
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "title": page_title, "chars": len(body_text), "source": extraction_source}, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
