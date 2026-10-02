"""Streaming hardening (DESIGN-002 §6.2, §7.4): heartbeats, and stopping on disconnect."""

import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from services.glassbox.api import ask as _ask_module
from services.glassbox.api import sse
from services.glassbox.api.main import app
from services.glassbox.api.sse import PING_FRAME, with_heartbeat
from services.tests.test_ask_endpoint import TEST_MYSQL_PORT, MemoryRedis, events

ORIGINAL_SAVE_QUERY = _ask_module._save_query
BODY = {"question": "What is Basel's background?", "corpus": "about_me"}


# --- with_heartbeat ------------------------------------------------------------


async def _collect(stream, *, limit=100):
    started = time.monotonic()
    out = []
    async for item in stream:
        out.append((time.monotonic() - started, item))
        if len(out) >= limit:
            break
    return out


def test_heartbeat_pings_while_source_is_quiet_and_keeps_event_order():
    async def slow_source():
        yield "event: a\n\n"
        await asyncio.sleep(0.35)
        yield "event: b\n\n"
        await asyncio.sleep(0.35)
        yield "event: c\n\n"

    out = asyncio.run(_collect(with_heartbeat(slow_source(), interval_s=0.1)))
    items = [item for _, item in out]
    assert [item for item in items if item != PING_FRAME] == [
        "event: a\n\n",
        "event: b\n\n",
        "event: c\n\n",
    ]
    between_a_and_b = items[items.index("event: a\n\n") + 1 : items.index("event: b\n\n")]
    between_b_and_c = items[items.index("event: b\n\n") + 1 : items.index("event: c\n\n")]
    assert between_a_and_b and set(between_a_and_b) == {PING_FRAME}
    assert between_b_and_c and set(between_b_and_c) == {PING_FRAME}
    # Pings arrive at the interval: no silent gap much longer than 0.1 s.
    times = [at for at, _ in out]
    assert max(later - earlier for earlier, later in zip(times, times[1:], strict=False)) < 0.2
    # And the first ping waits a full interval after the last real event.
    first_ping_at = times[items.index(PING_FRAME)]
    assert first_ping_at >= 0.09


def test_heartbeat_sends_no_ping_while_events_flow():
    async def fast_source():
        for index in range(20):
            await asyncio.sleep(0.01)
            yield f"event: {index}\n\n"

    out = asyncio.run(_collect(with_heartbeat(fast_source(), interval_s=0.1)))
    assert PING_FRAME not in [item for _, item in out]
    assert len(out) == 20


def test_heartbeat_ping_is_an_sse_comment():
    assert PING_FRAME == ": ping\n\n"


async def _drain_cleanup():
    """Wait for with_heartbeat's cleanup tasks instead of guessing with sleeps."""
    while pending := [task for task in sse._CLEANUP_TASKS if not task.done()]:
        await asyncio.gather(*pending, return_exceptions=True)


class CountingSource:
    """An endless token source that records how far it got and whether it was closed."""

    def __init__(self, delay=0.02):
        self.delay = delay
        self.produced = 0
        self.closed = False
        self.cancelled = False

    async def __call__(self):
        try:
            while True:
                await asyncio.sleep(self.delay)
                self.produced += 1
                yield f"event: token\ndata: {self.produced}\n\n"
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.closed = True


class GatedSource:
    """Yields one event, then blocks until released: a known point to stop it at."""

    def __init__(self):
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.produced = 0
        self.closed = False
        self.cancelled = False

    async def __call__(self):
        try:
            self.produced += 1
            yield "event: token\ndata: 1\n\n"
            self.waiting.set()
            await self.release.wait()
            self.produced += 1
            yield "event: token\ndata: 2\n\n"
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.closed = True


def test_heartbeat_stops_the_source_when_the_consumer_closes():
    """Closed while the source is suspended at its yield: GeneratorExit closes it."""

    async def run():
        source = GatedSource()
        stream = with_heartbeat(source(), interval_s=10)
        assert (await anext(stream)).startswith("event: token")
        await stream.aclose()
        await _drain_cleanup()
        source.release.set()
        await asyncio.sleep(0)
        return source

    source = asyncio.run(run())
    assert source.closed and not source.cancelled
    assert source.produced == 1


def test_heartbeat_cancels_the_source_when_the_consumer_is_cancelled_mid_await():
    """Cancelled while the source is awaiting its next item: the source is cancelled."""

    async def consume(stream):
        async for _ in stream:
            pass

    async def run():
        source = GatedSource()
        task = asyncio.create_task(consume(with_heartbeat(source(), interval_s=10)))
        await source.waiting.wait()  # The source is now blocked inside its await.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain_cleanup()
        source.release.set()
        await asyncio.sleep(0)
        return source

    source = asyncio.run(run())
    assert source.cancelled and source.closed
    assert source.produced == 1


def test_heartbeat_stops_a_running_source_when_the_consumer_is_cancelled():
    """Whichever point the cancel lands at, the source ends up closed and stops producing."""

    async def consume(stream):
        async for _ in stream:
            pass

    async def run():
        source = CountingSource(delay=0.001)
        task = asyncio.create_task(consume(with_heartbeat(source(), interval_s=10)))
        while source.produced < 5:
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain_cleanup()
        produced = source.produced
        await asyncio.sleep(0.05)
        return source, produced

    source, produced = asyncio.run(run())
    assert source.closed
    assert source.produced == produced


def test_heartbeat_polls_for_disconnect_and_stops_the_source():
    """Fallback for servers that never cancel the response on disconnect."""
    source = CountingSource(delay=1.0)

    async def run():
        started = time.monotonic()

        async def is_disconnected():
            return time.monotonic() - started > 0.1

        out = await _collect(
            with_heartbeat(
                source(), interval_s=10, is_disconnected=is_disconnected, poll_interval_s=0.05
            )
        )
        await _drain_cleanup()
        produced = source.produced
        await asyncio.sleep(0.3)
        return out, produced

    out, produced = asyncio.run(run())
    assert out == []  # Disconnected long before the first 1 s token.
    assert source.cancelled and source.closed
    assert source.produced == produced == 0


def test_heartbeat_polls_for_disconnect_even_while_events_flow():
    """A fast token stream after a disconnect must not starve the fallback poll."""
    source = CountingSource(delay=0.005)
    polls = []

    async def run():
        started = time.monotonic()

        async def is_disconnected():
            polls.append(time.monotonic() - started)
            return time.monotonic() - started > 0.2

        out = await _collect(
            with_heartbeat(
                source(), interval_s=10, is_disconnected=is_disconnected, poll_interval_s=0.25
            ),
            limit=2000,  # ~10 s of events: reached only if the poll never stops the stream.
        )
        ended = time.monotonic() - started
        await _drain_cleanup()
        produced = source.produced
        await asyncio.sleep(0.2)
        return out, ended, produced

    out, ended, produced = asyncio.run(run())
    assert len(out) > 10, "events were relayed before the disconnect"
    assert len(out) < 2000
    # Poll interval 0.25 s: expected ~0.25-0.5 s; without the time-based poll it runs ~10 s.
    assert ended < 1.5, f"stream kept running {ended:.2f}s after a 0.2s disconnect"
    assert polls and polls[-1] < 1.5
    assert source.closed
    assert source.produced == produced


def test_heartbeat_relays_source_errors():
    async def failing():
        yield "event: a\n\n"
        raise RuntimeError("boom")

    async def run():
        return [item async for item in with_heartbeat(failing(), interval_s=10)]

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run())


# --- /api/ask with a slow fake provider ---------------------------------------------


class SlowLLM:
    model_id = "slow-llm"

    def __init__(self, delay=0.05, count=40):
        self.delay = delay
        self.count = count
        self.produced = 0
        self.closed = False

    async def generate(self, prompt, *, max_tokens, system=None):
        try:
            for index in range(self.count):
                await asyncio.sleep(self.delay)
                self.produced += 1
                yield f" w{index}"
        finally:
            self.closed = True


class SlowWorkerRedis(MemoryRedis):
    """Publishes the worker's retrieval result after a delay, like a busy worker."""

    def __init__(self, delay=0.0, outcome="retrieval"):
        super().__init__(outcome)
        self.delay = delay

    async def xadd(self, stream, fields, **kwargs):
        async def later():
            await asyncio.sleep(self.delay)
            await MemoryRedis.xadd(self, stream, fields, **kwargs)

        self.channel = f"trace:{fields['request_id']}"
        asyncio.get_running_loop().create_task(later())


class RecordingAnswerCache:
    def __init__(self):
        self.puts = 0

    async def get(self, *args):
        return None

    async def put(self, *args):
        self.puts += 1


class CountingBudget:
    def __init__(self):
        self.reservations = 0

    async def reserve(self, *, units=4):
        self.reservations += 1
        return True


class AllowAllRate:
    async def allow(self, client_hash):
        return True, 0


@pytest.fixture
def harness(monkeypatch):
    from services.glassbox.api import ask

    state = {
        "redis": SlowWorkerRedis(),
        "llm": SlowLLM(),
        "cache": RecordingAnswerCache(),
        "budget": CountingBudget(),
        "saved": [],
    }
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr(ask.redis, "from_url", lambda url: state["redis"])
    monkeypatch.setattr(ask, "get_llm_provider", lambda: state["llm"])
    monkeypatch.setattr(ask, "get_answer_cache", lambda client: state["cache"])
    monkeypatch.setattr(ask, "get_daily_budget", lambda client: state["budget"])
    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: AllowAllRate())
    monkeypatch.setattr(ask, "_save_query", lambda **kwargs: state["saved"].append(kwargs))
    return state


def test_ask_stream_pings_during_retrieval_wait_and_token_loop(harness, monkeypatch):
    from services.glassbox.api import ask

    monkeypatch.setattr(ask, "HEARTBEAT_INTERVAL_S", 0.05)
    harness["redis"].delay = 0.3
    harness["llm"].delay = 0.12
    harness["llm"].count = 4
    response = TestClient(app).post("/api/ask", json=BODY)
    frames = response.text.strip().split("\n\n")
    kinds = [
        "ping" if frame == PING_FRAME.strip() else frame.splitlines()[0].removeprefix("event: ")
        for frame in frames
    ]
    retrieval = kinds.index("retrieval")
    first_token = kinds.index("token")
    assert "ping" in kinds[:retrieval], "no ping while waiting for the worker"
    assert "ping" in kinds[first_token : kinds.index("done")], "no ping between slow tokens"
    stream = events(response)
    names = [name for name, _ in stream]
    assert names.index("retrieval") < names.index("token") < names.index("done")
    assert "".join(data["text"] for name, data in stream if name == "token") == " w0 w1 w2 w3"
    assert stream[-1] == ("done", stream[-1][1]) and stream[-1][1]["mode"] == "full"
    assert harness["saved"][0]["mode"] == "full"


def _asgi_ask(body, *, disconnect_when):
    """Drive the real ASGI app, disconnecting once ``disconnect_when(sent_text)`` is true."""

    async def run():
        disconnected = asyncio.Event()
        sent = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                sent.append(message.get("body", b"").decode())
                if disconnect_when("".join(sent)):
                    disconnected.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ask",
            "raw_path": b"/api/ask",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        # Cleanup (cancelling generation, logging the stop) runs in its own task.
        await _drain_cleanup()
        return "".join(sent)

    return run


def test_disconnect_mid_answer_stops_generation_and_logs_stopped(harness):
    llm = harness["llm"]

    async def scenario():
        text = await _asgi_ask(
            json.dumps(BODY).encode(),
            disconnect_when=lambda sent: sent.count("event: token") >= 3,
        )()
        produced = llm.produced
        await asyncio.sleep(0.3)
        return text, produced

    text, produced = asyncio.run(scenario())
    assert "event: done" not in text
    assert llm.closed
    assert produced < llm.count
    assert llm.produced == produced, "the LLM kept being iterated after the client left"
    [saved] = harness["saved"]
    assert saved["mode"] == "stopped"
    assert saved["tokens_in"] > 0
    assert 3 <= saved["tokens_out"] <= produced
    assert saved["chunks"][0].chunk_id == 42
    # The reserved answer slot stays spent; a stopped answer is never cached.
    assert harness["budget"].reservations == 1
    assert harness["cache"].puts == 0


def test_disconnect_during_retrieval_logs_stopped_without_spending_budget(harness):
    harness["redis"].delay = 1.0

    async def scenario():
        return await _asgi_ask(
            json.dumps(BODY).encode(),
            disconnect_when=lambda sent: '"node":"queue","status":"end"' in sent,
        )()

    text = asyncio.run(scenario())
    assert "event: retrieval" not in text
    assert harness["llm"].produced == 0
    [saved] = harness["saved"]
    assert saved["mode"] == "stopped"
    assert saved["tokens_in"] == 0 and saved["tokens_out"] == 0
    assert saved["chunks"] == []
    assert harness["budget"].reservations == 0


def test_closing_the_stream_generator_mid_answer_closes_the_provider(harness):
    """GeneratorExit path: the stream is closed while suspended at a token yield."""
    from services.glassbox.api import ask

    llm = harness["llm"]

    async def scenario():
        stream = ask._stream(ask.AskRequest(**BODY), "01TESTREQUEST0000000000000", 0, "hash")
        tokens = 0
        async for item in stream:
            if item.startswith("event: token"):
                tokens += 1
                if tokens == 2:
                    break
        await stream.aclose()
        produced = llm.produced
        await asyncio.sleep(0.2)
        return produced

    produced = asyncio.run(scenario())
    assert llm.closed
    assert llm.produced == produced == 2
    [saved] = harness["saved"]
    assert saved["mode"] == "stopped"
    assert saved["tokens_out"] == 2
    assert harness["cache"].puts == 0


def test_client_leaving_after_an_error_is_not_logged_as_stopped(harness, monkeypatch):
    from services.glassbox.api import ask

    class Deny:
        async def allow(self, client_hash):
            return False, 30

    monkeypatch.setattr(ask, "get_rate_limiter", lambda client: Deny())

    async def scenario():
        stream = ask._stream(ask.AskRequest(**BODY), "01TESTREQUEST0000000000000", 0, "hash")
        async for item in stream:
            if item.startswith("event: error"):
                break
        await stream.aclose()

    asyncio.run(scenario())
    assert harness["saved"] == []


def test_completed_answer_is_logged_once_as_full(harness):
    harness["llm"].delay = 0
    harness["llm"].count = 3
    stream = events(TestClient(app).post("/api/ask", json=BODY))
    assert stream[-1][0] == "done"
    assert [saved["mode"] for saved in harness["saved"]] == ["full"]
    assert harness["cache"].puts == 1


# --- MySQL: the stopped mode (0004) and ttft_ms (0005) are storable ----------------


def test_stopped_query_row_is_stored(monkeypatch):
    from services.glassbox.db.session import create_db_engine, get_session_factory

    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", TEST_MYSQL_PORT)
    monkeypatch.setenv("MYSQL_USER", "glassbox")
    monkeypatch.setenv("MYSQL_PASSWORD", "glassbox")
    monkeypatch.setenv("MYSQL_DATABASE", "glassbox")
    get_session_factory.cache_clear()
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            mode = connection.exec_driver_sql("SHOW COLUMNS FROM queries LIKE 'mode'").one()[1]
            has_ttft = bool(
                connection.exec_driver_sql("SHOW COLUMNS FROM queries LIKE 'ttft_ms'").all()
            )
    except Exception as error:  # pragma: no cover - depends on local services
        engine.dispose()
        get_session_factory.cache_clear()
        pytest.skip(f"MySQL unavailable: {error}")
    if "stopped" not in str(mode) or not has_ttft:
        engine.dispose()
        get_session_factory.cache_clear()
        message = "MySQL queries not migrated to 0005 (missing mode 'stopped' or ttft_ms)"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    from services.glassbox.db.models import Query

    request_id = "01STOPPEDQUERYTEST00000000"
    try:
        ORIGINAL_SAVE_QUERY(
            request_id=request_id,
            request=_ask_module.AskRequest(**BODY),
            chunks=[],
            timings={"embed": 1},
            total_ms=12,
            tokens_in=30,
            tokens_out=4,
            turn_index=0,
            rewritten_query=None,
            ttft_ms=321,
            mode="stopped",
        )
        with get_session_factory()() as session:
            row = session.scalar(select(Query).where(Query.request_id == request_id))
            assert row is not None
            assert row.mode == "stopped"
            assert row.tokens_out == 4
            assert row.ttft_ms == 321
    finally:
        with get_session_factory()() as session:
            session.execute(delete(Query).where(Query.request_id == request_id))
            session.commit()
        engine.dispose()
        get_session_factory.cache_clear()


def test_token_counts_prefer_measured_usage_and_otherwise_estimate():
    from services.glassbox.api.ask import _token_counts

    assert _token_counts("a b c", "x y", {"inputTokens": 40, "outputTokens": 7}) == (40, 7)
    # A stopped Bedrock stream never reaches its usage metadata: estimate.
    assert _token_counts("a b c", "x y", {}) == (3, 2)


def test_completed_answer_logs_provider_reported_usage(harness):
    class UsageLLM(SlowLLM):
        reports_usage = True

        async def generate(self, prompt, *, max_tokens, system=None, usage=None):
            async for part in super().generate(prompt, max_tokens=max_tokens, system=system):
                yield part
            usage.update(inputTokens=1234, outputTokens=56)

    harness["llm"] = UsageLLM(delay=0, count=3)
    stream = events(TestClient(app).post("/api/ask", json=BODY))
    done = stream[-1][1]
    assert (done["tokens_in"], done["tokens_out"]) == (1234, 56)
    assert (harness["saved"][0]["tokens_in"], harness["saved"][0]["tokens_out"]) == (1234, 56)


# --- Time to first token (DESIGN-002 §7.5, §9.3; migration 0005) -------------------


@pytest.fixture
def fake_clock(monkeypatch):
    """Replaces ask.elapsed_ms: every request-relative time reads clock["now"]."""
    from services.glassbox.api import ask

    clock = {"now": 10}
    monkeypatch.setattr(ask, "elapsed_ms", lambda request_start_ts: clock["now"])
    return clock


class ClockedLLM:
    """Advances the fake clock before each part, so TTFT is known exactly."""

    model_id = "clocked-llm"

    def __init__(self, clock, parts):
        self.clock = clock
        self.parts = parts

    async def generate(self, prompt, *, max_tokens, system=None):
        for at_ms, text in self.parts:
            self.clock["now"] = at_ms
            yield text


def _run_stream(*, stop_after_tokens=None):
    from services.glassbox.api import ask

    async def scenario():
        stream = ask._stream(ask.AskRequest(**BODY), "01TESTREQUEST0000000000000", 0, "hash")
        tokens = 0
        async for item in stream:
            if item.startswith("event: token"):
                tokens += 1
                if tokens == stop_after_tokens:
                    break
        await stream.aclose()

    asyncio.run(scenario())


def test_ttft_is_the_first_non_empty_token_not_the_whole_answer(harness, fake_clock):
    # An empty part sends no frame; the first answer text goes out at 740 ms.
    harness["llm"] = ClockedLLM(fake_clock, [(500, ""), (740, "Basel"), (900, " builds")])
    _run_stream()
    [saved] = harness["saved"]
    assert saved["mode"] == "full"
    assert saved["ttft_ms"] == 740
    assert saved["total_ms"] == 900


def test_ttft_is_recorded_for_an_answer_stopped_after_its_first_token(harness, fake_clock):
    harness["llm"] = ClockedLLM(fake_clock, [(610, "Basel"), (800, " builds"), (950, " x")])
    _run_stream(stop_after_tokens=1)
    [saved] = harness["saved"]
    assert saved["mode"] == "stopped"
    assert saved["ttft_ms"] == 610


def test_ttft_is_null_when_stopped_before_any_token(harness):
    harness["redis"].delay = 1.0

    asyncio.run(
        _asgi_ask(
            json.dumps(BODY).encode(),
            disconnect_when=lambda sent: '"node":"queue","status":"end"' in sent,
        )()
    )
    [saved] = harness["saved"]
    assert saved["mode"] == "stopped"
    assert saved["ttft_ms"] is None


def test_ttft_is_null_for_retrieval_only(harness, fake_clock):
    class NoBudget:
        async def reserve(self, *, units=4):
            return False

    harness["budget"] = NoBudget()
    _run_stream()
    [saved] = harness["saved"]
    assert saved["mode"] == "retrieval_only"
    assert saved["ttft_ms"] is None


def test_ttft_is_measured_for_an_answer_cache_hit(harness, fake_clock):
    class HitCache(RecordingAnswerCache):
        async def get(self, *args):
            fake_clock["now"] = 140
            return {
                "answer": "Cached answer.",
                "chunks": [
                    {
                        "n": 1,
                        "chunk_id": 42,
                        "text": "source text",
                        "source_path": "docs/DESIGN.md",
                        "title": "Design",
                        "score": 0.9,
                    }
                ],
            }

    harness["cache"] = HitCache()
    _run_stream()
    [saved] = harness["saved"]
    assert saved["cache_status"] == "answer_hit"
    assert saved["ttft_ms"] == 140


def test_ttft_is_written_to_the_query_row(monkeypatch):
    """_save_query passes ttft_ms through to the ORM row (no database needed)."""
    from services.glassbox.api import ask

    added = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def add(self, row):
            added.append(row)

        def commit(self):
            pass

    monkeypatch.setattr(ask, "get_session_factory", lambda: lambda: Session())
    common = dict(
        request=ask.AskRequest(**BODY),
        chunks=[],
        timings={},
        total_ms=900,
        tokens_in=1,
        tokens_out=1,
        turn_index=0,
        rewritten_query=None,
    )
    ORIGINAL_SAVE_QUERY(request_id="01A", ttft_ms=740, **common)
    ORIGINAL_SAVE_QUERY(request_id="01B", mode="retrieval_only", **common)
    assert [row.ttft_ms for row in added] == [740, None]
