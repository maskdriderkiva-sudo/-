"""バックグラウンドジョブ (ビルド・書き込み) の進捗管理。"""
from __future__ import annotations

import threading
import traceback
import uuid
from typing import Callable


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.log: list[str] = []
        self.progress = (0, 0, "")
        self.done = False
        self.ok = False
        self.result: dict = {}
        self.lock = threading.Lock()

    def write(self, msg: str):
        with self.lock:
            self.log.append(str(msg))

    def set_progress(self, done: int, total: int, label: str):
        with self.lock:
            self.progress = (done, total, label)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {
                "id": self.id, "kind": self.kind, "log": self.log[since:], "log_len": len(self.log),
                "progress": {"done": self.progress[0], "total": self.progress[1], "label": self.progress[2]},
                "done": self.done, "ok": self.ok, "result": self.result,
            }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.busy = threading.Lock()

    def start(self, kind: str, fn: Callable[[Job], bool]) -> Job:
        job = Job(kind)
        self.jobs[job.id] = job

        def runner():
            with self.busy:   # ビルドと書き込みは同時に 1 つだけ
                try:
                    job.ok = bool(fn(job))
                except Exception as e:  # noqa: BLE001
                    job.write(f"エラー: {e}")
                    job.write(traceback.format_exc(limit=3))
                    job.ok = False
                finally:
                    job.done = True

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)
