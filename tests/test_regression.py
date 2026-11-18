from argparse import ArgumentTypeError
from threading import Event

import pytest

from runner.regression import Case, collect_cases, positive_int


class SandboxProcess:
    def __init__(self):
        self.exited = Event()

    def poll(self):
        return 1 if self.exited.is_set() else None


class Processes:
    def __init__(self):
        self.stopped = Event()

    def stop(self):
        self.stopped.set()


def outcome(case, verdict="passed", status="complete"):
    return {"index": case.index, "scenario": case.scenario, "verdict": verdict, "status": status}


def test_refills_available_slot_after_failed_case_with_duplicate_names():
    sandbox, processes = SandboxProcess(), Processes()
    first_started, third_started = Event(), Event()
    results = []
    cases = [Case(1, "order"), Case(2, "quote"), Case(3, "order"), Case(4, "s0-time")]

    def collect(case):
        if case.index == 1:
            first_started.set()
            assert third_started.wait(3)
        if case.index == 2:
            assert first_started.wait(3)
            return outcome(case, "failed")
        if case.index == 3:
            third_started.set()
            return outcome(case, "unavailable", "setup_error")
        return outcome(case)

    collect_cases(cases, jobs=2, collect=collect, sandbox=sandbox, sandbox_log="sandbox.log",
                  publish=results.append, processes=processes)
    assert results[0] == outcome(cases[1], "failed")
    assert {row["index"]: row["verdict"] for row in results} == {
        1: "passed", 2: "failed", 3: "unavailable", 4: "passed",
    }
    assert not processes.stopped.is_set()


@pytest.mark.parametrize("sandbox_exit", [True, False])
def test_batch_stop_preserves_started_outcomes_without_dispatching_remaining_cases(sandbox_exit):
    sandbox, processes = SandboxProcess(), Processes()
    first_started = Event()
    results = []

    def collect(case):
        if case.index == 1:
            first_started.set()
            assert processes.stopped.wait(3)
            return outcome(case, "unavailable", "execution_error")
        assert case.index == 2
        assert first_started.wait(3)
        if sandbox_exit:
            sandbox.exited.set()
            assert processes.stopped.wait(3)
            return outcome(case)
        return outcome(case, "unavailable", "interrupted")

    with pytest.raises(RuntimeError if sandbox_exit else KeyboardInterrupt):
        collect_cases([Case(index, "order") for index in range(1, 4)], jobs=2, collect=collect,
                      sandbox=sandbox, sandbox_log="sandbox.log", publish=results.append, processes=processes)
    assert {row["index"] for row in results} == {1, 2}


@pytest.mark.parametrize("value", ["0", "-2", "1.5"])
def test_invalid_job_limits(value):
    with pytest.raises(ValueError if "." in value else ArgumentTypeError):
        positive_int(value)


def test_workers_bind_distinct_credentials_and_publish_a_setup_exception(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from functools import partial
    from threading import Lock

    from runner import regression

    launched = {}
    capture = {}
    lock = Lock()

    @contextmanager
    def launch(command, *, cwd, env, log, processes):
        with lock:
            index = len(launched) + 1
            iris = f"http://127.0.0.1:{10000 + index}"
            launched[iris] = (env, cwd, log)
        assert "--spring.sql.init.mode=never" in command
        yield iris

    def run_case(task, *, iris_url, sandbox_url, mcp_token, episode_id, **kwargs):
        env, work, log = launched[iris_url.removesuffix("/api")]
        assert env["MCD_MCP_URL"] == f"{sandbox_url}/mcp/{episode_id}/1"
        assert env["MCD_MCP_TOKEN"] == mcp_token
        assert work.is_dir()
        with lock:
            capture[log.stem] = (episode_id, mcp_token, work)
        if log.stem.startswith("02-"):
            raise ValueError("controlled runtime startup error")
        return {"provenance": {}}

    def save_case(directory, evidence):
        directory.mkdir()
        (directory / "evidence.json").write_text("{}")

    monkeypatch.setattr(regression, "running", launch)
    monkeypatch.setattr(regression, "wait_ready", lambda iris, *args: iris)
    monkeypatch.setattr(regression, "run_case", run_case)
    monkeypatch.setattr(regression, "save", save_case)
    monkeypatch.setattr(regression, "summarize", lambda evidence: {
        "verdict": "passed", "status": "complete", "failedChecks": [], "errors": {}, "diagnostics": {},
    })
    cases = [Case(index, "order") for index in range(1, 4)]
    processes, results = Processes(), []
    collect = partial(regression.collect_case, batch=tmp_path, jar=tmp_path / "iris.jar", iris_env={},
                      sandbox_url="http://127.0.0.1:8080", admin_token="admin", seed=0, key="",
                      provenance={}, processes=processes)
    collect_cases(cases, jobs=2, collect=collect, sandbox=SandboxProcess(), sandbox_log="sandbox.log",
                  publish=results.append, processes=processes)
    assert len(capture) == 3
    assert all(len(set(group)) == 3 for group in zip(*capture.values()))
    assert all(not work.exists() for _, _, work in capture.values())
    assert {row["index"]: row["verdict"] for row in results} == {1: "passed", 2: "unavailable", 3: "passed"}
    assert sorted(path.parent.name for path in tmp_path.glob("*/evidence.json")) == ["01-order", "03-order"]
