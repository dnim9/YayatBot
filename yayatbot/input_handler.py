"""
Input Handler
- Receive input (text/voice/command)
- Preprocessing pipeline hooks
"""
from typing import Optional, Tuple

try:
	import yayat as _Y
except Exception:
	_Y = None


def receive_text(raw: str) -> str:
	return (raw or "").strip()


def receive_voice() -> str:
	if _Y and hasattr(_Y, "input_suara"):
		return _Y.input_suara() or ""
	return ""


def preprocess(text: str) -> Tuple[str, Optional[str], Optional[str]]:
	"""Return (clean_text, intent, lang_style)."""
	clean = _Y.normalize_text(text) if _Y and hasattr(_Y, "normalize_text") else (text or "").lower().strip()
	intent = None
	style = None
	if _Y and hasattr(_Y, "deteksi_maksud"):
		intent = _Y.deteksi_maksud(clean)
	# style detection placeholder
	if any(w in clean for w in ["tolong", "harap", "mohon"]):
		style = "formal"
	elif any(w in clean for w in ["bro", "bos", "wkwk", "haha"]):
		style = "casual"
	return clean, intent, style