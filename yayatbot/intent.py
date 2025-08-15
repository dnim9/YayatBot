"""
Intent & Entity detection
"""
from typing import Optional, Dict

try:
	import yayat as _Y
except Exception:
	_Y = None


def detect_intent(text: str) -> Optional[str]:
	if _Y and hasattr(_Y, "deteksi_maksud"):
		return _Y.deteksi_maksud(text)
	return None


def extract_entities(text: str) -> Dict[str, str]:
	# Placeholder for simple entity extraction
	ents: Dict[str, str] = {}
	for k in ["jam", "tanggal", "wiki", "alarm"]:
		if k in text:
			ents[k] = k
	return ents