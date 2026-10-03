"""Public Telegram diagnostics must never expose credential-bearing exceptions."""
try:
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import HTTPError, Timeout
except ImportError:
    HTTP_ERRORS = ()
    TIMEOUT_ERRORS = (TimeoutError,)
    CONNECTION_ERRORS = (ConnectionError,)
else:
    HTTP_ERRORS = (HTTPError,)
    TIMEOUT_ERRORS = (Timeout, TimeoutError)
    CONNECTION_ERRORS = (RequestsConnectionError, ConnectionError)


def telegram_error_summary(exc: BaseException) -> str:
    # Exception messages, URLs, response bodies and request fields may contain
    # the bot token. Read only the exception category and a numeric HTTP status.
    if isinstance(exc, HTTP_ERRORS):
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None)
        if type(status) is int and 100 <= status <= 599:
            return f"HTTP {status} 응답을 받았습니다. 텔레그램 설정을 확인하세요."
    if isinstance(exc, TIMEOUT_ERRORS):
        return "응답 시간 초과입니다. 네트워크 연결을 확인하세요."
    if isinstance(exc, CONNECTION_ERRORS):
        return "네트워크 연결에 실패했습니다. 인터넷 연결을 확인하세요."
    return "알림을 보내지 못했습니다. 텔레그램 설정을 확인하세요."
