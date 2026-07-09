from __future__ import annotations

import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace

from app_cmd.config.BwsConfig import BwsConfig

TASK_COMPLETED_MARKER = "抢票完成后退出程序。。。。。"
TASK_STOPPED_MARKER = "BTB_TASK_STOPPED_BY_USER"
BWS_ATTEMPT_RE = re.compile(r"第\s*(\d+)\s*轮提交反馈")


@dataclass(slots=True)
class BwsTerminalState:
    stage: str = "初始化"
    countdown: str = "-"
    current_proxy: str = "未初始化"
    cooldown_remaining: int | None = None
    attempt_current: int | None = None
    attempt_total: int | None = None


@dataclass(slots=True)
class BwsTerminalEvent:
    kind: str
    message: str
    state: BwsTerminalState


def _resolve_log_file_name() -> str:
    configured_name = os.environ.get("BTB_APP_LOG_NAME", "").strip()
    if configured_name:
        return re.sub(r"[^\w.\-]", "_", os.path.basename(configured_name))
    return f"bws-{uuid.uuid4()}.log"


def _after_colon(message: str) -> str:
    for separator in (":", "："):
        if separator in message:
            return message.split(separator, 1)[1].strip()
    return ""


def _extract_bws_attempt(message: str) -> int | None:
    match = BWS_ATTEMPT_RE.search(message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _update_bws_terminal_state(
    state: BwsTerminalState,
    message: str,
    *,
    attempt_total: int | None = None,
) -> tuple[str, BwsTerminalState]:
    kind = "status"
    updated = replace(state, cooldown_remaining=None)

    if message.startswith("出口通道"):
        updated.current_proxy = _after_colon(message) or updated.current_proxy
        kind = "proxy"
    elif "并发出口" in message:
        updated.current_proxy = _after_colon(message.rsplit("|", 1)[-1])
        kind = "proxy"
    elif message.startswith("距开放还有"):
        updated.stage = "等待开始"
        updated.countdown = message.replace("距开放还有", "", 1).strip() or "-"
    elif "入场凭证" in message:
        updated.stage = "预约准备"
    elif "提交反馈" in message:
        updated.stage = "提交预约"
        updated.attempt_current = _extract_bws_attempt(message)
        updated.attempt_total = attempt_total
        kind = "attempt"
        if "[0]" in message or "预约成功" in message:
            updated.stage = "已拿到名额"
    elif "限流" in message:
        updated.stage = "限流处理"
        kind = "proxy"
    elif "已锁定" in message or "暂未拿到名额" in message:
        updated.stage = "预约结束"

    return kind, updated


def iter_bws_terminal_events(
    messages: Iterable[str],
    *,
    attempt_total: int | None = None,
) -> Iterator[BwsTerminalEvent]:
    state = BwsTerminalState()
    for message in messages:
        kind, state = _update_bws_terminal_state(
            state,
            str(message),
            attempt_total=attempt_total,
        )
        yield BwsTerminalEvent(kind=kind, message=str(message), state=replace(state))


def _bws_terminal_config_name(args: BwsConfig) -> str:
    suffix = args.reserve_date or args.reserve_dates or args.year or "auto"
    return f"BW名额 {args.reserve_id} / {suffix}"


def _hold_terminal(message: str) -> None:
    if os.environ.get("BTB_HOLD_TERMINAL", "") != "1":
        return
    try:
        if os.name == "nt":
            import msvcrt

            print(message, flush=True)
            msvcrt.getwch()
            return
        if sys.stdin and sys.stdin.isatty():
            input(message)
    except (EOFError, KeyboardInterrupt):
        pass


def _parent_pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        still_active = 259

        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information | synchronize,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _start_parent_watchdog(logger) -> None:
    raw_parent_pid = os.environ.get("BTB_PARENT_PID", "").strip()
    if not raw_parent_pid:
        return
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError:
        return
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    def _watch_parent() -> None:
        while True:
            time.sleep(1.0)
            if _parent_pid_is_running(parent_pid):
                continue
            try:
                logger.warning("检测到主进程已退出，当前 BW 名额助手子进程即将结束。")
            except Exception:
                pass
            os._exit(0)

    threading.Thread(
        target=_watch_parent,
        name="btb-bws-parent-watchdog",
        daemon=True,
    ).start()


def bws_cmd(args: BwsConfig) -> None:
    from loguru import logger

    from interface.bws import bws_reserve_stream
    from util import LOG_DIR
    from util.log.LogConfig import loguru_config
    from util.log.TerminalRenderer import (
        TerminalRenderContext,
        create_terminal_renderer,
        render_message_stream,
    )

    use_terminal_renderer = (
        os.name == "nt" and os.environ.get("BTB_CHILD_PROCESS", "") != "1"
    )
    enable_console_log = not use_terminal_renderer and (
        os.environ.get("BTB_CHILD_PROCESS", "") != "1"
    )
    log_file = loguru_config(
        LOG_DIR,
        _resolve_log_file_name(),
        enable_console=enable_console_log,
    )
    _start_parent_watchdog(logger)
    try:
        renderer = (
            create_terminal_renderer(
                TerminalRenderContext(
                    config_name=_bws_terminal_config_name(args),
                    log_file=log_file,
                    platform_name=os.name,
                ),
                prefer_rich=os.name == "nt",
            )
            if use_terminal_renderer
            else None
        )
        try:
            attempt_total = int(args.retry_limit or 0)
        except (TypeError, ValueError):
            attempt_total = 0
        render_message_stream(
            renderer,
            iter_bws_terminal_events(
                bws_reserve_stream(args),
                attempt_total=attempt_total if attempt_total > 0 else None,
            ),
            on_message=logger.info,
        )
    except KeyboardInterrupt:
        logger.warning(TASK_STOPPED_MARKER)
        logger.warning("收到 Ctrl+C，已停止 BW 名额助手流程。")
        _hold_terminal("已停止当前 BW 名额助手流程。按任意键关闭此窗口...")
    except Exception as exc:
        logger.exception(f"BW 名额助手流程异常退出: {exc}")
        _hold_terminal(f"BW 名额助手流程异常退出: {exc}\n按任意键关闭此窗口...")
        raise
    logger.info(TASK_COMPLETED_MARKER)
    _hold_terminal("BW 名额助手流程已结束。按任意键关闭此窗口...")
