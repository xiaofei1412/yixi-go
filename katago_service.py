"""One lazy analysis process, with correlated responses and bounded waits."""
import json
import subprocess
import threading
import uuid
from concurrent.futures import Future, TimeoutError
from product_paths import ENGINE_DIR, DATA_DIR, FROZEN

ENGINE_FILES = [ENGINE_DIR / "katago.exe",
                ENGINE_DIR / "kata1-b28c512nbt-s12192929536-d5655876072.bin.gz",
                ENGINE_DIR / "product_analysis.cfg"]


class EngineError(RuntimeError):
    pass


class KataGoEngine:
    def __init__(self):
        self.proc = None
        self.lock = threading.RLock()
        self.pending = {}
        self.slots = threading.BoundedSemaphore(4)
        self.cache_identity = [(path.name, path.stat().st_size, path.stat().st_mtime_ns)
                               for path in ENGINE_FILES if path.exists()]

    def _start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        exe, model, config = ENGINE_FILES
        if not exe.exists() or not model.exists():
            raise EngineError("缺少 KataGo 引擎或模型文件")
        logs = DATA_DIR
        logs.mkdir(parents=True, exist_ok=True)
        working_dir = DATA_DIR / 'engine' if FROZEN else ENGINE_DIR
        working_dir.mkdir(parents=True, exist_ok=True)
        command = [str(exe), "analysis", "-model", str(model), "-config", str(config)]
        if FROZEN:
            # KataGo otherwise writes tuning data beside its EXE, even with a new cwd.
            command += ["-override-config", "homeDataDir=."]
        with (logs / "engine.log").open("ab") as log:
            self.proc = subprocess.Popen(
                command,
                cwd=str(working_dir), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=log, text=True, encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        try:
            for line in proc.stdout:
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if result.get("warning") or result.get("isDuringSearch"):
                    continue
                with self.lock:
                    future = self.pending.get(result.get("id"))
                    if future and not future.done():
                        if "error" in result:
                            future.set_exception(EngineError("引擎无法分析此局面：" + result["error"]))
                        elif "rootInfo" in result:
                            future.set_result(result)
                    elif "error" in result and not result.get("id"):
                        for future in self.pending.values():
                            if not future.done():
                                future.set_exception(EngineError(result["error"]))
        finally:
            with self.lock:
                if self.proc is proc:
                    for future in self.pending.values():
                        if not future.done():
                            future.set_exception(EngineError(f"KataGo 已退出，请重试；详情见 {DATA_DIR / 'engine.log'}"))

    def query(self, payload, timeout=90):
        if not self.slots.acquire(timeout=5):
            raise EngineError("分析任务较多，请稍后重试")
        query_id = uuid.uuid4().hex
        future = Future()
        try:
            with self.lock:
                self._start()
                self.pending[query_id] = future
                self.proc.stdin.write(json.dumps(dict(payload, id=query_id)) + "\n")
                self.proc.stdin.flush()
            return future.result(timeout=timeout)
        except TimeoutError:
            with self.lock:
                if self.proc and self.proc.poll() is None:
                    self.proc.stdin.write(json.dumps({"id": uuid.uuid4().hex, "action": "terminate", "terminateId": query_id}) + "\n")
                    self.proc.stdin.flush()
            raise EngineError("分析超时，可降低思考预算后重试。首次启动可能需要显卡调优。")
        except (OSError, BrokenPipeError) as exc:
            raise EngineError("无法连接 KataGo，请重试并查看引擎日志") from exc
        finally:
            with self.lock:
                self.pending.pop(query_id, None)
            self.slots.release()

    def close(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

