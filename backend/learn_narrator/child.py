"""
learn_narrator/child.py — the HEAVY process. `python -m learn_narrator.child <mode> <arg>`:

  render <job.json>   engines.{chatterbox,kokoro}.render → <out_dir>/narration.m4a + render.json
  render_part <job.json>  Chatterbox only: blocks job["part"] = [start, end) → <out_dir>/parts/ +
                      render_part.json (main.py runs one per RENDER_PART_BLOCKS, then `render`)
  align  <job.json>   align.align_job (Chatterbox only)  → <out_dir>/align.json
  smoke  <out dir>    the image's build-time proof: one line per engine, the WAV2VEC2 weights, and
                      `torchaudio.functional.forced_align`, all with the network OFF (refused
                      in-process by `_refuse_network`: Railway rejects `RUN --network=none`)
                      → <out dir>/smoke.json

Only this module (and the functions it calls) imports torch / chatterbox / kokoro. The parent
(`main.py`, `runtime.run_child`) sets the thread caps and the offline switches in this process's
environment before Python starts, so every math library is sized before it loads.

A typed failure writes `error.json` (`{"code", "detail"}`) next to the outputs and exits
runtime.CHILD_TYPED_FAILURE; anything unexpected prints its traceback (the parent logs the tail)
and exits 1.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict

from learn_narrator import runtime


def _progress(mode: str):
    def emit(msg: str) -> None:
        print(f"[{mode}] {msg}", flush=True)
    return emit


def _weights_missing(exc: BaseException) -> bool:
    """huggingface_hub's offline refusals (LocalEntryNotFoundError, OfflineModeIsEnabled) — a file
    the image did not bake, refused instead of downloaded."""
    names = {c.__name__ for c in type(exc).__mro__}
    return bool(names & {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "EntryNotFoundError"})


def _classify(exc: BaseException, mode: str = "render") -> Dict[str, str]:
    from learn_narrator import align
    from learn_narrator.engines import chatterbox, kokoro

    detail = " ".join(f"{type(exc).__name__}: {exc}".split())[:600]
    if isinstance(exc, runtime.ChildFailed):
        return {"code": exc.code, "detail": exc.detail[:600]}
    if isinstance(exc, chatterbox.BlockGateFailed):
        return {"code": "block_gate", "detail": detail}
    if isinstance(exc, (chatterbox.VoiceUnsupported, kokoro.VoiceUnsupported)):
        return {"code": "voice_unsupported", "detail": detail}
    if isinstance(exc, align.AlignFailed):
        return {"code": "align_failed", "detail": detail}
    # torch's CPU allocator raises its own OutOfMemoryError (matched by name: torch is not imported
    # here). Named for the MODE, so a part's memory failure is `render_part_oom` — the code
    # main.render_in_parts retries — exactly as a SIGKILL of that child is (runtime.run_child).
    if isinstance(exc, MemoryError) or "OutOfMemoryError" in {c.__name__ for c in type(exc).__mro__}:
        return {"code": f"{mode}_oom", "detail": detail}
    if _weights_missing(exc):
        return {"code": "weights_missing", "detail": detail}
    return {}


def run(mode: str, job_path: Path) -> Dict[str, Any]:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"])
    threads = int(job.get("threads") or runtime.torch_threads())
    progress = _progress(mode)
    if mode == "render_part":
        if job["engine"] != "chatterbox":
            raise runtime.ChildFailed("engine_unsupported", f"render_part is Chatterbox-only, not {job['engine']!r}")
        from learn_narrator.engines import chatterbox
        return chatterbox.render_part(job, out_dir, threads=threads, progress=progress)
    if mode == "render":
        if job["engine"] == "chatterbox":
            from learn_narrator.engines import chatterbox
            return chatterbox.render(job, out_dir, threads=threads, progress=progress)
        if job["engine"] == "kokoro":
            from learn_narrator.engines import kokoro
            return kokoro.render(job, out_dir, threads=threads, progress=progress)
        raise runtime.ChildFailed("engine_unsupported", f"engine {job['engine']!r}")
    if mode == "align":
        from learn_narrator import align
        return align.align_job(job, out_dir, threads=threads, progress=progress)
    raise SystemExit(f"unknown mode {mode!r}")


_LOCAL_HOSTS = frozenset({None, "", "localhost", "127.0.0.1", "::1"})


def _refuse_network() -> list:
    """Make every IP connection from this process fail as an unreachable network would: the smoke's
    offline proof. The Dockerfile used `RUN --network=none`, which Railway's validation rejects
    ("flag '--network=none' is not supported", 2026-10-23), so the build step has the network and
    this takes its place: a weight the bake missed is refused, never downloaded. Unix sockets and
    localhost stay open (local IPC). Returns the list each refused attempt is appended to, so the
    smoke report and the build log name any library that tried."""
    import errno
    import socket

    attempts: list = []
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def _ip(sock: socket.socket) -> bool:
        return sock.family in (socket.AF_INET, socket.AF_INET6)

    def _refused(what: str) -> OSError:
        attempts.append(what[:200])
        print(f"[smoke] network refused: {what[:200]}", flush=True)
        return OSError(errno.ENETUNREACH, f"network is off for the smoke: {what[:200]}")

    def guarded_connect(self, address):
        if _ip(self) and address[0] not in _LOCAL_HOSTS:
            raise _refused(f"connect {address!r}")
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if _ip(self) and address[0] not in _LOCAL_HOSTS:
            _refused(f"connect_ex {address!r}")
            return errno.ENETUNREACH
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name not in _LOCAL_HOSTS:
            raise socket.gaierror(socket.EAI_NONAME, str(_refused(f"resolve {name!r}")))
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect          # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex    # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo         # type: ignore[assignment]
    return attempts


def smoke(out_dir: Path) -> Dict[str, Any]:
    """Prove the image renders with what it baked: Chatterbox (prepared once, exaggeration passed),
    the encode, a WAV2VEC2 forced alignment of that line, and Kokoro with every baked voice — with
    the network refused (`_refuse_network`) before any library is imported."""
    refused = _refuse_network()
    threads = runtime.torch_threads()
    for name, value in runtime.child_env(threads, base={}).items():
        os.environ.setdefault(name, value)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = _progress("smoke")
    import subprocess

    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio.functional as F  # noqa: F401 — the import itself is part of the proof
    from torchaudio.functional import forced_align  # noqa: F401

    from learn_narrator import align, verify
    from learn_narrator.engines import chatterbox as cb
    from learn_narrator.engines import kokoro as kk

    report: Dict[str, Any] = {"threads": threads, "torch": torch.__version__}
    line = "This is the Caydex narrator, checking the voice before it ships."
    t0 = time.monotonic()
    from chatterbox.tts import ChatterboxTTS

    torch.set_num_threads(threads)
    if cb.ref_sha256() != cb.REF_SHA256:
        raise runtime.ChildFailed("voice_unsupported", "reference clip sha256 mismatch")
    model = ChatterboxTTS.from_pretrained(device="cpu")
    model.prepare_conditionals(str(cb.REF), exaggeration=cb.EXAG)
    emotion = float(model.conds.t3.emotion_adv.reshape(-1)[0])
    if abs(emotion - cb.EXAG) > 1e-6:
        raise runtime.ChildFailed("voice_unsupported", f"emotion_adv {emotion} != {cb.EXAG}")
    torch.manual_seed(cb.SEED)
    wav = model.generate(line, exaggeration=cb.EXAG, cfg_weight=cb.CFG)
    audio = wav.detach().cpu().numpy().astype(np.float32).reshape(-1)
    raw_seconds = len(audio) / cb.SR
    if not 0.5 < raw_seconds < cb.TRUNCATION_SECONDS:
        raise runtime.ChildFailed("render_failed", f"smoke line rendered {raw_seconds:.2f}s")
    raw = out_dir / "smoke.raw.wav"
    sf.write(str(raw), audio, cb.SR, subtype="FLOAT")
    m4a = out_dir / "smoke_chatterbox.m4a"
    subprocess.run(cb.encode_command(raw, m4a, 1.0), check=True, capture_output=True, timeout=120)
    report["chatterbox"] = {"raw_seconds": round(raw_seconds, 2), "emotion_adv": emotion,
                            "seconds": round(time.monotonic() - t0, 1), "bytes": m4a.stat().st_size,
                            "duration": verify.duration(m4a)}
    progress(f"chatterbox OK {report['chatterbox']}")
    del model

    t1 = time.monotonic()
    aligner, dictionary = align.load_model(threads)
    pcm = align.decode_audio(m4a)
    res = align.align_block(aligner, dictionary, pcm, line, 0.0, len(pcm) / align.SAMPLE_RATE)
    timed = [w for w in res["words"] if w is not None]
    if len(timed) != len(line.split()):
        raise runtime.ChildFailed("align_failed", f"smoke alignment timed {len(timed)} of {len(line.split())} words")
    report["align"] = {"model": align.MODEL, "confidence": res["confidence"], "frames": res["frames"],
                       "seconds": round(time.monotonic() - t1, 1)}
    progress(f"align OK {report['align']}")
    del aligner

    t2 = time.monotonic()
    from kokoro import KPipeline

    voices = runtime.baked_kokoro_voices()
    if not voices:
        raise runtime.ChildFailed("weights_missing", "no Kokoro voice is baked")
    pipes: Dict[str, Any] = {}
    for v in voices:
        if v[0] not in pipes:                    # one model, shared by the 'a' and 'b' pipelines
            shared = next(iter(pipes.values())).model if pipes else True
            pipes[v[0]] = KPipeline(lang_code=v[0], repo_id=kk.KOKORO_REPO, model=shared)
        pipes[v[0]].load_voice(v)                # proves the file is baked (offline)
    first = "am_michael" if "am_michael" in voices else voices[0]
    results = list(pipes[first[0]](line, voice=first, speed=kk.SPEED))
    samples = sum(len(r.audio) for r in results)
    stamped = sum(1 for r in results for t in (r.tokens or []) if getattr(t, "start_ts", None) is not None)
    if samples == 0 or stamped == 0:
        raise runtime.ChildFailed("render_failed", f"Kokoro smoke: {samples} samples, {stamped} timed tokens")
    report["kokoro"] = {"voices": voices, "voice": first, "seconds_audio": round(samples / kk.SR, 2),
                        "timed_tokens": stamped, "seconds": round(time.monotonic() - t2, 1)}
    progress(f"kokoro OK {report['kokoro']}")
    # A refused attempt that a library tolerated is not a failure (as under --network=none), but
    # it is named here and in the build log above.
    report["network_refused"] = list(refused)
    raw.unlink(missing_ok=True)
    return report


def main(argv: list) -> int:
    if len(argv) != 3 or argv[1] not in ("render", "render_part", "align", "smoke"):
        print("usage: python -m learn_narrator.child {render|render_part|align} <job.json> | smoke <out dir>",
              file=sys.stderr)
        return 2
    mode, arg = argv[1], Path(argv[2])
    out_dir = arg if mode == "smoke" else Path(json.loads(arg.read_text(encoding="utf-8"))["out_dir"])
    try:
        result = smoke(arg) if mode == "smoke" else run(mode, arg)
    except BaseException as exc:  # noqa: BLE001 — classified, written, and the status says which
        typed = _classify(exc, mode) if isinstance(exc, Exception) else {}
        traceback.print_exc()
        if typed:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "error.json").write_text(json.dumps(typed), encoding="utf-8")
            print(f"[{mode}] FAILED {typed['code']}: {typed['detail']}", flush=True)
            return runtime.CHILD_TYPED_FAILURE
        return 1
    (out_dir / f"{mode}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"[{mode}] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
