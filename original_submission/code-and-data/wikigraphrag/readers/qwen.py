"""Local Qwen2.5-3B-Instruct reader (fp16) with a persistent generation cache.

Runs fully local on the GPU; every generation is cached to disk keyed by the prompt, so once
an experiment has run its numbers replay with no model load and no API key -- the paper's
"all LLM calls are cached" property. The same class doubles as the generator for the
LLM-judge (Table 2) and the claim-normalization front-end (Fig 3) via :meth:`generate`.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Sequence

from ..core.claim import Claim
from .base import build_prompt

DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CACHE_PATH = os.path.join(ROOT, "data", "cache", "qwen_generations.jsonl")


class QwenReader:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        max_new_tokens: int = 32,
        cache_path: str = CACHE_PATH,
        quantize: bool = False,
    ) -> None:
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens
        self.cache_path = cache_path
        self.quantize = quantize
        self._model = None
        self._tok = None
        self._cache: Dict[str, str] = {}
        self._load_cache()

    # -- cache --------------------------------------------------------------
    def _load_cache(self) -> None:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec["out"]

    def _key(self, prompt: str, max_new_tokens: int) -> str:
        blob = f"{self.model_name}\x00{max_new_tokens}\x00{prompt}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _store(self, key: str, out: str) -> None:
        self._cache[key] = out
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "out": out}, ensure_ascii=False) + "\n")

    # -- model --------------------------------------------------------------
    def _ensure(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        load_path = os.environ.get("QWEN_LOCAL_PATH", self.model_name)
        local_only = bool(os.environ.get("QWEN_LOCAL_PATH"))
        self._tok = AutoTokenizer.from_pretrained(
            load_path, local_files_only=local_only
        )
        if self.quantize:
            # 4-bit path (needs a working bitsandbytes; hangs on some Windows setups)
            from transformers import BitsAndBytesConfig

            self._model = AutoModelForCausalLM.from_pretrained(
                load_path,
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16,
                ),
                device_map="auto",
                local_files_only=local_only,
            )
        else:
            # fp16, no quantization -- reliable and fits an 8GB GPU for <=3B models
            self._model = AutoModelForCausalLM.from_pretrained(
                load_path, torch_dtype=torch.float16, device_map="cuda",
                local_files_only=local_only,
            )
        self._model.eval()

    # -- generation ---------------------------------------------------------
    def generate(self, prompt: str, max_new_tokens: Optional[int] = None) -> str:
        mnt = max_new_tokens or self.max_new_tokens
        key = self._key(prompt, mnt)
        if key in self._cache:
            return self._cache[key]
        self._ensure()
        import torch

        messages = [{"role": "user", "content": prompt}]
        text = self._tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tok(text, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(
                **inputs, max_new_tokens=mnt, do_sample=False,  # T=0 greedy, deterministic
                pad_token_id=self._tok.eos_token_id,
            )
        gen = out[0][inputs["input_ids"].shape[1]:]
        ans = self._tok.decode(gen, skip_special_tokens=True).strip()
        self._store(key, ans)
        return ans

    def generate_many(
        self,
        prompts: Sequence[str],
        max_new_tokens: Optional[int] = None,
        batch_size: int = 1,
    ) -> List[str]:
        """Generate several prompts with the same deterministic cache contract."""
        mnt = max_new_tokens or self.max_new_tokens
        prompts = list(prompts)
        answers: List[Optional[str]] = [None] * len(prompts)
        pending: Dict[str, tuple[str, List[int]]] = {}
        for index, prompt in enumerate(prompts):
            key = self._key(prompt, mnt)
            if key in self._cache:
                answers[index] = self._cache[key]
            else:
                if key not in pending:
                    pending[key] = (prompt, [])
                pending[key][1].append(index)

        missing = [
            (indices, prompt, key)
            for key, (prompt, indices) in pending.items()
        ]

        if missing:
            self._ensure()
            import torch

            self._tok.padding_side = "left"
            if self._tok.pad_token_id is None:
                self._tok.pad_token = self._tok.eos_token
            for start in range(0, len(missing), batch_size):
                batch = missing[start:start + batch_size]
                texts = [
                    self._tok.apply_chat_template(
                        [{"role": "user", "content": prompt}],
                        tokenize=False,
                        add_generation_prompt=True,
                    )
                    for _indices, prompt, _key in batch
                ]
                inputs = self._tok(texts, return_tensors="pt", padding=True).to(
                    self._model.device
                )
                with torch.no_grad():
                    outputs = self._model.generate(
                        **inputs, max_new_tokens=mnt, do_sample=False,
                        pad_token_id=self._tok.pad_token_id,
                    )
                input_width = inputs["input_ids"].shape[1]
                for row, (indices, _prompt, key) in enumerate(batch):
                    generated = outputs[row][input_width:]
                    answer = self._tok.decode(generated, skip_special_tokens=True).strip()
                    for index in indices:
                        answers[index] = answer
                    self._store(key, answer)

        return [answer or "" for answer in answers]

    def answer(self, query: str, contexts: Sequence[Claim]) -> str:
        return self.generate(build_prompt(query, contexts), self.max_new_tokens)

    def batch(self, queries: Sequence[str], contexts: Sequence[Sequence[Claim]]) -> List[str]:
        return [self.answer(q, cs) for q, cs in zip(queries, contexts)]
