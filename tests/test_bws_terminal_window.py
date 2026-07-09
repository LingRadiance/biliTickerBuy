import app_cmd.bws as bws_cmd_module
from app_cmd.config.BwsConfig import BwsConfig
from app_cmd.bws import iter_bws_terminal_events
from util.log.TerminalRenderer import PlainTerminalRenderer, TerminalRenderContext


def test_terminal_renderer_uses_context_title(capsys):
    renderer = PlainTerminalRenderer(
        TerminalRenderContext(
            config_name="BW名额 1001 / 20260710",
            log_file="btb_logs/bws_1001.log",
            platform_name="nt",
        )
    )

    renderer.render_header()

    output = capsys.readouterr().out
    assert "[抢票终端]" in output
    assert "BW名额 1001 / 20260710" in output


def test_bws_terminal_events_track_proxy_countdown_and_attempts():
    events = list(
        iter_bws_terminal_events(
            [
                "出口通道: none",
                "距开放还有 00:00:05",
                "BW 出口策略: local_fanout | 并发出口: none, http://127.0.0.1:8080",
                "第 2 轮提交反馈: [76650] 操作频繁 | busy",
            ]
        )
    )

    assert events[0].message == "出口通道: none"
    assert events[0].state.current_proxy == "none"
    assert events[1].state.countdown == "00:00:05"
    assert events[2].state.current_proxy == "none, http://127.0.0.1:8080"
    assert events[2].kind == "proxy"
    assert events[3].kind == "attempt"
    assert events[3].state.stage == "提交预约"
    assert events[3].state.attempt_current == 2


def test_bws_terminal_attempt_events_include_retry_total():
    events = list(
        iter_bws_terminal_events(
            ["第 2 轮提交反馈: [76650] 操作频繁 | busy"],
            attempt_total=5,
        )
    )

    assert events[0].kind == "attempt"
    assert events[0].state.attempt_current == 2
    assert events[0].state.attempt_total == 5


def test_bws_cmd_uses_shared_terminal_renderer_on_windows(monkeypatch):
    import interface.bws as bws_interface
    import util.log.LogConfig as log_config
    import util.log.TerminalRenderer as terminal_renderer

    captured = {}

    monkeypatch.setattr(bws_cmd_module.os, "name", "nt")
    monkeypatch.delenv("BTB_CHILD_PROCESS", raising=False)
    monkeypatch.setattr(
        log_config,
        "loguru_config",
        lambda *args, **kwargs: "btb_logs/bws_1001.log",
    )
    monkeypatch.setattr(bws_cmd_module, "_start_parent_watchdog", lambda logger: None)
    monkeypatch.setattr(
        bws_interface,
        "bws_reserve_stream",
        lambda args: ["出口通道: none"],
    )

    def fake_create_terminal_renderer(context, *, prefer_rich=True):
        captured["context"] = context
        captured["prefer_rich"] = prefer_rich
        return object()

    def fake_render_message_stream(renderer, messages, on_message=None):
        captured["renderer"] = renderer
        captured["events"] = list(messages)
        for event in captured["events"]:
            if on_message is not None:
                on_message(event.message)

    monkeypatch.setattr(
        terminal_renderer,
        "create_terminal_renderer",
        fake_create_terminal_renderer,
    )
    monkeypatch.setattr(
        terminal_renderer,
        "render_message_stream",
        fake_render_message_stream,
    )

    bws_cmd_module.bws_cmd(BwsConfig(reserve_id=1001, reserve_dates="20260710"))

    assert captured["context"].title == "抢票终端"
    assert captured["context"].config_name == "BW名额 1001 / 20260710"
    assert captured["context"].log_file == "btb_logs/bws_1001.log"
    assert captured["prefer_rich"] is True
    assert captured["events"][0].state.current_proxy == "none"
