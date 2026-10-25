"""压掉 asyncio 里「客户端断连」的假异常日志。

Proactor 事件循环在连接被对端直接关闭（壳关窗、浏览器刷新、fetch 被取消）时，
会向异常处理器抛 ConnectionResetError/10054。默认处理器把它打成整段 traceback，
在桌面壳的 runtime.log 里看起来像后端崩了。断连不影响请求语义，静默即可。
"""

from __future__ import annotations

import asyncio


def install_connection_reset_filter(loop: asyncio.AbstractEventLoop) -> None:
    previous = loop.get_exception_handler()

    def _handler(active_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        if previous is not None:
            previous(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
