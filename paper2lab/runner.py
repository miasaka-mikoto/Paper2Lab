"""Standard-library experiment process runner with mock execution support."""

from __future__ import annotations

import json
import os
import platform
import random
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ._interop import get_field, to_plain


EventCallback = Callable[[str, Mapping[str, Any]], None]


@dataclass
class RunRequest:
    experiment_dir: Path
    command: list[str]
    config_path: Path | None = None
    output_path: Path | None = None
    seed: int | None = None
    timeout: float | None = None
    env: dict[str, str] = field(default_factory=dict)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("experiment_dir", "config_path", "output_path"):
            if result[key] is not None:
                result[key] = str(result[key])
        return result


@dataclass
class RunResult:
    run_id: str
    status: str
    experiment_dir: str
    command: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    runtime_seconds: float = 0.0
    seed: int | None = None
    config: Any = None
    result: Any = None
    environment: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    result_path: str = ""
    error: str = ""

    @property
    def id(self) -> str:
        """Compatibility alias used by the lightweight UI records."""

        return self.run_id

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["config"] = to_plain(result["config"])
        result["result"] = to_plain(result["result"])
        return result


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _safe_run_id() -> str:
    return time.strftime("run_%Y%m%d_%H%M%S", time.localtime()) + "_" + uuid.uuid4().hex[:8]


class _CancelledBeforeStart(RuntimeError):
    """Internal sentinel for a cancellation during the queued hand-off."""


class ExperimentRunner:
    """Run generated experiments and retain auditable run records.

    ``start`` is asynchronous and is suitable for a desktop UI.  ``run`` is a
    blocking convenience method.  Commands are never sent to a shell, which
    avoids accidental command injection from paper metadata.
    """

    def __init__(self, *, python_executable: str | None = None, on_event: EventCallback | None = None) -> None:
        self.python_executable = python_executable or sys.executable
        self.on_event = on_event
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._requests: dict[str, RunRequest] = {}
        self._results: dict[str, RunResult] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._paused: set[str] = set()
        self._cancelled: set[str] = set()

    def _emit(self, event: str, payload: Mapping[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(event, payload)
            except Exception:
                # Observers must not break the experiment process.
                pass

    def make_request(
        self,
        experiment_dir: str | Path,
        *,
        command: Sequence[str] | None = None,
        config_path: str | Path | None = None,
        output_path: str | Path | None = None,
        seed: int | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        run_id: str | None = None,
    ) -> RunRequest:
        root = Path(experiment_dir).expanduser().resolve()
        config = Path(config_path).expanduser().resolve() if config_path else root / "config.yaml"
        output = Path(output_path).expanduser().resolve() if output_path else root / "result.json"
        if command is None:
            command_list = [self.python_executable, str(root / "run.py"), "--config", str(config), "--output", str(output)]
            if seed is not None:
                command_list.extend(["--seed", str(seed)])
        elif isinstance(command, str):
            command_list = [command]
        else:
            command_list = [str(item) for item in command]
        merged_env = {str(k): str(v) for k, v in (env or {}).items()}
        return RunRequest(root, command_list, config, output, seed, timeout, merged_env, run_id or _safe_run_id())

    def run(self, experiment_dir: str | Path, **kwargs: Any) -> RunResult:
        request = self.make_request(experiment_dir, **kwargs)
        return self._execute(request)

    # Explicit alias reads naturally at call sites and preserves compatibility
    # with an earlier prototype API.
    run_experiment = run

    def start(self, experiment_dir: str | Path, **kwargs: Any) -> str:
        request = self.make_request(experiment_dir, **kwargs)
        with self._lock:
            self._requests[request.run_id] = request
            thread = threading.Thread(target=self._execute, args=(request,), name=f"paper2lab-{request.run_id}", daemon=True)
            self._threads[request.run_id] = thread
            thread.start()
        return request.run_id

    def wait(self, run_id: str, timeout: float | None = None) -> RunResult | None:
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout=timeout)
        return self._results.get(run_id)

    def status(self, run_id: str) -> str:
        with self._lock:
            result = self._results.get(run_id)
            if result:
                return result.status
            # ``cancel`` may be accepted while the worker is still in the
            # queued hand-off (before a child process exists).  Surface that
            # intent immediately instead of reporting ``queued`` until the
            # worker gets a chance to publish its terminal record.
            if run_id in self._cancelled:
                return "cancelled"
            if run_id in self._paused:
                return "paused"
            if run_id in self._processes:
                return "running"
            # ``start`` registers the request before launching its worker.
            # Reporting queued during that tiny hand-off window avoids a
            # misleading ``unknown`` status to a desktop UI polling rapidly.
            if run_id in self._requests:
                return "queued"
        return "unknown"

    def pause(self, run_id: str) -> bool:
        process = self._processes.get(run_id)
        if process is None or process.poll() is not None:
            return False
        try:
            if os.name == "nt":
                self._windows_suspend(process.pid)
            else:
                os.kill(process.pid, signal.SIGSTOP)
            with self._lock:
                self._paused.add(run_id)
            self._emit("paused", {"run_id": run_id})
            return True
        except (OSError, AttributeError):
            return False

    def resume(self, run_id: str) -> bool:
        process = self._processes.get(run_id)
        if process is None or process.poll() is not None:
            return False
        try:
            if os.name == "nt":
                self._windows_resume(process.pid)
            else:
                os.kill(process.pid, signal.SIGCONT)
            with self._lock:
                self._paused.discard(run_id)
            self._emit("resumed", {"run_id": run_id})
            return True
        except (OSError, AttributeError):
            return False

    def cancel(self, run_id: str, *, kill_after: float = 2.0) -> bool:
        queued = False
        with self._lock:
            # ``start`` registers the request before its worker has created a
            # child process.  Record cancellation intent in that window so a
            # fast UI click cannot be lost and later overwritten by a normal
            # completion.
            if run_id in self._results:
                return False
            process = self._processes.get(run_id)
            if process is None:
                if run_id not in self._requests:
                    return False
                self._cancelled.add(run_id)
                queued = True
                paused = False
            else:
                # Set the intent before checking poll().  A child can exit
                # between those operations while its worker is still
                # collecting output; retaining the intent lets final
                # publication resolve the race in favour of an already
                # accepted cancellation.
                self._cancelled.add(run_id)
                paused = run_id in self._paused
        if queued:
            self._emit("cancel_requested", {"run_id": run_id, "queued": True})
            return True
        try:
            if os.name != "nt" and paused and process.poll() is None:
                os.kill(process.pid, signal.SIGCONT)
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=kill_after)
            except subprocess.TimeoutExpired:
                process.kill()
            self._emit("cancel_requested", {"run_id": run_id})
            return True
        except OSError:
            return False

    def retry(self, run_id: str) -> str | None:
        request = self._requests.get(run_id)
        if request is None:
            return None
        # Retrying an active process would reuse its output path and can
        # corrupt both result files.  Callers should wait/cancel first.
        state = self.status(run_id)
        with self._lock:
            terminal_recorded = run_id in self._results
        if state in {"queued", "running", "paused"} or (state == "cancelled" and not terminal_recorded):
            # A queued cancellation is not terminal until the worker has
            # persisted its result.  Do not start a retry beside that worker.
            return None
        clone_id = _safe_run_id()
        # Give a retry its own result path.  Generated commands normally
        # contain the previous ``--output`` argument; replace that exact
        # token while leaving arbitrary user commands untouched.
        clone_output = request.output_path
        clone_command = list(request.command)
        if request.output_path is not None:
            clone_output = request.experiment_dir / "runs" / f"{clone_id}.result.json"
            old_output = str(request.output_path)
            clone_command = [str(clone_output) if item == old_output else item for item in clone_command]
        clone = RunRequest(
            request.experiment_dir,
            clone_command,
            request.config_path,
            clone_output,
            request.seed,
            request.timeout,
            dict(request.env),
            clone_id,
        )
        with self._lock:
            self._requests[clone.run_id] = clone
            thread = threading.Thread(target=self._execute, args=(clone,), name=f"paper2lab-{clone.run_id}", daemon=True)
            self._threads[clone.run_id] = thread
            thread.start()
        return clone.run_id

    def get_result(self, run_id: str) -> RunResult | None:
        return self._results.get(run_id)

    def _execute(self, request: RunRequest) -> RunResult:
        started_epoch = time.time()
        started_at = _now()
        config = _load_json(request.config_path)
        result_path = request.output_path or request.experiment_dir / "result.json"
        run = RunResult(
            run_id=request.run_id,
            status="running",
            experiment_dir=str(request.experiment_dir),
            command=list(request.command),
            seed=request.seed,
            config=config,
            environment={"python": sys.version.split()[0], "platform": platform.platform()},
            started_at=started_at,
            result_path=str(result_path),
        )
        with self._lock:
            self._requests[request.run_id] = request
        self._emit("started", run.to_dict())
        process: subprocess.Popen[str] | None = None
        timed_out = False
        try:
            with self._lock:
                cancelled_before_start = request.run_id in self._cancelled
            if cancelled_before_start:
                raise _CancelledBeforeStart("Experiment cancelled before process start.")
            child_env = os.environ.copy()
            child_env.update(request.env)
            process = subprocess.Popen(
                request.command,
                cwd=str(request.experiment_dir),
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            with self._lock:
                self._processes[request.run_id] = process
                cancelled_after_start = request.run_id in self._cancelled
            if cancelled_after_start:
                # A cancel can arrive between the pre-start check and child
                # registration.  Reuse the normal termination path now that
                # a process handle is available.
                self.cancel(request.run_id)
            try:
                stdout, stderr = process.communicate(timeout=request.timeout)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                self.cancel(request.run_id)
                stdout, stderr = process.communicate()
                run.status = "timeout"
                run.error = f"Experiment exceeded timeout ({request.timeout}s)."
                stdout = stdout or (exc.stdout or "")
                stderr = stderr or (exc.stderr or "")
            run.stdout, run.stderr = stdout or "", stderr or ""
            run.returncode = process.returncode
            with self._lock:
                cancelled = request.run_id in self._cancelled
            # Timeout owns the terminal state even though the implementation
            # uses the same terminate path as an explicit cancellation.
            if timed_out:
                run.status = "timeout"
            elif cancelled:
                run.status = "cancelled"
            elif run.status != "timeout":
                run.status = "completed" if process.returncode == 0 else "failed"
            run.result = _load_json(result_path)
        except _CancelledBeforeStart as exc:
            run.status = "cancelled"
            run.error = str(exc)
        except (OSError, ValueError) as exc:
            run.status = "failed"
            run.error = str(exc)
        finally:
            run.runtime_seconds = round(time.time() - started_epoch, 6)
            run.finished_at = _now()
            with self._lock:
                # Cancellation can arrive after the worker chose a normal
                # terminal status but before it publishes ``_results``.
                # Resolve that narrow race while the same lock protects both
                # the intent and the published result.  Timeout remains more
                # specific and therefore keeps precedence.
                if (
                    not timed_out
                    and request.run_id in self._cancelled
                    and run.status not in {"cancelled", "timeout"}
                ):
                    run.status = "cancelled"
                    if not run.error:
                        run.error = "Experiment cancelled."
                self._processes.pop(request.run_id, None)
                self._paused.discard(request.run_id)
                self._cancelled.discard(request.run_id)
                self._results[request.run_id] = run
            self._persist(run)
            self._emit("finished", run.to_dict())
        return run

    def _persist(self, run: RunResult) -> None:
        directory = Path(run.experiment_dir) / "runs"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{run.run_id}.json").write_text(
                json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    @staticmethod
    def _windows_suspend(pid: int) -> None:
        # Best-effort standard-library implementation for Windows.  On other
        # systems pause/resume use SIGSTOP/SIGCONT above.
        import ctypes
        import ctypes.wintypes

        THREAD_SUSPEND_RESUME = 0x0002
        TH32CS_SNAPTHREAD = 0x00000004
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == ctypes.wintypes.HANDLE(-1).value:
            raise OSError("CreateToolhelp32Snapshot failed")
        class THREADENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD), ("th32ThreadID", ctypes.wintypes.DWORD), ("th32OwnerProcessID", ctypes.wintypes.DWORD), ("tpBasePri", ctypes.wintypes.LONG), ("tpDeltaPri", ctypes.wintypes.LONG), ("dwFlags", ctypes.wintypes.DWORD)]
        entry = THREADENTRY32(ctypes.sizeof(THREADENTRY32))
        try:
            if kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.th32OwnerProcessID == pid:
                        handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                        if handle:
                            kernel32.SuspendThread(handle)
                            kernel32.CloseHandle(handle)
                    if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)

    @staticmethod
    def _windows_resume(pid: int) -> None:
        import ctypes
        import ctypes.wintypes

        THREAD_SUSPEND_RESUME = 0x0002
        TH32CS_SNAPTHREAD = 0x00000004
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == ctypes.wintypes.HANDLE(-1).value:
            raise OSError("CreateToolhelp32Snapshot failed")
        class THREADENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD), ("th32ThreadID", ctypes.wintypes.DWORD), ("th32OwnerProcessID", ctypes.wintypes.DWORD), ("tpBasePri", ctypes.wintypes.LONG), ("tpDeltaPri", ctypes.wintypes.LONG), ("dwFlags", ctypes.wintypes.DWORD)]
        entry = THREADENTRY32(ctypes.sizeof(THREADENTRY32))
        try:
            if kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                while True:
                    if entry.th32OwnerProcessID == pid:
                        handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                        if handle:
                            kernel32.ResumeThread(handle)
                            kernel32.CloseHandle(handle)
                    if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)


class MockExperimentRunner:
    """Deterministic no-API runner used by demos and tests."""

    def run(self, blueprint: Any, *, seed: int = 42, run_id: str | None = None) -> RunResult:
        started = time.time()
        plain = to_plain(blueprint)
        if not isinstance(plain, Mapping):
            plain = {}
        paper = plain.get("paper_reported_result", plain.get("paper_result", plain.get("expected_result", {})))
        local_override = plain.get("local_result", plain.get("local_results", {}))
        paper_metrics = self._metrics(paper)
        local_metrics = self._metrics(local_override)
        rng = random.Random(seed)
        metrics: dict[str, Any] = {}
        for name, value in paper_metrics.items():
            override = local_metrics.get(name)
            if override is not None:
                metrics[name] = override
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                # Preserve the scale used by the source paper: 81.4 means
                # percentage points while 0.814 means a [0,1] fraction.
                delta = 0.5 if abs(float(value)) > 1 else 0.005
                if str(name).lower() not in {"accuracy", "f1", "auc", "f1_score", "exact_match"}:
                    delta = abs(float(value)) * 0.005
                metrics[name] = round(value - delta, 6)
            else:
                metrics[name] = round(rng.random(), 6)
        for name, value in local_metrics.items():
            metrics.setdefault(name, value)
        # A blueprint can define metrics before the paper reports any numeric
        # value.  Keep the mock workflow useful by emitting deterministic
        # synthetic values for those declared names; the result is labelled
        # Mock and must not be mistaken for a reproduction.
        declared = plain.get("metrics", [])
        if isinstance(declared, Mapping):
            declared_names = declared.keys()
        elif isinstance(declared, (list, tuple, set, frozenset)):
            declared_names = [
                item.get("name", item.get("metric", item.get("id"))) if isinstance(item, Mapping) else item
                for item in declared
            ]
        else:
            declared_names = []
        for name in declared_names:
            if name is not None:
                metrics.setdefault(str(name), round(0.5 + rng.random() * 0.1, 6))
        result_payload = {"status": "completed", "mode": "mock", "seed": seed, "metrics": metrics, "notes": ["Mock result only; no paid API was called."]}
        return RunResult(
            run_id=run_id or _safe_run_id(),
            status="completed",
            experiment_dir="",
            command=["mock"],
            returncode=0,
            stdout=json.dumps(result_payload, ensure_ascii=False),
            runtime_seconds=round(time.time() - started, 6),
            seed=seed,
            config=plain,
            result=result_payload,
            environment={"python": sys.version.split()[0], "platform": platform.platform()},
            started_at=_now(),
            finished_at=_now(),
        )

    @staticmethod
    def _metrics(value: Any) -> dict[str, Any]:
        plain = to_plain(value)
        if not isinstance(plain, Mapping):
            return {}
        if "name" in plain and "value" in plain:
            return {str(plain["name"]): plain["value"]}
        output: dict[str, Any] = {}
        for name, record in plain.items():
            if isinstance(record, Mapping):
                output[str(name)] = record.get("local", record.get("value", record.get("paper")))
            else:
                output[str(name)] = record
        return output


def run_mock_experiment(blueprint: Any, *, seed: int = 42, run_id: str | None = None) -> RunResult:
    return MockExperimentRunner().run(blueprint, seed=seed, run_id=run_id)


__all__ = ["RunRequest", "RunResult", "ExperimentRunner", "MockExperimentRunner", "run_mock_experiment"]
