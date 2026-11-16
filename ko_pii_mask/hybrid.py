"""규칙 결과와 모델 결과를 합친다.

형식이 고정된 유형(Class R)은 규칙이, 문맥 유형(Class C)은 모델이 잘 잡는다. 한쪽에 넘기지 않고 **양쪽을 다 쓰고 겹치는
자리만 정리한다**(union) — 가리기는 놓치지 않는 쪽이 우선이다. 겹칠 때 Class R이면 규칙의 라벨을 남긴다(예: 주민등록번호와
외국인등록번호는 7번째 자리 하나로 갈리는데 규칙은 그 자리를 읽는다). 규칙이 못 찾은 자리는 모델의 스팬이 그대로 남는다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import baseline
from . import schema
def load(path: Path) -> dict[str, list[tuple[str, int, int, float]]]:
    out: dict[str, list[tuple[str, int, int, float]]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out[rec["uid"]] = [(s["label"], s["start"], s["end"],
                                float(s.get("score", 1.0)))
                               for s in rec.get("spans", [])]
    return out


def merge(rule: dict, model: dict,
          rule_types: frozenset[str]) -> dict[str, list]:
    """타입별로 담당을 갈라 가져온 뒤 겹침만 정리한다.

    겹침 정리는 `baseline.resolve`를 그대로 쓴다. 규칙과 모델이 같은 자리를
    다르게 잡는 경우가 있고(카드 vs 계좌), 그때는 점수가 높은 쪽을 남긴다.
    BIO 태깅과 평가가 모두 스팬 비겹침을 전제하므로 반드시 거쳐야 한다.
    """
    out: dict[str, list] = {}
    for uid in set(rule) | set(model):
        picked = [s for s in rule.get(uid, []) if s[0] in rule_types]
        picked += [s for s in model.get(uid, []) if s[0] not in rule_types]
        out[uid] = baseline.resolve(picked)
    return out


BOOST = 10.0
"""겹침 정리에서 규칙 스팬을 앞세우는 데만 쓰는 값. 밖으로 나가지 않는다."""


def merge_union(rule: dict, model: dict,
                rule_types: frozenset[str]) -> dict[str, list]:
    """양쪽을 다 쓰고 겹치는 자리만 정리한다. 제품 기본값.

    `resolve`는 (-점수, -길이, 시작) 순으로 욕심껏 고르니, 규칙 스팬의 점수에
    `BOOST`를 얹으면 겹치는 자리에서 규칙이 이긴다. 규칙이 아무것도 못 찾은
    자리에는 경쟁자가 없으므로 모델 스팬이 그대로 남는다.

    얹은 값을 빼서 되돌리지 않고 **원래 점수를 따로 들고 간다.** 0.82에
    10을 더하고 빼면 0.8200000000000003이 되고, 그 점수가 그대로 임계값
    스윕에 들어간다. 1을 넘는 점수가 새어 나가면 곡선이 평평해진다.

    **규칙의 기여는 담당 타입으로 제한한다.** 모델은 전부 기여한다. 규칙이
    Class C까지 보태게 하면 규칙의 PERSON(정밀도가 낮다)이
    그대로 들어와 과잉 마스킹이 크게 는다. 규칙을 믿는 범위가
    Class R이라는 게 이 프로젝트의 논지이고, 합치는 코드도 그 선을 지켜야 한다.
    """
    out: dict[str, list] = {}
    for uid in set(rule) | set(model):
        original: dict[tuple[str, int, int], float] = {}
        picked: list[tuple[str, int, int, float]] = []
        for c, s, e, sc in rule.get(uid, []):
            if c not in rule_types:
                continue
            original[(c, s, e)] = sc
            picked.append((c, s, e, sc + BOOST))
        for c, s, e, sc in model.get(uid, []):
            original.setdefault((c, s, e), sc)
            picked.append((c, s, e, sc))
        out[uid] = [(c, s, e, original[(c, s, e)])
                    for c, s, e, _ in baseline.resolve(picked)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="규칙 + 모델 예측 합치기")
    ap.add_argument("--rule", type=Path, required=True, help="규칙 예측 jsonl")
    ap.add_argument("--model", type=Path, required=True, help="모델 예측 jsonl")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rule-types", help="규칙에 맡길 타입. 기본은 Class R 전부")
    ap.add_argument("--mode", choices=("union", "axis"), default="union",
                    help="union: 양쪽 다 쓴다(제품 기본값) / "
                         "axis: 타입 담당을 갈라 한쪽만 쓴다")
    args = ap.parse_args()

    rule_types = (frozenset(args.rule_types.split(","))
                  if args.rule_types else schema.R_CODES)
    unknown = rule_types - set(schema.CODES)
    if unknown:
        raise SystemExit(f"스키마에 없는 타입: {sorted(unknown)}")

    fn = merge_union if args.mode == "union" else merge
    merged = fn(load(args.rule), load(args.model), rule_types)
    with args.out.open("w", encoding="utf-8") as f:
        for uid, spans in merged.items():
            f.write(json.dumps({
                "uid": uid,
                "spans": [{"label": c, "start": s, "end": e,
                           "score": round(sc, 4)} for c, s, e, sc in spans],
            }, ensure_ascii=False) + "\n")

    total = sum(len(v) for v in merged.values())
    if args.mode == "axis":
        print(f"규칙 담당 {len(rule_types)}종: {', '.join(sorted(rule_types))}")
        print("모델 담당: 나머지 "
              f"({', '.join(sorted(set(schema.CODES) - rule_types))})")
    else:
        print(f"양쪽 다 씀. 겹치면 우선: {', '.join(sorted(rule_types))}는 규칙")
    print(f"{args.out}  문서 {len(merged):,}  스팬 {total:,}")


if __name__ == "__main__":
    main()
