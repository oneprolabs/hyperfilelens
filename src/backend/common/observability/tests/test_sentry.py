"""Tests for privacy-safe optional Sentry initialization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from common.observability.context import org_key_var, request_id_var, user_id_var
from common.observability.sentry import init_sentry


@pytest.fixture(autouse=True)
def _clear_sentry_env(monkeypatch):
    for name in (
        "SENTRY_ENABLED",
        "SENTRY_BACKEND_DSN",
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_RELEASE",
        "SENTRY_TRACES_SAMPLE_RATE",
        "SENTRY_COMPONENT",
        "SENTRY_SERVICE",
        "SECRET_KEY",
        "APP_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)


def test_init_sentry_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/1")
    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
    mock_init.assert_not_called()


@pytest.mark.parametrize(
    "dsn",
    ["", "not-a-dsn", "ftp://public@sentry.example.com/1", "https://sentry.example.com/1"],
)
def test_init_sentry_skips_invalid_dsn(monkeypatch, dsn):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", dsn)
    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
    mock_init.assert_not_called()


def test_init_sentry_uses_fixed_privacy_policy(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/42")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "hfl-preprod")
    monkeypatch.setenv("SENTRY_RELEASE", "hyperfilelens-backend@0.1.8")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    fake_django = MagicMock(name="DjangoIntegration")
    fake_celery = MagicMock(name="CeleryIntegration")
    fake_redis = MagicMock(name="RedisIntegration")
    with (
        patch("sentry_sdk.init") as mock_init,
        patch("sentry_sdk.integrations.django.DjangoIntegration", return_value=fake_django),
        patch("sentry_sdk.integrations.celery.CeleryIntegration", return_value=fake_celery),
        patch("sentry_sdk.integrations.redis.RedisIntegration", return_value=fake_redis),
    ):
        init_sentry()

    kwargs = mock_init.call_args.kwargs
    assert kwargs["environment"] == "hfl-preprod"
    assert kwargs["release"] == "hyperfilelens-backend@0.1.8"
    assert kwargs["traces_sample_rate"] == 0.25
    assert kwargs["profiles_sample_rate"] == 0.0
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["shutdown_timeout"] == 1
    assert kwargs["transport_queue_size"] == 32
    assert kwargs["send_client_reports"] is False
    assert kwargs["before_send_transaction"] is kwargs["before_send"]
    assert kwargs["integrations"] == [fake_django, fake_celery, fake_redis]


def test_before_send_scrubs_pii_and_hashes_context(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/42")
    monkeypatch.setenv("SECRET_KEY", "deployment-secret")
    monkeypatch.setenv("SENTRY_COMPONENT", "hfl-backend")
    monkeypatch.setenv("SENTRY_SERVICE", "api")
    request_id_var.set("trace-123")
    org_key_var.set("raw-organization")
    user_id_var.set("raw-user")

    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
    before_send = mock_init.call_args.kwargs["before_send"]
    event = before_send(
        {
            "user": {"email": "person@example.com"},
            "request": {
                "url": "https://app.example.com/api/source/private?token=secret",
                "cookies": {"session": "secret"},
                "data": {"password": "secret"},
                "query_string": "token=secret",
                "headers": {"Authorization": "Bearer secret", "User-Agent": "pytest"},
                "env": {"REMOTE_ADDR": "192.0.2.15", "customer": "private"},
            },
            "transaction": "/api/source/private-id",
            "transaction_info": {"source": "url", "private": "customer"},
            "spans": [
                {
                    "trace_id": "trace-span-1",
                    "span_id": "span-1",
                    "op": "db.sql.query",
                    "description": "SELECT * FROM private_files WHERE owner='customer'",
                    "data": {"db.statement": "/customer/private"},
                    "tags": {"customer": "raw-organization"},
                }
            ],
            "message": "customer prompt and response",
            "logentry": {"message": "customer filename"},
            "breadcrumbs": [{"message": "customer path"}],
            "extra": {"args": ["/customer/private"], "safe_count": 2},
            "contexts": {
                "customer": {"content": "private"},
                "runtime": {"name": "CPython", "private": "customer"},
                "trace": {"trace_id": "abc", "data": {"path": "/customer/private"}},
            },
            "exception": {
                "values": [
                    {
                        "value": "open /customer/private failed",
                        "mechanism": {"type": "generic", "data": {"path": "/customer/private"}},
                        "stacktrace": {"frames": [{"vars": {"token": "secret"}}]},
                    }
                ]
            },
        },
        {},
    )

    assert "user" not in event
    assert event["request"] == {
        "url": "https://app.example.com",
        "headers": {},
    }
    for key in ("breadcrumbs", "extra", "fingerprint", "logentry", "message"):
        assert key not in event
    assert event["contexts"] == {
        "runtime": {"name": "CPython"},
        "trace": {"trace_id": "abc"},
    }
    exception = event["exception"]["values"][0]
    assert exception["value"] == "[Filtered]"
    assert "data" not in exception["mechanism"]
    assert "vars" not in exception["stacktrace"]["frames"][0]
    assert event["tags"]["trace_id"] == "trace-123"
    assert event["tags"]["org_hash"] != "raw-organization"
    assert event["tags"]["user_hash"] != "raw-user"
    assert event["tags"]["component"] == "hfl-backend"
    assert event["tags"]["service"] == "api"
    assert "raw-organization" not in str(event)
    assert "raw-user" not in str(event)
    assert "/customer/private" not in str(event)
    assert "transaction" not in event
    assert "transaction_info" not in event
    assert event["spans"] == [
        {
            "trace_id": "trace-span-1",
            "span_id": "span-1",
            "op": "db.sql.query",
        }
    ]
    assert "192.0.2.15" not in str(event)


def test_before_send_retains_only_safe_route_transaction(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/42")
    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
    before_send = mock_init.call_args.kwargs["before_send"]

    event = before_send(
        {
            "transaction": "/api/sources/<uuid:source_id>",
            "transaction_info": {"source": "route", "private": "customer"},
        },
        {},
    )

    assert event["transaction"] == "/api/sources/<uuid:source_id>"
    assert event["transaction_info"] == {"source": "route"}


def test_before_send_removes_url_credentials(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/42")
    with patch("sentry_sdk.init") as mock_init:
        init_sentry()
    before_send = mock_init.call_args.kwargs["before_send"]
    event = before_send(
        {"request": {"url": "https://user:password@example.com:8443/private?token=secret"}},
        {},
    )
    assert event["request"]["url"] == "https://example.com:8443"


def test_init_failure_is_non_blocking(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://public@sentry.example.com/42")
    with patch("sentry_sdk.init", side_effect=ValueError("bad config")):
        init_sentry()


def _make_fail_open_transport():
    from sentry_sdk.consts import DEFAULT_OPTIONS
    from common.observability.sentry import _fail_open_transport

    options = dict(DEFAULT_OPTIONS)
    options.update(dsn="https://public@sentry.example.com/42", transport_queue_size=2, send_client_reports=False)
    return _fail_open_transport()(options)


def _test_envelope():
    from sentry_sdk.envelope import Envelope

    envelope = Envelope()
    envelope.add_event({"event_id": "0" * 32, "message": "test event"})
    return envelope


def test_transport_has_short_timeout_and_no_network_retries():
    transport = _make_fail_open_transport()
    try:
        options = transport._get_pool_options()
        assert options["timeout"].total == 2
        assert options["retries"] == 0
    finally:
        transport.kill()


@pytest.mark.parametrize("failure", ["dns", "timeout", "http_503"])
def test_sentry_network_failure_stays_off_api_request_thread(failure):
    """Even a blocked Sentry sender cannot delay the business response."""
    import socket
    import threading
    from django.http import JsonResponse
    from django.test import RequestFactory
    from urllib3.response import HTTPResponse

    transport = _make_fail_open_transport()
    started, release, finished = (threading.Event() for _ in range(3))
    api_thread = threading.get_ident()
    sender_threads = []

    def unavailable(*args, **kwargs):
        sender_threads.append(threading.get_ident())
        started.set()
        assert release.wait(5), "test failed to release the network sender"
        try:
            if failure == "dns":
                raise socket.gaierror("Sentry DNS unavailable")
            if failure == "timeout":
                raise TimeoutError("Sentry timeout")
            return HTTPResponse(status=503, body=b"unavailable")
        finally:
            finished.set()

    # A minimal API handler performing best-effort reporting through the
    # production transport; no real Sentry traffic or external DNS involved.
    def api_view(request):
        transport.capture_envelope(_test_envelope())
        return JsonResponse({"status": "ok"})

    try:
        with patch.object(transport._pool, "request", side_effect=unavailable) as send:
            response = api_view(RequestFactory().get("/health"))
            assert response.status_code == 200
            assert started.wait(2)
            assert not finished.is_set()  # API returned while sender is blocked.
            assert len(sender_threads) == 1
            assert sender_threads[0] != api_thread
            release.set()
            transport.flush(2)
            assert finished.is_set()
            assert send.call_count == 1
    finally:
        release.set()
        transport.kill()


def test_queue_overflow_drops_events_without_waiting_for_sender():
    import threading

    transport = _make_fail_open_transport()
    started, release = threading.Event(), threading.Event()

    def blocked(envelope):
        started.set()
        release.wait(5)

    try:
        with patch.object(transport, "_send_envelope", side_effect=blocked):
            transport.capture_envelope(_test_envelope())
            assert started.wait(2)
            # More than the queue's capacity must return without waiting.
            for _ in range(20):
                transport.capture_envelope(_test_envelope())
            assert transport._worker._queue.qsize() == 2
            release.set()
            transport.flush(2)
    finally:
        release.set()
        transport.kill()


def test_transport_thread_start_failure_does_not_escape_to_api():
    transport = _make_fail_open_transport()
    try:
        with patch.object(transport._worker, "submit", side_effect=RuntimeError("no thread")):
            transport.capture_envelope(_test_envelope())
    finally:
        transport.kill()
