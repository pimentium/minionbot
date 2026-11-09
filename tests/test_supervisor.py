import pytest

from minionbot.supervisor import RunForever


class Harness:
    def __init__(self, outcomes, runtimes=None):
        self.outcomes = list(outcomes)
        self.calls = []
        self.sleeps = []
        self.now = 0.0
        self.runtimes = list(runtimes or [])

    def Run(self, restartCount, lastError):
        self.calls.append((restartCount, lastError))
        self.now += self.runtimes.pop(0) if self.runtimes else 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome

    def Sleep(self, seconds):
        self.sleeps.append(seconds)

    def Go(self, **kwargs):
        RunForever(self.Run, sleep=self.Sleep, clock=lambda: self.now, **kwargs)


def test_clean_exit_is_not_restarted():
    harness = Harness([None])
    harness.Go()
    assert harness.calls == [(0, "")] and harness.sleeps == []


def test_restarts_after_crash_and_passes_reason():
    harness = Harness([RuntimeError("boom"), ValueError("again"), None])
    harness.Go()
    assert harness.calls == [(0, ""), (1, "RuntimeError: boom"), (2, "ValueError: again")]
    assert harness.sleeps == [2, 4]


def test_backoff_is_capped():
    harness = Harness([RuntimeError("x")] * 10 + [None])
    harness.Go(maxBackoff=30)
    assert max(harness.sleeps) == 30 and harness.sleeps[:4] == [2, 4, 8, 16]


def test_backoff_resets_after_stable_run():
    harness = Harness([RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), None], runtimes=[1, 1, 100, 1])
    harness.Go(stableSeconds=60)
    assert harness.sleeps == [2, 4, 2]


def test_fatal_error_is_not_retried():
    harness = Harness([KeyError("bad token")])
    with pytest.raises(KeyError):
        harness.Go(isFatal=lambda error: isinstance(error, KeyError))
    assert len(harness.calls) == 1 and harness.sleeps == []


def test_keyboard_interrupt_and_system_exit_pass_through():
    for exit_ in (KeyboardInterrupt(), SystemExit(0)):
        harness = Harness([exit_])
        with pytest.raises(type(exit_)):
            harness.Go()
        assert harness.sleeps == []


def test_error_text_is_scrubbed():
    harness = Harness([RuntimeError("token SECRET leaked"), None])
    harness.Go(scrub=lambda text: text.replace("SECRET", "***"))
    assert harness.calls[1][1] == "RuntimeError: token *** leaked"
