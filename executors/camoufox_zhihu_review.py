#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from .camoufox_zhihu import ArticleParser, ensure_camoufox, find_article
except ImportError:
    from camoufox_zhihu import ArticleParser, ensure_camoufox, find_article

ROOT = Path(__file__).resolve().parents[1]

GENERIC_HEADINGS = (
    "任务", "先看看", "接下来", "分享", "实验环境", "提示词",
)

MODEL_ALIASES: dict[str, list[str]] = {
    "DeepSeek": ["DeepSeek"],
    "MiniMax": ["MiniMax"],
    "Xiaomi": ["Xiaomi", "MiMo", "小米"],
    "Doubao": ["Doubao", "豆包"],
    "GLM": ["GLM"],
    "Kimi": ["Kimi"],
    "Qwen": ["Qwen"],
    "Hunyuan": ["Hunyuan", "混元"],
    "ERNIE": ["ERNIE", "文心"],
    "Step": ["Step", "阶跃"],
    "Yi": ["Yi", "零一万物"],
}

MODEL_PATTERNS: dict[str, str] = {
    "DeepSeek": r"DeepSeek[^\n，。；]{0,50}",
    "MiniMax": r"MiniMax[^\n，。；]{0,50}",
    "Xiaomi": r"(?:Xiaomi|MiMo|小米)[^\n，。；]{0,50}",
    "Doubao": r"(?:Doubao|豆包)[^\n，。；]{0,50}",
    "GLM": r"GLM[^\n，。；]{0,50}",
    "Kimi": r"Kimi[^\n，。；]{0,50}",
    "Qwen": r"Qwen[^\n，。；]{0,50}",
    "Hunyuan": r"(?:Hunyuan|混元)[^\n，。；]{0,50}",
    "ERNIE": r"(?:ERNIE|文心)[^\n，。；]{0,50}",
    "Step": r"(?:Step|阶跃)[^\n，。；]{0,50}",
    "Yi": r"(?:Yi|零一万物)[^\n，。；]{0,50}",
}

EVAL_TERMS = ["造型", "结构", "细节", "材质", "颜色", "光影", "还原", "速度", "效率", "耗时", "稳定", "完成度"]
METHOD_TERMS = ["相同提示词", "一次性输出", "四个维度", "Borda", "评委", "实验环境", "提示词", "Blender"]
ANCHORS = ["最大优势", "实际体验", "高峰", "实验环境", "提示词", "新手组", "实力组"] + EVAL_TERMS


def compact(value: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def sentence_list(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    out: list[str] = []
    for part in parts:
        value = compact(part, 220)
        if value:
            out.append(value)
    return out


def unique(values: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def section_preview(text: str, headings: list[str], target: str, limit: int = 180) -> str | None:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    heading_set = {re.sub(r"\s+", " ", h).strip() for h in headings}
    target_norm = re.sub(r"\s+", " ", target).strip()
    for idx, line in enumerate(lines):
        if line != target_norm:
            continue
        selected: list[str] = []
        for nxt in lines[idx + 1 :]:
            if not nxt:
                continue
            if nxt in heading_set:
                break
            selected.append(nxt)
            if len(" ".join(selected)) >= limit:
                break
        if selected:
            return compact(" ".join(selected), limit)
    return None


def extract_review_packet(text: str, headings: list[str], title: str) -> dict[str, Any]:
    sents = sentence_list(text)
    model_candidates: list[str] = []
    model_heading_names: list[str] = []

    for heading in headings:
        h = compact(heading, 100)
        if h and not h.startswith(GENERIC_HEADINGS) and len(h) <= 80:
            model_heading_names.append(h)
            model_candidates.append(h)

    haystack = title + "\n" + text
    model_signals: dict[str, Any] = {}
    for label, aliases in MODEL_ALIASES.items():
        pattern = MODEL_PATTERNS[label]
        mentions = [compact(m.group(0), 110) for m in re.finditer(pattern, haystack, flags=re.I)]
        evidence = [
            s for s in sents
            if any(alias.lower() in s.lower() for alias in aliases)
        ]
        if mentions or evidence:
            model_signals[label] = {
                "mentions": unique(mentions, 6),
                "evidence_sentences": unique(evidence, 4),
            }
            model_candidates.extend(mentions[:2])

    timings: list[dict[str, Any]] = []
    time_re = re.compile(
        r"(?<![A-Za-z0-9_.])(?P<value>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>秒|分钟|minutes?|mins?|s)(?![A-Za-z])",
        re.I,
    )
    for s in sents:
        for m in time_re.finditer(s):
            timings.append({
                "value": m.group("value"),
                "unit": m.group("unit"),
                "context": compact(s, 180),
            })

    anchor_evidence: dict[str, list[str]] = {}
    for anchor in ANCHORS:
        hits = [s for s in sents if anchor.lower() in s.lower()]
        if hits:
            anchor_evidence[anchor] = unique(hits, 3)

    model_section_evidence: dict[str, str] = {}
    for heading in model_heading_names:
        preview = section_preview(text, headings, heading, 160)
        if preview:
            model_section_evidence[heading] = preview

    group_windows: dict[str, dict[str, Any]] = {}
    group_specs = [
        ("新手组", "先看看新手组：", "接下来是实力组："),
        ("实力组", "接下来是实力组：", "分享一些实际体验"),
    ]
    for group_name, start_marker, end_marker in group_specs:
        start = text.find(start_marker)
        end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
        if start >= 0:
            segment = text[start : end if end >= 0 else len(text)]
            members = []
            for label, aliases in MODEL_ALIASES.items():
                if any(alias.lower() in segment.lower() for alias in aliases):
                    members.append(label)
            group_windows[group_name] = {
                "members": members,
                "evidence": compact(segment, 360),
            }

    comparison_evidence = unique([
        s for s in sents
        if any(term.lower() in s.lower() for term in EVAL_TERMS + METHOD_TERMS + ["十分之一", "十倍", "最高", "排名"])
    ], 18)

    grouped_members: list[str] = []
    for group in group_windows.values():
        for member in group.get("members") or []:
            if member not in grouped_members:
                grouped_members.append(member)
    participants = []
    if "DeepSeek" in model_signals:
        participants.append("DeepSeek")
    for member in grouped_members:
        if member not in participants:
            participants.append(member)
    for label in model_signals:
        if label not in participants:
            participants.append(label)

    method_evidence: dict[str, str] = {}
    for heading in ("实验环境", "提示词"):
        preview = section_preview(text, headings, heading, 220)
        if preview:
            method_evidence[heading] = preview

    return {
        "participants": participants,
        "model_candidates": unique([compact(x, 100) for x in model_candidates], 30),
        "model_signals": model_signals,
        "model_section_evidence": model_section_evidence,
        "task_headings": [h for h in headings if h.startswith("任务")],
        "group_headings": [h for h in headings if "组" in h],
        "group_windows": group_windows,
        "timing_mentions": timings[:30],
        "evaluation_dimensions_present": [term for term in EVAL_TERMS if term in text],
        "comparison_evidence": comparison_evidence,
        "anchor_evidence": anchor_evidence,
        "method_evidence": method_evidence,
        "copyright_note": "Only compact structured signals and short evidence windows are persisted; full article text is not stored.",
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
        raise SystemExit("camoufox_zhihu_review only accepts Zhihu URLs")

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
    packet = extract_review_packet(body_text, parser.headings, page_title)

    artifact = {
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
        "image_urls": list(dict.fromkeys(parser.images))[:40],
        "review_packet": packet,
        "camoufox_setup": setup,
    }

    artifact_path = artifact_dir / "review_packet.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    enough = (
        not blocked
        and len(body_text) >= 1000
        and bool(extraction_source)
        and len(packet["task_headings"]) >= 2
        and len(packet["participants"]) >= 7
        and len(packet["timing_mentions"]) >= 1
        and bool(packet["group_windows"])
        and len(packet["comparison_evidence"]) >= 3
    )
    status = "PASS" if enough else "FAIL"
    rel_artifact = artifact_path.relative_to(ROOT).as_posix()
    summary = (
        f"Fresh Zhihu review packet captured: participants={len(packet['participants'])}; "
        f"tasks={len(packet['task_headings'])}; timings={len(packet['timing_mentions'])}; "
        f"chars={len(body_text)}"
        if status == "PASS"
        else f"Review packet insufficient: blocked={blocked}; participants={len(packet['participants'])}; "
             f"tasks={len(packet['task_headings'])}; timings={len(packet['timing_mentions'])}; chars={len(body_text)}"
    )

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
            {"type": "body_text_chars", "value": len(body_text)},
            {"type": "body_text_sha256", "value": text_sha},
            {"type": "participant_count", "value": len(packet["participants"])},
            {"type": "model_signal_count", "value": len(packet["model_signals"])},
            {"type": "task_heading_count", "value": len(packet["task_headings"])},
            {"type": "timing_count", "value": len(packet["timing_mentions"])},
            {"type": "image_count", "value": artifact["image_count"]},
            {"type": "structured_artifact", "value": rel_artifact, "ref": rel_artifact},
        ],
        "artifacts": [rel_artifact],
        "fault_boundary": "none" if status == "PASS" else "zhihu_review_capture",
        "log_excerpt": "Camoufox captured the article; full body remained transient; only compact review evidence was persisted.",
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "summary": summary}, ensure_ascii=False))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
