"""규칙 탐지 — 정규식·체크섬·낱말 목록으로 꼴이 정해진 개인정보를 찾는다.

체크섬(주민·사업자등록번호, 카드 Luhn)은 거르는 데 쓰지 않고 **신뢰도 가중치로만** 쓴다. 맞으면 점수를 올리고 틀려도
버리지 않는다 — 오타·가짜 번호도 가려야 하기 때문이다.

모든 스팬에 0~1 점수를 단다. 점수는 규칙의 구조적 특이도에서 온다. 예를 들어 '생년월일:' 뒤의 날짜는 0.85지만 맨 날짜는
0.10이다.

    python -m ko_pii_mask.baseline --text "주민등록번호는 900101-1234567 입니다"
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import schema
Scored = tuple[str, int, int, float]   # (label, start, end, score)


# ---------------------------------------------------------------- 가제티어

#: 한국 성씨. 인구 대부분을 덮는 선까지만. 두 자 성은 따로 둔다.
SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구"
    "민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국"
    "어은편용예봉사부가복태피감호시")
SURNAMES_2 = ("남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방")

#: 직책. 닫힌 집합이라 규칙이 잘 먹는 유일한 Class C 타입이다.
TITLES = (
    "대표이사", "부사장", "본부장", "센터장", "연구소장", "부교수", "조교수",
    "사원", "주임", "대리", "과장", "차장", "부장", "이사", "상무", "전무",
    "사장", "회장", "대표", "팀장", "실장", "그룹장", "파트장", "연구원",
    "선임", "책임", "수석", "교수", "강사", "원장", "교장", "교감", "반장",
)

#: 조직 접미사.
ORG_TAIL = (
    "주식회사", "유한회사", "대학교", "연구소", "연구원", "병원", "의원",
    "센터", "공사", "공단", "은행", "그룹", "재단", "법인", "상사", "물산",
    "전자", "화학", "건설", "산업", "제약", "학교", "지점", "본부", "협회",
)

#: 이름 뒤에 붙는 조사·경칭. 이걸 떼지 못하면 경계가 틀린다.
PARTICLES = ("님께서", "님에게", "씨에게", "님", "씨", "군", "양", "귀하",
             "은", "는", "이", "가", "을", "를", "의", "와", "과", "에게", "께")

#: 성씨로 시작하지만 사람이 아닌 흔한 낱말. 규칙 베이스라인이라도 이 정도
#: 불용어는 당연히 넣는다 — 안 넣으면 허수아비를 세우고 이기는 셈이 된다.
#: 그래도 한계는 분명하다. 제대로 하려면 한국어 사전이 필요하고, 사전이 없다는
#: 것이 규칙이 Class C에서 지는 구조적 이유다.
NOT_NAMES = frozenset((
    "성명", "주소", "이메일", "연락처", "전화", "휴대폰", "우편", "번호", "계좌",
    "금액", "기간", "날짜", "시간", "장소", "내용", "제목", "구분", "비고",
    "신청", "접수", "처리", "확인", "조회", "등록", "변경", "삭제", "수정",
    "발송", "수신", "참조", "첨부", "문의", "안내", "공지", "보고", "제출",
    "고객", "회원", "직원", "부서", "소속", "직급", "담당", "대상", "결과",
    "정보", "자료", "서류", "양식", "문서", "기록", "관리", "운영", "사용",
    "필요", "가능", "방문", "진행", "완료", "예정", "최종", "최초", "지급",
    "오늘", "내일", "어제", "현재", "이후", "이전", "이상", "이하", "이고",
    "하고", "그리고", "서울", "경기", "강원", "제주", "국내", "해외", "전체",
    "본인", "귀하", "여부", "사항", "경우", "관련", "포함", "제외", "추가",
    # 아래는 **train split에서만** 오탐 빈도를 세어 뽑았다. test를 보고 고치면
    # 그 순간 평가가 거짓말이 된다. 그래서 test에만 나오는 오탐('감사',
    # '소재지' 등)은 일부러 남겨 두었다.
    "사번", "이며", "연락", "송달지", "시스템", "조치", "주시기", "신고자",
    "배송지", "명의", "이다", "선생", "선생님", "사업자",
))


# ---------------------------------------------------------------- 검증 함수


def rrn_checksum_ok(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if len(digits) != 13:
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(d * w for d, w in zip(digits, weights))
    return (11 - (total % 11)) % 10 == digits[12]


def brn_checksum_ok(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if len(digits) != 10:
        return False
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    total = sum(d * w for d, w in zip(digits, weights))
    total += (digits[8] * 5) // 10
    return (10 - (total % 10)) % 10 == digits[9]


def luhn_ok(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if not digits:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


#: 실재하는 휴대폰·지역번호. 맞으면 점수를 올린다(거르지는 않는다).
REAL_PREFIX = re.compile(
    r"^(?:\+?82[-. ]?)?0?(?:1[0-9]|2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|50\d)\b")


# ---------------------------------------------------------------- 규칙

#: 행정구역 토막. 주소 앞머리에 0~3개가 붙는다.
ADMIN = (r"(?:[가-힣]{1,8}(?:특별자치시|특별자치도|특별시|광역시|시|군|구|읍|면|동)"
         r"\s*){0,3}")

#: 층·호. 한국 주소는 여기까지가 한 덩어리다. 빼면 전부 경계 오류가 된다.
UNIT = r"(?:\s*,?\s*(?:지하\s*)?\d{1,3}\s*층)?(?:\s*,?\s*[가-힣A-Za-z]?\d{1,4}\s*호)?"

#: 은행명. 계좌번호는 형식만으로는 사업자번호와 구분되지 않아 문맥이 필요하다.
BANKS = (r"신한|국민|우리|하나|농협|기업|산업|수협|새마을|신협|우체국|카카오뱅크|"
         r"케이뱅크|토스뱅크|씨티|SC제일|부산|대구|경남|광주|전북|제주")

DATE = r"(?:\d{4}|\d{2})\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}(?:\s*일)?"

# ---------------------------------------------------------------- 문맥 열쇠말
#
# 모델이 약한 자리다. 규칙이 놓친 FRN·
# PASSPORT·PHONE은 거의 전부 OCR 노이즈('0'→'O', '1'→'l')나 개체 안의 줄바꿈이었다.
# 그런 값은 형식만 보고 잡으면 오탐이 폭발한다. 그래서 **열쇠말이 바로 앞에 있을 때만**
# 노이즈 문자를 숫자로 받아 준다(Dernoncourt: ID류는 정교한 정규식이 신경망보다 낫다).

#: OCR이 숫자와 헷갈리는 글자. 열쇠말 뒤에서만 숫자로 친다.
ND = r"[0-9OoIlSBbZ]"
#: 개체 안에 끼어든 공백·줄바꿈 하나
GAP = r"[ \t\n]?"
SEP = r"[ \t\n]{0,2}[-－]?[ \t\n]{0,2}"


def _noisy(n: int) -> str:
    """노이즈 숫자 n개. 사이사이 공백·줄바꿈 하나씩을 허용한다."""
    return "(?:" + ND + GAP + "){" + str(n - 1) + "}" + ND


#: 열쇠말 안에도 띄어쓰기 붕괴·삽입이 생긴다('외국인등 록번호'). 글자 사이 공백 허용.
def _kw(word: str) -> str:
    return r"\s?".join(re.escape(ch) for ch in word)


KW_FRN = "(?:" + "|".join(_kw(w) for w in ("외국인등록번호", "외국인등록", "외국인번호")) + ")"
KW_RRN = "(?:" + "|".join(_kw(w) for w in ("주민등록번호", "주민번호")) + ")"
KW_PASS = "(?:" + "|".join(_kw(w) for w in ("여권번호", "여권")) + r"|[Pp]assport(?:\s?[Nn]o\.?)?)"
KW_PHONE = ("(?:" + "|".join(_kw(w) for w in ("비상연락처", "연락처", "전화번호", "휴대폰",
                                               "휴대전화", "핸드폰", "전화", "문의"))
            + r"|[Tt][Ee][Ll]\.?)")

#: 법원 사건부호(「사건별 부호문자의 부여에 관한 예규」 중 흔한 것). 한 글자 부호는
#: 연도와 일련번호 사이에서만 받는다. '2024년 3'이 사건번호로 잡히면 안 된다.
CASE_CODES = ("개회", "개확", "개기", "회합", "회단", "하단", "하면", "하합", "가합", "가단",
              "가소", "카합", "카단", "카기", "타경", "고단", "고합", "고정", "고약", "구합",
              "구단", "드합", "드단", "느합", "느단", "즈합", "즈단", "노", "도", "나", "다",
              "누", "두", "르", "므", "머", "라", "마")
CASE_NO = (r"(?<![\d가-힣])(?:19|20)\d{2}\s?(?:" + "|".join(sorted(CASE_CODES, key=len, reverse=True))
           + r")\s?\d{1,7}(?!\d)")
#: 자동차 등록번호판의 용도 문자(비사업용·사업용·렌터카).
PLATE = "가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주아바사자배하허호"
REGION = "서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주"
VEHICLE = (r"(?<![\d가-힣])(?:(?:" + REGION + r")\s?)?\d{2,3}\s?[" + PLATE + r"]\s?\d{4}(?!\d)")
#: 운전면허 옛 꼴의 지역 이름
DL_REGION = "서울|부산|경기|강원|충북|충남|전북|전남|경북|경남|제주|대구|인천|광주|대전|울산"
#: 한글로 읽은 전화번호('공일공 일이삼사 오육칠팔'). 반드시 '공'으로 시작한다.
KD = "[공영일이삼사오육칠팔구]"
PHONE_KO = r"(?<![가-힣])공" + KD + "{1,2}[ -]?" + KD + "{3,4}[ -]?" + KD + r"{4}(?![가-힣])"
BORN = r"\d{2,4}\s*년\s*생"

#: (코드, 패턴, 기본점수, 값 그룹). 값 그룹이 0이 아니면 그 그룹만 스팬으로 쓴다.
RULES: list[tuple[str, str, float, int]] = [
    # ---------------- Class R : 형식이 고정된 것들 ----------------
    ("RRN", r"(?<![\d-])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
            r"-[1-4]\d{6}(?![\d-])", 0.90, 0),
    ("FRN", r"(?<![\d-])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
            r"-[5-8]\d{6}(?![\d-])", 0.90, 0),
    ("BRN", r"(?<![\d-])\d{3}-\d{2}-\d{5}(?![\d-])", 0.85, 0),
    # 로컬 파트에 한글을 허용한다. 국제화 주소가 실제로 있고, openpii 한국어는
    # 이메일의 78%가 '여@tutanota.com' 꼴이라 ASCII만 받으면 recall이 0.2로 떨어진다.
    ("EMAIL", r"[가-힣A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", 0.97, 0),
    ("CARD", r"(?<![\d-])(?:\d{4}[- ]){3}\d{4}(?![\d-])", 0.88, 0),
    ("CARD", r"(?<![\d-])\d{13,19}(?![\d-])", 0.70, 0),
    ("PHONE", r"(?<![\d-])(?:\+?82[-. ]?|0)\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}"
              r"(?![\d-])", 0.80, 0),
    ("PASSPORT", r"(?<![A-Za-z0-9])[MSRODGmsrodg]\d{8}(?![A-Za-z0-9])", 0.75, 0),
    # 차세대 전자여권 M123A4567
    ("PASSPORT", r"(?<![A-Za-z0-9])[MSRODG]\d{3}[A-Z]\d{4}(?![A-Za-z0-9])", 0.72, 0),
    # 열쇠말 문맥. 노이즈 숫자와 개체 안 줄바꿈을 받아 준다
    ("FRN", KW_FRN + r"\s*[:：]?\s*(" + _noisy(6) + SEP + _noisy(7) + r")(?![0-9])", 0.96, 1),
    ("RRN", KW_RRN + r"\s*[:：]?\s*(" + _noisy(6) + SEP + _noisy(7) + r")(?![0-9])", 0.95, 1),
    ("PASSPORT", KW_PASS + r"\s*[:：]?\s*([A-Za-z0O]" + GAP + _noisy(8) + r")(?![0-9A-Za-z])",
     0.93, 1),
    ("PHONE", KW_PHONE + r"\s*[:：]?\s*(" + ND + "{2,3}" + SEP + _noisy(3) + "(?:" + GAP + ND
     + ")?" + SEP + _noisy(4) + r")(?![0-9])", 0.90, 1),
    # 010의 노이즈 꼴(Ol0, O10, 0l0)은 열쇠말이 없어도 휴대폰 번호다
    ("PHONE", r"(?<![0-9A-Za-z])[0O][1lI][0O]" + SEP + _noisy(4) + SEP + _noisy(4)
     + r"(?![0-9])", 0.70, 0),
    ("PHONE", PHONE_KO, 0.80, 0),
    ("CASE_NO", CASE_NO, 0.90, 0),
    ("VEHICLE", VEHICLE, 0.85, 0),
    # 운전면허번호(v4): 지역코드 11~28-연도-일련 6자리-2자리, 옛 꼴은 지역 이름 + 10자리, 열쇠말 뒤 붙여 쓴 12자리
    ("DRIVER_LICENSE", r"(?<![\d-])(?:1[1-9]|2[0-8])-\d{2}-\d{6}-\d{2}(?![\d-])", 0.90, 0),
    ("DRIVER_LICENSE", r"(?<![가-힣])(?:" + DL_REGION + r")\s?\d{2}-\d{6}-\d{2}(?![\d-])", 0.90, 0),
    ("DRIVER_LICENSE", r"(?:운전면허|면허)\s*(?:번호|증)?\s*[:：]?\s*((?:1[1-9]|2[0-8])\d{10})(?!\d)", 0.88, 1),
    # 은행명이 붙어 있으면 그것까지가 한 덩어리다. 정답이 '우리 1884-99-9389491'
    # 꼴이라 숫자만 잡으면 전부 경계 오류가 된다.
    ("ACCOUNT", r"(?:" + BANKS + r")\s?"
                r"(?:\d{2,6}-\d{2,6}-\d{2,8}|\d{10,16})(?![\d-])", 0.82, 0),
    ("ACCOUNT", r"(?:계좌|입금|송금|이체|예금주)[^\d\n]{0,12}"
                r"((?<![\d-])\d{2,6}-\d{2,6}-\d{2,8}(?![\d-])|(?<!\d)\d{10,16}(?!\d))",
     0.78, 1),

    # ---------------- Class C : 형식이 없는 것들 ----------------
    # 문맥 열쇠말이 있을 때. 규칙이 그나마 먹는 경우다.
    ("PERSON", r"(?:성명|이름|담당자|신청인|수신인?|대표자|환자명|고객명|작성자)"
               r"\s*[:：]\s*([가-힣]{2,4})", 0.82, 1),
    ("DOB", r"(?:생년월일|생일|출생일|출생년월일)\s*[:：]?\s*(" + DATE + ")", 0.85, 1),
    ("USERID", r"(?:아이디|ID|사번|계정|회원번호|사용자명)\s*[:：]?\s*"
               r"([A-Za-z0-9_.\-]{3,20})", 0.70, 1),
    # 문맥이 없을 때. 여기서부터 정밀도가 무너진다.
    ("ADDRESS", ADMIN + r"[가-힣A-Za-z0-9]{1,12}(?:대로|로|길)\s?\d{1,4}(?:번길)?"
                r"(?:\s?\d{1,4}(?:-\d{1,4})?)?" + UNIT, 0.72, 0),
    ("ADDRESS", r"\((\d{5})\)", 0.42, 1),
    ("ADDRESS", r"(?<!\d)\d{5}(?!\d)", 0.15, 0),
    ("DOB", r"(?:생년월일|생일|출생)\s*[:：]?\s*(" + BORN + ")", 0.80, 1),
    ("DOB", BORN, 0.30, 0),
    ("DOB", DATE, 0.10, 0),
]

COMPILED = [(code, re.compile(pattern), score, group)
            for code, pattern, score, group in RULES]


# ---------------------------------------------------------------- 가제티어 규칙


def _strip_particle(text: str, start: int, end: int) -> int:
    """이름 뒤에 붙은 조사를 뗀다. 한국어에서 경계 오류의 최대 원인이다."""
    surface = text[start:end]
    for particle in PARTICLES:
        if len(surface) - len(particle) >= 2 and surface.endswith(particle):
            return end - len(particle)
    return end


_NAME = re.compile(r"[가-힣]{2,6}")


def _person_gazetteer(text: str) -> list[Scored]:
    """성씨로 시작하는 2~4자 토막. 문맥 없이 이름을 잡는 유일한 방법이고,
    동시에 Class C가 왜 어려운지를 보여 주는 규칙이다. '김치', '이상한'이
    그대로 걸린다."""
    out: list[Scored] = []
    for m in _NAME.finditer(text):
        start, end = m.start(), m.end()
        # '성명:' 처럼 콜론이 뒤따르면 항목 이름이지 사람이 아니다
        if text[end:end + 2].lstrip().startswith((":", "：")):
            continue
        end = _strip_particle(text, start, end)
        if end - start < 2:
            continue
        surface = text[start:end]
        if surface in NOT_NAMES:
            continue
        if surface[:2] in SURNAMES_2:
            given = len(surface) - 2
        elif surface[0] in SURNAMES:
            given = len(surface) - 1
        else:
            continue
        if not 1 <= given <= 2:
            continue
        # 뒤에 경칭이 붙어 있었으면 사람일 확률이 확 오른다
        tail = text[end:end + 4]
        boosted = any(tail.startswith(p) for p in
                      ("님", "씨", "귀하", "선생", "과장", "부장", "팀장", "대리"))
        out.append(("PERSON", start, end, 0.62 if boosted else 0.38))
    return out


def _word_boundary(text: str, start: int, end: int) -> bool:
    """앞뒤가 다른 한글 낱말로 이어지지 않는가.

    이게 없으면 '대표자'의 '대표', '근영연구원'의 '연구원'이 직책으로 잡힌다.
    실제로 TITLE 오탐의 대부분이 그거였고, 긴 ORG 스팬까지 밀어내고 있었다.
    """
    if start > 0 and "가" <= text[start - 1] <= "힣":
        return False
    tail = text[end:end + 4]
    if tail and "가" <= tail[0] <= "힣":
        return any(tail.startswith(p) for p in PARTICLES)
    return True


def _title_gazetteer(text: str) -> list[Scored]:
    out: list[Scored] = []
    for title in TITLES:
        start = text.find(title)
        while start >= 0:
            end = start + len(title)
            if _word_boundary(text, start, end):
                out.append(("TITLE", start, end, 0.88))
            start = text.find(title, start + 1)
    return out


#: v3 직업. 직업 낱말은 일반 명사로도 쓰여서("간호사 인력 부족") 단서가 있을 때만 낸다:
#: 앞에 '직업'·'직종'·'종사자' 칸 표시가 있거나, 뒤에 '(으)로 일/근무'가 온다.
_JOB_BEFORE = re.compile(r"(?:직업|직종)\s*(?:은|는|:|：|\|)?\s*$")
_JOB_AFTER = re.compile(r"^\s*(?:으로|로)\s*(?:일|근무|종사)")


def _occupation_gazetteer(text: str) -> list[Scored]:
    from . import _lexicon as synth
    out: list[Scored] = []
    for job in sorted(synth.OCCUPATIONS, key=len, reverse=True):
        start = text.find(job)
        while start >= 0:
            end = start + len(job)
            if (_word_boundary(text, start, end) or _JOB_AFTER.match(text[end:end + 8])) and (
                    _JOB_BEFORE.search(text[max(0, start - 8):start])
                    or _JOB_AFTER.match(text[end:end + 8])):
                out.append(("OCCUPATION", start, end, 0.82))
            start = text.find(job, start + 1)
    return out


_ORG = re.compile(r"[가-힣A-Za-z0-9]{2,12}(?:" + "|".join(ORG_TAIL) + r")")
_ORG_PREFIX = re.compile(r"(?:주식회사|㈜|\(주\))\s?[가-힣A-Za-z0-9]{2,12}")


def _org_gazetteer(text: str) -> list[Scored]:
    out = [("ORG", m.start(), m.end(), 0.74) for m in _ORG.finditer(text)]
    out += [("ORG", m.start(), m.end(), 0.80) for m in _ORG_PREFIX.finditer(text)]
    return out


# ---------------------------------------------------------------- 탐지


def _adjust(code: str, surface: str, score: float) -> float:
    """체크섬·접두사로 신뢰도만 조정한다. 절대 버리지 않는다(상단 주석 참고)."""
    if code in ("RRN", "FRN"):
        return 0.99 if rrn_checksum_ok(surface) else score
    if code == "BRN":
        return 0.98 if brn_checksum_ok(surface) else score
    if code == "CARD":
        return min(0.97, score + 0.22) if luhn_ok(surface) else score
    if code == "PHONE":
        return 0.94 if REAL_PREFIX.match(surface) else score
    return score


def resolve(candidates: list[Scored]) -> list[Scored]:
    """겹치는 후보를 추린다. 점수 높은 것 → 긴 것 → 앞선 것 순으로 집는다.

    BIO 태깅이 되려면 스팬이 겹치면 안 된다. 평가도 겹침을 전제하지 않는다.
    """
    ordered = sorted(candidates, key=lambda c: (-c[3], -(c[2] - c[1]), c[1]))
    kept: list[Scored] = []
    for cand in ordered:
        if any(cand[1] < k[2] and k[1] < cand[2] for k in kept):
            continue
        kept.append(cand)
    return sorted(kept, key=lambda c: c[1])


def detect(text: str) -> list[Scored]:
    """규칙 탐지의 전부. 텍스트 하나를 받아 점수 달린 스팬 목록을 돌려준다."""
    candidates: list[Scored] = []
    for code, pattern, score, group in COMPILED:
        for m in pattern.finditer(text):
            start, end = m.span(group)
            if start < 0 or end <= start:
                continue
            candidates.append((code, start, end,
                               _adjust(code, text[start:end], score)))
    candidates += _person_gazetteer(text)
    candidates += _title_gazetteer(text)
    candidates += _org_gazetteer(text)
    if "OCCUPATION" in schema.CODES:
        candidates += _occupation_gazetteer(text)
    # 지금 스키마에 없는 타입(v1에서 CASE_NO·VEHICLE)은 내지 않는다
    return resolve([c for c in candidates if c[0] in ACTIVE])


ACTIVE = frozenset(schema.CODES)


# ---------------------------------------------------------------- KLUE 16종 대조
#
# KLUE는 사전학습 말뭉치를 KISA 지침 기반 정규식 16종으로 가명처리했다(표 29).
# 우리 규칙이 무엇을 덮고 무엇을 안 덮는지 이 표로 못 박는다. 스키마 밖인 것은
# AUDIT_RULES가 '점검용'으로만 찾는다 — 마스킹 라벨은 내지 않지만, LLM 답변 재스캔
# (coder.rescan)과 데이터 점검에 쓴다. test_eval.py가 표의 빈칸을 막는다.

KLUE_PII_16: dict[str, tuple[str, str]] = {
    # KLUE 종류: (우리 타입 또는 'audit:이름', 메모)
    "전화번호": ("PHONE", "숫자·노이즈 숫자·한글 숫자"),
    "주민등록번호": ("RRN", "성별자리 1~4 + 열쇠말 문맥"),
    "외국인등록번호": ("FRN", "성별자리 5~8 + 열쇠말 문맥(11-12 추가)"),
    "이메일": ("EMAIL", "한글 로컬 파트 허용"),
    "IP 주소": ("audit:IP", "스키마 밖. 점검만"),
    "MAC 주소": ("audit:MAC", "스키마 밖. 점검만"),
    "@멘션": ("audit:MENTION", "스키마 밖. USERID와 겹칠 수 있다"),
    "주소": ("ADDRESS", "도로명 + 층·호. 지번·랜드마크는 약하다"),
    "계좌번호": ("ACCOUNT", "은행명 또는 열쇠말 문맥"),
    "여권번호": ("PASSPORT", "구형 1자+8자리, 차세대 M123A4567, 열쇠말 문맥"),
    "운전면허번호": (("DRIVER_LICENSE" if "DRIVER_LICENSE" in schema.CODES else "audit:DRIVERLIC"),
                 "v4부터 타입. v3 이하는 점검만"),
    "사업자등록번호": ("BRN", "XXX-XX-XXXXX"),
    "건강보험번호": ("audit:HEALTHINS", "스키마 밖. 재설계안: 점검 목록에만 둔다"),
    "카드번호": ("CARD", "4그룹 + 13~19자리"),
    "차량번호": ("VEHICLE", "v2 (11-12 추가)"),
    "URL": ("audit:URL", "스키마 밖. 재설계안: 점검 목록에만 둔다"),
}

AUDIT_RULES: list[tuple[str, re.Pattern]] = [
    ("IP", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
                      r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("MAC", re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")),
    ("MENTION", re.compile(r"(?<![\w@.])@[A-Za-z0-9_]{2,30}\b")),
    ("DRIVERLIC", re.compile(r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)")),
    ("HEALTHINS", re.compile(r"(?:건강보험증?\s*번호|건보번호)\s*[:：]?\s*(\d[\d-]{9,13}\d)")),
    ("URL", re.compile(r"https?://[^\s<>\"')\]]+|www\.[^\s<>\"')\]]+")),
    ("STUDENT_ID", re.compile(r"학번\s*[:：]?\s*(\d{8,10})")),
    # 가명정보 가이드라인 2026.3 챗봇 사례(별권 p.216)의 아이디·비밀번호 단서어
    ("PASSWORD", re.compile(r"(?:비밀번호|패스워드|비번)\s*(?:은|는|이|가|:|：)?\s*([^\s,.]{4,20})")),
    ("LOGIN_ID", re.compile(r"(?:아이디|ID)\s*(?:은|는|가|:|：)?\s*([A-Za-z0-9_.\-]{4,20})")),
]


def audit(text: str) -> list[tuple[str, int, int]]:
    """스키마 밖 개인정보 후보. 라벨이 아니라 경고용이다."""
    out = []
    for name, pattern in AUDIT_RULES:
        for m in pattern.finditer(text):
            g = 1 if pattern.groups else 0
            out.append((name, m.start(g), m.end(g)))
    return out


# ---------------------------------------------------------------- CLI


def main() -> None:
    ap = argparse.ArgumentParser(description="규칙 탐지")
    ap.add_argument("--in", dest="input", type=Path, help="입력 jsonl")
    ap.add_argument("--out", type=Path, help="예측 jsonl")
    ap.add_argument("--text", help="문자열 하나를 바로 돌려 본다")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    if args.text:
        for code, start, end, score in detect(args.text):
            print(f"  {code:<9} {score:.2f}  [{start:>3}:{end:>3}]  "
                  f"{args.text[start:end]!r}")
        return

    if not args.input:
        raise SystemExit("--in 또는 --text 가 필요하다")

    with args.input.open(encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    if args.limit:
        records = records[: args.limit]

    out = []
    for rec in records:
        spans = detect(rec["source_text"])
        out.append({"uid": rec["uid"],
                    "spans": [{"label": c, "start": s, "end": e,
                               "score": round(sc, 4)} for c, s, e, sc in spans]})

    total = sum(len(r["spans"]) for r in out)
    print(f"{len(out):,}건에서 스팬 {total:,}개")
    if args.out:
        with args.out.open("w", encoding="utf-8") as f:
            for rec in out:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{args.out}")


if __name__ == "__main__":
    main()
