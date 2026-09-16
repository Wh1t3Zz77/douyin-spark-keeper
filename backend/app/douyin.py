"""Conservative Douyin web automation.

No CAPTCHA solving, stealth injection, or safety-check bypass is attempted. Any
login or security challenge stops the run and asks the account owner to act.
Parts of the conversation-switch verification approach are adapted from the
MIT-licensed Xiaowu-0916/douyin-spark project; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Page, sync_playwright


CHAT_URL = "https://www.douyin.com/chat"
SECURITY_WORDS = ("操作频繁", "请稍后再试", "安全验证", "滑动验证", "人机验证", "访问受限")
LOGIN_WORDS = ("扫码登录", "验证码登录", "登录后查看", "登录后即可")
LOGIN_CONTROL_SELECTORS = (
    'input[placeholder*="手机号"]',
    'input[placeholder*="验证码"]',
    'input[aria-label*="国家/地区"]',
    'input[autocomplete="tel"]',
)
POST_SCAN_WORDS = (
    "扫码成功",
    "已扫码",
    "需在手机上进行确认",
    "请在手机上确认",
    "请在抖音 App 中确认",
    "请在抖音App中确认",
)
EXPIRED_QR_WORDS = ("二维码已失效", "二维码已过期")
SMS_VERIFICATION_WORDS = (
    "接收短信验证码",
    "使用短信验证码",
    "手机号验证",
    "手机验证",
    "短信验证",
    "通过手机号验证",
    "使用手机验证",
    "验证手机号",
)
SMS_REQUEST_WORDS = (
    "获取短信验证码",
    "发送短信验证码",
    "获取验证码",
    "发送验证码",
    "免费获取验证码",
    "重新获取",
    "重新发送",
)
CODE_ERROR_WORDS = ("验证码错误", "验证码不正确", "验证码已过期", "请重新获取验证码")
CODE_ERROR_WORDS = CODE_ERROR_WORDS + ("验证码有误", "验证码无效", "验证码失效", "验证码不匹配", "请获取新的验证码")
BROWSER_ARGS = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
BROWSER_ARGS += ["--disk-cache-size=16777216", "--media-cache-size=8388608"]
PROFILE_ROOT = os.getenv("DOUYIN_PROFILE_ROOT", "/app/data/browser-profiles")
LOGGER = logging.getLogger(__name__)


@dataclass
class SendResult:
    contact_name: str
    status: str
    reason: str = ""


def _profile_path(profile_key: str) -> str:
    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", profile_key)[:80]
    if not safe_key:
        raise ValueError("抖音账号浏览器资料标识无效")
    return os.path.join(PROFILE_ROOT, safe_key)


def _persistent_context(playwright, state: dict, profile_key: str, reset: bool = False):
    path = _profile_path(profile_key)
    if reset and os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        path, headless=True, args=BROWSER_ARGS,
        viewport={"width": 1366, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai",
    )
    cookies = state.get("cookies") or []
    if cookies:
        context.add_cookies(cookies)
    origins = {
        item.get("origin"): {entry.get("name"): entry.get("value", "") for entry in item.get("localStorage", []) if entry.get("name")}
        for item in state.get("origins", []) if item.get("origin")
    }
    if origins:
        payload = json.dumps(origins, ensure_ascii=False).replace("</", "<\\/")
        context.add_init_script(f"const values={payload}[location.origin];if(values)for(const [key,value] of Object.entries(values))localStorage.setItem(key,value);")
    return context


def _context_page(context) -> Page:
    return context.pages[0] if context.pages else context.new_page()


def _visible_text(page: Page, words: tuple[str, ...]) -> str | None:
    for word in words:
        try:
            loc = page.get_by_text(word, exact=False)
            for index in range(min(loc.count(), 4)):
                if loc.nth(index).is_visible():
                    return word
        except Exception:
            continue
    return None


def login_state(page: Page) -> tuple[bool, str]:
    if "login" in page.url.lower() or "passport" in page.url.lower():
        return False, "页面跳转到登录入口"
    prompt = _visible_text(page, LOGIN_WORDS)
    if prompt:
        return False, f"页面出现登录提示：{prompt}"
    for selector in LOGIN_CONTROL_SELECTORS:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 8)):
                control = matches.nth(index)
                if control.is_visible() and _is_topmost(control):
                    return False, "页面显示手机号或验证码登录表单"
        except Exception:
            continue
    cookies = page.context.cookies()
    if not any(cookie["name"] in {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"} for cookie in cookies):
        return False, "没有检测到有效登录 Cookie"
    return True, "ok"


def security_challenge(page: Page) -> str | None:
    return _visible_text(page, SECURITY_WORDS)


def _qr_data_url(page: Page) -> str | None:
    # Never screenshot the outer container: before the QR is ready it contains
    # only a Douyin logo, which used to be mistaken for a scannable code.
    selectors = [
        "#animate_qrcode_container img",
        "#animate_qrcode_container canvas",
        '[data-e2e="login-qrcode"] img',
        'div[class*="qrcode"] img',
        'div[class*="qrcode"] canvas',
        'div[class*="QRCode"] img',
        'div[class*="QRCode"] canvas',
        'img[alt*="二维码"]',
        'img[src*="qrcode"]',
    ]
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 6)):
                locator = matches.nth(index)
                if not locator.is_visible():
                    continue
                box = locator.bounding_box()
                if not box or min(box["width"], box["height"]) < 120 or abs(box["width"] - box["height"]) > 45:
                    continue
                png = locator.screenshot(timeout=5000)
                return "data:image/png;base64," + base64.b64encode(png).decode()
        except Exception:
            continue
    return None


def _is_topmost(locator) -> bool:
    """Return whether the control is actually reachable, not covered by a modal."""
    try:
        return bool(
            locator.evaluate(
                """element => {
                    const rect = element.getBoundingClientRect();
                    if (!rect.width || !rect.height) return false;
                    const top = document.elementFromPoint(
                        rect.left + rect.width / 2,
                        rect.top + rect.height / 2
                    );
                    return top === element || element.contains(top);
                }"""
            )
        )
    except Exception:
        return False


def _verification_input(page: Page):
    selectors = [
        'input[placeholder*="验证码"]',
        'input[aria-label*="验证码"]',
        'input[name*="code"]',
        'input[name*="verify"]',
    ]
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 12)):
                locator = matches.nth(index)
                if locator.is_visible() and _is_topmost(locator):
                    return locator
        except Exception:
            continue
    return None


def _phone_login_input(page: Page):
    """Return the ordinary login phone field shown beside the QR, if visible."""
    selectors = (
        'input[placeholder*="手机号"]',
        'input[aria-label*="手机号"]',
        'input[name*="phone"]',
        'input[autocomplete="tel"]',
    )
    for selector in selectors:
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), 8)):
                locator = matches.nth(index)
                if locator.is_visible() and _is_topmost(locator):
                    return locator
        except Exception:
            continue
    return None


def _visible_verification_inputs(page: Page):
    """Return the visible OTP controls, including six-box code widgets."""
    selectors = (
        'input[placeholder*="验证码"]',
        'input[aria-label*="验证码"]',
        'input[name*="code"]',
        'input[name*="verify"]',
        'input[inputmode="numeric"][maxlength="1"]',
        'input[type="tel"][maxlength="1"]',
    )
    result = []
    try:
        # A comma-separated CSS selector returns each DOM node once even when
        # it matches several attributes above.
        matches = page.locator(", ".join(selectors))
        for index in range(min(matches.count(), 12)):
            locator = matches.nth(index)
            if locator.is_visible() and _is_topmost(locator):
                result.append(locator)
    except Exception:
        pass
    return result


def _select_sms_verification(page: Page) -> str | None:
    """Select Douyin's post-scan SMS option; never touches the normal login tab."""
    for label in SMS_VERIFICATION_WORDS:
        try:
            matches = page.get_by_text(label, exact=False)
            for index in range(min(matches.count(), 4)):
                option = matches.nth(index)
                if option.is_visible():
                    clickable = option.locator(
                        "xpath=ancestor-or-self::*[self::button or @role='button' or @tabindex][1]"
                    )
                    if clickable.count() and clickable.first.is_visible() and clickable.first.is_enabled():
                        clickable.first.click()
                    else:
                        option.click()
                    LOGGER.info("selected Douyin post-scan SMS verification: %s", label)
                    page.wait_for_timeout(1800)
                    return label
        except Exception:
            continue
    return None


def _capture_verification_diagnostics(page: Page) -> None:
    """Persist a private screenshot and log control labels without input values."""
    try:
        page.screenshot(path="/app/data/douyin-binding-debug-latest.png", full_page=True)
    except Exception as exc:
        LOGGER.warning("could not save Douyin verification screenshot: %s", type(exc).__name__)

    controls = []
    try:
        matches = page.locator('button, [role="button"], input, label')
        for index in range(min(matches.count(), 80)):
            control = matches.nth(index)
            if not control.is_visible():
                continue
            text = (control.inner_text() or "").strip()
            placeholder = control.get_attribute("placeholder") or ""
            if text or placeholder:
                controls.append(
                    {
                        "tag": control.evaluate("element => element.tagName.toLowerCase()"),
                        "text": text[:80],
                        "placeholder": placeholder[:80],
                        "disabled": control.is_disabled(),
                    }
                )
    except Exception as exc:
        LOGGER.warning("could not inspect Douyin verification controls: %s", type(exc).__name__)
    LOGGER.info("Douyin verification controls: %s", json.dumps(controls, ensure_ascii=False))


def _request_sms_verification_code(page: Page) -> bool:
    """Click Douyin's SMS send action after the QR login was confirmed."""
    for label in SMS_REQUEST_WORDS:
        try:
            buttons = page.get_by_role("button", name=label, exact=False)
            for index in range(min(buttons.count(), 8)):
                button = buttons.nth(index)
                if button.is_visible() and button.is_enabled():
                    button.click()
                    LOGGER.info("clicked Douyin SMS request action: %s", label)
                    page.wait_for_timeout(1800)
                    return True
        except Exception:
            continue

    # Some variants render this action as a span/div inside a clickable row.
    # This fallback is intentionally restricted to explicit SMS-send wording.
    for label in SMS_REQUEST_WORDS:
        try:
            matches = page.get_by_text(label, exact=False)
            for index in range(min(matches.count(), 8)):
                action = matches.nth(index)
                if action.is_visible() and action.is_enabled():
                    clickable = action.locator(
                        "xpath=ancestor-or-self::*[self::button or @role='button' or @tabindex][1]"
                    )
                    if clickable.count() and clickable.first.is_visible() and clickable.first.is_enabled():
                        clickable.first.click()
                    else:
                        action.click()
                    LOGGER.info("clicked Douyin SMS request text action: %s", label)
                    page.wait_for_timeout(1800)
                    return True
        except Exception:
            continue
    return False


def _submit_verification_code(page: Page, code: str) -> None:
    fields = _visible_verification_inputs(page)
    if not fields:
        raise RuntimeError("当前安全验证不是可输入验证码的类型，请重新发起绑定或在抖音 App 内完成验证")

    # Douyin currently uses both a normal input and a six-box OTP widget in
    # different rollouts. Real key events are more reliable than fill() for
    # React-controlled verification inputs.
    single_char_fields = []
    for field in fields:
        try:
            if field.get_attribute("maxlength") == "1":
                single_char_fields.append(field)
        except Exception:
            continue
    if len(single_char_fields) >= len(code):
        for index, digit in enumerate(code):
            single_char_fields[index].fill("")
            single_char_fields[index].type(digit, delay=80)
        field = single_char_fields[min(len(code), len(single_char_fields)) - 1]
    else:
        field = fields[0]
        field.fill("")
        field.type(code, delay=100)

    try:
        field.press("Tab")
    except Exception:
        pass

    labels = ("确认登录", "验证并登录", "登录", "确定", "验证", "提交", "确认", "下一步", "继续", "完成")
    for label in labels:
        try:
            buttons = page.get_by_role("button", name=label, exact=False)
            for index in range(min(buttons.count(), 8)):
                button = buttons.nth(index)
                if button.is_visible() and button.is_enabled():
                    button.click()
                    LOGGER.info("clicked Douyin verification action: %s", label)
                    page.wait_for_timeout(2500)
                    return
        except Exception:
            continue

    # Some Douyin variants render the confirmation as a div instead of a
    # semantic button. Only click elements whose visible text is an expected
    # verification action.
    for selector in ('[role="button"]', 'button', '[tabindex="0"]'):
        try:
            candidates = page.locator(selector)
            for index in range(min(candidates.count(), 30)):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                text = (candidate.inner_text() or "").strip()
                if text and any(label in text for label in labels):
                    candidate.click()
                    LOGGER.info("clicked Douyin verification fallback action")
                    page.wait_for_timeout(2500)
                    return
        except Exception:
            continue

    # The security modal currently renders "验证" as a non-semantic element.
    # Click only exact, unobscured action text so the ordinary login form behind
    # the modal can never receive the submission.
    for label in labels:
        try:
            candidates = page.get_by_text(label, exact=True)
            for index in range(min(candidates.count(), 12)):
                candidate = candidates.nth(index)
                if not candidate.is_visible() or not _is_topmost(candidate):
                    continue
                clickable = candidate.locator(
                    "xpath=ancestor-or-self::*[self::button or @role='button' or @tabindex][1]"
                )
                if clickable.count() and clickable.first.is_visible() and clickable.first.is_enabled():
                    clickable.first.click()
                else:
                    candidate.click()
                LOGGER.info("clicked Douyin verification text action: %s", label)
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue
    field.press("Enter")
    LOGGER.info("submitted Douyin verification with Enter fallback")
    page.wait_for_timeout(2500)


def bind_account(
    on_qr: Callable[[str], None],
    request_verification_code: Callable[[str], str | None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    timeout_seconds: int = 290,
    profile_key: str | None = None,
) -> str:
    """Wait for a user-confirmed QR login and return serialized storage state."""
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, {}, profile_key, reset=True) if profile_key else None
        browser = None if context else playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = context or browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai")
            page = _context_page(context)
            page.goto(CHAT_URL, timeout=90000, wait_until="domcontentloaded")
            deadline = time.time() + timeout_seconds
            sent_qr = False
            sms_selected = False
            sms_requested = False
            verification_diagnostics_captured = False
            code_submitted_at: float | None = None
            verification_retry_prompted = False
            qr_missing_since: float | None = None
            observed_page_count = len(context.pages)
            while time.time() < deadline:
                if should_cancel and should_cancel():
                    raise TimeoutError("扫码会话已取消，请重新生成二维码")

                # Douyin can open the post-confirmation security check in a new
                # page/popup while leaving the original QR page on "confirm on
                # mobile". Follow that page instead of waiting on the stale one.
                pages = context.pages
                if len(pages) != observed_page_count:
                    LOGGER.info("Douyin browser page count changed: %s -> %s", observed_page_count, len(pages))
                    observed_page_count = len(pages)
                if len(pages) > 1:
                    for candidate in reversed(pages):
                        if candidate == page:
                            continue
                        try:
                            if "douyin.com" in candidate.url and (
                                _verification_input(candidate) is not None
                                or _visible_text(candidate, SMS_VERIFICATION_WORDS + SMS_REQUEST_WORDS) is not None
                            ):
                                page = candidate
                                qr_missing_since = None
                                LOGGER.info("switched to Douyin post-confirmation page")
                                break
                        except Exception:
                            continue
                if not sent_qr:
                    qr = _qr_data_url(page)
                    if qr:
                        on_qr(qr)
                        sent_qr = True
                logged, _ = login_state(page)
                if logged:
                    page.wait_for_timeout(3000)
                    return json.dumps(context.storage_state(), ensure_ascii=False)

                # The normal login dialog always contains a phone-code form next
                # to the QR code. It is not a post-scan challenge and must not be
                # surfaced while the QR remains visible. Only relay a code after
                # the QR has disappeared for several seconds after being shown.
                if sent_qr and _visible_text(page, EXPIRED_QR_WORDS):
                    raise TimeoutError("二维码已失效，请重新生成二维码")

                # Keep v12's proven behavior: this wording is specific to the
                # post-scan security choice and does not occur in the ordinary
                # phone-login form beside the QR. Selecting it may itself send
                # the SMS, so look for it continuously after the QR is issued.
                if sent_qr and not sms_selected:
                    selected_sms_action = _select_sms_verification(page)
                    if selected_sms_action:
                        sms_selected = True
                        # These two choices are the actual send action in the
                        # observed Douyin flow, not merely a mode selector.
                        if selected_sms_action in ("接收短信验证码", "使用短信验证码"):
                            sms_requested = True
                qr_still_visible = _qr_data_url(page) is not None if sent_qr else False
                if qr_still_visible:
                    qr_missing_since = None
                elif sent_qr and qr_missing_since is None:
                    qr_missing_since = time.time()
                waiting_mobile_confirmation = _visible_text(page, POST_SCAN_WORDS) is not None
                # "Please confirm on mobile" is still part of the QR flow and
                # the ordinary login dialog beside it contains another phone
                # code input. Only the QR actually disappearing proves that the
                # browser may have moved on, and the mobile-confirm wording must
                # also be gone before touching any SMS controls.
                candidate_verification_field = _verification_input(page)
                ordinary_phone_login = _phone_login_input(page) is not None
                post_scan = sms_selected or (
                    not ordinary_phone_login
                    and (
                        qr_missing_since is not None
                        and time.time() - qr_missing_since >= 3
                        and not waiting_mobile_confirmation
                    )
                )
                verification_field = candidate_verification_field if post_scan else None
                if verification_field is not None and not verification_diagnostics_captured:
                    _capture_verification_diagnostics(page)
                    verification_diagnostics_captured = True
                code_rejected = code_submitted_at is not None and time.time() - code_submitted_at >= 3 and _visible_text(page, CODE_ERROR_WORDS) is not None
                submission_stalled = code_submitted_at is not None and time.time() - code_submitted_at >= 12 and verification_field is not None and not verification_retry_prompted
                if post_scan and verification_field is not None and request_verification_code and (code_submitted_at is None or code_rejected or submission_stalled):
                    retrying = code_rejected or submission_stalled
                    if retrying:
                        verification_retry_prompted = True
                    if code_submitted_at is None and not sms_requested:
                        sms_requested = _request_sms_verification_code(page)
                        if not sms_requested:
                            # Some Douyin variants send automatically when the
                            # SMS option opens and expose no separate action.
                            LOGGER.info("no enabled Douyin SMS request action; assuming auto-send variant")
                            sms_requested = True
                    prompt = "手机号验证码（上次提交未被抖音接受，请重新输入）" if retrying else "手机号验证码"
                    code = request_verification_code(prompt)
                    if not code:
                        raise TimeoutError("等待手动验证码超时，请重新发起绑定")
                    _submit_verification_code(page, code)
                    code_submitted_at = time.time()
                    verification_retry_prompted = False
                    continue
                challenge = security_challenge(page)
                if challenge:
                    raise RuntimeError(f"需要手动完成安全验证：{challenge}")
                page.wait_for_timeout(1500)
            raise TimeoutError("扫码会话已超时，请重新发起绑定")
        finally:
            context.close()
            if browser:
                browser.close()


def list_contacts(storage_state_json: str, profile_key: str | None = None) -> tuple[list[dict], str | None, str]:
    """Read visible conversations and conservative metadata; never sends a message.

    Conversation types are only labelled when the rendered DOM exposes useful
    evidence. Ambiguous entries remain ``unknown`` instead of being guessed.
    """
    state = json.loads(storage_state_json)
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, state, profile_key) if profile_key else None
        browser = None if context else playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = context or browser.new_context(
                storage_state=state,
                viewport={"width": 1366, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = _context_page(context)
            page.goto(CHAT_URL, timeout=90000, wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            logged, reason = login_state(page)
            if not logged:
                return [], "logged_out:" + reason, json.dumps(context.storage_state(), ensure_ascii=False)
            selector = ".conversationConversationItemtitle"
            for attempt in range(3):
                try:
                    page.wait_for_selector(selector, timeout=30000)
                    break
                except Exception:
                    if attempt == 2:
                        logged, reason = login_state(page)
                        if not logged:
                            return [], "logged_out:" + reason, json.dumps(context.storage_state(), ensure_ascii=False)
                        challenge = security_challenge(page)
                        if challenge:
                            return [], "security_challenge:" + challenge, json.dumps(context.storage_state(), ensure_ascii=False)
                        return [], "抖音聊天页暂时没有加载出会话列表，请稍后重试；登录状态未判为失效", json.dumps(context.storage_state(), ensure_ascii=False)
                    try:
                        page.reload(timeout=90000, wait_until="domcontentloaded")
                        page.wait_for_timeout(8000)
                    except Exception:
                        pass
            contacts: list[dict] = []
            seen: set[str] = set()
            stable = 0
            for _ in range(16):
                values = page.eval_on_selector_all(
                    selector,
                    """titles => titles.map(title => {
                        // The title class itself contains "conversationConversationItem".
                        // Walk upward so classification sees the complete conversation row.
                        const name = (title.textContent || '').trim();
                        const ancestors = [];
                        let cursor = title.parentElement;
                        for (let depth = 0; cursor && depth < 7; depth += 1, cursor = cursor.parentElement) {
                          ancestors.push(cursor);
                        }
                        const item = ancestors.find(node => {
                          const cls = String(node.className || '');
                          return /conversationConversationItem/i.test(cls)
                            && (node.innerText || '').trim() !== name;
                        }) || ancestors.find(node => node.querySelectorAll?.('img').length) || title.parentElement;
                        const text = (item?.innerText || '').trim();
                        const metadata = [
                          item?.className || '', item?.getAttribute?.('aria-label') || '',
                          item?.getAttribute?.('title') || '', JSON.stringify(item?.dataset || {})
                        ].join(' ');
                        let externalId = '';
                        if (item?.dataset) {
                          const key = Object.keys(item.dataset).find(k => /(conversation|sec.?uid|user.?id|chat.?id)/i.test(k));
                          if (key) externalId = String(item.dataset[key] || '');
                        }
                        if (!externalId) {
                          const href = item?.querySelector?.('a[href]')?.getAttribute('href') || '';
                          const match = href.match(/[?&](?:conversation_id|sec_uid|user_id|chat_id)=([^&]+)/i);
                          if (match) externalId = decodeURIComponent(match[1]);
                        }
                        if (!externalId) {
                          const wanted = /^(?:_?toParticipantSecUserId|participantSecUserId|secUid|sec_user_id|conversationId|conversation_id)$/i;
                          const seenObjects = new Set();
                          const walkReactProps = (value, depth) => {
                            if (!value || depth > 5 || typeof value !== 'object' || seenObjects.has(value)) return '';
                            seenObjects.add(value);
                            for (const [key, child] of Object.entries(value)) {
                              if (wanted.test(key) && (typeof child === 'string' || typeof child === 'number') && String(child)) return String(child);
                              const nested = walkReactProps(child, depth + 1);
                              if (nested) return nested;
                            }
                            return '';
                          };
                          for (const node of [title, ...ancestors]) {
                            const reactKey = Object.keys(node || {}).find(key => key.startsWith('__reactProps$'));
                            externalId = reactKey ? walkReactProps(node[reactKey], 0) : '';
                            if (externalId) break;
                          }
                        }
                        const avatar = item?.querySelector?.('[class*="avatar"] img, img[class*="avatar"]');
                        const avatarUrl = avatar?.getAttribute('src') || '';
                        const avatarCount = item?.querySelectorAll?.('[class*="avatar"] img, img[class*="avatar"]')?.length || 0;
                        const streakText = item?.querySelector?.('.commonStreaknormalText')?.textContent || '';
                        const streakMatch = streakText.match(/\\d+/);
                        const combined = `${name} ${text} ${metadata}`;
                        const groupEvidence = /群聊|群消息|群成员|群公告|粉丝群|交流群|同学群|家人群|工作群|群主|管理员|\\d+\\s*人|chatroom|group|multi.?chat/i.test(combined) || avatarCount > 1;
                        const systemEvidence = /抖音小助手|系统消息|钱包助手|订单助手|直播通知/.test(name);
                        const friendEvidence = Boolean(streakText) || /好友|朋友|私聊|单聊|private.?chat|single.?chat/i.test(metadata);
                        const conversationType = groupEvidence ? 'group' : (systemEvidence ? 'unknown' : (friendEvidence ? 'friend' : 'unknown'));
                        return {
                          display_name: name,
                          conversation_type: conversationType,
                          external_id: externalId || null,
                          source_key: externalId || `name:${name}`,
                          avatar_url: avatarUrl || null,
                          streak_days: streakMatch ? Number(streakMatch[0]) : null,
                        };
                    })""",
                )
                before = len(contacts)
                for value in values:
                    name = str(value.get("display_name") or "").strip()
                    source_key = str(value.get("source_key") or f"name:{name}")
                    if name and source_key not in seen:
                        seen.add(source_key)
                        value["display_name"] = name
                        contacts.append(value)
                stable = stable + 1 if len(contacts) == before else 0
                if stable >= 3:
                    break
                page.mouse.move(190, 500)
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(900)
            return contacts[:200], None if contacts else "会话列表为空", json.dumps(context.storage_state(), ensure_ascii=False)
        finally:
            context.close()
            if browser:
                browser.close()


def _verify_conversation(page: Page, name: str) -> bool:
    for exact in (True, False):
        try:
            loc = page.get_by_text(name, exact=exact)
            for index in range(min(loc.count(), 8)):
                box = loc.nth(index).bounding_box()
                if box and box.get("x", 0) > 300 and box.get("y", 0) < 140:
                    return True
        except Exception:
            continue
    return False


def _locator_external_id(locator) -> str:
    try:
        return str(locator.evaluate(
            """element => {
                const wanted = /^(?:_?toParticipantSecUserId|participantSecUserId|secUid|sec_user_id|conversationId|conversation_id)$/i;
                const seen = new Set();
                const walk = (value, depth) => {
                  if (!value || depth > 5 || typeof value !== 'object' || seen.has(value)) return '';
                  seen.add(value);
                  for (const [key, child] of Object.entries(value)) {
                    if (wanted.test(key) && (typeof child === 'string' || typeof child === 'number') && String(child)) return String(child);
                    const nested = walk(child, depth + 1);
                    if (nested) return nested;
                  }
                  return '';
                };
                let node = element;
                for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
                  const reactKey = Object.keys(node).find(key => key.startsWith('__reactProps$'));
                  const found = reactKey ? walk(node[reactKey], 0) : '';
                  if (found) return found;
                }
                return '';
            }"""
        ) or "")
    except Exception:
        return ""


def _find_conversation_title(page: Page, name: str, external_id: str | None = None):
    def current_match():
        try:
            titles = page.locator(".conversationConversationItemtitle")
            for index in range(min(titles.count(), 80)):
                title = titles.nth(index)
                if not title.is_visible():
                    continue
                rendered_name = (title.inner_text() or "").strip()
                if rendered_name == name or (external_id and _locator_external_id(title) == external_id):
                    return title, rendered_name
        except Exception:
            pass
        return None

    match = current_match()
    if match:
        return match
    try:
        page.mouse.move(190, 500)
        page.mouse.wheel(0, -12000)
        page.wait_for_timeout(700)
        for _ in range(24):
            match = current_match()
            if match:
                return match
            page.mouse.wheel(0, 650)
            page.wait_for_timeout(450)
    except Exception:
        pass
    return None


def _click_conversation_title(page: Page, title, expected_name: str) -> bool:
    try:
        row = title.locator('xpath=ancestor::*[@data-e2e="conversation-item"][1]')
        (row.first if row.count() else title).click(force=True, timeout=8000)
        page.wait_for_timeout(random.randint(1500, 2600))
        return _verify_conversation(page, expected_name)
    except Exception:
        return False


def _open_contact(page: Page, name: str, external_id: str | None = None) -> bool:
    match = _find_conversation_title(page, name, external_id)
    if match and _click_conversation_title(page, match[0], match[1] or name):
        return True

    for _ in range(2):
        try:
            search = page.get_by_placeholder("搜索", exact=False).first
            if not search.count():
                return False
            search.fill("")
            search.click()
            search.type(name, delay=80)
            page.wait_for_timeout(3000)
            matches = page.get_by_text(name, exact=True)
            for index in range(min(matches.count(), 12)):
                target = matches.nth(index)
                box = target.bounding_box() if target.is_visible() else None
                if not box or box.get("x", 999) > 360:
                    continue
                row = target.locator("xpath=ancestor-or-self::*[contains(@class,'SearchPanelitem')][1]")
                (row.first if row.count() else target).click(force=True, timeout=8000)
                page.wait_for_timeout(2200)
                message_button = page.get_by_text("发消息", exact=False).first
                if message_button.count() and message_button.is_visible():
                    message_button.click(force=True)
                    page.wait_for_timeout(1800)
                if _verify_conversation(page, name):
                    return True
        except Exception:
            pass
    return False


def _send_one(page: Page, name: str, message: str, external_id: str | None = None) -> SendResult:
    if not _open_contact(page, name, external_id):
        return SendResult(name, "failed", "找不到好友或无法确认会话标题")
    challenge = security_challenge(page)
    if challenge:
        return SendResult(name, "security_challenge", challenge)
    box = page.locator('div[contenteditable="true"]').first
    try:
        if not box.count() or not box.is_visible():
            return SendResult(name, "failed", "找不到聊天输入框")
        box.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(message, delay=90)
        page.wait_for_timeout(600)
        current = box.inner_text() or ""
        if message not in current:
            return SendResult(name, "failed", "消息没有进入输入框")
        page.keyboard.press("Enter")
        deadline = time.time() + 8
        while time.time() < deadline:
            page.wait_for_timeout(800)
            if message not in (box.inner_text() or ""):
                return SendResult(name, "submitted")
        return SendResult(name, "uncertain", "发送后状态无法确认；为避免重复，不自动重试")
    except Exception as exc:
        return SendResult(name, "failed", f"发送异常：{str(exc)[:180]}")


def send_batch(
    storage_state_json: str,
    recipients: list[tuple[str, str] | tuple[str, str, str | None]],
    on_before_send: Callable[[str], None] | None = None,
    on_result: Callable[[SendResult], None] | None = None,
    profile_key: str | None = None,
) -> tuple[list[SendResult], str | None, str]:
    state = json.loads(storage_state_json)
    results: list[SendResult] = []
    with sync_playwright() as playwright:
        context = _persistent_context(playwright, state, profile_key) if profile_key else None
        browser = None if context else playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = context or browser.new_context(
                storage_state=state,
                viewport={"width": 1366, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = _context_page(context)
            page.goto(CHAT_URL, timeout=90000, wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            logged, reason = login_state(page)
            if not logged:
                return results, "logged_out:" + reason, json.dumps(context.storage_state(), ensure_ascii=False)
            try:
                page.wait_for_selector(".conversationConversationItemtitle", timeout=30000)
            except Exception:
                logged, reason = login_state(page)
                if not logged:
                    return results, "logged_out:" + reason, json.dumps(context.storage_state(), ensure_ascii=False)
                challenge = security_challenge(page)
                if challenge:
                    return results, "security_challenge:" + challenge, json.dumps(context.storage_state(), ensure_ascii=False)
                return results, "transient:抖音聊天页暂时没有加载出会话列表，登录状态未判为失效", json.dumps(context.storage_state(), ensure_ascii=False)
            for recipient in recipients:
                name, message = recipient[:2]
                external_id = recipient[2] if len(recipient) > 2 else None
                if on_before_send:
                    on_before_send(name)
                result = _send_one(page, name, message, external_id)
                if on_result:
                    on_result(result)
                if result.status == "failed" and result.reason == "找不到好友或无法确认会话标题":
                    still_logged, current_reason = login_state(page)
                    if not still_logged:
                        return results, "logged_out:" + current_reason, json.dumps(context.storage_state(), ensure_ascii=False)
                results.append(result)
                if result.status == "security_challenge":
                    return results, "security_challenge:" + result.reason, json.dumps(context.storage_state(), ensure_ascii=False)
                page.wait_for_timeout(random.randint(8000, 16000))
            return results, None, json.dumps(context.storage_state(), ensure_ascii=False)
        finally:
            context.close()
            if browser:
                browser.close()
