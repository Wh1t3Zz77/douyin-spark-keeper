"""Read identity from the row's own React conversation model, including getters."""
IDENTITY_READER = r"""element => {
  const text = value => value == null ? '' : String(value).trim();
  const safe = fn => { try { return fn(); } catch { return null; } };
  const row = element.closest('[data-e2e="conversation-item"]') || element;
  const models = [];
  for (const node of [element, row]) {
    for (const key of Object.keys(node)) {
      if (key.startsWith('__reactProps$')) {
        const props = node[key];
        if (props?.conversation) models.push(props.conversation);
      } else if (key.startsWith('__reactFiber$')) {
        let fiber = node[key];
        for (let depth = 0; fiber && depth < 20; depth++, fiber = fiber.return) {
          if (fiber.memoizedProps?.conversation) {
            models.push(fiber.memoizedProps.conversation); break;
          }
          // Never inherit another conversation from the whole list.
          if (fiber.stateNode?.classList?.contains('conversationConversationListwrapper')) break;
        }
      }
    }
  }
  const conv = models.find(c => safe(() => c.id || c.conversationId || c.conversation_id)) || models[0];
  const dataset = row.dataset || {};
  const uid = text(safe(() => conv?.toParticipantUserId ?? conv?._toParticipantUserId ?? conv?.participantUserId) || dataset.userId || dataset.uid);
  const sec_uid = text(safe(() => conv?.toParticipantSecUserId ?? conv?._toParticipantSecUserId ?? conv?.participantSecUserId) || dataset.secUid);
  const conversation_id = text(safe(() => conv?.id || conv?.conversationId || conv?.conversation_id) || dataset.conversationId || dataset.chatId);
  const ext = safe(() => conv?.ext) || {};
  const user = safe(() => conv?.user || conv?.participant || conv?.userInfo) || {};
  const douyin_id = text(user.unique_id || user.uniqueId || ext['a:unique_id'] || user.short_id || user.shortId);
  const nickname = text(user.nickname || ext['a:nickname']);
  const remark_name = text(user.remark_name || user.remarkName || ext['a:remark_name']);
  const external_id = conversation_id ? `conv:${conversation_id}` : sec_uid ? `sec:${sec_uid}` : uid ? `uid:${uid}` : null;
  return {uid: uid || null, sec_uid: sec_uid || null, conversation_id: conversation_id || null,
          douyin_id: douyin_id || null, nickname: nickname || null, remark_name: remark_name || null,
          external_id, source_key: external_id};
}"""


def identity_matches(metadata, expected):
    if not expected:
        return False
    for prefix, key in (("conv:", "conversation_id"), ("sec:", "sec_uid"), ("uid:", "uid")):
        if expected.startswith(prefix):
            return str(metadata.get(key) or "") == expected[len(prefix):]
    # Compatibility with previously stored, untyped stable values only.
    return expected in {str(metadata.get(k) or "") for k in ("conversation_id", "uid", "sec_uid")}


class ProfileCollector:
    """Observe normal profile JSON responses for searchable mutable handles."""
    def __init__(self, page):
        self.profiles = {}
        page.on("response", self.observe)

    def observe(self, response):
        from urllib.parse import urlparse
        url = urlparse(response.url)
        if url.hostname not in {"www.douyin.com", "www.iesdouyin.com"} or not any(p in url.path for p in ("/user/", "/friend/", "/following/")):
            return
        try:
            if int(response.headers.get("content-length", "0")) > 2_000_000:
                return
            body = response.body()
            if len(body) > 2_000_000:
                return
            import json
            stack = [json.loads(body)]
            for _ in range(5000):
                if not stack:
                    break
                value = stack.pop()
                if isinstance(value, list):
                    stack.extend(value)
                elif isinstance(value, dict):
                    if value.get("uid") or value.get("sec_uid"):
                        profile = {k: str(value.get(k) or "") for k in ("uid", "sec_uid", "nickname", "remark_name") if k in value}
                        if "unique_id" in value or "short_id" in value:
                            profile["douyin_id"] = str(value.get("unique_id") or value.get("short_id") or "")
                        for key in (profile.get("uid"), profile.get("sec_uid")):
                            if key:
                                self.profiles[key] = profile
                    stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
        except Exception:
            pass

    def enrich(self, contacts):
        for contact in contacts:
            profile = self.profiles.get(contact.get("uid")) or self.profiles.get(contact.get("sec_uid"))
            if profile:
                for key in ("douyin_id", "nickname", "remark_name"):
                    if key in profile:
                        contact[key] = profile[key] or None
                contact["_profile_fields"] = [k for k in ("douyin_id", "nickname", "remark_name") if k in profile]
