import json
import os
import urllib.request
import urllib.error
import ssl
from typing import List, Dict, Optional

# Simple HTTP helpers using urllib to avoid extra deps

_DEFAULT_TIMEOUT = 20


def _http_post(url: str, headers: Dict[str, str], body: dict) -> Optional[dict]:
	data = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(url, data=data, headers=headers, method="POST")
	# Relax SSL for local dev endpoints if needed
	context = ssl.create_default_context()
	try:
		with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT, context=context) as resp:
			resp_data = resp.read().decode("utf-8")
			return json.loads(resp_data)
	except urllib.error.HTTPError as e:
		try:
			err = e.read().decode("utf-8")
			return json.loads(err)
		except Exception:
			return None
	except Exception:
		return None


# Provider: OpenAI (or OpenAI-compatible endpoints)

def _chat_openai(messages: List[Dict], system: Optional[str], temperature: float, max_tokens: int) -> Optional[str]:
	api_key = os.environ.get("OPENAI_API_KEY")
	base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
	model = os.environ.get("YAYAT_LLM_MODEL", "gpt-4o-mini")
	if not api_key:
		return None
	msgs = []
	if system:
		msgs.append({"role": "system", "content": system})
	msgs.extend(messages)
	payload = {
		"model": model,
		"messages": msgs,
		"temperature": float(temperature),
		"max_tokens": int(max_tokens),
	}
	res = _http_post(
		f"{base_url}/chat/completions",
		{"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
		payload,
	)
	if not res:
		return None
	try:
		return (res.get("choices") or [{}])[0].get("message", {}).get("content")
	except Exception:
		return None


# Provider: LM Studio (OpenAI-compatible; default http://localhost:1234)

def _chat_lmstudio(messages: List[Dict], system: Optional[str], temperature: float, max_tokens: int) -> Optional[str]:
	base_url = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
	if not base_url:
		return None
	model = os.environ.get("YAYAT_LMSTUDIO_MODEL", os.environ.get("YAYAT_LLM_MODEL", "Qwen2.5-7B-Instruct"))
	msgs = []
	if system:
		msgs.append({"role": "system", "content": system})
	msgs.extend(messages)
	payload = {
		"model": model,
		"messages": msgs,
		"temperature": float(temperature),
		"max_tokens": int(max_tokens),
	}
	res = _http_post(
		f"{base_url}/chat/completions",
		{"Content-Type": "application/json"},
		payload,
	)
	if not res:
		return None
	try:
		return (res.get("choices") or [{}])[0].get("message", {}).get("content")
	except Exception:
		return None


# Provider: Ollama (default http://localhost:11434)

def _chat_ollama(messages: List[Dict], system: Optional[str], temperature: float, max_tokens: int) -> Optional[str]:
	base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
	model = os.environ.get("YAYAT_OLLAMA_MODEL", os.environ.get("YAYAT_LLM_MODEL", "llama3.1:8b-instruct"))
	if not base:
		return None
	# Ollama expects a unified messages list; system can be put as first system message
	msgs = []
	if system:
		msgs.append({"role": "system", "content": system})
	msgs.extend(messages)
	payload = {
		"model": model,
		"messages": msgs,
		"options": {"temperature": float(temperature), "num_predict": int(max_tokens)},
		"stream": False,
	}
	res = _http_post(
		f"{base}/api/chat",
		{"Content-Type": "application/json"},
		payload,
	)
	if not res:
		return None
	try:
		# Some versions return {message: {content: ...}}; others may return 'choices'
		if "message" in res:
			return (res.get("message") or {}).get("content")
		choices = res.get("choices") or []
		if choices:
			return (choices[0].get("message") or {}).get("content")
		return None
	except Exception:
		return None


# Public API

def chat(messages: List[Dict], system: Optional[str] = None, temperature: float = 0.6, max_tokens: int = 400) -> Optional[str]:
	"""
	Try OpenAI, then LM Studio, then Ollama. Return first non-empty response.
	messages: list of {role: 'user'|'assistant'|'system', content: str}
	"""
	# Prefer OpenAI if API key present
	if os.environ.get("OPENAI_API_KEY"):
		ans = _chat_openai(messages, system, temperature, max_tokens)
		if ans and ans.strip():
			return ans.strip()
	# Try LM Studio
	ans = _chat_lmstudio(messages, system, temperature, max_tokens)
	if ans and ans.strip():
		return ans.strip()
	# Try Ollama
	ans = _chat_ollama(messages, system, temperature, max_tokens)
	if ans and ans.strip():
		return ans.strip()
	return None