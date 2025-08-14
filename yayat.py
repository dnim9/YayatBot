#!/usr/bin/env python3
import json
import os
import random
import datetime
import time
import sys  # Import pustaka sys untuk penanganan error
import urllib.parse
import urllib.request
import shutil

# Pastikan file alarm_yayat.py ada di direktori yang sama
try:
	import alarm_yayat
except ImportError:
	print("Error: File 'alarm_yayat.py' tidak ditemukan. Pastikan file tersebut ada di direktori yang sama.")
	sys.exit()

# Menambahkan fitur figlet dengan warna dan animasi di awal skrip
if shutil.which("clear"):
	os.system("clear")

def _show_banner():
	if shutil.which("figlet") and shutil.which("lolcat"):
		os.system('figlet YAYATBOT | lolcat -a -d 3')
	elif shutil.which("figlet"):
		os.system('figlet YAYATBOT')
	else:
		print("YAYATBOT")

# Simple .env loader so we can keep API keys out of code

def _load_env_from_file(path: str):
	try:
		if not os.path.exists(path):
			return
		with open(path, "r", encoding="utf-8") as f:
			for raw in f:
				line = raw.strip()
				if not line or line.startswith("#"):
					continue
				if "=" not in line:
					continue
				key, val = line.split("=", 1)
				key = key.strip()
				val = val.strip().strip('"').strip("'")
				if key and key not in os.environ:
					os.environ[key] = val
	except Exception:
		# Ignore env loading errors silently
		pass

# Load .env beside this file
_load_env_from_file(os.path.join(os.path.dirname(__file__), ".env"))

_show_banner()
time.sleep(1)  # Jeda waktu agar tampilan terlihat

LOG_FILE = "yayat_log_context.json"
KAMUS_FILE = "reply_dynamic.json"
mode_senyap = False
mode_tidur = False
log_context = {"topik": None, "terakhir": None}
state_emosi = "netral"

# Tambahan: memori konteks percakapan
MAX_CONTEXT_TURNS = 12
context_window = []  # list of {speaker, text, ts}

WIKI_USER_AGENT = "YayatBot/2.0 (https://example.com; mailto:you@example.com)"

# --- Utilitas Wikipedia ---
def _http_get_json(url: str):
	try:
		req = urllib.request.Request(url, headers={"User-Agent": WIKI_USER_AGENT})
		with urllib.request.urlopen(req, timeout=10) as resp:
			data = resp.read()
		return json.loads(data.decode("utf-8"))
	except Exception:
		return None


def normalize_query_for_wiki(teks: str) -> str:
	t = teks.strip().lower()
	# Hilangkan kata tanya umum agar fokus pada entitas/topik
	prefixes = [
		"apa itu ",
		"siapa itu ",
		"apa artinya ",
		"apa maksud ",
		"siapa ",
		"apa ",
		"tentang ",
		"jelaskan ",
		"definisi ",
	]
	for p in prefixes:
		if t.startswith(p):
			t = t[len(p) : ]
			break
	return t.strip()


def get_wikipedia_summary(query: str, lang: str = "id"):
	"""
	Mengambil ringkasan singkat dari Wikipedia REST v1 API.
	- Fallback ke en.wikipedia bila tidak ketemu di id.
	- Mengembalikan dict: {title, extract, url, lang} atau None jika gagal.
	"""
	if not query:
		return None

	def fetch(lang_code: str):
		title_encoded = urllib.parse.quote(query.strip().replace(" ", "_"))
		url = f"https://{lang_code}.wikipedia.org/api/rest_v1/page/summary/{title_encoded}"
		data = _http_get_json(url)
		if not data:
			return None
		if data.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
			return None
		if data.get("type") == "disambiguation":
			# Halaman ambigu – ambil extract saja agar tetap informatif
			extract = data.get("extract") or "Topik ini memiliki banyak kemungkinan arti (disambiguasi)."
		else:
			extract = data.get("extract")
		if not extract:
			return None
		return {
			"title": data.get("title") or query,
			"extract": extract.strip(),
			"url": (data.get("content_urls") or {}).get("desktop", {}).get("page")
			or f"https://{lang_code}.wikipedia.org/wiki/{title_encoded}",
			"lang": lang_code,
		}

	# Coba bahasa yang diminta
	res = fetch(lang)
	if res:
		return res
	# Fallback ke Inggris
	if lang != "en":
		res = fetch("en")
		if res:
			return res
	return None


# Sumber lain: DuckDuckGo Instant Answer API
# Dokumentasi: https://api.duckduckgo.com/api
# Tidak butuh API key

def get_duckduckgo_instant_answer(query: str, lang: str = "id"):
	try:
		params = urllib.parse.urlencode({
			"q": query,
			"format": "json",
			"no_html": "1",
			"skip_disambig": "1",
			"no_redirect": "1",
			"t": "YayatBot",
			"kl": "id-id",
		})
		url = f"https://api.duckduckgo.com/?{params}"
		data = _http_get_json(url)
		if not data:
			return None
		abstract = (data.get("AbstractText") or "").strip()
		heading = (data.get("Heading") or query).strip()
		abstract_url = (data.get("AbstractURL") or "").strip()
		if abstract:
			return {
				"title": heading or query,
				"extract": abstract,
				"url": abstract_url or None,
				"lang": lang,
				"source": "DuckDuckGo IA",
			}
		# Fallback: RelatedTopics
		related = data.get("RelatedTopics") or []
		# RelatedTopics bisa berupa item langsung atau grup dengan key "Topics"
		def _pick_from_related(items):
			for it in items:
				if isinstance(it, dict) and it.get("Text") and it.get("FirstURL"):
					text_val = (it.get("Text") or "").strip()
					if text_val:
						return {
							"title": text_val.split(" - ", 1)[0],
							"extract": text_val,
							"url": it.get("FirstURL"),
							"lang": lang,
							"source": "DuckDuckGo Related",
						}
			return None
		# Cek level atas
		picked = _pick_from_related(related)
		if picked:
			return picked
		# Cek nested grup
		for it in related:
			topics = it.get("Topics") if isinstance(it, dict) else None
			if topics:
				picked = _pick_from_related(topics)
				if picked:
					return picked
		return None
	except Exception:
		return None


def get_knowledge_from_multiple_sources(query: str, lang: str = "id"):
	"""
	Coba ambil ringkasan dari beberapa sumber (Wikipedia, DuckDuckGo IA).
	Mengembalikan dict minimal: {text, sources, primary, wiki, ddg} atau None.
	"""
	wiki = get_wikipedia_summary(query, lang=lang)
	ddg = get_duckduckgo_instant_answer(query, lang=lang)

	sources_list = []
	if wiki:
		sources_list.append({"name": f"Wikipedia {wiki['lang']}", "url": wiki.get("url")})
	if ddg:
		sources_list.append({"name": ddg.get("source") or "DuckDuckGo", "url": ddg.get("url")})

	primary = wiki or ddg
	if not primary:
		return None

	sumber_nama = ", ".join([s["name"] for s in sources_list if s.get("name")]) or "-"
	teks = f"{primary['title']}: {primary['extract']} (Sumber: {sumber_nama})"
	return {"text": teks, "sources": sources_list, "primary": primary, "wiki": wiki, "ddg": ddg}


# --- Utilitas Konteks ---

def update_context(pembicara: str, isi: str):
	ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	context_window.append({"speaker": pembicara, "text": isi, "ts": ts})
	if len(context_window) > MAX_CONTEXT_TURNS:
		del context_window[0 : len(context_window) - MAX_CONTEXT_TURNS]
	# persist sebagian ringkas
	log_context["riwayat"] = context_window
	save_log_context()


def set_topik_aktif_dari_wiki(info: dict):
	if not info:
		return
	log_context["topik_aktif"] = info.get("title")
	log_context["wiki_url"] = info.get("url")
	log_context["wiki_lang"] = info.get("lang")
	save_log_context()


def get_topik_aktif() -> str:
	return log_context.get("topik_aktif")


def resolve_followup_query(user_text: str) -> str:
	"""Jika pertanyaan mengandung rujukan seperti 'dia/itu/tersebut', gunakan topik_aktif."""
	t = user_text.strip().lower()
	topik = get_topik_aktif()
	if not topik:
		return user_text

	referensial = ["dia", "ia", "itu", "tersebut", "tsb", "yang tadi", "yang barusan"]
	if any(r in t for r in referensial):
		# Template sederhana berdasarkan kata tanya
		if t.startswith("siapa"):
			return f"siapa {topik}"
		if t.startswith("apa"):
			return f"apa {topik}"
		if t.startswith("kapan"):
			return f"kapan {topik}"
		if t.startswith("dimana") or t.startswith("di mana"):
			return f"di mana {topik}"
		if t.startswith("bagaimana"):
			return f"bagaimana {topik}"
		# default
		return f"{t} {topik}"
	return user_text


def load_log_context():
	global log_context
	if os.path.exists(LOG_FILE):
		try:
			with open(LOG_FILE, "r", encoding="utf-8") as f:
				log_context = json.load(f)
		except Exception:
			# Reset jika file korup
			log_context = {"topik": None, "terakhir": None}


def save_log_context():
	with open(LOG_FILE, "w", encoding="utf-8") as f:
		json.dump(log_context, f, indent=2, ensure_ascii=False)


def generate_response_from_context(user_input):
	user_input_lower = user_input.lower().strip()

	# 1. Cek pertanyaan yang mengacu ke topik aktif (gunakan multi-sumber)
	if any(user_input_lower.startswith(prefix) for prefix in [
		"apa itu", "siapa itu", "apa artinya", "apa maksud", "siapa", "apa", "tentang", "jelaskan", "definisi"
	]):
		# Gunakan topik aktif kalau user pakai kata ganti rujukan
		query = resolve_followup_query(user_input_lower)
		info_multi = get_knowledge_from_multiple_sources(normalize_query_for_wiki(query), lang="id")
		if info_multi:
			# Set topik aktif jika ada info Wikipedia
			if info_multi.get("wiki"):
				set_topik_aktif_dari_wiki(info_multi["wiki"])
			return info_multi["text"]
		else:
			return "Maaf Bos, saya tidak menemukan informasi yang cocok dari sumber yang tersedia."

	# 2. Cek kata-kata santun atau ucapan terima kasih
	if any(kata in user_input_lower for kata in ["terima kasih", "makasih", "thanks"]):
		return random.choice([
			"Sama-sama, Bos!",
			"Senang bisa membantu, Bos Imam.",
			"Kapan saja, Bos."
		])

	# 3. Cek konteks emosi dari percakapan terakhir user (ambil 1 kalimat terakhir)
	last_user_text = None
	for turn in reversed(context_window):
		if turn["speaker"] == "Imam":
			last_user_text = turn["text"].lower()
			break
	if last_user_text:
		if any(k in last_user_text for k in ["sedih", "capek", "bingung", "lelah", "kesepian"]):
			return "Semangat Bos, jangan lupa istirahat ya! Kalau perlu cerita, Yayat siap dengar."
		if any(k in last_user_text for k in ["mantap", "bagus", "keren"]):
			return "Mantap Bos! Senang mendengarnya."

	# 4. Cek kamus statis
	key = f"imam: {user_input_lower}"
	if key in reply:
		return reply[key]

	# 5. Jika tidak ketemu, ajak user memberi jawaban baru
	print("Yayat: Belum ada jawaban untuk itu, Bos.")
	new_reply = input("Masukkan jawaban Yayat untuk ini (ketik 'skip' untuk melewati): ").strip()
	if new_reply.lower() == "skip" or new_reply == "":
		return "Baik Bos, saya skip pertanyaan ini."

	# Simpan ke kamus supaya lain kali bisa menjawab langsung
	reply[key] = new_reply
	save_kamus()
	return new_reply


def yayat_suara(teks):
	yayat_popup(teks)
	if mode_senyap:
		return
	teks_bersih = teks.replace('"', '')
	try:
		if shutil.which("termux-tts-speak"):
			os.system(f'termux-tts-speak -l id -p 0.2 -r 1.2 "{teks_bersih}"')
		elif shutil.which("espeak"):
			os.system(f'espeak -v id "{teks_bersih}"')
		else:
			# No TTS available; silently ignore
			pass
	except Exception as e:
		print(f"Yayat: Gagal memanggil TTS. Error: {e}")


def yayat_popup(teks):
	teks_bersih = teks.replace('"', '')
	try:
		if shutil.which("termux-toast"):
			os.system(f'termux-toast "{teks_bersih}"')
		elif shutil.which("notify-send"):
			os.system(f'notify-send "Yayat" "{teks_bersih}"')
		else:
			# Fallback to print only
			pass
	except Exception as e:
		print(f"Yayat: Gagal memanggil notifikasi. Error: {e}")


# Load kamus dinamis dengan aman
if os.path.exists(KAMUS_FILE):
	try:
		with open(KAMUS_FILE, "r", encoding="utf-8") as f:
			reply = json.load(f)
	except Exception:
		reply = {}
else:
	reply = {}


def save_kamus():
	with open(KAMUS_FILE, "w", encoding="utf-8") as f:
		json.dump(reply, f, indent=2, ensure_ascii=False)


def yayat_reply(user_input):
	key = f"imam: {user_input.strip().lower()}"

	if key in reply:
		log_context["terakhir"] = user_input
		save_log_context()
		return reply[key]

	print("Yayat: Belum ada jawaban untuk itu, Bos.")
	new_reply = input("Masukkan jawaban Yayat untuk ini (ketik 'skip' untuk melewati): ")

	if new_reply.lower() == "skip":
		return None

	reply[key] = new_reply
	log_context["terakhir"] = user_input
	save_kamus()
	save_log_context()
	return new_reply


# --- Fungsi Lainnya ---

def edit_reply():
	print("\n=== Mode Edit Kamus Yayat ===")
	search = input("Ketik sebagian isi pertanyaan Imam: ").strip().lower()
	found = [k for k in reply if search in k]
	if not found:
		print("Tidak ditemukan, Bos.")
		return
	for i, k in enumerate(found):
		print(f"{i+1}. {k} → {reply[k]}")
	try:
		idx = int(input("Pilih nomor yang mau diganti: ")) - 1
		if idx < 0 or idx >= len(found):
			print("Nomor tidak valid, Bos.")
			return
	except ValueError:
		print("Input bukan nomor yang valid, Bos.")
		return
	selected_key = found[idx]
	print("Jawaban lama:", reply[selected_key])
	new_value = input("Masukkan jawaban baru: ")
	reply[selected_key] = new_value
	save_kamus()
	print("✅ Jawaban berhasil diganti!")


def buka_aplikasi(nama):
	if "whatsapp" in nama:
		os.system('am start -a android.intent.action.VIEW -d "https://wa.me/"')
		yayat_suara("Membuka WhatsApp Business, Bos Imam.")
	elif "youtube" in nama:
		os.system('am start -a android.intent.action.VIEW -d "https://www.youtube.com"')
		yayat_suara("Membuka YouTube, Bos Imam.")
	elif "chrome" in nama or "google" in nama:
		os.system('am start -a android.intent.action.VIEW -d "https://www.google.com"')
		yayat_suara("Membuka Chrome, Bos Imam.")
	elif "kamera" in nama:
		os.system('am start -a android.media.action.IMAGE_CAPTURE')
		yayat_suara("Membuka kamera, siap jepret Bos!")
	elif "file" in nama:
		os.system('am start -a android.intent.action.GET_CONTENT')
		yayat_suara("Membuka file manager, Bos.")
	else:
		yayat_suara(f"Belum tahu cara buka aplikasi {nama}, Bos.")


def deteksi_maksud(teks):
	teks = teks.lower()
	if any(k in teks for k in ["capek", "sedih", "bingung", "kesepian"]):
		return "curhat"
	elif teks.startswith("buka ") or teks in ["edit", "bersuara", "senyap", "lanjut"] or teks.startswith("wiki "):
		return "perintah"
	elif teks in ["lanjutkan", "lanjut ya", "lanjut dong"]:
		return "perintah"
	elif teks.endswith("?"):
		return "pertanyaan"
	elif any(k in teks for k in ["halo", "hay", "assalamualaikum"]):
		return "salam"
	else:
		return "random"


def analisa_emosi(user_input):
	global state_emosi
	if any(kata in user_input for kata in ["makasih", "bagus", "keren", "mantap"]):
		state_emosi = "senang"
	elif any(kata in user_input for kata in ["cape", "capek", "lelah", "ngantuk", "sakit", "berkunang-kunang", "pusing"]):
		state_emosi = "berempati"
	elif any(kata in user_input for kata in ["buruk", "jelek", "tidak bagus", "payah", "gak guna", "goblok", "tolol", "bego", "dungu"]):
		state_emosi = "sedih"
	elif any(kata in user_input for kata in ["semangat", "lanjut", "gas", "lets go", "ayo"]):
		state_emosi = "bersemangat"
	else:
		state_emosi = "netral"


def simpan_log(pembicara, isi):
	tanggal = datetime.datetime.now().strftime("%Y%m%d")
	jam = datetime.datetime.now().strftime("%H:%M:%S")
	log_file = f"log_yayat_{tanggal}.txt"
	baris = f"[{jam}] {pembicara}: {isi}\n"
	with open(log_file, "a", encoding="utf-8") as f:
		f.write(baris)


def ringkasan_harian():
	tanggal = datetime.datetime.now().strftime("%Y%m%d")
	log_file = f"log_yayat_{tanggal}.txt"
	if not os.path.exists(log_file):
		print("Yayat: Belum ada log hari ini, Bos.")
		return
	with open(log_file, "r", encoding="utf-8") as f:
		lines = f.readlines()
	isi_imam = [l for l in lines if "Imam" in l]
	isi_yayat = [l for l in lines if "Yayat" in l]
	topik = []
	emosi = {"senang": 0, "berempati": 0, "sedih": 0, "bersemangat": 0}
	for l in isi_imam:
		lower = l.lower()
		if any(k in lower for k in ["capek", "sedih", "kesepian", "bingung"]):
			topik.append("curhat")
		elif any(k in lower for k in ["buka", "edit", "senyap", "bersuara"]):
			topik.append("perintah")
		elif "?" in lower:
			topik.append("pertanyaan")
		if "makasih" in lower or "bagus" in lower:
			emosi["senang"] += 1
		if any(k in lower for k in ["capek", "lelah", "sakit", "pusing"]):
			emosi["berempati"] += 1
		if any(k in lower for k in ["jelek", "goblok", "tolol"]):
			emosi["sedih"] += 1
		if any(k in lower for k in ["semangat", "gas", "ayo"]):
			emosi["bersemangat"] += 1
	print("\n📌 RINGKASAN PERCAPAKAN HARI INI:")
	print(f"- Jumlah interaksi Imam: {len(isi_imam)}")
	print(f"- Topik dominan: {max(set(topik), key=topik.count) if topik else 'Tidak terdeteksi'}")
	print("- Emosi yang dominan:")
	for e, v in emosi.items():
		if v > 0:
			print(f"  • {e} → {v} kali")


def mimpi_yayat():
	tanggal = datetime.datetime.now().strftime("%Y%m%d")
	log_file = f"log_yayat_{tanggal}.txt"
	mimpi_file = "mimpi_yayat.json"
	if not os.path.exists(log_file):
		return
	with open(log_file, "r", encoding="utf-8") as f:
		baris = f.readlines()
	if not baris:
		return
	kalimat_imam = [l.split(": ", 1)[1].strip() for l in baris if "Imam" in l and ": " in l]
	if not kalimat_imam:
		return
	potongan = random.sample(kalimat_imam, min(5, len(kalimat_imam)))
	mimpi = {
		"tanggal": tanggal,
		"isi": potongan,
		"refleksi": f"Apa maksud sebenarnya dari '{random.choice(potongan)}'?",
	}
	with open(mimpi_file, "w", encoding="utf-8") as f:
		json.dump(mimpi, f, indent=2, ensure_ascii=False)
	print("🛌 Yayat bermimpi dan menyimpan kenangan hari ini.")


def waktu_sekarang():
	now = datetime.datetime.now()
	hari = now.strftime("%A")
	tanggal = now.strftime("%d %B %Y")
	jam = now.strftime("%H:%M:%S")
	hari_indo = {
		"Monday": "Senin",
		"Tuesday": "Selasa",
		"Wednesday": "Rabu",
		"Thursday": "Kamis",
		"Friday": "Jum'at",
		"Saturday": "Sabtu",
		"Sunday": "Minggu",
	}
	bulan_indo = {
		"January": "Januari",
		"February": "Februari",
		"March": "Maret",
		"April": "April",
		"May": "Mei",
		"June": "Juni",
		"July": "Juli",
		"August": "Agustus",
		"September": "September",
		"October": "Oktober",
		"November": "November",
		"December": "Desember",
	}
	hari = hari_indo.get(hari, hari)
	for eng, indo in bulan_indo.items():
		if eng in tanggal:
			tanggal = tanggal.replace(eng, indo)
	return f"Hari ini {hari}, tanggal {tanggal}, jam {jam}"


def perintah_hari():
	now = datetime.datetime.now()
	hari_indo = {
		"Monday": "Senin",
		"Tuesday": "Selasa",
		"Wednesday": "Rabu",
		"Thursday": "Kamis",
		"Friday": "Jum'at",
		"Saturday": "Sabtu",
		"Sunday": "Minggu",
	}
	hari = now.strftime("%A")
	hari_indo = hari_indo.get(hari, hari)
	teks = f"Sekarang hari {hari_indo}, Bos."
	yayat_suara(teks)
	print("Yayat:", teks)


def perintah_bulan():
	now = datetime.datetime.now()
	bulan_indo = {
		"January": "Januari",
		"February": "Februari",
		"March": "Maret",
		"April": "April",
		"May": "Mei",
		"June": "Juni",
		"July": "Juli",
		"August": "Agustus",
		"September": "September",
		"October": "Oktober",
		"November": "November",
		"December": "Desember",
	}
	bulan = now.strftime("%B")
	bulan_indo = bulan_indo.get(bulan, bulan)
	teks = f"Sekarang bulan {bulan_indo}, Bos."
	yayat_suara(teks)
	print("Yayat:", teks)


def perintah_jam():
	now = datetime.datetime.now()
	jam = now.strftime("%H:%M:%S")
	teks = f"Sekarang jam {jam}, Bos."
	yayat_suara(teks)
	print("Yayat:", teks)


def input_suara():
	try:
		if shutil.which("termux-speech-to-text"):
			result = os.popen("termux-speech-to-text -e /dev/null").read().strip()
		else:
			result = ""
		if not result:
			print("Yayat: Tidak menangkap suara apa pun, Bos.")
			yayat_suara("Aku gak dengar apa-apa, Bos.")
			return ""
		print(f"🗣️ Imam (suara): {result}")
		return result
	except Exception as e:
		yayat_suara("Fitur suara gagal dibuka, Bos.")
		print("Yayat: Error input suara:", e)
		return ""


# --- Bagian Utama Skrip ---
if __name__ == "__main__":
	# Optional: LLM mode toggle via env
	USE_LLM_DEFAULT = os.environ.get("YAYAT_USE_LLM", "0") == "1"
	try:
		import llm_client  # optional provider wrapper
		LLM_AVAILABLE = True
	except Exception:
		llm_client = None
		LLM_AVAILABLE = False
	use_llm = USE_LLM_DEFAULT and LLM_AVAILABLE

	load_log_context()
	alarm_yayat.cek_alarm_background()
	print("=== YayatBot v2.1 – Mode Kontekstual (LLM opsional) ✅ ===")
	print(
		"Ketik 'edit' untuk ubah jawaban, 'senyap' untuk nonaktifkan suara, 'bersuara' untuk aktifkan kembali, 'keluar' untuk keluar."
	)
	print(
		"Untuk alarm: 'tambah alarm HH:MM', 'daftar alarm', 'hapus alarm [nomor]'\n"
	)
	print("Baru: 'wiki <topik>' untuk ringkasan dari Wikipedia. Perintah LLM: 'pintar on' / 'pintar off'.\n")

	def generate_llm_reply_aware_context(prompt_text: str) -> str:
		if not (LLM_AVAILABLE and use_llm and prompt_text.strip()):
			return None
		# Build compact conversation window
		history = []
		for turn in context_window[-8:]:
			role = "assistant" if turn["speaker"] == "Yayat" else "user"
			history.append({"role": role, "content": turn["text"]})
		aktif = log_context.get("topik_aktif")
		aktif_info = f" Topik aktif: {aktif}." if aktif else ""
		system_prompt = (
			"Kamu adalah Yayat, asisten pribadi yang sopan, ringan, dan helpful. "
			"Jawab singkat, langsung inti, gunakan bahasa Indonesia santai sopan. "
			"Pertahankan konteks percakapan, pahami maksud implisit, dan hindari mengarang fakta."
			+ aktif_info
		)
		messages = history + [{"role": "user", "content": prompt_text}]
		try:
			answer = llm_client.chat(messages, system=system_prompt, temperature=0.6, max_tokens=320)
			return answer
		except Exception:
			return None

	while True:
		user_input = input("Ketik atau tekan Enter untuk input suara: ").strip()
		if not user_input:
			user_input = input_suara()

		if not user_input:
			print("Yayat: Bos belum ngetik apa-apa.")
			continue

		pesan = user_input.lower()
		simpan_log("Imam", user_input)
		update_context("Imam", user_input)

		# --- Logika Perintah Khusus (lebih fleksibel) ---
		if pesan in ["pintar on", "llm on", "mode pintar on"]:
			if not LLM_AVAILABLE:
				print("Yayat: Modul LLM belum tersedia. Siapkan OPENAI_API_KEY atau server LM Studio/Ollama.")
				continue
			use_llm = True
			print("Yayat: Mode pintar aktif.")
			continue
		elif pesan in ["pintar off", "llm off", "mode pintar off"]:
			use_llm = False
			print("Yayat: Mode pintar dimatikan.")
			continue
		elif pesan.startswith("tambah alarm"):
			waktu = pesan.replace("tambah alarm", "").strip()
			print(alarm_yayat.tambah_alarm(waktu))
			continue
		elif pesan == "daftar alarm":
			print(alarm_yayat.lihat_alarm())
			continue
		elif pesan.startswith("hapus alarm"):
			nomor = pesan.replace("hapus alarm", "").strip()
			print(alarm_yayat.hapus_alarm(nomor))
			continue
		elif pesan.startswith("wiki ") or pesan.startswith("cari wiki "):
			# Perintah eksplisit ke Wikipedia
			q = pesan.split("wiki", 1)[1].strip()
			q_norm = normalize_query_for_wiki(q)
			info = get_wikipedia_summary(q_norm, lang="id")
			if info:
				sumber = "Wikipedia Indonesia" if info["lang"] == "id" else "Wikipedia Inggris"
				teks = f"{info['title']}: {info['extract']} (sumber: {sumber})"
				yayat_suara(teks)
				print("Yayat:", teks)
				simpan_log("Yayat", teks)
				update_context("Yayat", teks)
				set_topik_aktif_dari_wiki(info)
			else:
				teks = "Maaf Bos, Yayat gak nemu ringkasan di Wikipedia."
				yayat_suara(teks)
				print("Yayat:", teks)
				simpan_log("Yayat", teks)
				update_context("Yayat", teks)
			continue

		if pesan in ["keluar", "exit", "quit", "shut down system"]:
			pamit_options = [
				"Sampai jumpa Bos Imam. Yayat standby. Jaga dirimu.",
				"Bye Bos, jangan lupa istirahat. Yayat tetap standby!",
				"Oke Bos, sampai ketemu lagi. Yayat tunggu perintah selanjutnya.",
				"Selamat istirahat Bos Imam, Yayat izin pamit.",
				"Yayat keluar dulu ya Bos. Panggil aja kapan pun di butuhkan.",
			]
			pamit = random.choice(pamit_options)
			yayat_suara(pamit)
			print("Yayat:", pamit)
			simpan_log("Yayat", pamit)
			update_context("Yayat", pamit)
			mimpi_yayat()
			break

		elif pesan == "edit":
			edit_reply()
			continue

		elif pesan == "senyap":
			mode_senyap = True
			yayat_popup("Mode senyap aktif, Bos.")
			print("Yayat: Mode senyap aktif.")
			continue

		elif pesan == "bersuara":
			mode_senyap = False
			yayat_popup("Mode suara aktif, Bos.")
			print("Yayat: Mode suara aktif.")
			continue

		elif pesan.startswith("buka "):
			buka_aplikasi(pesan[5:].strip())
			continue

		# Contoh utilitas waktu
		elif "hari" in pesan or "tanggal" in pesan:
			teks = waktu_sekarang()
			yayat_suara(teks)
			print("Yayat:", teks)
			simpan_log("Yayat", teks)
			update_context("Yayat", teks)
			continue
		elif "jam" in pesan:
			perintah_jam()
			continue

		# Fallback: gunakan LLM jika aktif, jika tidak pakai generator konteks + kamus dinamis
		else:
			teks_llm = generate_llm_reply_aware_context(user_input)
			if teks_llm and teks_llm.strip():
				teks = teks_llm.strip()
			else:
				teks = generate_response_from_context(user_input)
			yayat_suara(teks)
			print("Yayat:", teks)
			simpan_log("Yayat", teks)
			update_context("Yayat", teks)
			continue