"""탐지 결과 경계 다듬기와 문서 단위 판단 (재학습 없음).

1. **표 행 번호를 뗀다.** 표에서 날짜가 다음 행 번호까지 먹는 경우("2020년 8월⏎2 | 박예연") 줄바꿈 앞에서 자른다.
2. **문맥형 유형은 낱말 경계까지 넓힌다.** "만성신|부전", "스리|랑카계"처럼 낱말 일부만 가리면 나머지 글자로 뜻이 드러난다.
   오른쪽에 붙은 조사·"님"은 넓히지 않는다("만성신부전으로" → "만성신부전").
3. **사람이 없는 일반 문장**(법 조문·안내문·통계)의 병명·종교·노조 같은 낱말은 개인정보가 아니므로 푼다(gate_impersonal).
행 번호 말고는 가린 글자를 줄이지 않는다. "2019년 보|이스피싱"처럼 날짜가 다음 낱말 첫 글자를 먹는 경우는 떼지 않는다 —
떼면 그 낱말이 통째로 드러난다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import schema
Span = tuple[str, int, int, float]

#: 낱말 경계까지 넓힐 타입. 형식이 있는 타입(Class R)·날짜·금액·나이·주소는 넓히지 않는다.
SNAP_CODES = {"PERSON", "ORG", "TITLE", "HEALTH", "CRIME", "BELIEF", "POLITICS", "UNION", "SEXLIFE",
              "ETHNIC", "OCCUPATION"}

#: 낱말 오른쪽에 붙는 조사·어미·호칭. 넓힐 조각이 이것으로 끝나면 그 부분은 빼고 넓힌다. 긴 것부터 본다.
SUFFIXES = sorted({
    "으로부터", "으로서는", "으로써", "으로서", "으로는", "으로", "로부터", "로서", "로써", "로는", "로",
    "에서는", "에서", "에게는", "에게", "에는", "께서는", "께서", "한테", "까지", "부터", "처럼", "보다", "마다",
    "이라고", "라고", "이라는", "라는", "이라며", "라며", "이었다", "였다", "이다", "입니다", "이며", "이고", "이나",
    "이랑", "랑", "임을", "임", "인", "이에요", "예요", "이고요",
    "은", "는", "이", "가", "을", "를", "의", "에", "과", "와", "도", "만", "나", "께",
    "님께서", "님께", "님은", "님이", "님의", "님을", "님", "씨는", "씨가", "씨의", "씨",
}, key=len, reverse=True)

MAX_WORD = 10
DATE_WORD = re.compile(r"[0-9０-９]|년|월|일|오전|오후|상순|중순|하순|초순|분기|반기|봄|여름|가을|겨울|새벽|아침|저녁|"
                       r"^경$|^께$|^말$|^초$|^중$|^쯤$|^무렵$|시|분")


def _is_word(ch: str) -> bool:
    return ch.isalnum() or ch in "·-_"


def cut_row_numbers(text: str, spans: list[Span]) -> list[Span]:
    """날짜 끝의 "⏎ 숫자 한두 자"가 표 행 번호면(뒤가 | 또는 탭) 줄바꿈 앞에서 자른다."""
    out = []
    for label, s, e, sc in spans:
        if label == "DATE":
            m = re.search(r"\n[ \t]*[0-9]{1,2}\.?$", text[s:e])
            if m and re.match(r"[ ]*[|\t]", text[e:]) and m.start() > 0:
                e = s + m.start()
        out.append((label, s, e, sc))
    return out


def snap_words(text: str, spans: list[Span]) -> list[Span]:
    out = []
    for label, s, e, sc in spans:
        if label in SNAP_CODES and any("가" <= ch <= "힣" for ch in text[s:e]):
            lo, hi = s, e
            while lo > 0 and _is_word(text[lo - 1]) and _is_word(text[lo]):
                lo -= 1
            while hi < len(text) and _is_word(text[hi]) and _is_word(text[hi - 1]):
                hi += 1
            if (lo, hi) != (s, e) and _word_len(text, lo, hi) <= MAX_WORD:
                piece = text[e:hi]
                for suf in SUFFIXES:
                    if piece.endswith(suf):
                        piece = piece[: len(piece) - len(suf)]
                        break
                s, e = lo, e + len(piece)
        out.append((label, s, e, sc))
    return out


def _word_len(text: str, lo: int, hi: int) -> int:
    """스팬 안 공백으로 나뉜 조각 중 가장 긴 것의 길이."""
    return max(len(w) for w in text[lo:hi].split())


def merge_overlaps(spans: list[Span]) -> list[Span]:
    """넓히다 겹친 스팬은 하나로 합친다. 라벨은 긴 쪽(같으면 앞쪽)을 따른다."""
    out: list[Span] = []
    for sp in sorted(spans, key=lambda x: (x[1], -x[2])):
        if out and sp[1] < out[-1][2]:
            prev = out[-1]
            keep = prev if prev[2] - prev[1] >= sp[2] - sp[1] else sp
            out[-1] = (keep[0], prev[1], max(prev[2], sp[2]), max(prev[3], sp[3]))
        else:
            out.append(sp)
    return out


def refine(text: str, spans: list[Span]) -> list[Span]:
    spans = cut_row_numbers(text, spans)
    spans = drop_label_persons(text, spans)
    spans = snap_words(text, spans)
    spans = extend_speaker_names(text, spans)
    return merge_overlaps(spans)


#: 대화 화자 칸("정다은: …", "[메신저] 정다은 : …"). 모델이 이름 끝 '은·이'를 조사로 보고 떼어 "정다"만 가렸다.
_SPEAKER_TAIL = re.compile(r"[가-힣]\s?[:：]")


def extend_speaker_names(text: str, spans: list[Span]) -> list[Span]:
    """이름 스팬 바로 뒤가 한 글자 + 쌍점이면(대화 화자 칸) 그 글자까지 이름으로 본다. 이름이 4자를 넘으면 늘리지 않는다."""
    out = []
    for sp in spans:
        label, s, e, sc = sp
        if label == "PERSON" and e - s <= 3 and _SPEAKER_TAIL.match(text, e) and (s == 0 or not ("가" <= text[s - 1] <= "힣")):
            sp = (label, s, e + 1, sc)
        out.append(sp)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="탐지 결과 경계 다듬기")
    ap.add_argument("--text", type=Path, required=True, help="원문 jsonl (uid, source_text)")
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    texts = {}
    with args.text.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            texts[r["uid"]] = r["source_text"]
    with args.pred.open(encoding="utf-8") as f, args.out.open("w", encoding="utf-8") as g:
        for line in f:
            r = json.loads(line)
            spans = [(s["label"], s["start"], s["end"], s.get("score", 1.0)) for s in r["spans"]]
            spans = refine(texts[r["uid"]], spans)
            g.write(json.dumps({"uid": r["uid"], "spans": [
                {"label": c, "start": s, "end": e, "score": sc} for c, s, e, sc in spans]},
                ensure_ascii=False) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- 문서 단위 판단 

#: 이 타입들은 **특정한 사람에 대한 것일 때만** 개인정보다(병명·죄명·종교·정치·노조·성적 지향·출신).
GATED_CODES = {"HEALTH", "CRIME", "BELIEF", "POLITICS", "UNION", "SEXLIFE", "ETHNIC"}

#: 찾은 스팬 중 이 타입이 하나라도 있으면 문서에 특정한 사람이 있다고 본다.
PERSON_EVIDENCE_CODES = {"PERSON", "RRN", "FRN", "PASSPORT", "PHONE", "EMAIL", "ACCOUNT", "CARD", "USERID",
                         "DOB", "AGE", "CASE_NO", "TITLE", "VEHICLE"}

#: 이름 없이 특정인을 가리키는 말. 평가 셋에 맞추지 않으려고 낱말이 아니라 범주로 넓게 잡았다:
#: 1인칭, 친족, 특정인을 가리키는 역할 명사(조사가 붙은 꼴), 3인칭 대명사, 익명 호칭.
PERSON_CUES = re.compile(
    r"(?:^|[^가-힣])(?:저는|저도|제가|저의|저희|저랑|저한테|제\s|저\s|나는|내가|나의|본인|자신)"
    # 친족 호칭 체계 전체(처음 목록에 '삼촌'이 없어 놓친 적이 있다)
    r"|남편|아내|배우자|부인|아버지|어머니|아빠|엄마|부친|모친|부모|아들|딸이|딸은|딸의|딸도|자녀|자식|아이가|아이는|아이의"
    r"|할머니|할아버지|조부|조모|외할|친할|손자|손녀|증손|시어머니|시아버지|시부모|시댁|시누|시동생|며느리|사위"
    r"|장인|장모|처남|처제|처형|동서|형부|제부|매형|매부|올케|형수|제수|형이|형은|형의|형도|누나|오빠|언니|동생|남매|형제|자매"
    r"|삼촌|외삼촌|숙부|숙모|백부|백모|고모|이모|이모부|고모부|당숙|사촌|조카|친척|가족|식구|보호자"
    r"|(?:^|[^가-힣\d])\d{1,3}\s*(?:세|살)(?:[^가-힣]|$|\s*(?:여성|남성|여자|남자|여아|남아))"
    # 역할 명사는 뒤에 무엇이 와도 근거로 본다 — 모델이 이름을 놓친 "신청인유영연은", "채무자 남도의"도 지킨다
    r"|환자|내담자|신청인|청구인|민원인|채무자|채권자|피고인|피고|원고|피해자|가해자|지원자|응시자"
    r"|직원|학생|고객|대상자|당사자|수급자|의뢰인|상담자|입소자|근로자"
    # 양식 칸 이름(표 머리 포함)과 인적 사항
    r"|(?:성명|이름|환자명|성별|생년월일|연령|나이)\s*[:：|\t]|\d0대\s*(?:여성|남성|여자|남자)"
    r"|(?:^|[^가-힣])(?:그는|그녀는|그가|그녀가|그의|그녀의|그를|그녀를)"
    r"|[A-Z○◯]\s?씨|모\s?씨|고객님"
)


#: 일반 문장이라는 근거. 사람 표현은 열린 집합이라 목록이 늘 모자란다('삼촌', '대리님',
#: '아주머니'가 나왔다). 그래서 사람 근거가 없을 뿐 아니라 **일반 문장 근거가 있어야만** 푼다 — 둘 다 모르는 글은
#: 가린 채 둔다(실패가 안전한 쪽으로). 낱말이 아니라 문서 종류로 잡았다: 법조문, 통계·조사, 안내·공지·모집,
#: 정책·제도·보도, 연구·분석, 단체의 공식 활동.
IMPERSONAL_CUES = re.compile(
    r"제\s?\d+\s?조|법률|시행령|규정|조례|고시|부칙|법 개정|개정안"
    r"|통계|조사 결과|조사에서|조사에 따르면|설문|비율|비중|조직률|%|퍼센트|전년|평균|추세|현황|증가|감소|늘었|줄었|늘어|줄어"
    r"|안내|공지|알림|모집|강좌|강의|교육|세미나|설명회|박람회|행사|캠페인|프로그램|과정|신청 기간|참가"
    r"|정책|제도|대책|시행|발표|단속|예방|검진|지원 사업|지원금|예산|보도|기자|브리핑|성명"
    r"|연구|분석|논문|보고서|학술|토론회|간담회"
    r"|협상안|교섭|파업|집회|총회|창립|출범|대회|축제"
)


def is_impersonal(text: str) -> bool:
    """제도·통계·안내·보도·연구 같은 일반 문장이라는 근거가 있는가."""
    return bool(IMPERSONAL_CUES.search(text))


def has_person(text: str, spans: list[Span]) -> bool:
    """문서에 특정한 사람이 있다는 근거가 있는가(찾은 사람·식별자 스팬, 또는 사람을 가리키는 말)."""
    return any(sp[0] in PERSON_EVIDENCE_CODES for sp in spans) or bool(PERSON_CUES.search(text))


def gate_impersonal(text: str, spans: list[Span]) -> list[Span]:
    """특정한 사람이 없고 일반 문장 근거가 있는 문서에서만 민감 타입 스팬을 푼다
    ("천주교 신자 수가 줄었다", "금속노조는 협상안을 공개했다").

    사람 근거가 하나라도 있거나 일반 문장 근거가 없으면 그대로 둔다 — 잘못 풀면 유출이다.
    """
    if has_person(text, spans) or not is_impersonal(text):
        return spans
    return [sp for sp in spans if sp[0] not in GATED_CODES and not _short_region(text, sp)
            and not _suffixless_org(text, sp)]


#: 기관 이름의 유형 끝말(일반 목록 — 평가 셋 낱말에 맞추지 않았다). 이전 모델은 사람 없는 일반 문장에서 민감 낱말·전문용어를
#: 기관으로 잡았다("[ORG:뇌경색] 환자의 재입원율", "[ORG:천주교] 신자 수"). 기관으로 잡혀 GATED_CODES를 비켜 갔다.
ORG_END = re.compile(
    r"(?:회사|㈜|\(주\)|산업|기술|엔지니어링|물산|건설|전자|제약|유통|그룹|홀딩스|상사|무역|식품|화학|물류|운수|통신|에너지|자동차|"
    r"개발|컨설팅|출판사|신문사|방송|방송사|호텔|마트|백화점|카페|제과|농장|은행|금고|캐피탈|카드|증권|보험|생명|화재|투자|법인|"
    r"사무소|대학교|대학|대학원|학교|학원|유치원|어린이집|교육원|연구원|연구소|진흥원|공단|공사|센터|재단|협회|조합|연합회|위원회|"
    r"병원|의원|치과|한의원|약국|요양원|조리원|복지관|도서관|구청|시청|군청|도청|법원|검찰청|경찰서|소방서|세무서|우체국|등기소|"
    r"노조|노동조합|교회|성당|정당|당|부|처|청|원|단|회|사)\s*$")


def _suffixless_org(text: str, sp: Span) -> bool:
    """기관 유형 끝말이 없는 '기관' 스팬. 사람 없는 일반 문서에서만 푼다 — 특정인과 이어지지 않는 이름은 개인정보가 아니고,
    끝말 없는 브랜드 이름("카카오")도 이런 문서에서는 풀린다. 사람이 있는 문서에서는 그대로 가린다."""
    label, s, e, _ = sp
    value = text[s:e]
    return label == "ORG" and not ORG_END.search(value) and not ORG_START.match(value)


#: 앞에 붙는 기관 유형("주식회사 동방", "㈜한결"). 끝말만 보면 이런 이름을 놓친다.
ORG_START = re.compile(r"\s*(?:주식회사|유한회사|㈜|\(주\)|\(유\)|재단법인|사단법인|학교법인|의료법인|사회복지법인|법무법인|농업회사법인)")


def _short_region(text: str, sp: Span) -> bool:
    """숫자 없는 짧은 주소(시도·시군구·동네 이름). 특정인의 위치가 아니면 개인정보가 아니다.
    도로명·번지가 있는 전체 주소는 그대로 가린다."""
    label, s, e, _ = sp
    value = text[s:e]
    return label == "ADDRESS" and not re.search(r"\d", value) and len(value) <= 10 and len(value.split()) <= 2


# ---------------------------------------------------------------- 칸 이름·상태 낱말을 이름으로 잡은 것 

#: 칸 이름에 쓰는 명사. 이 낱말로 끝나는 이름은 거의 없다. 학습 데이터의 칸 이름이 몇 가지로 고정돼 있어 모델이
#: 처음 보는 칸 이름("고충 유형:")을 메신저 대화("홍민연: …")의 이름처럼 잡는다.
LABEL_NOUN = re.compile(
    r"(?:^|(?<=\s)|(?<=[가-힣]))(?:유형|사항|내용|기간|일자|일시|날짜|시간|방법|범위|결과|의견|구분|경로|단계|장소|대상|항목|현황|사유|여부|기준|"
    r"목적|분류|형태|조치|계획|요청|요지|요약|비고|번호|명칭|서류|기관|부서|금액|비용|요금|분야|종류|상태|주제|과목|"
    r"출처|근거|안내|문의|참고|공지|질문|답변|제목|첨부|위치|규모|수준|정도|빈도|횟수|등급|점수|조건|절차|일정)$")

#: 표 칸 하나를 통째로 차지한 상태·수량 낱말(이름이 아니다). 예: "60대 이상 | 높음 | 다수"의 '다수'.
STATUS_WORDS = {"다수", "소수", "일부", "전체", "전원", "없음", "해당 없음", "해당없음", "미정", "완료", "진행 중", "진행중",
                "검토 중", "검토중", "예정", "보류", "상시", "수시", "매일", "매주", "매월", "높음", "낮음", "보통", "양호",
                "미흡", "적합", "부적합", "가능", "불가", "필수", "선택", "있음", "확인", "미확인", "기타", "공통", "별도"}


def drop_label_persons(text: str, spans: list[Span]) -> list[Span]:
    """이름(PERSON)으로 잡혔지만 줄 맨 앞 칸 이름이거나 표 칸 하나를 채운 상태 낱말인 것을 뺀다."""
    out = []
    for sp in spans:
        label, s, e, _ = sp
        if label == "PERSON":
            word = text[s:e].strip()
            line_start = text.rfind("\n", 0, s) + 1
            before, after = text[line_start:s], text[e:e + 3]
            # 1) 줄 맨 앞(말머리 기호만 허용) + 바로 뒤 쌍점 + 칸 이름 명사로 끝남
            # "박기준: 안녕"(메신저의 진짜 이름)은 지킨다: 띄어 쓴 구간, 칸 이름 명사 그 자체, 다섯 글자 이상만 뺀다
            shape_ok = " " in word or LABEL_NOUN.fullmatch(word) or len(word) >= 5
            if (shape_ok and re.fullmatch(r"[\s\-•·*□■○●\[\]()0-9.]*", before) and re.match(r"\s*[:：]", after)
                    and LABEL_NOUN.search(word)):
                continue
            # 2) 표 칸 하나를 통째로 차지한 상태 낱말
            cell_l = re.search(r"(?:^|[|\t])\s*$", text[line_start:s])
            cell_r = re.match(r"\s*(?:[|\t]|$|\n)", text[e:])
            if word in STATUS_WORDS and cell_l and cell_r:
                continue
        out.append(sp)
    return out
