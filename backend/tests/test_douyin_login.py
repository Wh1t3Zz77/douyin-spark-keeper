from app.douyin import login_state


class FakeLocator:
    def __init__(self, visible: bool = False):
        self.visible = visible

    def count(self):
        return 1 if self.visible else 0

    def nth(self, _index):
        return self

    def is_visible(self):
        return self.visible

    def evaluate(self, _script):
        return True


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class FakePage:
    def __init__(self, visible_selector=None, cookies=None):
        self.url = "https://www.douyin.com/chat"
        self.visible_selector = visible_selector
        self.context = FakeContext(cookies or [])

    def get_by_text(self, _word, exact=False):
        return FakeLocator()

    def locator(self, selector):
        return FakeLocator(selector == self.visible_selector)


def test_login_state_rejects_visible_phone_form_even_when_cookie_remains():
    page = FakePage(
        visible_selector='input[placeholder*="手机号"]',
        cookies=[{"name": "sessionid", "value": "stale"}],
    )

    logged, reason = login_state(page)

    assert logged is False
    assert reason == "页面显示手机号或验证码登录表单"


def test_login_state_still_accepts_chat_page_with_session_cookie():
    page = FakePage(cookies=[{"name": "sessionid", "value": "active"}])

    logged, reason = login_state(page)

    assert logged is True
    assert reason == "ok"
