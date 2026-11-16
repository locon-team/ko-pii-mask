"""ko_pii_mask — 한국어 개인정보 탐지·가명화(기기 안 실행). Apache-2.0."""
from .detector import PiiDetector, finish

__all__ = ["PiiDetector", "finish"]
__version__ = "0.1.0"
