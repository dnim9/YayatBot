"""
Router: tie input -> preprocess -> engine -> output
Currently only used for fallback handling to avoid duplication with legacy commands in yayat.py
"""
from typing import Optional

from . import input_handler, engine, output_handler, context_memory

QUESTION_PREFIXES = (
	"apa itu",
	"siapa itu",
	"apa artinya",
	"apa maksud",
	"siapa",
	"apa",
	"tentang",
	"jelaskan",
	"definisi",
)


def handle_fallback(user_input: str) -> None:
	clean, intent, style = input_handler.preprocess(user_input)
	# Knowledge-leaning question: try augment first
	if clean.startswith(QUESTION_PREFIXES):
		aug = engine.augment_with_wiki(clean)
		if aug:
			output_handler.speak(aug)
			print("Yayat:", aug)
			output_handler.log_as("Yayat", aug)
			context_memory.push_turn("Yayat", aug)
			return
	# Default: use local generator
	ans = engine.answer(user_input)
	output_handler.speak(ans)
	print("Yayat:", ans)
	output_handler.log_as("Yayat", ans)
	context_memory.push_turn("Yayat", ans)