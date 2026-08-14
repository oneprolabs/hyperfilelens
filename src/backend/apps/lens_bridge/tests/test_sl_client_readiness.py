"""Tests for SourceLens transport classification and business readiness."""

from unittest.mock import Mock, patch

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.lens_bridge.api.views import _lens_error_response
from apps.lens_bridge.services import sl_client


class SourceLensClientReadinessTests(SimpleTestCase):
    def test_retryable_status_survives_hfl_api_boundary(self) -> None:
        response = _lens_error_response(sl_client.LensBridgeUnavailable())

        self.assertEqual(response.status_code, 503)

    def test_remote_application_error_remains_bad_gateway(self) -> None:
        error = sl_client.LensBridgeError("not found")
        error.status_code = 404

        response = _lens_error_response(error)

        self.assertEqual(response.status_code, 502)

    def test_remote_server_error_is_retryable_and_sanitized(self) -> None:
        response = Mock(status_code=503, content=b"provider api_key=must-not-leak")

        with self.assertRaises(sl_client.LensBridgeUnavailable) as raised:
            sl_client._raise_for_response(response)

        self.assertNotIn("must-not-leak", str(raised.exception))

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_failed_stream_response_is_closed(
        self,
        get,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(status_code=503, content=b"unavailable")
        get.return_value = response

        with self.assertRaises(sl_client.LensBridgeUnavailable):
            sl_client.stream_sse("/api/lens/runs/stream")

        response.close.assert_called_once()

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(
        sl_client.requests,
        "request",
        side_effect=requests.ConnectionError("connection refused"),
    )
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    def test_request_transport_failure_is_retryable_503(
        self,
        _base_url,
        _request,
        _headers,
    ) -> None:
        with self.assertRaises(sl_client.LensBridgeUnavailable) as raised:
            sl_client.request_json("GET", "/api/lens/assistants/")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.default_code, "lens_bridge_unavailable")

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "post")
    def test_multipart_upload_forwards_the_file_without_copying_it(
        self,
        post,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(status_code=201, content=b"{}")
        response.json.return_value = {"uuid": "attachment-uuid"}
        post.return_value = response
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"pdf-bytes",
            content_type="application/pdf",
        )
        user = Mock(pk=7)

        result = sl_client.request_multipart(
            "/api/lens/sessions/session-uuid/attachments/",
            uploaded_file=uploaded,
            hfl_user=user,
        )

        self.assertEqual(result["uuid"], "attachment-uuid")
        forwarded = post.call_args.kwargs["files"]["file"]
        self.assertEqual(forwarded[0], "report.pdf")
        self.assertIs(forwarded[1], uploaded)
        self.assertEqual(forwarded[2], "application/pdf")

    @patch.object(sl_client, "_invalidate_access_token")
    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "post")
    def test_multipart_upload_rewinds_the_file_after_reauthentication(
        self,
        post,
        _base_url,
        _headers,
        invalidate_access_token,
    ) -> None:
        unauthorized = Mock(status_code=401)
        accepted = Mock(status_code=201, content=b"{}")
        accepted.json.return_value = {"uuid": "attachment-uuid"}
        positions = []

        def send(*_args, **kwargs):
            uploaded_file = kwargs["files"]["file"][1]
            positions.append(uploaded_file.tell())
            uploaded_file.read()
            return unauthorized if len(positions) == 1 else accepted

        post.side_effect = send
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"pdf-bytes",
            content_type="application/pdf",
        )
        user = Mock(pk=7)

        result = sl_client.request_multipart(
            "/api/lens/sessions/session-uuid/attachments/",
            uploaded_file=uploaded,
            hfl_user=user,
        )

        self.assertEqual(result["uuid"], "attachment-uuid")
        self.assertEqual(positions, [0, 0])
        unauthorized.close.assert_called_once()
        invalidate_access_token.assert_called_once_with(user)

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_binary_attachment_is_streamed_and_closed(
        self,
        get,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(
            status_code=200,
            headers={
                "Content-Type": "image/png",
                "Content-Length": "6",
            },
        )
        response.iter_content.return_value = [b"abc", b"def"]
        get.return_value = response
        user = Mock(pk=7)

        upstream = sl_client.stream_binary(
            "/api/lens/attachments/attachment-uuid/",
            hfl_user=user,
        )

        self.assertEqual(upstream.content_type, "image/png")
        self.assertEqual(b"".join(upstream.body), b"abcdef")
        response.close.assert_called_once()
        _headers.assert_called_once_with(
            {
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
            hfl_user=user,
        )

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_binary_attachment_omits_encoded_content_length(
        self,
        get,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(
            status_code=200,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": "99",
            },
        )
        response.iter_content.return_value = iter([b"decoded"])
        get.return_value = response

        upstream = sl_client.stream_binary(
            "/api/lens/attachments/attachment-uuid/",
            hfl_user=Mock(pk=7),
        )

        self.assertEqual(upstream.content_length, "")
        upstream.body.close()

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_binary_attachment_closes_before_iteration_starts(
        self,
        get,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter([b"abc"])
        get.return_value = response

        upstream = sl_client.stream_binary(
            "/api/lens/attachments/attachment-uuid/",
            hfl_user=Mock(pk=7),
        )
        upstream.body.close()

        response.close.assert_called_once()

    @patch.object(sl_client, "_auth_headers", return_value={})
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_binary_attachment_rejects_public_cache_policy(
        self,
        get,
        _base_url,
        _headers,
    ) -> None:
        response = Mock(
            status_code=200,
            headers={"Cache-Control": "public, max-age=3600"},
        )
        response.iter_content.return_value = iter([])
        get.return_value = response

        upstream = sl_client.stream_binary(
            "/api/lens/attachments/attachment-uuid/",
            hfl_user=Mock(pk=7),
        )

        self.assertEqual(upstream.cache_control, "private, no-store")
        upstream.body.close()

    @patch.object(sl_client.deploy, "lens_bridge_legacy_username", return_value="")
    @patch.object(sl_client.deploy, "lens_bridge_password", return_value="password")
    @patch.object(sl_client.deploy, "lens_bridge_email", return_value="admin@example.test")
    @patch.object(sl_client, "_ensure_credentials")
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "post")
    def test_login_invalid_json_is_retryable_and_sanitized(
        self,
        post,
        _base_url,
        _credentials,
        _email,
        _password,
        _legacy_username,
    ) -> None:
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("provider secret must-not-leak")
        post.return_value = response

        with self.assertRaises(sl_client.LensBridgeUnavailable) as raised:
            sl_client._login()

        self.assertNotIn("must-not-leak", str(raised.exception))

    @patch.object(sl_client, "_login")
    @patch.object(sl_client, "_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "post")
    def test_refresh_invalid_json_reauthenticates(
        self,
        post,
        _base_url,
        login,
    ) -> None:
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("invalid json")
        post.return_value = response

        with patch.object(sl_client, "_ADMIN_REFRESH_TOKEN", "refresh-token"):
            sl_client._refresh_access()

        login.assert_called_once_with()

    @patch.object(sl_client, "request_json", return_value={"results": []})
    @patch.object(sl_client, "_get_admin_access_token", return_value="token")
    @patch.object(sl_client.deploy, "lens_bridge_configured", return_value=True)
    @patch.object(sl_client.deploy, "lens_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_ping_requires_authenticated_business_endpoint(
        self,
        get,
        _base_url,
        _configured,
        _token,
        request_json,
    ) -> None:
        get.return_value = Mock(status_code=200)

        result = sl_client.ping(timeout=3)

        self.assertTrue(result["reachable"])
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["business_ready"])
        self.assertEqual(result["status"], "ready")
        request_json.assert_called_once_with(
            "GET",
            "/api/lens/admin/lensnodes/",
            params={"page": 1, "page_size": 1},
            timeout=3,
        )

    @patch.object(
        sl_client,
        "request_json",
        side_effect=sl_client.LensBridgeUnavailable(),
    )
    @patch.object(sl_client, "_get_admin_access_token", return_value="token")
    @patch.object(sl_client.deploy, "lens_bridge_configured", return_value=True)
    @patch.object(sl_client.deploy, "lens_base_url", return_value="http://sourcelens")
    @patch.object(sl_client.requests, "get")
    def test_ping_reports_degraded_without_blocking_health_endpoint(
        self,
        get,
        _base_url,
        _configured,
        _token,
        _request_json,
    ) -> None:
        get.return_value = Mock(status_code=200)

        result = sl_client.ping(timeout=3)

        self.assertTrue(result["reachable"])
        self.assertFalse(result["business_ready"])
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(
            result["warning"],
            "SourceLens business API is temporarily unavailable.",
        )
