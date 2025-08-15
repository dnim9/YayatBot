"""
Router: tie input -> preprocess -> engine -> output
"""
from typing import Optional
import random

from . import input_handler, engine, output_handler, context_memory

try:
	import yayat as _Y
	import alarm_yayat as _A
except Exception:
	_Y = None
	_A = None

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


def route(user_input: str) -> str:
	clean, intent, style = input_handler.preprocess(user_input)
	pesan = clean

	# Exit
	if pesan in ["keluar", "exit", "quit", "shut down system"]:
		pamit_options = [
			"Sampai jumpa Bos Imam. Yayat standby. Jaga dirimu.",
			"Bye Bos, jangan lupa istirahat. Yayat tetap standby!",
			"Oke Bos, sampai ketemu lagi. Yayat tunggu perintah selanjutnya.",
			"Selamat istirahat Bos Imam, Yayat izin pamit.",
			"Yayat keluar dulu ya Bos. Panggil aja kapan pun di butuhkan.",
		]
		pamit = random.choice(pamit_options)
		output_handler.speak(pamit)
		print("Yayat:", pamit)
		output_handler.log_as("Yayat", pamit)
		if _Y and hasattr(_Y, "mimpi_yayat"):
			_Y.mimpi_yayat()
		return "exit"

	# Edit kamus interaktif
	if pesan == "edit" and _Y and hasattr(_Y, "edit_reply"):
		_Y.edit_reply()
		return "handled"

	# Senyap/bersuara
	if pesan == "senyap" and _Y:
		_Y.mode_senyap = True
		_Y.yayat_popup("Mode senyap aktif, Bos.")
		print("Yayat: Mode senyap aktif.")
		return "handled"
	if pesan == "bersuara" and _Y:
		_Y.mode_senyap = False
		_Y.yayat_popup("Mode suara aktif, Bos.")
		print("Yayat: Mode suara aktif.")
		return "handled"

	# Buka aplikasi
	if pesan.startswith("buka ") and _Y and hasattr(_Y, "buka_aplikasi"):
		_Y.buka_aplikasi(pesan[5:].strip())
		return "handled"

	# Waktu/tanggal/jam
	if ("hari" in pesan or "tanggal" in pesan) and _Y and hasattr(_Y, "waktu_sekarang"):
		teks = _Y.waktu_sekarang()
		output_handler.speak(teks)
		print("Yayat:", teks)
		output_handler.log_as("Yayat", teks)
		return "handled"
	if "jam" in pesan and _Y and hasattr(_Y, "perintah_jam"):
		_Y.perintah_jam()
		return "handled"

	# Alarm
	if pesan.startswith("tambah alarm") and _A:
		waktu = pesan.replace("tambah alarm", "").strip()
		print(_A.tambah_alarm(waktu))
		return "handled"
	if pesan == "daftar alarm" and _A:
		print(_A.lihat_alarm())
		return "handled"
	if pesan.startswith("hapus alarm") and _A:
		nomor = pesan.replace("hapus alarm", "").strip()
		print(_A.hapus_alarm(nomor))
		return "handled"

	# Wiki utama
	if pesan.startswith("wiki ") or pesan.startswith("cari wiki "):
		q = pesan.split("wiki", 1)[1].strip()
		q_norm = _Y.normalize_query_for_wiki(q) if _Y else q
		info = _Y.get_wikipedia_summary(q_norm, lang="id") if _Y else None
		if info:
			sumber = "Wikipedia Indonesia" if info["lang"] == "id" else "Wikipedia Inggris"
			teks = f"{info['title']}: {info['extract']} (sumber: {sumber})"
			output_handler.speak(teks)
			print("Yayat:", teks)
			output_handler.log_as("Yayat", teks)
			if _Y and hasattr(_Y, "set_topik_aktif_dari_wiki"):
				_Y.set_topik_aktif_dari_wiki(info)
			if _Y and hasattr(_Y, "prepare_wiki_session"):
				_Y.prepare_wiki_session(info["title"], lang=info.get("lang", "id"))
		else:
			teks = "Maaf Bos, Yayat gak nemu ringkasan di Wikipedia."
			output_handler.speak(teks)
			print("Yayat:", teks)
			output_handler.log_as("Yayat", teks)
		return "handled"

	# Wiki misc
	if pesan.startswith("wiki cari ") and _Y:
		q = pesan.replace("wiki cari", "").strip()
		info = _Y.wiki_search_then_summary(q, lang="id")
		if info:
			_Y.set_topik_aktif_dari_wiki(info)
			_Y.prepare_wiki_session(info["title"], lang=info.get("lang", "id"))
			teks = f"{info['title']}: {info['extract']} (sumber: Wikipedia {info['lang']})"
			print("Yayat:", teks)
			output_handler.log_as("Yayat", teks)
		else:
			print("Yayat: Tidak ketemu hasil yang cocok di Wikipedia.")
		return "handled"
	if pesan in ["wiki lanjut", "lanjut wiki", "detail wiki"] and _Y:
		teks = _Y.wiki_session_next()
		print("Yayat:", teks)
		output_handler.log_as("Yayat", teks)
		return "handled"
	if (pesan.startswith("arti ") or pesan.startswith("definisi ")) and _Y:
		term = pesan.split(" ", 1)[1].strip()
		res = _Y.get_wiktionary_definition(term, lang="id")
		if res:
			teks = f"{res['title']}: {res['extract']} (sumber: {res['source']})"
			print("Yayat:", teks)
			output_handler.log_as("Yayat", teks)
		else:
			print("Yayat: Belum nemu arti di Wiktionary, Bos.")
		return "handled"
	if pesan.startswith("wiki index ") and _Y:
		title = pesan.replace("wiki index", "").strip()
		if not title:
			print("Yayat: Judul kosong, Bos.")
			return "handled"
		added = _Y.index_wiki_title_into_memory(title, lang="id")
		print(f"Yayat: Sudah diindeks {added} bagian dari Wikipedia untuk '{title}'.")
		return "handled"
	if pesan.startswith("wiki clear ") and _Y:
		title = pesan.replace("wiki clear", "").strip()
		removed = _Y.clear_wiki_index_from_memory(title)
		print(f"Yayat: {removed} potongan dihapus dari memori untuk '{title}'.")
		return "handled"

	# Memori
	if (pesan.startswith("ingat ") or pesan.startswith("remember ")) and _Y:
		fakta = user_input.split(" ", 1)[1].strip()
		if fakta:
			_Y.memory_add_fact(fakta)
			print("Yayat: Oke, sudah kuingat, Bos.")
		else:
			print("Yayat: Apa yang mau diingat, Bos?")
		return "handled"
	if (pesan.startswith("lupa ") or pesan.startswith("forget ")) and _Y:
		kw = user_input.split(" ", 1)[1].strip()
		removed = _Y.memory_forget(kw)
		print(f"Yayat: Sudah kulupakan {removed} item yang cocok.")
		return "handled"
	if pesan.startswith("project set ") and _Y:
		name = user_input.split(" ", 2)[2].strip() if len(user_input.split(" ")) >= 3 else ""
		if name:
			_Y.memory_set_project(name)
			print(f"Yayat: Project aktif diset ke: {name}.")
		else:
			print("Yayat: Nama project kosong.")
		return "handled"
	if pesan in ["project?", "project", "lihat project"] and _Y:
		cp = _Y.memory_get_project()
		print(f"Yayat: Project aktif: {cp if cp else '-'}.")
		return "handled"
	if pesan in ["memori?", "memory?", "ingatanku", "ringkas memori"] and _Y:
		print("Yayat:")
		print(_Y.memory_quick_summary())
		return "handled"

	# Kamus dinamis
	if pesan.startswith("kamus tambah ") and _Y:
		q = pesan.replace("kamus tambah", "").strip()
		if ":" not in q:
			print("Yayat: Format 'kamus tambah pertanyaan:jawaban'")
			return "handled"
		pert, jaw = q.split(":", 1)
		key = f"imam: {_Y.normalize_text(pert)}"
		_Y.reply[key] = jaw.strip()
		_Y.save_kamus()
		print("Yayat: Kamus ditambah.")
		return "handled"
	if pesan == "kamus list" and _Y:
		print("Yayat: Daftar entri kamus (maks 20):")
		cnt = 0
		for k in list(_Y.reply.keys()):
			print(f"- {k} -> {_Y.reply[k]}")
			cnt += 1
			if cnt >= 20:
				break
		return "handled"
	if pesan.startswith("kamus hapus ") and _Y:
		k = f"imam: {pesan.replace('kamus hapus', '').strip()}"
		if k in _Y.reply:
			del _Y.reply[k]
			_Y.save_kamus()
			print("Yayat: Entri kamus dihapus.")
		else:
			print("Yayat: Entri tidak ditemukan.")
		return "handled"
	if pesan.startswith("kamus edit ") and _Y:
		nama = pesan.replace("kamus edit", "").strip()
		k = f"imam: {nama}"
		if k not in _Y.reply:
			print("Yayat: Entri tidak ditemukan.")
			return "handled"
		baru = input("Masukkan jawaban baru: ").strip()
		_Y.reply[k] = baru
		_Y.save_kamus()
		print("Yayat: Entri kamus diperbarui.")
		return "handled"

	# Summarization/paraphrase/creative
	if pesan.startswith("ringkas ") and _Y:
		konten = user_input.split(" ", 1)[1].strip()
		if not konten:
			print("Yayat: Teks kosong, Bos.")
			return "handled"
		teks = _Y.summarize_text_id(konten, max_sentences=3)
		print("Yayat:", teks)
		return "handled"
	if (pesan.startswith("parafrase ") or pesan.startswith("parafrasa ")) and _Y:
		konten = user_input.split(" ", 1)[1].strip()
		if not konten:
			print("Yayat: Teks kosong, Bos.")
			return "handled"
		teks = _Y.paraphrase_simple_id(konten)
		print("Yayat:", teks)
		return "handled"
	if pesan.startswith("puisi ") and _Y:
		tema = user_input.split(" ", 1)[1].strip()
		teks = _Y.generate_poem_id(tema)
		print("Yayat:\n" + teks)
		return "handled"
	if pesan.startswith("cerita ") and _Y:
		tema = user_input.split(" ", 1)[1].strip()
		teks = _Y.generate_story_id(tema)
		print("Yayat:", teks)
		return "handled"
	if pesan in ["mode formal", "mode casual", "mode santai"] and _Y:
		_Y.memory["preferences"]["tone"] = "formal" if "formal" in pesan else "casual"
		_Y.save_memory()
		print(f"Yayat: Mode gaya diset ke {_Y.memory['preferences']['tone']}.")
		return "handled"
	if pesan.startswith("bahasa ") and _Y:
		lang = pesan.split(" ", 1)[1].strip()
		_Y.memory["preferences"]["lang"] = lang
		_Y.save_memory()
		print(f"Yayat: Bahasa preferensi diset ke {lang}.")
		return "handled"

	return "unhandled"