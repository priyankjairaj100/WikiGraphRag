"""Smoke test: load the default Qwen2.5-3B-Instruct reader (fp16) and run one generation.

Triggers the model download on first run, then validates a single deterministic generation and
prints GPU memory use. Writes a small status file so progress is visible even if stdout is
swallowed.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qwen_smoke.log")


def log(msg: str) -> None:
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


if __name__ == "__main__":
    open(STATUS, "w").close()
    log("importing QwenReader")
    from wikigraphrag.readers.qwen import QwenReader

    log("constructing reader (this downloads ~15GB on first run)")
    reader = QwenReader(max_new_tokens=16)
    log("first generation (loads model into GPU)")
    t0 = time.time()
    ans = reader.generate("Reply with exactly the word: ready", max_new_tokens=8)
    log(f"generation done in {time.time()-t0:.1f}s -> {ans!r}")

    try:
        import torch

        if torch.cuda.is_available():
            log(f"gpu_mem_allocated={torch.cuda.memory_allocated()/1e9:.2f}GB "
                f"reserved={torch.cuda.memory_reserved()/1e9:.2f}GB")
    except Exception as e:  # noqa: BLE001
        log(f"mem check err: {e}")
    log("SMOKE_OK")
