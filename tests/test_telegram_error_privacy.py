import logging

import pytest
import requests

from backend import ktx_monitor_service, monitor_service
from backend.schemas import MonitorConfig


@pytest.mark.parametrize('module,service_class', [
    (monitor_service, monitor_service.MonitorService),
    (ktx_monitor_service, ktx_monitor_service.KtxMonitorService),
])
@pytest.mark.parametrize('failure,expected', [
    ('http', 'HTTP 401'), ('timeout', '시간 초과'),
    ('connection', '네트워크'), ('unexpected', '설정을 확인'),
])
def test_telegram_failures_never_publish_secrets(module, service_class, failure, expected,
                                              tmp_path, monkeypatch, caplog):
    token = 'DUMMYTOKEN-DO-NOT-PUBLISH'
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    private_message = 'PRIVATE-REQUEST-BODY'
    response = requests.Response()
    response.status_code = 401
    response.url = url
    response.reason = 'Unauthorized'
    response.request = requests.Request('POST', url, data=private_message).prepare()
    response._content = private_message.encode()
    failures = {
        'timeout': requests.exceptions.Timeout(f'{url} {private_message}'),
        'connection': requests.exceptions.ConnectionError(f'{url} {private_message}'),
        'unexpected': RuntimeError(f'{url} {private_message}'),
    }
    calls = []

    def failed_post(target, **kwargs):
        calls.append((target, kwargs))
        if failure == 'http':
            # Real requests.Response creates HTTPError containing the credential URL.
            return response
        raise failures[failure]

    monkeypatch.setattr(module.requests, 'post', failed_post)
    log_path = tmp_path / 'telegram-errors.log'
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    monkeypatch.setattr(module.log, 'handlers', [file_handler])
    caplog.set_level(logging.WARNING, logger=module.log.name)
    config = MonitorConfig(telegram_bot_token=token, telegram_chat_id='DUMMY-CHAT')
    service = service_class()
    try:
        service._send_telegram(config, private_message)
        # Clearing secrets must not leave the token behind in previously captured events.
        config.telegram_bot_token = ''
        config.telegram_chat_id = ''
        service._send_telegram(config, private_message)
        file_handler.flush()
        public_outputs = [event.message for event in service.recent_events()]
        public_outputs += [service.status().last_message, caplog.text,
                           log_path.read_text(encoding='utf-8')]
        assert len(calls) == 1
        assert expected in service.status().last_message
        assert service.recent_events()[0].level == 'warn'
        for output in public_outputs:
            assert token not in output
            assert url not in output
            assert 'api.telegram.org' not in output
            assert private_message not in output
    finally:
        file_handler.close()
