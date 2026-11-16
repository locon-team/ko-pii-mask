"""한국어 개인정보 탐지·가명화 — 기기 안에서만 동작한다.

    from ko_pii_mask import PiiDetector
    det = PiiDetector("path/to/model")            # 모델 폴더: model_int8w8.onnx, tokenizer.json, tags.json
    masked, table = det.mask("김민수 고객님, 010-1234-5678로 연락 주세요.")
    # masked = "[PERSON_1] 고객님, [PHONE_1]로 연락 주세요." — table은 기기 밖으로 내보내지 않는다
    answer = det.restore(llm_answer, table)

흐름: 모델 스팬 → 규칙(정규식·체크섬) 합치기 → 경계 다듬기 → 사람 없는 일반 문장의 민감 낱말 풀기 → 개인 문맥 지역 이름 규칙.
"""
from __future__ import annotations

from pathlib import Path

from . import baseline, hybrid, postprocess, region_rule, schema


def finish(texts: list[str], model_spans: list[list[tuple]], rules: bool = True, post: bool = True, gate: bool = True,
           region: bool = True) -> list[list[dict]]:
    """모델 스팬((label, start, end, score)) 뒤의 공통 처리."""
    uids = [str(i) for i in range(len(texts))]
    found = dict(zip(uids, [list(s) for s in model_spans]))
    if rules:
        rule = {u: baseline.detect(t) for u, t in zip(uids, texts)}
        found = hybrid.merge_union(rule, found, schema.R_CODES)
    out = []
    for u, text in zip(uids, texts):
        spans = found.get(u, [])
        if post:
            spans = postprocess.refine(text, spans)
        if gate:
            spans = postprocess.gate_impersonal(text, spans)
        if region and "ADDRESS" in schema.CODES:
            spans = region_rule.apply(text, spans)
        spans = sorted(spans, key=lambda s: s[1])
        out.append([{"label": c, "start": s, "end": e, "text": text[s:e], "score": round(float(sc), 4)}
                    for c, s, e, sc in spans])
    return out


class PiiDetector:
    """model_dir=None이면 규칙만 쓴다(번호·이메일 등 꼴이 정해진 것)."""

    def __init__(self, model_dir: str | Path | None, threads: int = 2, file: str = "model_int8w8.onnx",
                 rules: bool = True, postprocess: bool = True, impersonal_gate: bool = True, region_rule: bool = True) -> None:
        self.ner = None
        if model_dir is not None:
            from .runtime import OnnxNer
            self.ner = OnnxNer(model_dir, threads=threads, file=file)
        self.opts = dict(rules=rules, post=postprocess, gate=impersonal_gate, region=region_rule)

    def detect_many(self, texts: list[str]) -> list[list[dict]]:
        model = self.ner.spans(texts) if self.ner else [[] for _ in texts]
        model = [postprocess.merge_overlaps(m) for m in model]
        return finish(texts, model, **self.opts)

    def detect(self, text: str) -> list[dict]:
        """[{"label", "start", "end", "text", "score"}, ...] — 시작 위치 순."""
        return self.detect_many([text])[0]

    def mask(self, text: str, spans: list[dict] | None = None) -> tuple[str, dict[str, str]]:
        """찾은 값을 [LABEL_n]으로 바꾼다. 같은 값은 같은 코드를 받는다. (가린 글, 대응표)"""
        if spans is None:
            spans = self.detect(text)
        alias_of: dict[tuple[str, str], str] = {}
        counters: dict[str, int] = {}
        table: dict[str, str] = {}
        for sp in spans:
            key = (sp["label"], text[sp["start"]:sp["end"]])
            if key not in alias_of:
                counters[sp["label"]] = counters.get(sp["label"], 0) + 1
                code = schema.alias(sp["label"], counters[sp["label"]])
                alias_of[key] = code
                table[code] = key[1]
        out = text
        for sp in sorted(spans, key=lambda s: s["start"], reverse=True):
            out = out[:sp["start"]] + alias_of[(sp["label"], text[sp["start"]:sp["end"]])] + out[sp["end"]:]
        return out, table

    @staticmethod
    def restore(text: str, table: dict[str, str]) -> str:
        """답변 속 코드를 원래 값으로 되돌린다."""
        for code in sorted(table, key=len, reverse=True):
            text = text.replace(code, table[code])
        return text
