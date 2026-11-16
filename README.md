# ko-pii-mask — 한국어 개인정보 탐지·가명화 (기기 안 실행)

문서·채팅·메일·상담 기록에서 개인정보를 찾아 `[PERSON_1]`, `[PHONE_1]` 같은 코드로 바꾸고, 외부 AI(LLM)가 돌려준 답의 코드를
원래 값으로 되돌립니다. **모든 처리는 사용자의 기기 안에서** 일어나며, 원래 값과 코드의 대응표는 기기 밖으로 나가지 않습니다.

- 모델: KoELECTRA-base-v3 기반 토큰 분류(12층), ONNX Runtime 8비트 — 파일 약 108MB, PyTorch 불필요
- 속도(노트북 CPU 2스레드, GPU 없음): 1만 자 문서 약 4초, 채팅 한 줄 약 17ms, 메모리 약 280MB
- 28가지 유형: 이름, 전화, 이메일, 주소, 주민·외국인·사업자 등록번호, 카드·계좌·여권·운전면허 번호, 차량 번호, 아이디,
  생년월일, 날짜, 나이, 금액, 사건 번호, 기관, 직함, 직업, 건강, 범죄 경력, 종교·신념, 정치 성향, 노조 가입, 성생활·성적 지향, 출신 민족

## 설치

```bash
pip install git+https://github.com/locon-team/ko-pii-mask
pip install huggingface_hub   # 모델 받기용
```

모델(`model_int8w8.onnx` 약 108MB, `tokenizer.json`, `tags.json`)은 Hugging Face [`locon-team/ko-pii-mask-base`](https://huggingface.co/locon-team/ko-pii-mask-base)에 있습니다.

## 사용

```python
from ko_pii_mask import PiiDetector

from huggingface_hub import snapshot_download

det = PiiDetector(snapshot_download("locon-team/ko-pii-mask-base"))
masked, table = det.mask("김민수 고객님, 지난주 부산 해운대구로 이사하셨죠? 010-2345-6789로 연락드릴게요.")
# "[PERSON_1] 고객님, 지난주 [ADDRESS_1]로 이사하셨죠? [PHONE_1]로 연락드릴게요."
answer = llm(masked)                 # 가린 글만 밖으로 보냅니다
print(det.restore(answer, table))    # 대응표로 원래 값 복원
```

Windows에서 받을 위치를 직접 정하려면 `cache_dir` 대신 `local_dir`를 쓰세요. 일반 사용자 계정은 캐시의 심볼릭 링크를 만들
권한이 없어 오류(WinError 1314)가 날 수 있습니다: `snapshot_download("locon-team/ko-pii-mask-base", local_dir="models/ko-pii-mask-base")`.

`det.detect(text)`는 `[{"label", "start", "end", "text", "score"}, ...]`를 돌려줍니다.
`PiiDetector(None)`은 모델 없이 규칙(번호 꼴·체크섬)만 씁니다.

## 성능과 한계

합성 평가 셋과 공개 데이터로 잰 값입니다(모델 카드에 자세히). **이 도구는 개인정보 보호를 돕는 보조 수단이며 모든 개인정보를
찾는다고 보장하지 않습니다.** 중요한 문서는 사람이 확인해 주세요.

| 평가 | 결과 |
|---|---|
| KDPII 대화(외부 공개 데이터, 7,000여 발화) | 개인정보가 있는 발화 중 하나라도 놓친 발화 22% — 이름 85%, 기관 58%를 통째로 잡음 |
| 합성 업무·상담 문서(1만여 건, 여러 셋) | 하나라도 놓친 문서 0.4~10% |
| 개인 문맥 지명("저도 부산 살아요") | 94%를 잡음. 다만 일반 언급 지명(출장지·행사 장소)도 약 16%를 함께 가림 |

알려진 한계:
- 처음 보는 회사·기관 이름(유형 끝말 없는 이름)과 별명을 놓칠 수 있습니다.
- 출장지·행사 장소 같은 일반 언급 지명, 사람이 등장하지 않는 글의 병명·노조 같은 낱말을 가리는 경우가 있습니다.
- 학습·평가는 대부분 합성 데이터입니다. 실제 업무 문서에서는 결과가 다를 수 있습니다.

## ko-pii-mask와 ko-pii-guard

**ko-pii-guard**는 같은 탐지 모델에 **과잉 가림을 줄이는 판단 단계**를 더한 기업용 판입니다. 원래 값과 대응표는 ko-pii-guard에서도
기기 밖으로 나가지 않습니다. 같은 합성 평가 셋에서 잰 결과입니다.

| 평가 | ko-pii-mask | ko-pii-guard |
|---|---|---|
| 일반 언급 지명(출장지·행사 장소·지점 위치)을 함께 가린 비율 | 15.5~16.7% | **1.6~3.6%** |
| 사람이 없는 안내문·법 조문의 병명·노조 같은 낱말을 가린 문서 | 3.3~5.7% | **0%** |
| 업무 문서(v4 합성 셋)에서 과잉 가림이 있는 문서 | 4.8% | **2.5%** |
| 개인정보를 하나라도 놓친 문서·발화 | 기준 | **같음**(차이 0~0.4점) |

ko-pii-guard는 **필요 없이 가린 것을 풀어** 외부 AI가 더 정확히 답하게 돕습니다. 놓침은 두 판이 같으므로, 개인정보를 더 많이 찾아
주지는 않습니다. 기업용 판과 기술 지원은 LOCON에 문의해 주세요.

## 라이선스

코드와 모델은 Apache License 2.0입니다(`LICENSE`). 사용한 공개 모델·데이터의 출처 표기는 `NOTICE`에 있습니다.
