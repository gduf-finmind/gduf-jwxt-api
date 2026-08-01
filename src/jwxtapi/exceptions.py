class JwxtError(Exception):
    """JWXT 客户端的基础异常。"""


class AuthenticationError(JwxtError):
    """账号、密码或登录状态无效。"""


class CaptchaError(AuthenticationError):
    """验证码缺失或校验失败。"""


class SessionExpiredError(AuthenticationError):
    """会话尚未登录或已经过期。"""


class RequestError(JwxtError):
    """网络请求或远端 HTTP 响应异常。"""


class ValidationError(JwxtError, ValueError):
    """调用参数无效。"""


class ParseError(JwxtError):
    """远端页面结构与预期不一致。"""

