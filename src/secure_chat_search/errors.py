"""利用者に返してよいエラーだけを定義する。外部応答の本文は公開しない。"""
class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

class UpstreamError(AppError):
    def __init__(self, code="upstream_unavailable"):
        super().__init__(503, code, "外部サービスを確認できません。権限やデータを推測せず処理を停止しました。")
