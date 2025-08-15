"""
Output Handler
- Format text/voice/notification
- Update memory & logs
"""
try:
	import yayat as _Y
except Exception:
	_Y = None


def speak(text: str):
	if _Y and hasattr(_Y, "yayat_suara"):
		_Y.yayat_suara(text)


def notify(text: str):
	if _Y and hasattr(_Y, "yayat_popup"):
		_Y.yayat_popup(text)


def log_as(role: str, text: str):
	if _Y and hasattr(_Y, "simpan_log"):
		_Y.simpan_log(role, text)
	if _Y and hasattr(_Y, "update_context"):
		_Y.update_context(role, text)