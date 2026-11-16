"""개인정보 유형 스키마 — 28가지 유형(라벨 코드), BIO 태그, 법적 등급, 가명 코드 규칙.

유형을 두 축으로 가른다.
- Class R (Rule)    : 형식이 고정되어 정규식으로 잘 잡히는 유형(전화·주민등록번호·카드번호 …)
- Class C (Context) : 형식이 없어 문맥으로 판단해야 하는 유형(이름·기관·건강·지역 …)

법적 등급(고유식별정보·민감정보·준식별·일반)은 이 축과 따로 간다. 고유식별정보는 전부 R이고 민감정보는 전부 C다.
모델 파일에는 스키마 버전과 태그 목록을 함께 저장하고, 불러올 때 지금 버전과 맞는지 검사한다(check_tags).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

CLASS_R = "R"
CLASS_C = "C"

# 법적 등급 (개인정보보호법 기준. 법률 자문이 아니다 — 출시 전 전문가 확인)
GRADE_GENERAL = "general"      # 일반 개인정보
GRADE_UNIQUE = "unique_id"     # 고유식별정보 (시행령 제19조)
GRADE_SENSITIVE = "sensitive"  # 민감정보 (법 제23조, 시행령 제18조)
GRADE_QUASI = "quasi"          # 혼자서는 개인정보가 아니지만 조합하면 사람을 좁힌다
GRADES: tuple[str, ...] = (GRADE_GENERAL, GRADE_UNIQUE, GRADE_SENSITIVE, GRADE_QUASI)

# 온톨로지 코어. 코드 문법 [코어(.역할)?(:일반화값)?#번호]의 맨 앞 부분이다.
# schema.org 대응과 일반화 위계는 ontology/core.json이 맡는다.
CORES: tuple[str, ...] = (
    "Person", "Org", "Location", "Contact", "ID", "Date", "Amount",
    # TITLE 전용. 역할 단서로 쓰거나 [Title:관리자급#01]처럼 뭉갠다
    "Title",
    "Health", "Crime", "Belief", "Politics", "Union", "SexLife", "Ethnic",
    # v3. 직업은 직함(TITLE)과 다르다 — 직함은 조직 안의 자리, 직업은 하는 일이다
    "Occupation",
)

VERSIONS: tuple[int, ...] = (1, 2, 3, 4)
DEFAULT_VERSION = 4   # v4: 28종(운전면허번호 포함)


@dataclass(frozen=True)
class LabelSpec:
    code: str
    ko: str
    axis: str
    grade: str
    core: str
    since: int = 1
    note: str = ""


ALL_LABELS: tuple[LabelSpec, ...] = (
    # ------------------------------------------------ v1 · Class R: 형식 고정
    LabelSpec("RRN", "주민등록번호", CLASS_R, GRADE_UNIQUE, "ID", 1,
              "YYMMDD-GXXXXXC, 성별자리 1~4"),
    LabelSpec("FRN", "외국인등록번호", CLASS_R, GRADE_UNIQUE, "ID", 1,
              "RRN과 같은 구조, 성별자리 5~8"),
    LabelSpec("BRN", "사업자등록번호", CLASS_R, GRADE_GENERAL, "ID", 1, "XXX-XX-XXXXX"),
    LabelSpec("PHONE", "전화·휴대폰", CLASS_R, GRADE_GENERAL, "Contact", 1,
              "010/02/0XX 지역번호"),
    LabelSpec("EMAIL", "이메일", CLASS_R, GRADE_GENERAL, "Contact", 1),
    LabelSpec("CARD", "카드번호", CLASS_R, GRADE_GENERAL, "ID", 1, "16자리 4그룹"),
    LabelSpec("ACCOUNT", "계좌번호", CLASS_R, GRADE_GENERAL, "ID", 1, "은행별 자리수 상이"),
    LabelSpec("PASSPORT", "여권번호", CLASS_R, GRADE_UNIQUE, "ID", 1, "M + 8자리"),
    # ------------------------------------------------ v1 · Class C: 문맥 의존
    LabelSpec("PERSON", "사람이름", CLASS_C, GRADE_GENERAL, "Person", 1),
    LabelSpec("ORG", "조직·소속", CLASS_C, GRADE_GENERAL, "Org", 1),
    LabelSpec("ADDRESS", "주소", CLASS_C, GRADE_GENERAL, "Location", 1,
              "도로명·지번. 시/도·시군구 단독은 특정인의 거주지·출신·현재 위치일 때만 PII"
              "(\"저도 부산 살아요\"). 행사·지점·통계 속 지역 이름은 아니다"),
    LabelSpec("TITLE", "직책·직급", CLASS_C, GRADE_GENERAL, "Title", 1, "닫힌 집합"),
    LabelSpec("DOB", "생년월일", CLASS_C, GRADE_GENERAL, "Date", 1,
              "일반 날짜(DATE)와의 구분이 핵심 난이도"),
    LabelSpec("USERID", "아이디·사번", CLASS_C, GRADE_GENERAL, "ID", 1),
    # ------------------------------------------------ v2 · 민감정보 7종 (전부 Class C)
    # 라벨 규약: 범주를 특정하는 최소 명사구만 라벨한다.
    #   "정신건강의학과에서 우울증으로 2년째 치료 중" → 정신건강의학과, 우울증
    LabelSpec("HEALTH", "건강", CLASS_C, GRADE_SENSITIVE, "Health", 2,
              "질병·진단·장애·투약·치료 이력. 유전자 검사 언급도 여기"),
    LabelSpec("CRIME", "범죄경력", CLASS_C, GRADE_SENSITIVE, "Crime", 2,
              "죄명·처분·전과"),
    LabelSpec("BELIEF", "사상·신념", CLASS_C, GRADE_SENSITIVE, "Belief", 2, "종교·신념"),
    LabelSpec("POLITICS", "정치적 견해", CLASS_C, GRADE_SENSITIVE, "Politics", 2,
              "정당명·가입·지지 표명"),
    LabelSpec("UNION", "노동조합", CLASS_C, GRADE_SENSITIVE, "Union", 2,
              "노조명·가입·탈퇴"),
    LabelSpec("SEXLIFE", "성생활 등", CLASS_C, GRADE_SENSITIVE, "SexLife", 2,
              "성적 지향·성생활. 합성은 최소한으로"),
    LabelSpec("ETHNIC", "인종·민족", CLASS_C, GRADE_SENSITIVE, "Ethnic", 2,
              "인종·민족·출신"),
    # ------------------------------------------------ v2 · 뭉갤 대상 3종
    # 채무·회생 이력은 민감정보가 아니라 신용정보법의 신용정보다. 등급만으로
    # 가릴 것을 정할 수 없어서 도메인 팩이 따로 정한다.
    LabelSpec("AMOUNT", "금액", CLASS_C, GRADE_QUASI, "Amount", 2, "구간으로 뭉갠다"),
    LabelSpec("DATE", "일반 날짜", CLASS_C, GRADE_QUASI, "Date", 2,
              "생년월일이 아닌 날짜. DOB와 다른 타입으로 둔다"),
    LabelSpec("AGE", "나이", CLASS_C, GRADE_QUASI, "Date", 2, "연령대로 뭉갠다"),
    # ------------------------------------------------ v2 · Class R 2종
    # 맨 끝에 붙인다. 그래야 앞의 태그 번호가 그대로라 v1·v2 모델의 출력층 행을
    # 옮길 수 있다. 근거: TAB에서 Presidio가 직접 식별자를 놓친 주원인이 사건번호였고,
    # 가명정보 가이드라인 2024 챗봇 사례의 18종에 둘 다 들어 있다.
    LabelSpec("CASE_NO", "사건번호", CLASS_R, GRADE_GENERAL, "ID", 2,
              "연도 + 사건부호 + 일련번호. 예 2024개회12345"),
    LabelSpec("VEHICLE", "차량번호", CLASS_R, GRADE_GENERAL, "ID", 2,
              "12가3456, 123가4567, 서울12가3456"),
    # ------------------------------------------------ v3 · 준식별 1종
    LabelSpec("OCCUPATION", "직업", CLASS_C, GRADE_QUASI, "Occupation", 3,
              "택시 기사, 간호사, 자영업. 조합 검사의 표준 속성. 직업군으로 뭉갠다"),
    # ------------------------------------------------ v4 · 고유식별 1종
    LabelSpec("DRIVER_LICENSE", "운전면허번호", CLASS_R, GRADE_UNIQUE, "ID", 4,
              "지역코드 2자리-발급연도 2자리-일련 6자리-2자리. 11-12-345678-90, 서울 12-345678-90"),
)

ALL_BY_CODE: dict[str, LabelSpec] = {spec.code: spec for spec in ALL_LABELS}

# 닫힌 집합이라 train/test로 값을 갈라 두면 오히려 비현실적으로 어려워진다.
# 값 중복 제거(누수 방지 2원칙)에서 제외한다. 질병명·죄명은 수백 개라 가른다.
ALL_CLOSED_CLASS: frozenset[str] = frozenset(
    {"TITLE", "BELIEF", "POLITICS", "UNION", "SEXLIFE", "ETHNIC"})


def labels_for(version: int) -> tuple[LabelSpec, ...]:
    if version not in VERSIONS:
        raise ValueError(f"모르는 스키마 버전: {version} (가능: {VERSIONS})")
    return tuple(s for s in ALL_LABELS if s.since <= version)


def bio_tags(version: int | None = None) -> list[str]:
    """BIO 태그 목록. 인덱스가 곧 클래스 id다.

    새 타입은 항상 뒤에 붙는다. 그래서 v1의 태그 id는 v2에서도 같다 —
    v1 모델의 출력층을 v2로 옮길 때 앞 29행을 그대로 쓸 수 있다.
    """
    tags = ["O"]
    for spec in labels_for(VERSION if version is None else version):
        tags.append(f"B-{spec.code}")
        tags.append(f"I-{spec.code}")
    return tags


def _active_version() -> int:
    raw = os.environ.get("PII_SCHEMA", "").strip()
    if not raw:
        return DEFAULT_VERSION
    try:
        version = int(raw)
    except ValueError:
        raise SystemExit(f"PII_SCHEMA={raw!r}: 숫자여야 한다 (가능: {VERSIONS})")
    if version not in VERSIONS:
        raise SystemExit(f"PII_SCHEMA={version}: 모르는 버전 (가능: {VERSIONS})")
    return version


#: 지금 프로세스가 쓰는 스키마 버전. 아래 모든 상수가 이 버전을 따른다.
VERSION: int = _active_version()

LABELS: tuple[LabelSpec, ...] = labels_for(VERSION)
BY_CODE: dict[str, LabelSpec] = {spec.code: spec for spec in LABELS}
CODES: tuple[str, ...] = tuple(spec.code for spec in LABELS)

R_CODES: frozenset[str] = frozenset(s.code for s in LABELS if s.axis == CLASS_R)
C_CODES: frozenset[str] = frozenset(s.code for s in LABELS if s.axis == CLASS_C)
CLOSED_CLASS: frozenset[str] = ALL_CLOSED_CLASS & frozenset(CODES)

BIO_TAGS: tuple[str, ...] = tuple(bio_tags())
TAG2ID: dict[str, int] = {t: i for i, t in enumerate(BIO_TAGS)}


def codes_of_grade(grade: str) -> frozenset[str]:
    return frozenset(s.code for s in LABELS if s.grade == grade)


# ---------------------------------------------------------------- 식별자 종류
#
# 평가에서 "직접 식별자 recall"과 "준식별자 recall"을 따로 낸다(TAB: Presidio
# 전체 recall 0.71인데 직접 식별자는 0.46). 가르는 기준은 법적 등급이다.
#   직접   = 고유식별 + 일반 (값 하나로 사람을 가리킨다)
#   준식별 = quasi (조합해야 사람을 좁힌다)
#   민감   = sensitive (식별자가 아니라 보호 대상 속성)

KIND_DIRECT = "direct"
KIND_QUASI = "quasi"
KIND_SENSITIVE = "sensitive"
KINDS: tuple[str, ...] = (KIND_DIRECT, KIND_QUASI, KIND_SENSITIVE)


def kind_of(code: str) -> str:
    grade = ALL_BY_CODE[code].grade
    if grade == GRADE_QUASI:
        return KIND_QUASI
    if grade == GRADE_SENSITIVE:
        return KIND_SENSITIVE
    return KIND_DIRECT


#: 등급 가중 유출의 가중치. 고유식별 > 민감 > 일반 > 준식별 (재설계안 1-1).
GRADE_WEIGHT: dict[str, float] = {
    GRADE_UNIQUE: 4.0, GRADE_SENSITIVE: 3.0, GRADE_GENERAL: 2.0, GRADE_QUASI: 1.0,
}

#: 등급별 비용비 k = 누락 1자 비용 / 과잉 마스킹 1자 비용 (Elkan).
#: 확률이 보정돼 있으면 기대비용을 최소로 하는 임계값은 1/(1+k)다.
GRADE_COST: dict[str, float] = {
    GRADE_UNIQUE: 100.0, GRADE_SENSITIVE: 50.0, GRADE_GENERAL: 20.0, GRADE_QUASI: 5.0,
}


def cost_floor(code: str) -> float:
    """등급 비용비로 정한 임계값 floor = 1/(1+k)."""
    return 1.0 / (1.0 + GRADE_COST[ALL_BY_CODE[code].grade])


def stamp() -> dict:
    """모델 파일에 같이 저장할 스키마 정보."""
    return {"version": VERSION, "tags": list(BIO_TAGS)}


def check_tags(stored: dict | None, what: str = "모델") -> None:
    """저장된 스키마가 지금 버전과 다르면 멈춘다.

    stored가 None이면 스키마를 저장하기 전에 만든 파일이다 — v1으로 본다.
    """
    if stored is None:
        stored = {"version": 1, "tags": bio_tags(1)}
    if list(stored["tags"]) != list(BIO_TAGS):
        raise SystemExit(
            f"{what}: 스키마 v{stored['version']} ({len(stored['tags'])} 태그)로 "
            f"만든 파일인데 지금은 v{VERSION} ({len(BIO_TAGS)} 태그)다. "
            f"PII_SCHEMA={stored['version']}로 맞추거나 새 스키마로 다시 학습한다.")


# ---------------------------------------------------------------- 라이선스
#
# 제품에 들어갈 모델은 상업 사용이 가능한 출처로만 학습해야 한다.
# CC-BY-SA는 ShareAlike라 가중치를 2차적 저작물로 보는 해석에서 같은 조건으로
# 공개해야 할 수 있다. 라이선스 미표기는 권리 부여가 없다는 뜻이라 더 나쁘다.
# 학습 데이터의 출처별 라이선스.

SOURCE_LICENSE: dict[str, str] = {
    "synth": "proprietary",     # 자체 생성. 제약 없음
    "openpii": "CC-BY-4.0",     # 귀속 표시 필요
    "klue": "CC-BY-SA-4.0",     # ShareAlike — 상업 빌드에서 제외
    "ours": "proprietary",      # 자체 수집 평가셋
    "veil": "Apache-2.0",       # 1T/veil-pii-ko-synth 빈칸 틀(값은 우리 생성기). NOTICE에 표기
    # AI Hub 상담·민원 틀(값은 자체 생성기). AI Hub 이용 조건: 학습한 모델의 영리 이용 가능,
    # 출처 표기 필요, 원본 재배포 금지.
    "aihub": "AIHub-commercial",
}

#: 상업 빌드에 넣어도 되는 라이선스.
COMMERCIAL_SAFE: frozenset[str] = frozenset({"proprietary", "CC-BY-4.0", "Apache-2.0", "AIHub-commercial"})

#: 귀속 표시가 필요한 라이선스 → NOTICE 파일에 적어야 한다.
NEEDS_ATTRIBUTION: frozenset[str] = frozenset({"CC-BY-4.0", "CC-BY-SA-4.0", "Apache-2.0", "AIHub-commercial"})


def license_of(origin: str) -> str:
    return SOURCE_LICENSE.get(origin, "unknown")


def is_commercial_safe(origin: str) -> bool:
    return license_of(origin) in COMMERCIAL_SAFE


SPLITS: tuple[str, ...] = ("train", "validation", "test")
DOC_TYPES: tuple[str, ...] = ("sentence", "email", "form", "table", "report", "legal")
DIFFICULTIES: tuple[str, ...] = ("clean", "noisy", "adversarial")


def axis_of(code: str) -> str:
    """지금 버전 밖의 코드도 답한다. 평가 파일에 v2 라벨이 섞여 있어도 축은 정해진다."""
    return ALL_BY_CODE[code].axis


def grade_of(code: str) -> str:
    return ALL_BY_CODE[code].grade


def core_of(code: str) -> str:
    return ALL_BY_CODE[code].core


def alias(code: str, index: int) -> str:
    """번호 코드 자리표시자. 복원은 이 문자열을 키로 되돌린다.

    온톨로지 코드([Person.채무자#01])는 coder.py가 만든다.
    """
    return f"[{code}_{index}]"
