"""
Context & Memory Manager
- Short-term: current conversation window
- Long-term: facts, preferences, episodic
- Retriever: fetch relevant info for answers
"""
from typing import List, Dict, Any

try:
	import yayat as _Y
except Exception:
	_Y = None


def push_turn(speaker: str, text: str):
	if _Y and hasattr(_Y, "update_context"):
		_Y.update_context(speaker, text)
	if _Y and hasattr(_Y, "memory_push_message"):
		_Y.memory_push_message(speaker, text)


def quick_summary() -> str:
	if _Y and hasattr(_Y, "memory_quick_summary"):
		return _Y.memory_quick_summary()
	return ""


def remember(text: str, tags=None):
	if _Y and hasattr(_Y, "memory_add_fact"):
		_Y.memory_add_fact(text, tags=tags or [])


def forget(keyword: str) -> int:
	if _Y and hasattr(_Y, "memory_forget"):
		return _Y.memory_forget(keyword)
	return 0


def retrieve(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
	if _Y and hasattr(_Y, "retrieve_from_memory"):
		return _Y.retrieve_from_memory(query, top_k=top_k)
	return []