"""
learn_narrator/bake.py — download every weight the narrator loads INTO THE IMAGE, at build time
(`RUN python -m learn_narrator.bake` in the Dockerfile; the only step with a reason to touch the
network). A Railway cron container is fresh on every tick, and the runtime refuses to download
(HF_HUB_OFFLINE=1, torch.hub downloads replaced by a refusal), so anything not baked here is a
`weights_missing` failure, never a silent pull.

Pinned revisions: the Chatterbox snapshot is the one the catalog was voiced with on the owner's
Mac (`~/.cache/huggingface/hub/models--ResembleAI--chatterbox/snapshots/5bb1f6ee…`), and the Kokoro
snapshot the marketing worker measured (f3ff3571…). They are downloaded BY COMMIT and `refs/main`
is pointed at that commit, because chatterbox and kokoro call `hf_hub_download` without a revision
(= "main"), which offline mode resolves through `refs/main` alone.

Self-contained on purpose (stdlib + huggingface_hub + torchaudio): the Dockerfile copies this file
before the rest of the package, so a code change does not re-download ~3 GB. The file lists are
pinned equal to `runtime.py`'s by tests/test_learn_narrator_worker.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

CHATTERBOX_REPO = "ResembleAI/chatterbox"
CHATTERBOX_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
CHATTERBOX_FILES = ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt")
KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
KOKORO_FILES = ("config.json", "kokoro-v1_0.pth")
#: Male voices first (the plan's fallback wants a male narrator, F0 ≈ 133 Hz); af_heart is the
#: marketing voice, kept for comparison. Each file is ~0.5 MB.
KOKORO_VOICES = ("am_michael", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_onyx", "am_puck",
                 "bm_daniel", "bm_fable", "bm_george", "bm_lewis", "af_heart")
WAV2VEC2_CHECKPOINT = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"


def _pin_main(repo: str, revision: str) -> None:
    """Point the cache's refs/main at `revision` (what `hf_hub_download(repo, f)` resolves offline)."""
    from huggingface_hub.constants import HF_HUB_CACHE

    refs = Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--")) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        print("bake: HF_HUB_OFFLINE=1 is set — the bake step must run before it", file=sys.stderr)
        return 2
    from huggingface_hub import hf_hub_download

    record = {"files": {}}
    for f in CHATTERBOX_FILES:
        p = Path(hf_hub_download(CHATTERBOX_REPO, f, revision=CHATTERBOX_REVISION))
        record["files"][f"{CHATTERBOX_REPO}/{f}"] = _sha256(p)
    _pin_main(CHATTERBOX_REPO, CHATTERBOX_REVISION)
    for f in KOKORO_FILES + tuple(f"voices/{v}.pt" for v in KOKORO_VOICES):
        p = Path(hf_hub_download(KOKORO_REPO, f, revision=KOKORO_REVISION))
        record["files"][f"{KOKORO_REPO}/{f}"] = _sha256(p)
    _pin_main(KOKORO_REPO, KOKORO_REVISION)

    import torch.hub
    import torchaudio

    torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.get_model()
    ckpt = Path(torch.hub.get_dir()) / "checkpoints" / WAV2VEC2_CHECKPOINT
    if not ckpt.exists():
        print(f"bake: {ckpt} missing after the download", file=sys.stderr)
        return 1
    record["files"][f"torch_hub/{WAV2VEC2_CHECKPOINT}"] = _sha256(ckpt)
    record["revisions"] = {CHATTERBOX_REPO: CHATTERBOX_REVISION, KOKORO_REPO: KOKORO_REVISION}
    out = Path(os.environ.get("LEARN_NARRATOR_MODELS", "/app/models")) / "revisions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True))
    print(out.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
