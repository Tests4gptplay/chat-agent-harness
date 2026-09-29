from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse


CHATGPT_HOST = "chatgpt.com"
COMPOSER_SELECTORS = (
    "#prompt-textarea",
    'textarea[data-testid="prompt-textarea"]',
    'div[contenteditable="true"][data-lexical-editor="true"]',
    '[contenteditable="true"][role="textbox"]',
)
STOP_SELECTORS = (
    'button[data-testid="stop-button"]',
    'button[aria-label*="Stop"]',
    'button[aria-label*="停止"]',
)
SEND_SELECTORS = (
    'button[data-testid="send-button"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
)
ASSISTANT_COMPLETE_ACTION = re.compile(
    r"(Regenerate(?:\s+response)?|重新生成(?:回复|回答)?|再生成(?:する)?|답변\s*다시\s*생성)",
    re.I,
)
MESSAGE_SELECTOR = (
    '[data-chatgpt-search-unit-key$=":user"], '
    '[data-chatgpt-search-unit-key$=":assistant"]'
)


@dataclass(frozen=True)
class ChatLocation:
    url: str
    project_key: str | None
    conversation_id: str | None
    project_root: bool


def chatgpt_location(url: str) -> ChatLocation | None:
    try:
        u = urlparse(str(url or ""))
    except Exception:
        return None
    if u.scheme != "https" or u.hostname != CHATGPT_HOST:
        return None
    project = re.match(r"^/g/(g-p-[A-Za-z0-9]+)(?:-[^/]+)?(?:/|$)", u.path)
    conversation = re.search(r"/c/([A-Za-z0-9-]+)(?:/|$)", u.path)
    conversation_id = conversation.group(1) if conversation else None
    if not conversation_id:
        query = parse_qs(u.query)
        for key in ("conversation", "conversationId", "conversation_id"):
            values = query.get(key) or []
            if values and values[0]:
                conversation_id = values[0]
                break
    return ChatLocation(
        url=url,
        project_key=project.group(1) if project else None,
        conversation_id=conversation_id,
        project_root=bool(project and re.search(r"/project/?$", u.path) and not conversation_id),
    )


def conversation_key(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return ""
    if parsed.hostname != CHATGPT_HOST:
        return ""
    conversation = re.search(r"/c/([A-Za-z0-9-]+)(?:/|$)", parsed.path)
    if conversation:
        return "c:" + conversation.group(1)
    query = parse_qs(parsed.query)
    for key in ("conversation", "conversationId", "conversation_id"):
        values = query.get(key) or []
        if values and values[0]:
            return "q:" + values[0]
    return "path:" + parsed.path + (("?" + parsed.query) if parsed.query else "")


def project_route_matches(url: str, project_key: str, *, root_only: bool = False) -> bool:
    loc = chatgpt_location(url)
    if not loc or loc.project_key != project_key:
        return False
    return loc.project_root if root_only else True


def conversation_route_matches(url: str, project_key: str, conversation_id: str) -> bool:
    loc = chatgpt_location(url)
    return bool(loc and loc.project_key == project_key and loc.conversation_id == conversation_id)


def wake_marker(text: str) -> str | None:
    for line in str(text or "").splitlines():
        value = line.strip()
        if re.fullmatch(r"GAH_WAKE v=1 id=[A-Za-z0-9._-]{8,128} project=\S+", value):
            return value
    return None


def parse_dispatch_context(text: str) -> dict[str, Any] | None:
    match = re.search(
        r"GAH_DISPATCH task_id=(\S+) backend_cl=(\S+) dispatch_id=(\S+) "
        r"generation=(\d+) fence_token=(\S+)",
        str(text or ""),
    )
    if not match:
        return None
    return {
        "task_id": match.group(1),
        "backend_cl": match.group(2),
        "dispatch_id": match.group(3),
        "dispatch_generation": int(match.group(4)),
        "fence_token": match.group(5),
    }


def tool_call_blocks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"CAH_TOOL_CALL_BEGIN\s*([\s\S]*?)\s*CAH_TOOL_CALL_END", str(text or "")):
        raw = match.group(1).strip()
        if not raw or len(raw) > 32768:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def syscall_blocks(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in re.finditer(
        r"GAH_SYSCALL_BEGIN\s*([\s\S]*?)\s*GAH_SYSCALL_END",
        str(text or ""),
    ):
        raw = match.group(1).strip()
        if not raw or len(raw) > 32768:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


class ChatGPTUI:
    def __init__(self, cdp_url: str, browser_endpoint_path: Path | str | None = None):
        self.cdp_url = cdp_url
        self.browser_endpoint_path = Path(browser_endpoint_path) if browser_endpoint_path else None
        self.bound_endpoint = None
        self._pw = None
        self.browser = None
        self.context = None

    def _clear_bound_endpoint_file(self) -> None:
        if self.browser_endpoint_path:
            try:
                self.browser_endpoint_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _publish_bound_endpoint(self, endpoint: str) -> None:
        if not self.browser_endpoint_path:
            return
        self.browser_endpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "v": 1,
            "endpoint": endpoint,
            "transport": "playwright",
            "source_cdp": self.cdp_url,
            "host_pid": os.getpid(),
        }
        tmp = self.browser_endpoint_path.with_suffix(self.browser_endpoint_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.browser_endpoint_path)

    def connect(self) -> "ChatGPTUI":
        from playwright.sync_api import sync_playwright

        self._clear_bound_endpoint_file()
        try:
            self._pw = sync_playwright().start()
            self.browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            contexts = list(self.browser.contexts)
            if not contexts:
                raise RuntimeError("CDP browser has no context")
            self.context = contexts[0]
            if self.browser_endpoint_path:
                bound = self.browser.bind(
                    "cah-playwright-host",
                    workspace_dir=str(self.browser_endpoint_path.parent),
                    host="127.0.0.1",
                    port=0,
                )
                self.bound_endpoint = str(bound["endpoint"])
                if not self.bound_endpoint.startswith("ws://127.0.0.1:"):
                    raise RuntimeError("Playwright bound endpoint is not localhost-only")
                self._publish_bound_endpoint(self.bound_endpoint)
            # Secondary browser clients attach through Browser.bind(), not Chrome CDP.
            # Keep a dialog listener so native Playwright does not auto-dismiss dialogs
            # before an authorized secondary client can observe them.
            self.context.on("dialog", lambda dialog: None)
            return self
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        self._clear_bound_endpoint_file()
        if self._pw:
            self._pw.stop()
        self.bound_endpoint = None
        self.browser = None
        self.context = None
        self._pw = None

    def pages(self) -> list[Any]:
        if not self.context:
            raise RuntimeError("Playwright is not connected")
        return list(self.context.pages)

    def list_tabs(self) -> dict[str, Any]:
        tabs = []
        for page in self.pages():
            loc = chatgpt_location(page.url)
            if not loc:
                continue
            target = (
                {"conversation_id": loc.conversation_id, **({"project_key": loc.project_key} if loc.project_key else {})}
                if loc.conversation_id
                else {"url": page.url}
            )
            tabs.append({
                "target": target,
                "title": page.title(),
                "url": page.url,
                "project_key": loc.project_key,
                "conversation_id": loc.conversation_id,
            })
        return {"tabs": tabs, "count": len(tabs)}

    def matching_pages(
        self,
        *,
        project_key: str | None = None,
        conversation_id: str | None = None,
        url: str | None = None,
    ) -> list[Any]:
        matches = []
        for page in self.pages():
            loc = chatgpt_location(page.url)
            if not loc:
                continue
            if project_key and loc.project_key != project_key:
                continue
            if conversation_id and loc.conversation_id != conversation_id:
                continue
            if url and conversation_key(page.url) != conversation_key(url):
                continue
            if project_key or conversation_id or url:
                matches.append(page)
        return matches

    def find_page(
        self,
        *,
        project_key: str | None = None,
        conversation_id: str | None = None,
        url: str | None = None,
    ) -> Any | None:
        matches = self.matching_pages(
            project_key=project_key,
            conversation_id=conversation_id,
            url=url,
        )
        return matches[0] if matches else None

    def new_page(self, url: str) -> Any:
        if not self.context:
            raise RuntimeError("Playwright is not connected")
        # Background targets preserve the user's active tab and window state.
        session = self.browser.new_browser_cdp_session()
        try:
            target_id = session.send(
                "Target.createTarget", {"url": "about:blank", "background": True}
            )["targetId"]
            deadline = time.monotonic() + 30
            page = None
            while page is None:
                for candidate in self.context.pages:
                    candidate_session = self.context.new_cdp_session(candidate)
                    try:
                        info = candidate_session.send("Target.getTargetInfo")["targetInfo"]
                    finally:
                        candidate_session.detach()
                    if info["targetId"] == target_id:
                        page = candidate
                        break
                if page is None:
                    if time.monotonic() >= deadline:
                        session.send("Target.closeTarget", {"targetId": target_id})
                        raise RuntimeError("Background tab did not attach to Playwright")
                    session.send("Target.getTargetInfo", {"targetId": target_id})
        finally:
            session.detach()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        return page

    def conversation_page(
        self,
        *,
        conversation_id: str,
        url: str,
        project_key: str | None = None,
    ) -> Any:
        page = self.find_page(
            project_key=project_key,
            conversation_id=conversation_id,
        )
        if page is not None:
            return page

        page = self.new_page(url)
        try:
            def matches(current_url: str) -> bool:
                location = chatgpt_location(str(current_url))
                return bool(
                    location
                    and location.conversation_id == conversation_id
                    and (not project_key or location.project_key == project_key)
                )

            page.wait_for_url(matches, timeout=20000)
            return page
        except Exception:
            page.close()
            raise

    def project_root(self, project_key: str, project_root_url: str, *, reuse: bool = True) -> Any:
        if reuse:
            for page in self.pages():
                if project_route_matches(page.url, project_key, root_only=True):
                    return page
        page = self.new_page(project_root_url)
        page.wait_for_url(re.compile(rf"^https://chatgpt\.com/g/{re.escape(project_key)}(?:-[^/]+)?/project/?$"), timeout=20000)
        return page

    @staticmethod
    def _visible(locator: Any) -> bool:
        try:
            return locator.is_visible()
        except Exception:
            return False

    def composer(self, page: Any) -> Any:
        return page.locator(", ".join(COMPOSER_SELECTORS)).filter(visible=True)

    def clear_composer(self, page: Any) -> dict[str, Any]:
        locator = page.locator(", ".join(COMPOSER_SELECTORS))
        visible = [
            locator.nth(index)
            for index in range(locator.count())
            if self._visible(locator.nth(index))
        ]
        if not visible:
            return {
                "cleared": False,
                "cleared_count": 0,
                "composer_count": 0,
                "prior_length": 0,
                "composer_present": False,
            }

        cleared = 0
        prior_length = 0
        for composer in visible:
            prior = (
                composer.inner_text()
                if composer.get_attribute("contenteditable") == "true"
                else composer.input_value()
            )
            prior_length += len(prior or "")
            if prior:
                composer.fill("")
                cleared += 1
        return {
            "cleared": cleared > 0,
            "cleared_count": cleared,
            "composer_count": len(visible),
            "prior_length": prior_length,
            "composer_present": True,
        }

    def dismiss_history_rate_limit(self, page: Any) -> bool:
        dialogs = page.locator('[role="dialog"], [aria-modal="true"]')
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if not self._visible(dialog):
                continue
            text = " ".join((dialog.inner_text() or "").split())
            lower = text.lower()
            zh = (
                "请求过于频繁" in text
                and ("访问对话记录" in text or "对话记录" in text)
                and ("暂时限制" in text or "限制你访问" in text or "限制访问" in text)
            )
            en = (
                ("too many requests" in lower or "requests too frequent" in lower)
                and ("conversation history" in lower or "chat history" in lower)
                and "temporarily" in lower
                and ("limit" in lower or "restrict" in lower)
            )
            if not (zh or en):
                continue
            candidates = dialog.get_by_role("button", name=re.compile(r"^(明白了|知道了|Got it|OK|Okay)$", re.I))
            if candidates.count() == 1:
                candidates.click()
                return True
        return False

    def response_running(self, page: Any) -> bool:
        return page.locator(", ".join(STOP_SELECTORS)).filter(visible=True).count() > 0

    def message_nodes(self, page: Any) -> Any:
        messages = page.locator('[data-message-author-role="user"], [data-message-author-role="assistant"]')
        return messages if messages.count() else page.locator(MESSAGE_SELECTOR)

    @staticmethod
    def message_role(node: Any) -> str:
        role = node.get_attribute("data-message-author-role")
        if role in {"user", "assistant"}:
            return role
        key = node.get_attribute("data-chatgpt-search-unit-key") or ""
        if key.endswith(":user"):
            return "user"
        if key.endswith(":assistant"):
            return "assistant"
        return ""

    @staticmethod
    def message_text(node: Any, role: str) -> str:
        if role == "assistant":
            body = node.locator('[data-markdown-text-style="assistant-message"]')
            if body.count():
                return body.last.inner_text() or ""
            # Current fallback search-unit nodes include a screen-reader heading
            # such as "ChatGPT 说：" before the actual assistant text. Strip only
            # that explicit accessibility heading so one-word CAH signals remain
            # exact even while the markdown wrapper is being attached.
            headings = node.locator('h4[data-conversation-role="assistant"]')
            text = node.inner_text() or ""
            if headings.count():
                heading = (headings.first.inner_text() or "").strip()
                if heading and text.lstrip().startswith(heading):
                    text = text.lstrip()[len(heading):].lstrip(" \t\r\n:：")
            return text
        return node.inner_text() or ""

    def assistant_turn_complete(self, node: Any) -> bool:
        """Return message-local evidence that this exact assistant turn ended.

        ChatGPT can leave the page-level Stop control visible briefly after the
        completed assistant action toolbar has already appeared. The
        Regenerate action belongs to the completed assistant turn and is a
        stronger local completion signal than an unrelated/stale page control.
        """
        current = node
        for _ in range(4):
            parent = current.locator("xpath=..")
            if parent.count() == 0:
                break
            current = parent
            buttons = current.locator("button").filter(visible=True)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                label = " ".join(filter(None, [
                    button.get_attribute("aria-label"),
                    button.get_attribute("title"),
                    (button.inner_text() or "").strip(),
                ]))
                if ASSISTANT_COMPLETE_ACTION.search(label):
                    return True
        return False

    def assistant_count(self, page: Any) -> int:
        messages = page.locator('[data-message-author-role="assistant"]')
        return messages.count() or page.locator('[data-chatgpt-search-unit-key$=":assistant"]').count()

    def _turn_after(self, page: Any, nodes: Any, index: int) -> dict[str, Any]:
        assistant_seen = False
        texts: list[str] = []
        last_assistant = None
        superseded = False
        for next_index in range(index + 1, nodes.count()):
            node = nodes.nth(next_index)
            role = self.message_role(node)
            if role == "user":
                text = self.message_text(node, role).lstrip()
                if text.startswith("CAH_TOOL_RESULT v=1 "):
                    texts = []
                    last_assistant = None
                    continue
                superseded = True
                break
            if role == "assistant":
                assistant_seen = True
                last_assistant = node
                texts.append(self.message_text(node, role))
        page_running = not superseded and self.response_running(page)
        local_complete = (
            not superseded
            and last_assistant is not None
            and self.assistant_turn_complete(last_assistant)
        )
        running = not superseded and page_running and not local_complete
        ended = not superseded and bool(texts) and (local_complete or not page_running)
        # Only the current completed reply can carry a control word. Quoted
        # examples, historical messages and tool results are not instructions.
        word = texts[-1].strip().lower() if ended else ""
        signal = word if word in {"done", "continue", "complete", "handoff", "rework", "wait", "need_user", "blocked", "error"} else None
        return {
            "response_started": assistant_seen or page_running,
            "current_response_started": bool(texts) or page_running,
            "response_ended": ended,
            "superseded": superseded,
            "assistant_texts": texts if not superseded else [],
            "signal": signal,
            "assistant_complete": local_complete,
        }

    def marker_state(self, page: Any, marker: str) -> dict[str, Any]:
        nodes = self.message_nodes(page)
        for index in range(nodes.count() - 1, -1, -1):
            node = nodes.nth(index)
            role = self.message_role(node)
            if role != "user":
                continue
            text = self.message_text(node, role)
            lines = [line.strip() for line in text.splitlines()]
            if marker not in lines:
                continue

            return {
                "marker_visible": True,
                **self._turn_after(page, nodes, index),
                "dispatch_context": parse_dispatch_context(text),
            }
        return {
            "marker_visible": False,
            "response_started": False,
            "response_ended": False,
            "dispatch_context": None,
            "assistant_texts": [],
            "signal": None,
        }

    def dispatch_turn_state(self, page: Any) -> dict[str, Any] | None:
        nodes = self.message_nodes(page)
        dispatch_index = -1
        ctx = None
        for index in range(nodes.count() - 1, -1, -1):
            node = nodes.nth(index)
            role = self.message_role(node)
            if role != "user":
                continue
            ctx = parse_dispatch_context(self.message_text(node, role))
            if ctx:
                dispatch_index = index
                break
        if dispatch_index < 0 or ctx is None:
            return None

        return {
            "dispatch_context": ctx,
            **self._turn_after(page, nodes, dispatch_index),
        }

    def submit_text(
        self,
        page: Any,
        text: str,
        *,
        marker: str | None = None,
        wait_response_start: bool = True,
        timeout_ms: int = 20000,
        replace_existing: bool = False,
        require_new_turn: bool = False,
    ) -> dict[str, Any]:
        self.dismiss_history_rate_limit(page)
        if self.response_running(page):
            raise RuntimeError("ChatGPT response is running; composer is busy")
        before = self.assistant_count(page)
        before_marker = self.marker_occurrences(page, marker) if require_new_turn else 0
        composer = self.composer(page)
        prior = composer.inner_text() if composer.get_attribute("contenteditable") == "true" else composer.input_value()
        if prior and prior.strip() and not replace_existing:
            raise RuntimeError("ChatGPT composer is busy")
        composer.fill(text)
        self.dismiss_history_rate_limit(page)
        # A real Send-button click is materially more reliable on CAH's
        # background-created ChatGPT tabs than synthetic Enter. Live #335
        # reproduction showed Enter could leave no user marker for >30s while
        # the same background flow started within ~1s after clicking Send.
        send = page.locator(", ".join(SEND_SELECTORS)).filter(visible=True)
        try:
            send.first.wait_for(state="visible", timeout=2500)
            send.first.click()
        except Exception:
            # Compatibility fallback for a future/localized UI that temporarily
            # exposes no recognizable Send control.
            composer.press("Enter")
        if not wait_response_start:
            return {"ok": True, "response_started": False, "prior_length": len(prior or "")}

        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            started = (
                self.marker_state(page, marker).get("current_response_started", False)
                if marker else self.assistant_count(page) > before
            )
            if started:
                if require_new_turn and self.marker_occurrences(page, marker) <= before_marker:
                    page.wait_for_timeout(120)
                    continue
                return {"ok": True, "response_started": True, "prior_length": len(prior or "")}
            page.wait_for_timeout(120)
        return {"ok": True, "response_started": False, "prior_length": len(prior or "")}

    def marker_occurrences(self, page: Any, marker: str | None) -> int:
        nodes = self.message_nodes(page)
        return sum(self.message_role(nodes.nth(i)) == "user" and marker in
                   [line.strip() for line in self.message_text(nodes.nth(i), "user").splitlines()]
                   for i in range(nodes.count()))

    def bootstrap_page(self, project_key: str, marker: str) -> Any | None:
        """Find only the original wake, including its not-yet-persisted route."""
        matches = []
        for page in self.context.pages:
            if not project_route_matches(page.url, project_key):
                continue
            if self.marker_state(page, marker).get("marker_visible"):
                matches.append(page)
        if len(matches) > 1:
            raise RuntimeError("BOOTSTRAP_TARGET_AMBIGUOUS")
        return matches[0] if matches else None

    def bootstrap_conversation(
        self,
        project_key: str,
        project_root_url: str,
        text: str,
        marker: str,
        *,
        timeout_ms: int = 25000,
        before_submit: Any | None = None,
        require_existing: bool = False,
    ) -> tuple[Any, str, str]:
        # Recover the already-submitted wake before creating another context.
        # The browser can finish after the host's response-start timeout.
        candidate = self.bootstrap_page(project_key, marker)
        if candidate is not None:
            observed = self.marker_state(candidate, marker)
            location = chatgpt_location(candidate.url)
            if location and location.conversation_id and observed.get("response_started"):
                return candidate, location.conversation_id, candidate.url
            raise RuntimeError("Existing bootstrap wake is awaiting response start; retained for recovery")
        if require_existing:
            raise RuntimeError("BOOTSTRAP_TARGET_MISSING")
        page = self.project_root(project_key, project_root_url, reuse=False)
        try:
            if before_submit is not None:
                text = before_submit(page, text)
            result = self.submit_text(
                page,
                text,
                marker=marker,
                wait_response_start=True,
                timeout_ms=timeout_ms,
                replace_existing=True,
            )
            if not result.get("response_started"):
                raise RuntimeError("ChatGPT bootstrap response did not start")
            page.wait_for_url(
                re.compile(
                    rf"^https://chatgpt\.com/g/{re.escape(project_key)}"
                    rf"(?:-[^/]+)?/c/[A-Za-z0-9-]+/?$"
                ),
                timeout=timeout_ms,
            )
            loc = chatgpt_location(page.url)
            if not loc or not loc.conversation_id:
                raise RuntimeError("ChatGPT conversation route missing conversation id")
            return page, loc.conversation_id, page.url
        except Exception:
            # Keep a submitted wake available to the next observation/Helper.
            # Closing it and resubmitting would duplicate semantic execution.
            if not self.marker_state(page, marker).get("marker_visible"):
                try:
                    page.close()
                except Exception:
                    pass
            raise

    def _project_conversation_id(self, page: Any, anchor: Any, project_key: str) -> str | None:
        href = anchor.get_attribute("href") or ""
        absolute = href if href.startswith("http") else "https://chatgpt.com" + (href if href.startswith("/") else "/" + href)
        path = urlparse(absolute).path

        scoped = re.match(
            rf"^/g/{re.escape(project_key)}(?:-[^/]+)?/c/([A-Za-z0-9-]+)(?:/|$)",
            path,
        )
        if scoped:
            return scoped.group(1)

        generic = re.match(r"^/c/([A-Za-z0-9-]+)(?:/|$)", path)
        if generic:
            in_main = bool(anchor.evaluate("(e) => Boolean(e.closest('main, [role=main]'))"))
            if in_main:
                return generic.group(1)
        return None

    def list_project_conversations(self, page: Any, project_key: str) -> list[dict[str, Any]]:
        if not project_route_matches(page.url, project_key, root_only=True):
            raise RuntimeError("Project conversation listing requires exact Project root")

        root_path = urlparse(page.url).path
        root_match = re.match(
            rf"^(/g/{re.escape(project_key)}(?:-[^/]+)?)/project/?$",
            root_path,
        )
        route_prefix = root_match.group(1) if root_match else f"/g/{project_key}"

        # Current ChatGPT can render project conversations only in the
        # left Project sidebar even when the exact Project root main pane has
        # no conversation cards. Scoped /g/<project_key>/c/<id> links are
        # authoritative project membership wherever they are visible;
        # generic /c/<id> links remain accepted only when _project_conversation_id
        # confirms they belong to the Project main pane.
        rows = page.locator('a[href]:visible')
        seen: dict[str, dict[str, Any]] = {}
        for index in range(rows.count()):
            row = rows.nth(index)
            try:
                conversation_id = self._project_conversation_id(page, row, project_key)
                if not conversation_id:
                    continue
                seen[conversation_id] = {
                    "conversation_id": conversation_id,
                    "url": f"https://chatgpt.com{route_prefix}/c/{conversation_id}",
                    "title": (row.inner_text() or "").strip()[:160] or None,
                }
            except Exception:
                continue
        return list(seen.values())

    def _project_conversation_anchors(
        self,
        page: Any,
        project_key: str,
        conversation_id: str,
    ) -> list[Any]:
        matches = []
        anchors = page.locator('a[href]:visible')
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                if (
                    anchor.is_visible()
                    and self._project_conversation_id(page, anchor, project_key) == conversation_id
                ):
                    matches.append(anchor)
            except Exception:
                continue
        return matches

    def confirm_project_conversation_absent(
        self,
        page: Any,
        project_key: str,
        conversation_id: str,
        *,
        witness_conversation_ids: list[str] | tuple[str, ...],
        timeout_ms: int = 15000,
    ) -> bool:
        """Confirm one externally-deleted chat without trusting an unloaded Project list.

        Absence is accepted only after a refresh and after at least one other
        known managed sibling is visible in the same Project. An empty or
        otherwise unproven list fails closed.
        """
        witnesses = {str(value) for value in witness_conversation_ids if str(value)}
        if not witnesses:
            return False

        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.locator(':is(main, [role="main"]):visible').first.wait_for(
            state="visible",
            timeout=15000,
        )

        deadline = time.monotonic() + max(1000, int(timeout_ms)) / 1000.0
        while time.monotonic() < deadline:
            visible = {
                item["conversation_id"]
                for item in self.list_project_conversations(page, project_key)
            }
            if conversation_id in visible:
                return False
            if visible.intersection(witnesses):
                return True
            page.wait_for_timeout(250)
        return False

    def delete_project_conversation(self, page: Any, project_key: str, conversation_id: str) -> bool:
        # DOMContentLoaded precedes the asynchronous Project chat list. An
        # immediate empty locator is not evidence that the owned chat is absent.
        try:
            page.locator(f'a[href*="/c/{conversation_id}"]:visible').first.wait_for(state="visible", timeout=15000)
        except Exception:
            return False
        anchors = self._project_conversation_anchors(page, project_key, conversation_id)
        if not anchors:
            return False

        row_link = anchors[0]
        row_link.hover()
        card = row_link.locator("xpath=ancestor::*[self::li or @role='group'][.//button[@aria-haspopup='menu']][1]")
        card.locator('button[aria-haspopup="menu"]:visible').first.click()

        delete_item = page.locator(
            '[data-testid="delete-chat-menu-item"]:visible'
        )
        if delete_item.count() == 0:
            delete_item = page.locator('[role="menu"]:visible').last.get_by_role(
                "menuitem",
                name=re.compile(r"(delete|删除|刪除|削除)", re.I),
            )
        delete_item.first.click()

        dialog = page.locator('[role="dialog"]:visible')
        dialog.last.wait_for(state="visible", timeout=5000)
        if dialog.count():
            confirm = dialog.last.get_by_role(
                "button",
                name=re.compile(
                    r"(delete|删除|刪除|削除|confirm|确认|確認|確定|确定)",
                    re.I,
                ),
            )
            if not confirm.count():
                raise RuntimeError("Exact chat deletion confirmation button missing")
            confirm.last.click()

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if conversation_id not in {
                item["conversation_id"]
                for item in self.list_project_conversations(page, project_key)
            }:
                return True
            page.wait_for_timeout(250)
        # The site can commit deletion while retaining a stale Project list.
        # Refresh only after this exact chat's confirmation was submitted;
        # an initially missing target still returns False above.
        page.reload(wait_until="networkidle", timeout=30000)
        page.locator(':is(main, [role="main"]):visible').first.wait_for(state="visible", timeout=15000)
        if conversation_id not in {
            item["conversation_id"]
            for item in self.list_project_conversations(page, project_key)
        }:
            return True
        raise RuntimeError("Project chat remained after delete action and refresh")

    def drain_project_page(self, page: Any, project_key: str) -> dict[str, int]:
        deleted = 0
        for _ in range(500):
            conversations = self.list_project_conversations(page, project_key)
            if not conversations:
                page.wait_for_timeout(500)
                if not self.list_project_conversations(page, project_key):
                    return {"deleted_count": deleted, "remaining_count": 0}
                continue
            if self.delete_project_conversation(page, project_key, conversations[0]["conversation_id"]):
                deleted += 1
        remaining = len(self.list_project_conversations(page, project_key))
        raise RuntimeError(f"Project clear did not reach empty state; remaining={remaining}")

    def drain_project(self, project_key: str, project_root_url: str) -> dict[str, int]:
        page = self.project_root(project_key, project_root_url, reuse=False)
        try:
            return self.drain_project_page(page, project_key)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def read_page(self, page: Any, max_chars: int = 50000) -> dict[str, Any]:
        limit = max(1, min(50000, int(max_chars)))
        text = page.locator("body").inner_text()
        return {
            "url": page.url,
            "title": page.title(),
            "text": text[:limit],
            "truncated": len(text) > limit,
            "dom_only": True,
        }

    def read_conversation(self, page: Any, last_n: int = 8) -> dict[str, Any]:
        nodes = self.message_nodes(page)
        count = nodes.count()
        take = max(1, min(100, int(last_n)))
        start = max(0, count - take)
        budget = 60000
        reversed_messages = []
        truncated = start > 0

        for index in range(count - 1, start - 1, -1):
            if budget <= 0:
                truncated = True
                break
            node = nodes.nth(index)
            role = self.message_role(node)
            raw = self.message_text(node, role)
            allowed = min(12000, budget)
            clipped = raw[:allowed]
            budget -= len(clipped)
            if len(clipped) < len(raw):
                truncated = True
            reversed_messages.append({
                "role": role,
                "text": clipped,
                "truncated": len(clipped) < len(raw),
            })

        messages = list(reversed(reversed_messages))
        return {
            "url": page.url,
            "title": page.title(),
            "dom_message_count": count,
            "returned_count": len(messages),
            "messages": messages,
            "truncated": truncated,
            "dom_only": True,
        }

    def attach_image(self, page: Any, attachment: dict[str, Any]) -> None:
        payload = {
            "name": str(attachment.get("file_name") or "gah-image"),
            "mimeType": str(attachment.get("mime_type") or "image/png"),
            "buffer": base64.b64decode(str(attachment.get("base64") or "")),
        }
        composer = self.composer(page)
        form = composer.locator("xpath=ancestor::form[1]")
        file_input = (
            form.locator('input[type="file"]')
            if form.count()
            else page.locator('input[type="file"]')
        )
        file_input.last.set_input_files(payload)


    def select_thinking_endpoint(self, page: Any, endpoint: str) -> dict[str, Any]:
        target = endpoint.strip().lower()
        if target not in {"min", "max"}:
            raise ValueError("thinking endpoint must be min|max")
        page.keyboard.press("Control+Shift+M")
        page.get_by_role("slider").press("Home" if target == "min" else "End")
        return {
            "ok": True,
            "requested_endpoint": target,
            "selection_method": "keyboard",
        }
