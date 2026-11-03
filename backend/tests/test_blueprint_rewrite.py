"""The rewrite pass's loop (M6 §3), with a fake LLM session, writer, clock and cancel
flag: retries, caps, isolation, the budget and cancellation. The DB side is
test_blueprint_rewrite_http.py's."""
from backend.ml.prompt_generator import BudgetExhausted, RewriteError
from backend.services import blueprint_rewrite as br
from backend.services.blueprint_rewrite import LeafTarget, run_pass
from backend.tests.conftest import run


class FakeSession:
    """`script` items answer successive rewrite() calls: "ok" returns a prose prompt per
    template, a str kind raises that RewriteError. Each call costs one request."""

    def __init__(self, *script, max_requests=None, default="ok"):
        self.script = list(script)
        self.default = default
        self.max_requests = max_requests
        self.requests = 0
        self.mode = "plain"
        self.seen: list[list[str]] = []

    async def rewrite(self, templates, descriptors):
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise BudgetExhausted()
        self.requests += 1
        self.seen.append(list(templates))
        item = self.script.pop(0) if self.script else self.default
        if item != "ok":
            raise RewriteError(f"fake {item}", item)
        return [f"prose of {t}" for t in templates]


class Writer:
    def __init__(self):
        self.rows: dict[str, str] = {}
        # Row ids a hand edit took over mid-run: the re-check skips them.
        self.manual: set[str] = set()

    async def __call__(self, pairs):
        n = 0
        for rid, text in pairs:
            if rid not in self.manual:
                self.rows[rid] = text
                n += 1
        return n


class Clock:
    def __init__(self):
        self.slept = 0.0
        self.on_sleep = None

    async def sleep(self, s):
        self.slept += s
        if self.on_sleep:
            self.on_sleep()


def _leaf(name, n):
    return LeafTarget(leaf_id=name, path=name, descriptors=[], rows=[(f"{name}{i}", f"{name}-t{i}") for i in range(n)])


def _go(targets, session, *, batch=2, cancelled=lambda: False, clock=None, writer=None):
    clock = clock or Clock()
    writer = writer or Writer()
    result = run(run_pass(
        targets, session=session, write=writer, batch_size=batch, cancelled=cancelled,
        sleep=clock.sleep, rng=lambda: 0.5,
    ))
    return result, writer, clock


def test_every_leaf_rewritten_in_batches():
    session = FakeSession()
    result, writer, _ = _go([_leaf("a", 3), _leaf("b", 2)], session)
    assert (result.rewritten, result.requested, result.leaves, result.leaves_failed) == (5, 5, 2, 0)
    assert result.stop_reason == "done" and result.calls == 3 and result.leaves_done == 2
    assert session.seen == [["a-t0", "a-t1"], ["a-t2"], ["b-t0", "b-t1"]]
    assert writer.rows["a2"] == "prose of a-t2"


def test_a_transient_error_backs_off_then_succeeds():
    result, _, clock = _go([_leaf("a", 1)], FakeSession("transient", "transient", "ok"))
    assert result.rewritten == 1 and result.stop_reason == "done"
    # 2 s then 4 s at the jitter's midpoint.
    assert clock.slept == 6.0


def test_transient_errors_past_the_retries_fail_the_leaf_and_keep_its_templates():
    session = FakeSession("transient", "transient", "transient", "ok", "ok")
    result, writer, _ = _go([_leaf("a", 1), _leaf("b", 1), _leaf("c", 1)], session)
    assert result.leaves_failed == 1 and result.failed_paths == ["a"]
    assert "a0" not in writer.rows and set(writer.rows) == {"b0", "c0"}
    assert result.stop_reason == "done"


def test_a_miscount_is_asked_again_without_a_backoff_until_the_leaf_cap():
    # One batch: cap is 1 + 2 = 3 requests, all miscounts.
    session = FakeSession("mismatch", "mismatch", "mismatch")
    result, _, clock = _go([_leaf("a", 2), _leaf("b", 1)], session)
    assert clock.slept == 0
    assert result.leaves_failed == 1 and result.rewritten == 1
    assert session.requests == 4


def test_a_fatal_error_stops_the_pass():
    result, writer, _ = _go([_leaf("a", 1), _leaf("b", 1)], FakeSession("fatal"))
    assert result.stop_reason == "provider_error" and result.error == "fake fatal"
    assert writer.rows == {} and result.leaves_failed == 0


def test_the_pass_aborts_past_a_quarter_of_leaves_failed():
    # 12 leaves → abort once more than 3 fail. Every request is a miscount.
    leaves = [_leaf(f"l{i}", 1) for i in range(12)]
    result, _, _ = _go(leaves, FakeSession(default="mismatch"))
    assert result.stop_reason == "too_many_failures" and result.leaves_failed == 4


def test_the_budget_is_a_hard_stop_and_keeps_what_was_written():
    session = FakeSession(max_requests=2)
    result, writer, _ = _go([_leaf("a", 2), _leaf("b", 2), _leaf("c", 2)], session)
    assert result.stop_reason == "call_budget" and result.rewritten == 4
    # c was never reached: neither done nor failed.
    assert (result.leaves_done, result.leaves_failed) == (2, 0)
    assert set(writer.rows) == {"a0", "a1", "b0", "b1"} and session.requests == 2


def test_a_stop_during_a_backoff_is_seen_within_one_slice():
    state = {"stop": False}
    clock = Clock()
    clock.on_sleep = lambda: state.update(stop=True)
    session = FakeSession("transient", "ok")
    result, _, _ = _go([_leaf("a", 1)], session, cancelled=lambda: state["stop"], clock=clock)
    assert result.stop_reason == "cancelled" and result.rewritten == 0
    assert clock.slept == br.CANCEL_POLL_S and session.requests == 1


def test_a_stop_between_batches_keeps_the_written_batch():
    state = {"calls": 0}

    def cancelled():
        state["calls"] += 1
        return state["calls"] > 1  # the first check passes, the next one stops
    result, writer, _ = _go([_leaf("a", 4)], FakeSession(), cancelled=cancelled)
    assert result.stop_reason == "cancelled" and set(writer.rows) == {"a0", "a1"}


def test_a_row_edited_by_hand_mid_run_is_not_counted():
    writer = Writer()
    writer.manual = {"a1"}
    result, _, _ = _go([_leaf("a", 2)], FakeSession(), writer=writer)
    assert result.rewritten == 1 and set(writer.rows) == {"a0"}


def test_budget_arithmetic():
    assert br.expected_calls([5, 3, 0], 5) == 2
    assert br.leaf_cap(5, 5) == 3
    # Slack is a quarter of the expected calls, at least two.
    assert br.call_budget([5, 3], 5) == 4
    assert br.call_budget([10] * 20, 5) == 50
    assert br.call_budget([], 5) == 0


def test_a_rejected_request_fails_only_its_leaf():
    result, writer, clock = _go([_leaf("a", 1), _leaf("b", 1)], FakeSession("rejected"))
    assert result.leaves_failed == 1 and result.failed_paths == ["a"]
    assert set(writer.rows) == {"b0"} and result.stop_reason == "done" and clock.slept == 0


def test_a_failed_write_stops_with_what_was_committed():
    class Flaky(Writer):
        async def __call__(self, pairs):
            if any(rid.startswith("b") for rid, _ in pairs):
                raise RuntimeError("database is locked")
            return await super().__call__(pairs)
    result, writer, _ = _go([_leaf("a", 2), _leaf("b", 2), _leaf("c", 2)], FakeSession(), writer=Flaky())
    assert result.stop_reason == "write_error" and "database is locked" in result.error
    assert result.rewritten == 2 and set(writer.rows) == {"a0", "a1"}
