"""Build Iris once and run the scripted Sandbox scenarios against fresh Iris processes."""

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from functools import partial
from pathlib import Path
import re
from secrets import token_hex
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic, sleep

import httpx
import psycopg

from monitor.qwen import api_key
from runner.__main__ import run_case, save, source_revision
from runner.domains import get_domain, task_domains
from runner.report import save_report, summarize
from assertions.load import load_checks
from runner.tasks import TASKS_DIR, load_tasks
from sandbox.config import load_local_env
from sandbox.episodes import utc_now


SANDBOX_ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks()


def suite_tasks(tasks_dir: Path) -> dict:
    """回归要跑的任务。任务集目录可以自带 checks.py，与 runner.batch 一样先加载。"""
    load_checks(Path(tasks_dir) / "checks.py")
    return load_tasks(tasks_dir)
SANDBOX_PORT = re.compile(r"Uvicorn running on http://127\.0\.0\.1:(\d+)")
IRIS_PORT = re.compile(r"Tomcat started on port (\d+)")
IRIS_PROBE = "/api/agent/state?userId=regression-health&sessionId=regression-health"


def signal_process(process, sig):
    if process.poll() is None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


class CaseProcesses:
    """Allow the coordinator to release workers blocked in Iris HTTP calls."""

    def __init__(self):
        self._lock = Lock()
        self._active = set()
        self._stopped = False

    def start(self, command, **kwargs):
        with self._lock:
            if self._stopped:
                raise RuntimeError("Batch stopped before Iris could start.")
            process = subprocess.Popen(command, start_new_session=True, **kwargs)
            self._active.add(process)
            return process

    def remove(self, process):
        with self._lock:
            self._active.discard(process)

    def stop(self):
        with self._lock:
            self._stopped = True
            for process in self._active:
                # A graceful Spring shutdown can wait for the request we need to unblock.
                signal_process(process, signal.SIGKILL)


@contextmanager
def running(command, *, cwd: Path, env: dict, log: Path, processes=None):
    with log.open("wb") as output:
        launch = {"cwd": cwd, "env": env, "stdout": output, "stderr": subprocess.STDOUT}
        process = (processes.start(command, **launch) if processes is not None
                   else subprocess.Popen(command, start_new_session=True, **launch))
        try:
            yield process
        finally:
            try:
                signal_process(process, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    signal_process(process, signal.SIGKILL)
                    process.wait()
            finally:
                if processes is not None:
                    processes.remove(process)


def wait_ready(process, log: Path, pattern: re.Pattern, path: str, *, headers=None) -> str:
    deadline = monotonic() + 90
    address = None
    with httpx.Client(timeout=1, trust_env=False) as client:
        while monotonic() < deadline and process.poll() is None:
            if address is None:
                match = pattern.search(log.read_text(encoding="utf-8", errors="replace"))
                if match:
                    address = f"http://127.0.0.1:{match[1]}"
            if address is not None:
                try:
                    if client.get(address + path, headers=headers).status_code == 200:
                        return address
                except httpx.TransportError:
                    pass
            sleep(0.2)
    raise RuntimeError(f"Service did not become ready. See {log}.")


def check_dependency(label: str, port: int) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except OSError as error:
        raise RuntimeError(f"{label} is unavailable at 127.0.0.1:{port}.") from error


def build_iris(root: Path, log: Path) -> Path:
    with running(["mvn", "-B", "-pl", ":iris-app", "-am", "-DskipTests", "package"],
                 cwd=root, env=os.environ, log=log) as process:
        code = process.wait(timeout=900)
    jars = sorted((root / "iris-app" / "target").glob("iris-app-*.jar"))
    if code or len(jars) != 1:
        raise RuntimeError(f"Could not build a unique Iris application JAR. See {log}.")
    return jars[0]


def initialize_schema(args, schema: Path) -> None:
    # Serialize schema creation even if two batches target the same test database.
    # The transaction lock is automatically released on success, rollback, or disconnect.
    with psycopg.connect(host="127.0.0.1", port=args.pg_port, dbname=args.database,
                         user=args.db_user, password=os.environ.get("IRIS_REGRESSION_DB_PASSWORD", ""),
                         connect_timeout=3, options="-c statement_timeout=30000") as connection:
        connection.execute("SELECT pg_advisory_xact_lock(624769897192753301)")
        connection.execute(schema.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Case:
    index: int
    scenario: str
    spec: object = field(default=None, compare=False)   # 本用例的任务定义；未给出时按名字查根目录的任务

    @property
    def label(self) -> str:
        return f"{self.index:02d}-{self.scenario}"


def collect_case(case: Case, *, batch: Path, jar: Path, iris_env: dict, sandbox_url: str,
                 admin_token: str, seed: int, key: str, provenance: dict, processes: CaseProcesses) -> dict:
    started = utc_now()
    episode_id, mcp_token = token_hex(16), token_hex(24)
    task = case.spec if case.spec is not None else TASKS[case.scenario]
    mode = "active" if task.script.interactions else "off"
    domains = [get_domain(name) for name in task_domains(task)]
    log = batch / f"{case.label}.log"
    result = {"index": case.index, "scenario": case.scenario, "episodeId": episode_id,
              "startedAt": started, "log": log.name}
    try:
        with TemporaryDirectory(prefix="iris-regression-") as work:
            # 连接方式由领域决定：麦乐送注入令牌，微博先在工作目录写授权文件。
            # 多领域任务用按领域的端点，每个助手只看到本领域的工具。
            case_env = dict(iris_env)
            for domain in domains:
                case_env.update(domain.connect_iris(sandbox_url, episode_id, mcp_token, Path(work),
                                                    scoped=len(domains) > 1))
            with running(
                ["java", "-Dfile.encoding=UTF-8", "-jar", str(jar), "--server.address=127.0.0.1",
                 "--server.port=0", "--spring.sql.init.mode=never", "--iris.memory.daily-note.cron=-"],
                cwd=Path(work), env=case_env, log=log, processes=processes,
            ) as iris:
                iris_url = wait_ready(iris, log, IRIS_PORT, IRIS_PROBE)
                evidence = run_case(task, iris_url=iris_url + "/api", sandbox_url=sandbox_url,
                                    admin_token=admin_token, mcp_token=mcp_token, episode_id=episode_id,
                                    seed=seed, monitor_mode=mode, key=key if mode == "active" else "")
                evidence["provenance"].update(provenance)
                save(batch / case.label, evidence)
                result.update(evidence=f"{case.label}/evidence.json", summary=f"{case.label}/summary.json")
                summary = summarize(evidence)
                result.update({field: summary[field] for field in
                               ("verdict", "status", "failedChecks", "errors", "diagnostics")})
    except KeyboardInterrupt:
        result.update(verdict="unavailable", status="interrupted")
    except Exception as error:
        result.update(verdict="unavailable", status="setup_error",
                      errors={"setup": f"{type(error).__name__}: {error}"})
    return {**result, "finishedAt": utc_now()}


def collect_cases(cases, *, jobs, collect, sandbox, sandbox_log, publish, processes,
                  interrupted=lambda: False):
    remaining = iter(cases)
    pending = {}

    def record(future):
        case = pending.pop(future)
        result = future.result()
        publish(result)
        checks = " ".join(result.get("failedChecks", []))
        print(f"{case.label}: {result['verdict']} {checks}".rstrip(), flush=True)
        return result["status"] == "interrupted"

    def check_running():
        if interrupted():
            raise KeyboardInterrupt
        if sandbox.poll() is not None:
            raise RuntimeError(f"Sandbox stopped during the suite. See {sandbox_log}.")

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        try:
            while True:
                check_running()
                while len(pending) < jobs:
                    case = next(remaining, None)
                    if case is None:
                        break
                    check_running()
                    print(f"Running {case.label}", flush=True)
                    pending[executor.submit(collect, case)] = case
                if not pending:
                    return
                done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    if record(future):
                        raise KeyboardInterrupt
        except BaseException:
            processes.stop()
            # Keep the Sandbox up while released workers close their episodes and save evidence.
            for future in list(pending):
                if future.cancel():
                    pending.pop(future)
                else:
                    record(future)
            raise


def run_suite(args, batch: Path, key: str) -> int:
    started = monotonic()
    iris_root = args.iris_repo.resolve()
    source = source_revision()
    admin_token = token_hex(24)
    env = dict(os.environ, SANDBOX_ADMIN_TOKEN=admin_token)
    iris_env = dict(env, DB_URL=f"jdbc:postgresql://127.0.0.1:{args.pg_port}/{args.database}",
                    DB_USERNAME=args.db_user, DB_PASSWORD=os.environ.get("IRIS_REGRESSION_DB_PASSWORD", ""),
                    IRIS_REDIS_HOST="127.0.0.1", IRIS_REDIS_PORT=str(args.redis_port),
                    IRIS_REDIS_PASSWORD=os.environ.get("IRIS_REGRESSION_REDIS_PASSWORD", ""),
                    SPRING_DATA_REDIS_DATABASE=str(args.redis_db), IRIS_AUTO_REVIEW="false",
                    IRIS_SKILLS_DIR=str(iris_root / "skills"),
                    SPRING_CONFIG_IMPORT=f"optional:{(iris_root / '.env').as_uri()}[.properties]")
    tasks = getattr(args, "tasks", None) or TASKS
    cases = [Case(index, scenario, tasks[scenario]) for index, scenario in enumerate(args.scenarios, 1)]
    report = {"sandboxSource": source, "seed": args.seed, "jobs": args.jobs,
              "startedAt": utc_now(), "requestedScenarios": args.scenarios, "cases": [], "status": "running"}
    report_path = batch / "summary.json"

    def publish(result):
        report["cases"].append(result)
        report["cases"].sort(key=lambda case: case["index"])
        save_report(report_path, report)

    save_report(report_path, report)
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=iris_root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=iris_root, text=True).strip())
        report.update(irisRevision=revision, irisWorkingTreeDirty=dirty)
        save_report(report_path, report)
        check_dependency("PostgreSQL", args.pg_port)
        check_dependency("Redis", args.redis_port)
        jar = build_iris(iris_root, batch / "build.log")
        initialize_schema(args, iris_root / "docs/schema.sql")
        # Force this on the JVM command line so even the imported Iris config cannot run DDL.
        sandbox_log = batch / "sandbox.log"
        processes = CaseProcesses()
        with running([sys.executable, "-m", "sandbox.server", "--port", "0"],
                     cwd=SANDBOX_ROOT, env=env, log=sandbox_log) as sandbox:
            sandbox_url = wait_ready(sandbox, sandbox_log, SANDBOX_PORT, "/admin/catalog",
                                     headers={"Authorization": f"Bearer {admin_token}"})
            collect = partial(collect_case, batch=batch, jar=jar, iris_env=iris_env, sandbox_url=sandbox_url,
                              admin_token=admin_token, seed=args.seed, key=key,
                              provenance={"irisRevision": revision, "irisWorkingTreeDirty": dirty,
                                          "runnerSource": source}, processes=processes)
            interrupted = False

            def handle_interrupt(signum, frame):
                nonlocal interrupted
                interrupted = True

            # Let the coordinator save results even when SIGINT arrives during submission or publication.
            previous = signal.signal(signal.SIGINT, handle_interrupt)
            try:
                collect_cases(cases, jobs=args.jobs, collect=collect, sandbox=sandbox, sandbox_log=sandbox_log,
                              publish=publish, processes=processes, interrupted=lambda: interrupted)
            finally:
                signal.signal(signal.SIGINT, previous)
        verdicts = {case["verdict"] for case in report["cases"]}
        report["status"] = "failed" if "failed" in verdicts else "unavailable" if "unavailable" in verdicts else "passed"
        return 0 if report["status"] == "passed" else 1
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        return 130
    except (OSError, RuntimeError, subprocess.SubprocessError, psycopg.Error) as error:
        report.update(status="unavailable", setupError=str(error))
        print(error, file=sys.stderr)
        return 1
    finally:
        report.update(finishedAt=utc_now(), wallSeconds=round(monotonic() - started, 3))
        save_report(report_path, report)
        print(f"Regression summary: {report_path}", flush=True)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", metavar="SCENARIO", help="task IDs in input order (default: all tasks); repeats are allowed")
    iris_repo = os.environ.get("IRIS_REGRESSION_REPO")
    parser.add_argument("--iris-repo", type=Path, default=SANDBOX_ROOT / iris_repo if iris_repo else None,
                        help="Iris checkout (default: IRIS_REGRESSION_REPO, relative to the Sandbox root)")
    parser.add_argument("--pg-port", type=int, default=os.environ.get("IRIS_REGRESSION_PG_PORT", "54328"))
    parser.add_argument("--database", default=os.environ.get("IRIS_REGRESSION_DB_NAME", "iris_eval"))
    parser.add_argument("--db-user", default=os.environ.get("IRIS_REGRESSION_DB_USER", "postgres"))
    parser.add_argument("--redis-port", type=int, default=os.environ.get("IRIS_REGRESSION_REDIS_PORT", "59000"))
    parser.add_argument("--redis-db", type=int,
                        default=os.environ.get("IRIS_REGRESSION_REDIS_DB", "13"), metavar="0..15")
    parser.add_argument("--jobs", type=positive_int, default=1, help="maximum concurrent task executions (default: 1)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("runs/regression"))
    parser.add_argument("--tasks-dir", type=Path, default=TASKS_DIR,
                        help="task spec directory, for example tasks/20261124T0900 (default: tasks/)")
    args = parser.parse_args()
    args.tasks = suite_tasks(args.tasks_dir)
    args.scenarios = args.scenarios or list(args.tasks)
    for scenario in args.scenarios:
        if scenario not in args.tasks:
            parser.error(f"unknown task {scenario!r}; choose from {', '.join(args.tasks)}")
    if args.iris_repo is None or not all((args.iris_repo / name).is_file() for name in ("docs/schema.sql", "pom.xml")):
        parser.error("set --iris-repo or IRIS_REGRESSION_REPO to an Iris checkout")
    if not shutil.which("java") or not shutil.which("mvn"):
        parser.error("Java and Maven must be available on PATH")
    if any(not 1 <= port <= 65535 for port in (args.pg_port, args.redis_port)):
        parser.error("PostgreSQL and Redis ports must be between 1 and 65535")
    if not 0 <= args.redis_db <= 15:
        parser.error("Redis database must be between 0 and 15")
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.database):
        parser.error("--database must contain only letters, digits, or underscores")
    key = api_key()
    if any(args.tasks[case].script.interactions for case in args.scenarios) and not key:
        parser.error("set MONITOR_DASHSCOPE_API_KEY or DASHSCOPE_API_KEY for the clarification scenarios")
    batch = (args.out / token_hex(16)).resolve()
    batch.parent.mkdir(parents=True, exist_ok=True)
    batch.mkdir(mode=0o700)
    return run_suite(args, batch, key)


if __name__ == "__main__":
    raise SystemExit(main())
