"""
Preprocess utilities
"""
from typing import Tuple

try:
	import yayat as _Y
except Exception:
	_Y = None


def clean(text: str) -> str:
	if _Y and hasattr(_Y, "normalize_text"):
		return _Y.normalize_text(text)
	return (text or "").lower().strip()


def detect_lang_and_style(text: str) -> Tuple[str, str]:
	lang = "id"
	style = "casual" if any(w in text for w in ["bos", "bro", "wkwk"]) else "formal"
	return lang, style