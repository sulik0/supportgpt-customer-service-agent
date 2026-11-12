"""Keep unittest's isolated loops from clearing pytest's shared event loop."""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def restore_pytest_event_loop(event_loop):
    """恢复 IsolatedAsyncioTestCase 清空的当前循环，供后续 pytest 用例使用。

    独立 unittest 运行不加载此文件，仍由 unittest 管理自己的循环。
    """
    yield
    asyncio.set_event_loop(event_loop)
