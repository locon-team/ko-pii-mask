"""ONNX Runtime 실행기 — PyTorch 없이 onnxruntime + tokenizers만 쓴다."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class OnnxNer:
    """PyTorch 없는 예측기. spans(texts) → 문서마다 [(label, start, end, score)] (pretrained_ner.predict와 같은 꼴)."""

    def __init__(self, folder: str | Path, int8: bool = True, threads: int = 2, max_len: int = 256, stride: int = 64, file: str | None = None,
                 batch: int = 8) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        folder = Path(folder)
        meta = json.loads((folder / "tags.json").read_text(encoding="utf-8"))
        self.tags, self.cls, self.sep, self.pad = meta["tags"], meta["cls"], meta["sep"], meta["pad"]
        self.tok = Tokenizer.from_file(str(folder / "tokenizer.json"))
        so = ort.SessionOptions(); so.intra_op_num_threads = threads; so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(folder / (file or ("model_int8.onnx" if int8 else "model.onnx"))), so,
                                         providers=["CPUExecutionProvider"])
        self.max_len, self.stride, self.batch = max_len, stride, batch

    def _windows(self, text: str):
        enc = self.tok.encode(text, add_special_tokens=False)
        ids, offs = enc.ids, enc.offsets
        body, start, out = self.max_len - 2, 0, []
        while True:
            end = min(start + body, len(ids))
            out.append(([self.cls] + ids[start:end] + [self.sep], offs[start:end], start))
            if end >= len(ids):
                break
            start = end - self.stride
        return out

    def spans(self, texts: list[str]) -> list[list[tuple]]:
        wins = [(di, w) for di, t in enumerate(texts) for w in self._windows(t)]
        per_doc: dict[int, dict[int, tuple]] = {}
        for i in range(0, len(wins), self.batch):
            chunk = wins[i:i + self.batch]
            n = max(len(w[0]) for _, w in chunk)
            x = np.full((len(chunk), n), self.pad, np.int64); a = np.zeros_like(x)
            for j, (_, (ids, _, _)) in enumerate(chunk):
                x[j, :len(ids)] = ids; a[j, :len(ids)] = 1
            logits = self.sess.run(None, {"input_ids": x, "attention_mask": a})[0].astype(np.float32)
            e = np.exp(logits - logits.max(-1, keepdims=True)); probs = e / e.sum(-1, keepdims=True)
            for j, (di, (_, offs, start)) in enumerate(chunk):
                d = per_doc.setdefault(di, {})
                for k, off in enumerate(offs):
                    if start + k not in d:          # pretrained_ner와 같이 처음 본 창을 쓴다
                        d[start + k] = (tuple(off), probs[j, k + 1])
        return [self._decode(list(per_doc.get(di, {}).values())) for di in range(len(texts))]

    def _decode(self, token_probs) -> list[tuple]:
        spans, cur = [], None
        for (a, b), p in sorted(token_probs, key=lambda x: x[0][0]):
            k = int(p.argmax()); t = self.tags[k]
            if t == "O":
                if cur: spans.append(cur)
                cur = None
                continue
            typ = t[2:]
            if cur and cur[0] == typ and t.startswith("I-"):
                cur = (typ, cur[1], b, min(cur[3], float(p[k])))
            else:
                if cur: spans.append(cur)
                cur = (typ, a, b, float(p[k]))
        if cur: spans.append(cur)
        return [(c, s, e, sc) for c, s, e, sc in spans if e > s]
