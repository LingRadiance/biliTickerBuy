from __future__ import annotations

import importlib
import sys
import types

try:
    import qrcode  # noqa: F401
except ModuleNotFoundError:
    sys.modules["qrcode"] = types.ModuleType("qrcode")

buy_module = importlib.import_module("task.buy")


class FakeRequest:
    def __init__(self) -> None:
        self.prewarmed_urls: list[str] = []

    def prewarm_h2_connection(self, url: str) -> None:
        self.prewarmed_urls.append(url)


def test_refresh_project_detail_failure_does_not_abort_warmup(monkeypatch):
    request = FakeRequest()
    tickets_info = {"project_id": 1003349, "is_hot_project": False}

    def fail_fetch_project_payload(*, request, project_id):
        raise RuntimeError("new=410 Gone; old=HTTP 429")

    monkeypatch.setattr(
        buy_module,
        "fetch_project_payload",
        fail_fetch_project_payload,
    )

    is_hot_project, messages = buy_module._refresh_project_detail_and_warm_connection(
        request=request,
        tickets_info=tickets_info,
        is_hot_project=False,
    )

    assert is_hot_project is False
    assert request.prewarmed_urls == ["https://show.bilibili.com/"]
    assert len(messages) == 1
    assert "410 Gone" in messages[0]
    assert "HTTP 429" in messages[0]


def test_refresh_project_detail_can_upgrade_hot_project(monkeypatch):
    request = FakeRequest()
    tickets_info = {"project_id": 1003349, "is_hot_project": False}

    def fetch_hot_project(*, request, project_id):
        return {"hotProject": True}

    monkeypatch.setattr(buy_module, "fetch_project_payload", fetch_hot_project)

    is_hot_project, messages = buy_module._refresh_project_detail_and_warm_connection(
        request=request,
        tickets_info=tickets_info,
        is_hot_project=False,
    )

    assert is_hot_project is True
    assert tickets_info["is_hot_project"] is True
    assert request.prewarmed_urls == ["https://show.bilibili.com/"]
    assert messages == []
