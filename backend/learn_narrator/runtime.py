"""
learn_narrator/runtime.py — what the PARENT process needs to drive the heavy children: the CPU
quota, the child runner (timeout + heartbeat + typed failures) and the baked-weights preflight.

Stdlib only. The children (`python -m learn_narrator.child …`) import torch / chatterbox / kokoro;
this process never does, so a child the memory limit kills takes only itself down, and a wedged
model call cannot hold the Railway cron slot forever (Railway skips every later tick while one
container lives). Same shape as `marketing/voice.py`'s `run_child`, with two differences:

* the child's output goes to a LOG FILE, not a pipe — a 2-hour render prints far more than a pipe
  buffer holds, and a full pipe blocks the child (marketing reads its pipe only after `wait`);
  the parent logs the newest progress line on every heartbeat and the tail on a failure;
* a child reports a typed failure by writing `error.json` (`{"code", "detail"}`) and exiting
  CHILD_TYPED_FAILURE, so a block that fails its gate reaches `/fail` as `block_gate`, not as an
  anonymous exit status.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

logger = logging.getLogger("learn_narrator.runtime")

#: Exit status of a child that wrote error.json.
CHILD_TYPED_FAILURE = 3
HEARTBEAT_SECONDS = 60
POLL_SECONDS = 5
DEFAULT_THREADS = 2

CPU_MAX = Path("/sys/fs/cgroup/cpu.max")

#: Where the image bakes the weights (Dockerfile ENV). Overridable for a local layout.
HF_HOME_DEFAULT = "/app/models/hf"
TORCH_HOME_DEFAULT = "/app/models/torch"

CHATTERBOX_REPO = "ResembleAI/chatterbox"
CHATTERBOX_FILES = ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt")
KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_FILES = ("config.json", "kokoro-v1_0.pth")
#: torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H's checkpoint (MIT), under $TORCH_HOME/hub/checkpoints.
WAV2VEC2_CHECKPOINT = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"


class ChildFailed(RuntimeError):
    """A child failed. `code` is the worker's failure code for `/fail`."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ── CPU quota ────────────────────────────────────────────────────────────────


def torch_threads(env: Optional[Mapping[str, str]] = None, cpu_max: Path = CPU_MAX) -> int:
    """The container's CPU quota, not the host's core count (`os.cpu_count()` sees the HOST, so
    torch would start one thread per host core on a 4-vCPU slice and thrash). Order:
    LEARN_NARRATOR_THREADS (a positive integer) → cgroup v2 `cpu.max` (quota / period, rounded up)
    → DEFAULT_THREADS."""
    env = os.environ if env is None else env
    raw = (env.get("LEARN_NARRATOR_THREADS") or "").strip()
    if raw:
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        logger.warning("LEARN_NARRATOR_THREADS=%r is not a positive integer — ignored", raw)
    try:
        parts = cpu_max.read_text().split()
        quota, period = parts[0], parts[1]
        if quota != "max" and int(period) > 0 and int(quota) > 0:
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError, IndexError):
        pass
    return DEFAULT_THREADS


def child_env(threads: int, base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The child's environment: thread caps for every math library (set BEFORE torch is imported —
    `torch.set_num_threads` alone does not reach OpenMP pools a library already sized), and the
    offline switches, forced on whatever the parent's environment says: the baked weights are the
    ONLY weights."""
    env = dict(os.environ if base is None else base)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS"):
        env[name] = str(threads)
    env.update({
        "LEARN_NARRATOR_THREADS": str(threads),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
    })
    env.setdefault("NUMBA_CACHE_DIR", "/tmp/learn_narrator_numba")
    return env


# ── the child runner ─────────────────────────────────────────────────────────


def _tail(path: Path, n: int = 1200) -> str:
    try:
        data = path.read_bytes()[-n:]
    except OSError:
        return ""
    return " ".join(data.decode("utf-8", "replace").split())


def _last_line(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            lines = [ln for ln in f.read().decode("utf-8", "replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return lines[-1].strip()[:300] if lines else ""


def run_child(mode: str, job_path: Path, out_dir: Path, *, timeout: float, threads: int,
              heartbeat: Optional[Callable[[], None]] = None, beat_every: float = HEARTBEAT_SECONDS,
              popen: Callable[..., "subprocess.Popen"] = subprocess.Popen,
              clock: Callable[[], float] = time.monotonic, poll: float = POLL_SECONDS) -> dict:
    """Run `python -m learn_narrator.child <mode> <job.json>`; heartbeat while it works; return
    its `<mode>.json`. Raises ChildFailed with a worker failure code:

      * `<mode>_timeout` — over `timeout` seconds (the child is killed);
      * `<mode>_oom`     — SIGKILL / exit 137: almost always the memory limit;
      * the child's own code (error.json) — a typed failure (`block_gate`, `weights_missing`, …);
      * `<mode>_failed`  — any other non-zero exit, or no result file.

    `heartbeat` may raise (the job was cancelled or re-claimed): the child is killed and the
    exception propagates — nothing renders for a job that is no longer ours."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{mode}.log"
    result_path = out_dir / f"{mode}.json"
    error_path = out_dir / "error.json"
    for stale in (result_path, error_path):
        stale.unlink(missing_ok=True)
    t0 = clock()
    last_beat = t0
    with log_path.open("ab") as log:
        proc = popen([sys.executable, "-m", "learn_narrator.child", mode, str(job_path)],
                     stdout=log, stderr=subprocess.STDOUT, env=child_env(threads),
                     cwd=str(Path(__file__).resolve().parent.parent))
        try:
            while True:
                try:
                    proc.wait(timeout=poll)
                    break
                except subprocess.TimeoutExpired:
                    now = clock()
                    if now - t0 > timeout:
                        proc.kill()
                        proc.wait()
                        raise ChildFailed(f"{mode}_timeout",
                                          f"{mode} ran over {timeout / 60:.0f} min; last: {_last_line(log_path)}")
                    if heartbeat is not None and now - last_beat >= beat_every:
                        last_beat = now
                        logger.info("%s running %.0fs: %s", mode, now - t0, _last_line(log_path))
                        heartbeat()
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
    code = proc.returncode
    wall = clock() - t0
    if code in (-9, 137):
        raise ChildFailed(f"{mode}_oom", f"{mode} child killed (exit {code}) after {wall:.0f}s — the memory "
                                         f"limit? {_tail(log_path, 300)}")
    if code == CHILD_TYPED_FAILURE and error_path.exists():
        try:
            err = json.loads(error_path.read_text(encoding="utf-8"))
            raise ChildFailed(str(err["code"]), str(err.get("detail") or ""))
        except (ValueError, KeyError, TypeError):
            pass
    if code != 0:
        raise ChildFailed(f"{mode}_failed", f"{mode} child exited {code} after {wall:.0f}s: {_tail(log_path, 600)}")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ChildFailed(f"{mode}_failed", f"{mode} child exited 0 without a readable {result_path.name}: "
                                            f"{type(e).__name__}") from e
    logger.info("%s child done in %.0fs", mode, wall)
    return result


# ── baked weights ────────────────────────────────────────────────────────────


def hf_home(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("HF_HOME") or HF_HOME_DEFAULT)


def torch_home(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("TORCH_HOME") or TORCH_HOME_DEFAULT)


def main_snapshot(repo: str, env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The snapshot `refs/main` points at — the ONLY one an offline `hf_hub_download(repo, f)`
    (no revision: what chatterbox and kokoro call) can resolve. `bake.py` downloads by commit and
    writes this ref."""
    base = hf_home(env) / "hub" / ("models--" + repo.replace("/", "--"))
    try:
        commit = (base / "refs" / "main").read_text().strip()
    except OSError:
        return None
    snap = base / "snapshots" / commit
    return snap if commit and snap.is_dir() else None


def _missing_in_snapshot(repo: str, files: List[str], env: Optional[Mapping[str, str]]) -> List[str]:
    snap = main_snapshot(repo, env)
    if snap is None:
        return [f"{repo} (no refs/main snapshot under {hf_home(env)})"]
    return [f"{repo}/{f}" for f in files if not (snap / f).exists()]


def missing_weights(engine: str, *, voice: Optional[str] = None,
                    env: Optional[Mapping[str, str]] = None) -> List[str]:
    """Files `engine` (and its voice) would load that are NOT baked into the image. Empty = ready.
    Checked before a claim so the worker only declares engines it can render, and again for the
    job's voice — a missing file is a typed failure, never a download (the children also run with
    HF_HUB_OFFLINE=1 and refuse torch.hub downloads)."""
    if engine == "chatterbox":
        missing = _missing_in_snapshot(CHATTERBOX_REPO, list(CHATTERBOX_FILES), env)
        ckpt = torch_home(env) / "hub" / "checkpoints" / WAV2VEC2_CHECKPOINT
        if not ckpt.exists():
            missing.append(f"torch hub {WAV2VEC2_CHECKPOINT}")
        return missing
    if engine == "kokoro":
        files = list(KOKORO_FILES) + ([f"voices/{voice}.pt"] if voice else [])
        return _missing_in_snapshot(KOKORO_REPO, files, env)
    return [f"unknown engine {engine!r}"]


def baked_kokoro_voices(env: Optional[Mapping[str, str]] = None) -> List[str]:
    snap = main_snapshot(KOKORO_REPO, env)
    return sorted(p.stem for p in (snap / "voices").glob("*.pt")) if snap else []
