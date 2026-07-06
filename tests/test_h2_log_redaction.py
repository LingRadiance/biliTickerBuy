from util.h2client.h2connection import _headers_for_log


def test_h2_request_log_redacts_sensitive_headers():
    headers = [
        (":method", "POST"),
        ("cookie", "SESSDATA=secret"),
        ("authorization", "Bearer secret"),
        ("proxy-authorization", "Basic secret"),
        ("user-agent", "UA"),
    ]

    logged_headers = _headers_for_log(headers)

    assert logged_headers["cookie"] == "<redacted>"
    assert logged_headers["authorization"] == "<redacted>"
    assert logged_headers["proxy-authorization"] == "<redacted>"
    assert logged_headers["user-agent"] == "UA"
