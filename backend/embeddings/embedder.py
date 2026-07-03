"""Embedding model wrapper (ported from the old pipeline, schema_rag/embedder.py).

Primary  : unsloth/Qwen3-Embedding-4B via sentence-transformers, loaded in 4-bit
           (bitsandbytes NF4). Qwen3-Embedding is asymmetric: the QUERY side gets an
           "Instruct: ...\\nQuery: ..." prefix while documents are embedded raw, and it
           pools on the last token (left padding). 4-bit loading needs a CUDA GPU plus
           `bitsandbytes` + `accelerate`.
Fallback : a deterministic, dependency-free hashing embedder so the app *plumbing*
           runs even without torch (UI/dev only). NOTE: with EMBEDDER=st (recommended)
           the fallback is disabled and a load failure raises loudly instead of
           silently producing a dimension-mismatched index.

All vectors are L2-normalized so cosine similarity == dot product.
"""
from __future__ import annotations

import hashlib
import re
from typing import List

import numpy as np

from backend import config
from backend.common.logging import get_logger

log = get_logger(__name__)


def _normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat[None, :]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class SentenceTransformerEmbedder:
    """Real embedding model (Qwen3-Embedding-4B in 4-bit by default).

    Asymmetric encoding: pass ``is_query=True`` to prepend the Qwen3 instruction prefix
    to queries. Documents are embedded without a prefix.
    """

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy import

        self.model_name = model_name
        self.query_instruction = config.EMBED_QUERY_INSTRUCTION.strip()

        if config.EMBED_LOAD_IN_4BIT:
            # 4-bit (bitsandbytes) path. accelerate/bitsandbytes pin the quantized weights
            # on the GPU, so we must NOT pass device= or call .to(device) afterwards.
            self.device = "cuda"
            self.model = self._load_4bit(SentenceTransformer, model_name)
            log.info("device = cuda (4-bit / bitsandbytes nf4)")
        else:
            device = self._resolve_device(config.EMBED_DEVICE)
            self.device = device
            self.model = SentenceTransformer(model_name, device=device)
            log.info("device = %s", device)

        if hasattr(self.model, "get_embedding_dimension"):
            self.dim = int(self.model.get_embedding_dimension())
        else:
            self.dim = int(self.model.get_sentence_embedding_dimension())

    @staticmethod
    def _load_4bit(SentenceTransformer, model_name: str):
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError(
                "EMBED_LOAD_IN_4BIT=1 but torch.cuda.is_available() is False. 4-bit "
                "(bitsandbytes) embedding requires a CUDA GPU. Install a CUDA torch build "
                "(cu128 wheel) and run on a GPU host, or set EMBED_LOAD_IN_4BIT=0 to load "
                "the model in full precision."
            )
        try:
            from transformers import BitsAndBytesConfig
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "EMBED_LOAD_IN_4BIT=1 requires `bitsandbytes` and `accelerate` "
                "(pip install bitsandbytes accelerate). Underlying import error: "
                f"{exc.__class__.__name__}: {exc}"
            ) from exc

        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        # Qwen3-Embedding pools on the last token, so padding must be on the left.
        return SentenceTransformer(
            model_name,
            model_kwargs={"quantization_config": bnb},
            tokenizer_kwargs={"padding_side": "left"},
        )

    @staticmethod
    def _resolve_device(pref: str) -> str:
        import torch

        usable = torch.cuda.is_available()
        if pref == "cpu":
            return "cpu"
        if pref == "cuda":
            if not usable:
                log.warning(
                    "EMBED_DEVICE=cuda but torch.cuda.is_available() is False; using CPU. "
                    "Check that torch's CUDA build matches your driver (cu128)."
                )
                return "cpu"
            return "cuda"
        # auto
        if not usable:
            log.warning(
                "no usable CUDA device; running the embedder on CPU. If this box has a GPU, "
                "the torch CUDA build likely does not match the driver -- reinstall the cu128 wheel."
            )
        return "cuda" if usable else "cpu"

    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        texts = list(texts)
        if is_query and self.query_instruction:
            # Qwen3-Embedding query format: "Instruct: {task}\nQuery:{query}".
            texts = [f"Instruct: {self.query_instruction}\nQuery:{t}" for t in texts]
        vecs = self.model.encode(
            texts,
            batch_size=config.EMBED_BATCH_SIZE,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > config.EMBED_BATCH_SIZE,
        )
        return np.asarray(vecs, dtype=np.float32)


class HashingEmbedder:
    """Dependency-free fallback: hashed character n-grams -> fixed-dim vector.

    Not semantic like a transformer, but good enough to exercise the app plumbing
    offline. Deterministic across runs. Its dim (768) will NOT match a Qwen3 index.
    """

    def __init__(self, dim: int = 768, ngram: int = 3):
        self.dim = dim
        self.ngram = ngram
        self.model_name = f"hashing-{dim}d"

    def _vec(self, text: str) -> np.ndarray:
        text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
        tokens = text.split()
        v = np.zeros(self.dim, dtype=np.float32)
        grams: List[str] = list(tokens)  # whole words
        for tok in tokens:                # + char n-grams for sub-word matching
            padded = f"#{tok}#"
            for i in range(len(padded) - self.ngram + 1):
                grams.append(padded[i : i + self.ngram])
        for g in grams:
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            v[idx] += sign
        return v

    def encode(self, texts: List[str], is_query: bool = False) -> np.ndarray:
        # The hashing fallback is symmetric; is_query is accepted for interface parity.
        return _normalize(np.vstack([self._vec(t) for t in texts]))


_INSTANCE = None


def get_embedder():
    """Return a singleton embedder according to config.EMBEDDER."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    mode = config.EMBEDDER
    if mode in ("st", "auto"):
        try:
            _INSTANCE = SentenceTransformerEmbedder(config.EMBED_MODEL)
            log.info("using sentence-transformers: %s (dim=%d)", config.EMBED_MODEL, _INSTANCE.dim)
            return _INSTANCE
        except Exception as exc:  # noqa: BLE001
            if mode == "st":
                raise
            log.warning(
                "sentence-transformers/%s unavailable (%s: %s). Falling back to hashing "
                "embedder. WARNING: the hashing fallback is 768-dim; it will NOT match a "
                "Qwen3 (2560-dim) index. Set EMBEDDER=st to fail loudly.",
                config.EMBED_MODEL, exc.__class__.__name__, exc,
            )
    _INSTANCE = HashingEmbedder()
    log.info("using fallback embedder: %s", _INSTANCE.model_name)
    return _INSTANCE
