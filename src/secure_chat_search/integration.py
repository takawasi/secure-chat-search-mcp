"""既存MCP内で使う認可アダプター。HTTP認証を再実装しない。"""
from .auth import Auth
from .core import DomainError


class ExistingPoCAuthorizer(Auth):
    """検証済みの本人を受け、既存allow-listと参加情報をその都度確認する。"""
    def __init__(self, membership_provider, enabled_checker):
        self.provider = membership_provider
        self.enabled_checker = enabled_checker

    def authenticate(self, *args, **kwargs):
        raise DomainError("embedded_only", "埋込モードでは既存MCP側で検証した本人を渡してください。", 401)

    def user(self, session, principal):
        try:
            enabled = self.enabled_checker(principal.sub)
        except Exception:
            raise DomainError("allowlist_unavailable", "既存の利用許可を現在確認できません。", 503) from None
        if enabled is not True:
            raise DomainError("user_disabled", "既存の利用許可が失効しています。", 403)
        return super().user(session, principal)
