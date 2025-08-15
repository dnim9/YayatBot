import json
import os
import math
import random
import re
from typing import List, Dict, Tuple, Optional

MODEL_FILE = "local_lm_model.json"


def _normalize(text: str) -> str:
	if not text:
		return ""
	t = (text or "").lower().strip()
	t = t.replace("di mana", "dimana").replace("ke mana", "kemana")
	t = re.sub(r"\s+", " ", t)
	return t


def _tokenize(text: str) -> List[str]:
	t = _normalize(text)
	# split on non-word while keeping Indonesian letters
	tokens = re.findall(r"[a-z0-9_]+|[.,!?]", t)
	return tokens


class LocalLM:
	def __init__(self, n: int = 3):
		self.n = max(2, int(n))
		self.ngram_counts: Dict[Tuple[str, ...], int] = {}
		self.context_counts: Dict[Tuple[str, ...], int] = {}
		self.unigram_counts: Dict[str, int] = {}
		self.vocab = set()

	def fit(self, texts: List[str]):
		self.ngram_counts.clear()
		self.context_counts.clear()
		self.unigram_counts.clear()
		self.vocab.clear()
		for text in texts or []:
			toks = _tokenize(text)
			for w in toks:
				self.unigram_counts[w] = self.unigram_counts.get(w, 0) + 1
				self.vocab.add(w)
			# add BOS padding
			pad = ["<s>"] * (self.n - 1)
			seq = pad + toks + ["</s>"]
			for i in range(len(seq) - self.n + 1):
				ng = tuple(seq[i : i + self.n])
				ctx = ng[:-1]
				nx = ng[-1]
				self.ngram_counts[ng] = self.ngram_counts.get(ng, 0) + 1
				self.context_counts[ctx] = self.context_counts.get(ctx, 0) + 1
				self.vocab.add(nx)

	def _dist_next(self, context: Tuple[str, ...], alpha: float = 0.1) -> Dict[str, float]:
		# Try full context; if empty, backoff by dropping leftmost token
		ctx = context
		while ctx:
			total = self.context_counts.get(ctx, 0)
			if total > 0:
				probs: Dict[str, float] = {}
				for w in self.vocab:
					cnt = self.ngram_counts.get((*ctx, w), 0)
					if cnt > 0:
						probs[w] = (cnt + alpha) / (total + alpha * len(self.vocab))
				return probs
			ctx = ctx[1:]
		# fallback to unigram
		total_uni = sum(self.unigram_counts.values()) or 1
		return {w: (self.unigram_counts.get(w, 0) + alpha) / (total_uni + alpha * len(self.vocab) or 1) for w in self.vocab}

	def _sample_from_dist(self, dist: Dict[str, float], top_k: int = 10, temperature: float = 1.0) -> str:
		if not dist:
			return ""
		items = sorted(dist.items(), key=lambda x: x[1], reverse=True)[: max(1, top_k)]
		words, weights = zip(*items)
		if temperature <= 0:
			return words[0]
		# temperature scaling
		scaled = [max(1e-8, w) ** (1.0 / temperature) for w in weights]
		sumw = sum(scaled)
		if sumw <= 0:
			return words[0]
		probs = [w / sumw for w in scaled]
		r = random.random()
		acc = 0.0
		for w, p in zip(words, probs):
			acc += p
			if r <= acc:
				return w
		return words[-1]

	def generate(self, prompt: str, max_tokens: int = 40, temperature: float = 0.9, top_k: int = 8) -> str:
		prompt_tokens = _tokenize(prompt)
		ctx = ["<s>"] * (self.n - 1)
		# use last (n-1) tokens of prompt as context when available
		if prompt_tokens:
			ctx = (ctx + prompt_tokens)[- (self.n - 1):]
		out = []
		for _ in range(max_tokens):
			dist = self._dist_next(tuple(ctx))
			next_w = self._sample_from_dist(dist, top_k=top_k, temperature=temperature)
			if not next_w or next_w == "</s>":
				break
			out.append(next_w)
			ctx = (ctx + [next_w])[1:]
			# stop if sentence end
			if next_w in [".", "!", "?"] and len(out) >= 8:
				break
		# join tokens back
		text = " ".join(out)
		text = re.sub(r"\s+([.,!?])", r"\1", text).strip()
		return text

	def save(self, path: str = MODEL_FILE):
		data = {
			"n": self.n,
			"ngram_counts": {"|".join(k): v for k, v in self.ngram_counts.items()},
			"context_counts": {"|".join(k): v for k, v in self.context_counts.items()},
			"unigram_counts": self.unigram_counts,
			"vocab": list(self.vocab),
		}
		with open(path, "w", encoding="utf-8") as f:
			json.dump(data, f)

	@classmethod
	def load(cls, path: str = MODEL_FILE) -> Optional["LocalLM"]:
		if not os.path.exists(path):
			return None
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		lm = cls(n=int(data.get("n", 3)))
		lm.ngram_counts = {tuple(k.split("|")): int(v) for k, v in (data.get("ngram_counts") or {}).items()}
		lm.context_counts = {tuple(k.split("|")): int(v) for k, v in (data.get("context_counts") or {}).items()}
		lm.unigram_counts = {k: int(v) for k, v in (data.get("unigram_counts") or {}).items()}
		lm.vocab = set(data.get("vocab") or [])
		return lm


def build_corpus_from_state(memory: dict, context_window: List[dict], wiki_session: dict) -> List[str]:
	texts: List[str] = []
	for f in (memory or {}).get("facts", []):
		val = (f.get("text") or "").strip()
		if val:
			texts.append(val)
	for m in (memory or {}).get("last_messages", [])[-50:]:
		val = (m.get("text") or "").strip()
		if val:
			texts.append(val)
	for s in (wiki_session or {}).get("sections", [])[:10]:
		val = (s.get("text") or "").strip()
		if val:
			texts.append(val)
	# small seed to help punctuation
	texts.append("baik bos. oke siap. tentu saja. siap bantu.")
	return texts