import asyncio

from app.asyncio_quiet import install_connection_reset_filter


def test_connection_reset_is_silenced_and_others_forwarded():
    loop = asyncio.new_event_loop()
    seen: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    try:
        install_connection_reset_filter(loop)
        handler = loop.get_exception_handler()
        assert handler is not None

        handler(loop, {"exception": ConnectionResetError(10054, "reset")})
        handler(loop, {"exception": ConnectionAbortedError(10053, "aborted")})
        assert seen == []

        handler(loop, {"exception": ValueError("boom"), "message": "loop error"})
        assert [context["message"] for context in seen] == ["loop error"]
    finally:
        loop.close()
