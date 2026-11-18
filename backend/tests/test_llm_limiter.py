"""The per-provider limit shared by analyst chats and caption jobs (`services/llm_limiter`)."""
import asyncio

import pytest

from backend.services import llm_limiter
from backend.services.llm_limiter import SlotSkipped, in_use, resize, slot


def _run(coro):
    return asyncio.run(coro)


def test_slots_are_first_come_first_served_and_released_on_cancel():
    async def scenario():
        order: list[int] = []
        gate = asyncio.Event()

        async def holder():
            async with slot("p", 1):
                await gate.wait()

        async def waiter(n: int):
            async with slot("p", 1):
                order.append(n)

        first = asyncio.create_task(holder())
        await asyncio.sleep(0)
        waiters = [asyncio.create_task(waiter(n)) for n in range(3)]
        await asyncio.sleep(0.01)
        assert in_use("p") == 1 and order == []
        # A waiter cancelled while queued gives up its place without taking a slot.
        waiters[1].cancel()
        gate.set()
        await asyncio.gather(first, waiters[0], waiters[2], return_exceptions=True)
        assert order == [0, 2]
        assert in_use("p") == 0

    _run(scenario())


def test_resize_applies_in_place_whatever_size_callers_pass():
    async def scenario():
        gate = asyncio.Event()
        running = 0
        peak = 0

        async def call(size_seen: int):
            nonlocal running, peak
            # Each caller passes its own, possibly stale, copy of the setting.
            async with slot("q", size_seen):
                running += 1
                peak = max(peak, running)
                await gate.wait()
                running -= 1

        tasks = [asyncio.create_task(call(1)), asyncio.create_task(call(4)), asyncio.create_task(call(4))]
        await asyncio.sleep(0.01)
        assert in_use("q") == 1  # made at size 1; a caller's 4 does not split it
        resize("q", 3)
        await asyncio.sleep(0.01)
        assert in_use("q") == 3
        gate.set()
        await asyncio.gather(*tasks)
        assert peak == 3 and in_use("q") == 0

    _run(scenario())


def test_a_waiter_with_stop_gives_up_unsent(monkeypatch):
    monkeypatch.setattr(llm_limiter, "STOP_POLL_S", 0.02)

    async def scenario():
        stop = False
        gate = asyncio.Event()

        async def holder():
            async with slot("r", 1):
                await gate.wait()

        h = asyncio.create_task(holder())
        await asyncio.sleep(0)

        async def stoppable():
            async with slot("r", 1, stop=lambda: stop):
                return "sent"

        w = asyncio.create_task(stoppable())
        await asyncio.sleep(0.05)
        stop = True
        with pytest.raises(SlotSkipped):
            await w
        assert in_use("r") == 1  # only the holder
        gate.set()
        await h
        assert in_use("r") == 0
        # Stop already set: refused at once, even with a slot free.
        with pytest.raises(SlotSkipped):
            async with slot("r", 1, stop=lambda: True):
                pass

    _run(scenario())


def test_a_new_loop_gets_a_new_limit():
    async def hold():
        async with slot("s", 1):
            return in_use("s")

    assert _run(hold()) == 1
    # A limit left on a closed loop is not reused, so nothing is still "held".
    assert _run(hold()) == 1
