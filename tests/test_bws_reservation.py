import pytest

from app_cmd.config.BwsConfig import BwsConfig
import interface.bws as bws
from interface.bws import (
    DEFAULT_BWS_RESERVE_DATES,
    BwsApiClient,
    _extract_act_days,
    _extract_bws_year_param,
    _extract_event_year,
    effective_bws_reserve_begin_time,
    infer_bws_reserve_dates,
    resolve_bws_reserve_dates,
    verify_bws_ticket_activation,
)
from task.bws import Bws
from tab.bws import build_bws_proxy_config


def _reservation_info():
    return {
        "user_ticket_info": {
            "20260710": {
                "ticket": "TICKET-0710",
                "screen_name": "BW2026",
                "sku_name": "单日票",
            }
        },
        "user_reserve_info": {"20260710": []},
        "reserve_list": {
            "20260710": [
                {
                    "reserve_id": 1001,
                    "act_title": "签售项目",
                    "act_begin_time": 1783652400,
                    "reserve_begin_time": 1783648800,
                }
            ],
            "20260711": [
                {
                    "reserve_id": 1002,
                    "act_title": "舞台项目",
                    "act_begin_time": 1783738800,
                    "reserve_begin_time": 1783735200,
                }
            ],
        },
    }


def test_verify_bws_ticket_activation_uses_matching_activity_date():
    result = verify_bws_ticket_activation(_reservation_info(), reserve_id=1001)

    assert result["date"] == "20260710"
    assert result["ticket_no"] == "TICKET-0710"
    assert result["activity"]["reserve_id"] == 1001


def test_verify_bws_ticket_activation_fails_when_date_not_activated():
    with pytest.raises(RuntimeError, match="未绑定目标档期"):
        verify_bws_ticket_activation(_reservation_info(), reserve_id=1002)


def test_verify_bws_ticket_activation_fails_when_activity_missing():
    with pytest.raises(RuntimeError, match="未找到预约项目"):
        verify_bws_ticket_activation(_reservation_info(), reserve_id=9999)


def test_verify_bws_ticket_activation_allows_explicit_date_override():
    result = verify_bws_ticket_activation(
        _reservation_info(),
        reserve_id=1002,
        reserve_date="2026-07-10",
    )

    assert result["date"] == "20260710"
    assert result["ticket_no"] == "TICKET-0710"


def test_infer_bws_reserve_dates_uses_year_prefix():
    dates = infer_bws_reserve_dates("202601").split(",")

    assert dates[0] == "20260710"
    assert dates[-1] == "20260712"
    assert len(dates) == 3


def test_infer_bws_reserve_dates_supports_2025_demo_dates():
    assert infer_bws_reserve_dates("202501") == "20250711,20250712,20250713"


def test_resolve_bws_reserve_dates_defaults_to_starsbon_dates():
    assert resolve_bws_reserve_dates("", "202601") == DEFAULT_BWS_RESERVE_DATES


def test_resolve_bws_reserve_dates_keeps_manual_value():
    assert resolve_bws_reserve_dates("20260710,20260711", "202601") == (
        "20260710,20260711"
    )


def test_extract_official_bws_schedule_parts_from_minified_js():
    sample = (
        '<title>BW2026，次元新航线！</title>'
        'var c=e.isPre?202602:202601,l=e.isPre?202602:202601;'
        'e.ACT_DAYS=["20260710","20260711","20260712"];'
    )

    assert _extract_event_year(sample) == 2026
    assert _extract_bws_year_param(sample, 2026) == "202601"
    assert _extract_act_days(sample) == ["20260710", "20260711", "20260712"]


def test_bws_reservation_code_table_matches_official_2026_page():
    assert bws.OFFICIAL_TERMINAL_CODES == {
        0: "预约成功",
        412: "IP 或账号被限流，建议更换 IP 后再试",
        75574: "场次已被抢空",
        76647: "您的预约数已达上限",
    }
    assert bws.OFFICIAL_RETRYABLE_CODES == {
        -702: "当前预约火爆，请稍后重试",
        429: "当前预约火爆，请稍后重试",
        76650: "操作频繁，请重试",
        76651: "当前预约火爆，请稍后重试",
    }
    assert bws.HISTORICAL_TERMINAL_CODES == {76674: "预约已达上限"}
    assert bws.HISTORICAL_RETRYABLE_CODES == {75637: "尚未开放"}


def test_bws_ticket_bind_code_table_matches_official_2026_page():
    assert bws.BWS_TICKET_BIND_CODES == {
        0: "门票认证成功",
        75636: "票务身份信息校验不通过",
        75639: "购票所用证件信息，已被绑定至其他账户",
        75642: "当前账号已经被绑定",
        75643: "当前证件下，未查询到购票信息",
        76645: "邀请函用户暂不支持门票认证相关功能",
    }
    assert bws.bws_ticket_bind_code_meaning(88001) == "信息验证失败"


def test_bws_reserve_stream_stops_when_already_reserved(monkeypatch):
    captured = {}

    class FakeClient:
        cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {"20260710": [{"reserve_id": 1001}]}}

        def make_reservation(self, **kwargs):
            raise AssertionError("should not submit duplicate reservation")

    def fake_make_bws_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(bws, "_make_bws_client", fake_make_bws_client)

    logs = list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                https_proxys="http://127.0.0.1:8080",
                retry_limit=1,
            )
        )
    )

    assert captured["proxy"] == "http://127.0.0.1:8080"
    assert any("已在账号记录中锁定" in message for message in logs)


def test_bws_terminal_task_uses_bws_subcommand():
    args = Bws(
        BwsConfig(
            reserve_id=1001,
            reserve_dates="20260710",
            reserve_date="20260710",
            reserve_type=-1,
            year="202601",
            interval=300,
            retry_limit=2,
            cookies_path="cookies.json",
        )
    ).to_cli_args()

    assert args[0] == "bws"
    assert args[args.index("--reserve-id") + 1] == "1001"
    assert args[args.index("--reserve-date") + 1] == "20260710"
    assert args[args.index("--year") + 1] == "202601"
    assert args[args.index("--cookies-path") + 1] == "cookies.json"


def test_bws_terminal_task_passes_proxy_config_to_subcommand():
    args = Bws(
        BwsConfig(
            reserve_id=1001,
            reserve_dates="20260710",
            https_proxys="none,http://127.0.0.1:8080",
        )
    ).to_cli_args()

    assert args[args.index("--https-proxys") + 1] == "none,http://127.0.0.1:8080"


def test_bws_terminal_task_passes_concurrency_config_to_subcommand():
    args = Bws(
        BwsConfig(
            reserve_id=1001,
            reserve_dates="20260710",
            thread_count=3,
            proxy_assignment_strategy="local_fanout",
        )
    ).to_cli_args()

    assert args[args.index("--thread-count") + 1] == "3"
    assert args[args.index("--proxy-assignment-strategy") + 1] == "local_fanout"


def test_bws_api_client_applies_proxy_to_session(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.proxies = None
            self.trust_env = True

    fake_session = FakeSession()
    monkeypatch.setattr(bws.requests, "Session", lambda: fake_session)

    BwsApiClient(
        [{"name": "bili_jct", "value": "csrf-token"}],
        proxy="http://127.0.0.1:8080",
    )

    assert fake_session.trust_env is False
    assert fake_session.proxies == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }


def test_build_bws_proxy_config_includes_direct_when_enabled():
    assert build_bws_proxy_config("http://127.0.0.1:8080", include_direct=True) == (
        "none,http://127.0.0.1:8080"
    )


def test_make_reservation_adds_timestamp_and_random_nonce(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "message": "ok"}

    class FakeSession:
        headers = {}

        def post(self, url, data, cookies, timeout):
            captured["url"] = url
            captured["data"] = data
            captured["cookies"] = cookies
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bws.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(bws.time, "time", lambda: 1783648800.123)
    monkeypatch.setattr(bws.random, "randint", lambda start, end: 54321)

    client = BwsApiClient(
        [
            {"name": "bili_jct", "value": "csrf-token"},
            {"name": "SESSDATA", "value": "sess"},
        ]
    )

    result = client.make_reservation(
        ticket_no="TICKET-0710",
        reserve_id=1001,
        year="202601",
    )

    assert result["code"] == 0
    assert captured["data"]["year"] == "202601"
    assert captured["data"]["ts"] == 1783648800123
    assert captured["data"]["_"] == 54321


def test_make_reservation_retries_http_429_with_fresh_nonce(monkeypatch):
    captured_payloads = []

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        headers = {}

        def __init__(self):
            self.responses = [
                FakeResponse(429),
                FakeResponse(200, {"code": 0, "message": "ok"}),
            ]

        def post(self, url, data, cookies, timeout):
            captured_payloads.append(dict(data))
            return self.responses.pop(0)

    monkeypatch.setattr(bws.requests, "Session", FakeSession)
    times = iter([1000.001, 1000.222])
    nonces = iter([11111, 22222])
    monkeypatch.setattr(bws.time, "time", lambda: next(times))
    monkeypatch.setattr(bws.random, "randint", lambda start, end: next(nonces))

    client = BwsApiClient([{"name": "bili_jct", "value": "csrf-token"}])
    result = client.make_reservation(
        ticket_no="TICKET-0710",
        reserve_id=1001,
        year="202601",
    )

    assert result["code"] == 0
    assert captured_payloads[0]["ts"] == 1000001
    assert captured_payloads[0]["_"] == 11111
    assert captured_payloads[1]["ts"] == 1000222
    assert captured_payloads[1]["_"] == 22222


def test_make_reservation_returns_terminal_result_for_http_412(monkeypatch):
    class FakeResponse:
        status_code = 412

        def json(self):
            raise AssertionError("HTTP 412 response body should not be required")

    class FakeSession:
        headers = {}

        def post(self, url, data, cookies, timeout):
            return FakeResponse()

    monkeypatch.setattr(bws.requests, "Session", FakeSession)

    client = BwsApiClient([{"name": "bili_jct", "value": "csrf-token"}])
    result = client.make_reservation(
        ticket_no="TICKET-0710",
        reserve_id=1001,
        year="202601",
    )

    assert result["code"] == 412
    assert "IP 或账号被限流" in result["message"]


def test_fetch_bws_goods_info_requests_goods_reserve_type():
    captured = {}

    class FakeClient:
        def get_reservation_info(self, **kwargs):
            captured.update(kwargs)
            return {"reserve_list": {}}

    result = bws.fetch_bws_goods_info(
        reserve_dates="20260710",
        year="202601",
        request=FakeClient(),
    )

    assert result == {"reserve_list": {}}
    assert captured["reserve_type"] == 1


def test_effective_bws_reserve_begin_time_uses_common_time_for_non_vip_ticket():
    activity = {
        "reserve_begin_time": 100,
        "is_vip_ticket": 1,
        "next_reserve": {"reserve_begin_time": 200},
    }

    assert effective_bws_reserve_begin_time(activity, {"is_vip": False}) == 200
    assert effective_bws_reserve_begin_time(activity, {"is_vip": True}) == 100


def test_bws_reserve_stream_retries_official_hot_status(monkeypatch):
    class FakeClient:
        cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            return {"code": 76651, "message": "female only"}

    monkeypatch.setattr(bws, "_make_bws_client", lambda **kwargs: FakeClient())

    logs = list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=3,
            )
        )
    )

    assert sum("提交反馈" in message for message in logs) == 3
    assert any("当前预约火爆，请稍后重试" in message for message in logs)
    assert not any("仅限女性" in message for message in logs)


def test_bws_reserve_stream_stops_on_http_412_terminal_status(monkeypatch):
    attempts = 0

    class FakeClient:
        cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            nonlocal attempts
            attempts += 1
            return {"code": 412, "message": "[412] IP 或账号被限流，建议更换 IP 后再试"}

    monkeypatch.setattr(bws, "_make_bws_client", lambda **kwargs: FakeClient())

    logs = list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=3,
            )
        )
    )

    assert attempts == 1
    assert sum("提交反馈" in message for message in logs) == 1
    assert any("IP 或账号被限流" in message for message in logs)


def test_bws_reserve_stream_balanced_strategy_cycles_proxy_slots(monkeypatch):
    submitted_proxies = []

    class FakeClient:
        def __init__(self, proxy="none"):
            self.proxy = proxy
            self.cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            submitted_proxies.append(self.proxy)
            return {"code": 76650, "message": "busy"}

    monkeypatch.setattr(
        bws,
        "_make_bws_client",
        lambda **kwargs: FakeClient(kwargs.get("proxy", "none")),
    )

    list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=1,
                interval=0,
                thread_count=3,
                https_proxys="none,http://127.0.0.1:8080",
                proxy_assignment_strategy="balanced",
            )
        )
    )

    assert sorted(submitted_proxies) == [
        "http://127.0.0.1:8080",
        "none",
        "none",
    ]


def test_bws_reserve_stream_queue_strategy_caps_to_proxy_count(monkeypatch):
    submitted_proxies = []

    class FakeClient:
        def __init__(self, proxy="none"):
            self.proxy = proxy
            self.cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            submitted_proxies.append(self.proxy)
            return {"code": 76650, "message": "busy"}

    monkeypatch.setattr(
        bws,
        "_make_bws_client",
        lambda **kwargs: FakeClient(kwargs.get("proxy", "none")),
    )

    list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=1,
                interval=0,
                thread_count=5,
                https_proxys="none,http://127.0.0.1:8080",
                proxy_assignment_strategy="queue",
            )
        )
    )

    assert sorted(submitted_proxies) == ["http://127.0.0.1:8080", "none"]


def test_bws_reserve_stream_local_fanout_uses_full_proxy_pool(monkeypatch):
    submitted_proxies = []

    class FakeClient:
        def __init__(self, proxy="none"):
            self.proxy = proxy
            self.cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            submitted_proxies.append(self.proxy)
            if self.proxy == "http://127.0.0.1:8080":
                return {"code": 0, "message": "ok"}
            return {"code": 76650, "message": "busy"}

    monkeypatch.setattr(
        bws,
        "_make_bws_client",
        lambda **kwargs: FakeClient(kwargs.get("proxy", "none")),
    )

    logs = list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=1,
                interval=0,
                thread_count=1,
                https_proxys="none,http://127.0.0.1:8080",
                proxy_assignment_strategy="local_fanout",
            )
        )
    )

    assert sorted(submitted_proxies) == ["http://127.0.0.1:8080", "none"]
    assert any("[0]" in message and "ok" in message for message in logs)


def test_bws_parallel_attempt_reuses_base_client_timeout(monkeypatch):
    captured_timeouts = []

    class FakeClient:
        def __init__(self, *, timeout=10.0):
            self.timeout = timeout
            self.cookies = [{"name": "bili_jct", "value": "csrf"}]

        def make_reservation(self, **kwargs):
            return {"code": 76650, "message": "busy"}

    def fake_make_bws_client(**kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        return FakeClient(timeout=kwargs.get("timeout", 10.0))

    monkeypatch.setattr(bws, "_make_bws_client", fake_make_bws_client)

    bws._submit_bws_reservation_attempt(
        base_client=FakeClient(timeout=3.5),
        config=BwsConfig(reserve_id=1001, proxy_assignment_strategy="balanced"),
        ticket_no="TICKET-0710",
        year="202601",
        proxy_slots=["none", "http://127.0.0.1:8080"],
    )

    assert captured_timeouts == [3.5, 3.5]


def test_bws_reserve_stream_marks_unknown_codes_retryable(monkeypatch):
    class FakeClient:
        cookies = [{"name": "bili_jct", "value": "csrf"}]

        def get_username(self):
            return "tester"

        def get_reservation_info(self, **kwargs):
            return _reservation_info()

        def get_my_reservations(self, **kwargs):
            return {"reserve_list": {}}

        def make_reservation(self, **kwargs):
            return {"code": 88001, "message": "new status"}

    monkeypatch.setattr(bws, "_make_bws_client", lambda **kwargs: FakeClient())

    logs = list(
        bws.bws_reserve_stream(
            BwsConfig(
                reserve_id=1001,
                reserve_dates="20260710",
                time_start="2020-01-01 00:00:00",
                retry_limit=2,
            )
        )
    )

    assert sum("提交反馈" in message for message in logs) == 2
    assert any("【未知返回码】" in message for message in logs)
    assert any("按可重试处理" in message for message in logs)
