import inspect

import tab.bws as bws_tab


def test_bws_tab_uses_ticket_task_action_layout():
    source = inspect.getsource(bws_tab.bws_tab)

    assert 'elem_classes="btb-inline-actions !justify-end"' in source
    assert '"一键终止"' in source
    assert "btb-stop-all-button" in source
    assert '"启动预约任务"' in source
    assert "stop_all_running_tasks" in source
