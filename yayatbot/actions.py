"""
Action Executor
- Run scripts/commands (optional)
- Integrations (Termux, alarms, etc.)
"""
from typing import Optional

try:
	import yayat as _Y
	import alarm_yayat as _A
except Exception:
	_Y = None
	_A = None


def add_alarm(hhmm: str) -> str:
	if _A and hasattr(_A, "tambah_alarm"):
		return _A.tambah_alarm(hhmm)
	return "Alarm module not available"


def list_alarm() -> str:
	if _A and hasattr(_A, "lihat_alarm"):
		return _A.lihat_alarm()
	return ""


def delete_alarm(x: str) -> str:
	if _A and hasattr(_A, "hapus_alarm"):
		return _A.hapus_alarm(x)
	return ""


def open_app(name: str):
	if _Y and hasattr(_Y, "buka_aplikasi"):
		_Y.buka_aplikasi(name)