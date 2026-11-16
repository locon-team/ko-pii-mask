"""모델 없이(규칙만) 도는 기본 확인. 모델이 있으면 KO_PII_MASK_MODEL 환경 변수로 폴더를 주면 함께 확인한다."""
import os

from ko_pii_mask import PiiDetector


def test_rules_mask_restore():
    det = PiiDetector(None)
    text = "연락처는 010-2345-6789, 메일은 kim@example.com 입니다. 다시 010-2345-6789로 주세요."
    masked, table = det.mask(text)
    assert "010-2345-6789" not in masked and "kim@example.com" not in masked
    assert masked.count("[PHONE_1]") == 2                 # 같은 값은 같은 코드
    assert det.restore(masked, table) == text


def test_model_if_available():
    path = os.environ.get("KO_PII_MASK_MODEL")
    if not path:
        return
    det = PiiDetector(path)
    spans = det.detect("저는 김민수이고 부산 해운대구에 살아요.")
    labels = {s["label"] for s in spans}
    assert "PERSON" in labels and "ADDRESS" in labels
