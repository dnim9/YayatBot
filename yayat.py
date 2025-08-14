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

# Load LLM persona config
LLM_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "llm_config.json")
try:
	with open(LLM_CONFIG_PATH, "r", encoding="utf-8") as _cf:
		LLM_CONFIG = json.load(_cf)
except Exception:
	LLM_CONFIG = None

_show_banner()
time.sleep(1)  # Jeda waktu agar tampilan terlihat

LOG_FILE = "yayat_log_context.json"
KAMUS_FILE = "reply_dynamic.json"
MEMORY_FILE = "yayat_memory.json"
mode_senyap = False
mode_tidur = False
log_context = {"topik": None, "terakhir": None}
state_emosi = "netral"

# Long-term memory structure
memory = {
	"facts": [],  # list of {text, tags, ts}
	"current_project": None,
	"preferences": {},
	"last_seen": None,
	"last_messages": [],  # ring buffer of recent messages
	"pending": None  # {kind, data, ts}
}

def load_memory():
	global memory
	if os.path.exists(MEMORY_FILE):
		try:
			with open(MEMORY_FILE, "r", encoding="utf-8") as f:
				memory = json.load(f)
		except Exception:
			memory = {
				"facts": [],
				"current_project": None,
				"preferences": {},
				"last_seen": None,
				"last_messages": []
			}


def save_memory():
	with open(MEMORY_FILE, "w", encoding="utf-8") as f:
		json.dump(memory, f, indent=2, ensure_ascii=False)


def memory_add_fact(text: str, tags=None):
	if not text:
		return
	entry = {
		"text": text.strip(),
		"tags": tags or [],
		"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
	}
	memory.setdefault("facts", []).append(entry)
	# Limit facts to last 200 to avoid bloat
	if len(memory["facts"]) > 200:
		memory["facts"] = memory["facts"][-200:]
	save_memory()


def memory_set_pending(kind: str, data=None):
	memory["pending"] = {
		"kind": kind,
		"data": data or {},
		"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
	}
	save_memory()


def memory_get_pending():
	return memory.get("pending")


def memory_clear_pending():
	memory["pending"] = None
	save_memory()


def memory_forget(keyword: str):
	if not keyword:
		return 0
	facts = memory.get("facts", [])
	before = len(facts)
	keyword_lower = keyword.lower()
	facts = [f for f in facts if keyword_lower not in (f.get("text", "").lower())]
	memory["facts"] = facts
	save_memory()
	return before - len(facts)


def memory_set_project(name: str):
	memory["current_project"] = name.strip() if name else None
	save_memory()


def memory_get_project():
	return memory.get("current_project")


def memory_push_message(speaker: str, text: str):
	msg = {
		"speaker": speaker,
		"text": text,
		"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
	}
	buf = memory.setdefault("last_messages", [])
	buf.append(msg)
	if len(buf) > 50:
		memory["last_messages"] = buf[-50:]
	memory["last_seen"] = msg["ts"]
	save_memory()


def memory_quick_summary(max_items: int = 5) -> str:
	proj = memory.get("current_project")
	facts = memory.get("facts", [])[-max_items:]
	parts = []
	if proj:
		parts.append(f"Project aktif: {proj}.")
	if facts:
		for i, f in enumerate(facts, 1):
			parts.append(f"{i}. {f.get('text')}")
	return "\n".join(parts) if parts else "Memori masih kosong, Bos."

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
	"""Jika pertanyaan mengandung rujukan seperti 'dia/itu/tersebut', gunakan topik_aktif.
	Hindari mengganti jika user menyebut topik eksplisit, misal: 'apa itu kulkas'."""
	t = user_text.strip().lower()
	topik = get_topik_aktif()
	if not topik:
		return user_text

	# Jika bentuknya 'apa itu <sesuatu>' atau 'siapa itu <nama>', anggap eksplisit → jangan diganti
	if t.startswith("apa itu ") or t.startswith("siapa itu "):
		return user_text
	# Kasus 'apa itu' tanpa objek → rujuk ke topik aktif
	if t in ("apa itu", "siapa itu"):
		if t.startswith("apa"):
			return f"apa {topik}"
		else:
			return f"siapa {topik}"

	referensial_tokens = {"dia", "ia", "itu", "tersebut", "tsb"}
	tokens = t.split()
	# Jika kalimat berakhir dengan kata rujukan (tanpa objek setelahnya) atau mengandung frasa rujukan umum
	if tokens and (tokens[-1] in referensial_tokens or any(phr in t for phr in ["yang tadi", "yang barusan"])):
		if tokens[0].startswith("siapa"):
			return f"siapa {topik}"
		if tokens[0].startswith("apa"):
			return f"apa {topik}"
		if tokens[0].startswith("kapan"):
			return f"kapan {topik}"
		if tokens[0].startswith("dimana") or t.startswith("di mana"):
			return f"di mana {topik}"
		if tokens[0].startswith("bagaimana"):
			return f"bagaimana {topik}"
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


def small_talk_response(user_input_lower: str) -> str:
	# Small talk patterns with quick, friendly replies (more varied and conversational)
	# Return None if no match
	jam = datetime.datetime.now().hour
	waktu = "pagi" if 4 <= jam < 11 else ("siang" if jam < 15 else ("sore" if jam < 18 else "malam"))

	def any_in(keys):
		return any(k in user_input_lower for k in keys)

	def pantun():
		pantuns = [
			"Jalan-jalan ke Kota Tegal,\nBeli tahu sama si Bowo.\nKalau lelah istirahat sejenak,\nNanti lanjut biar lebih fokus, yo!",
			"Ke pasar beli pepaya,\nPulangnya mampir beli tomat.\nKalau ada yang mau ditanya,\nBilang ya Bos, biar aku bantu cepat.",
			"Pagi hari minum jamu,\nBiar badan makin sehat.\nKalau target sudah tersusun rapi,\nEksekusinya jadi lebih mudah dan tepat.",
		]
		return random.choice(pantuns)

	def joke():
		jokes = [
			"Kenapa programmer jarang keluar rumah? Karena banyak 'bugs' di luar sana.",
			"Kenapa komputer kedinginan? Soalnya buka 'Windows'.",
			"Trading itu kayak naik motor: jangan kebut, yang penting sampai tujuan selamat.",
		]
		return random.choice(jokes)

	# Kabar dan sapaan lanjutan
	if any_in(["apa kabar", "gimana kabar", "gmn kabar", "gmna kabar", "kabarmu", "kabarnya"]):
		return random.choice([
			f"Alhamdulillah baik, Bos Imam. Semoga {waktu} Bos juga lancar. Ada yang mau kita gas dulu?",
			"Aman dan siap siaga, Bos Imam. Kita mulai dari trading, coding, atau santai ngobrol?",
		])

	# Makan/minum
	if any_in(["sudah makan", "udah makan", "makan belum", "sdh makan", "suda makan", "mkn", "makan"]):
		memory_set_pending("makan_check", {})
		return random.choice([
			"Yayat ini bot jadi gak makan, Bos. Bos sendiri sudah makan?",
			"Jangan lupa makan ya Bos. By the way, sudah makan belum?",
		])
	if any_in(["udah ngopi", "ngopi belum", "ngopi", "kopi"]):
		memory_set_pending("ngopi_check", {})
		return random.choice([
			"Kalau ngopi, Yayat ikut semangatnya aja ☕. Bos udah ngopi?",
			"Sip, kopi bikin fokus. Bos sudah ngopi belum?",
		])

	# Lokasi/keberadaan
	if any_in(["lagi di mana", "lagi dimana", "dimana kamu", "di mana kamu", "posisi di mana", "posisi dimana"]):
		return random.choice([
			"Lagi standby di HP Bos Imam, siap dipanggil kapan saja.",
			"Aku di sini terus, Bos. Tinggal sebut, kita langsung jalan.",
		])

	# Ngopi/santai
	if any_in(["santai dulu"]):
		return random.choice([
			"Boleh santai bentar. Habis itu kita lanjut yang penting-penting, setuju Bos?",
		])

	# Mood/emosi ringan
	if any_in(["bosen", "bosan", "gabut", "jenuh", "blank"]):
		memory_set_pending("choose_menu", {})
		return random.choice([
			"Kalau lagi jenuh, pilih: bahas trading ringan, ngoding simpel, atau cerita santai. Mau yang mana, Bos?",
			"Biar gak jenuh, kita bisa bikin to-do kecil sekarang. Mau aku bantu susun 3 langkah cepat? (trading/coding/santai)",
		])

	# Cuaca
	if any_in(["cuaca", "hujan gak", "cerah gak", "panas gak", "lagi hujan", "mendung"]):
		return random.choice([
			"Semoga cuacanya bersahabat di tempat Bos. Kalau perlu, kita atur kerjaan indoor dulu.",
			"Kalau hujan, enak fokus di planning dan belajar dikit. Mau aku siapkan bacaan singkat?",
		])

	# Tertawa/reaksi ringan
	if any_in(["hehe", "haha", "wkwk", "wk wk", "lol", ":)", ":D"]):
		return random.choice(["wkwk siap Bos.", "hehe siap bantu, Bos.", "Mantap, lanjut yuk."])

	# Perkenalan/kepo bot
	if any_in(["siapa kamu", "kamu siapa", "lu siapa", "kenalan dong", "tentang kamu"]):
		return "Aku Yayat, asisten pribadi & trading assistant. Siap bantu Bos Imam kapan pun."

	# Umur/dll
	if any_in(["umur berapa", "berapa umur", "umurmu", "umur kamu"]):
		return "Kalau umur, Yayat ini program jadi gak punya umur, Bos. Yang penting bisa berguna buat Bos."

	# Ketidakpastian
	if any_in(["terserah", "bebas aja", "gimana ajalah", "apa aja deh", "ikut kamu aja", "ikut bos aja"]):
		memory_set_pending("choose_menu", {})
		return "Biar fokus, pilih ya Bos: trading, coding, bisnis, atau santai ngobrol."

	# Pantun/jokes/tebak-tebakan
	if any_in(["pantun", "bikin pantun", "pantun dong"]):
		return pantun()
	if any_in(["jokes", "joke", "lelucon", "cerita lucu", "guyon"]):
		return joke()
	if any_in(["tebak-tebakan", "tebakan"]):
		return "Tebakan: Apa bedanya trader sama nelayan? Sama-sama nunggu momen yang pas, bedanya satu nunggu sinyal, satu nunggu ikan 😄."

	# Motivasi
	if any_in(["semangat", "motivasi dong", "kasih semangat", "ayo semangat"]):
		return random.choice([
			"Semangat, Bos! Sedikit demi sedikit jadi bukit. Kita gas pelan tapi pasti.",
			"Ayo kita eksekusi satu hal kecil sekarang. Habis itu lanjut yang lain. Bisa!",
		])

	# Rencana/target ringan
	if any_in(["target hari ini", "agenda", "rencana", "to-do", "todo"]):
		memory_set_pending("todo_collect", {})
		return "Yuk sebutkan 3 hal cepat untuk hari ini (pisahkan dengan koma)."

	# Hobi/minat
	if any_in(["hobi", "suka apa", "minat kamu"]):
		return "Kalau hobi, aku senangnya bantu Bos: mulai dari trading, ngoding di Termux, sampai bikin ide bisnis."

	# Penutup ringan
	if any_in(["makasih", "terima kasih", "thanks banget"]):
		return random.choice(["Sama-sama, Bos!", "Siap, kapan pun!", "Sama-sama, selalu siap bantu."])

	return None


def generate_response_from_context(user_input):
	user_input_lower = user_input.lower().strip()

	# 0. Salam/sapaan umum
	greet_keywords = [
		"hai", "hi", "hello", "halo", "hay",
		"assalamualaikum", "assalamu'alaikum",
		"selamat pagi", "selamat siang", "selamat sore", "selamat malam",
	]
	if any(k in user_input_lower for k in greet_keywords):
		jam = datetime.datetime.now().hour
		if 4 <= jam < 11:
			waktu = "pagi"
		elif 11 <= jam < 15:
			waktu = "siang"
		elif 15 <= jam < 18:
			waktu = "sore"
		else:
			waktu = "malam"
		return f"Selamat {waktu}, Bos Imam. Ada yang bisa Yayat bantu?"

	# 0b. Small talk sederhana
	if any(phrase in user_input_lower for phrase in ["lagi apa", "ngapain", "ngapain nih", "sedang apa", "lagi ngapain"]):
		return "Lagi standby siap bantu, Bos."
	# 0c. Small talk tambahan
	st = small_talk_response(user_input_lower)
	if st:
		return st

	# 0d. Handle pending follow-up (yes/no or choices)
	pending = memory_get_pending()
	if pending:
		kind = pending.get("kind")
		affirm = ["iya", "ya", "y", "udah", "sudah", "siap", "ok", "oke", "okey", "sip", "done", "iyap", "yoi"]
		neg = ["belum", "tidak", "ga", "gak", "nggak", "enggak", "no"]
		if kind in ("ngopi_check", "makan_check"):
			if any(w in user_input_lower for w in affirm):
				memory_clear_pending()
				if kind == "ngopi_check":
					return "Mantap, semoga makin fokus. Lanjut apa, Bos: trading, coding, atau santai?"
				else:
					return "Sip, perut aman. Lanjut mau ngapain, Bos?"
			elif any(w in user_input_lower for w in neg):
				memory_clear_pending()
				if kind == "ngopi_check":
					return "Baik, jangan lupa rehat dan ngopi kalau sempat. Kita bahas apa dulu sekarang?"
				else:
					return "Jangan lupa makan dulu ya Bos. Sambil nunggu, mau bahas trading ringan atau coding?"
			else:
				# Not clear, ask again briefly
				return "Maksudnya sudah atau belum, Bos?"
		elif kind == "choose_menu":
			memory_clear_pending()
			if any(w in user_input_lower for w in ["trading", "xau", "forex", "emas"]):
				return "Siap. Mau analisis XAU/USD, bahas setup, atau tanya indikator tertentu?"
			if any(w in user_input_lower for w in ["coding", "ngoding", "python", "termux"]):
				return "Gas coding. Mau mulai dari apa, Bos: script Termux, bot trading, atau utilitas kecil?"
			if any(w in user_input_lower for w in ["bisnis", "kelapa", "dropship"]):
				return "Oke. Bahas strategi bisnis kelapa/dropship atau optimasi operasional dulu?"
			if any(w in user_input_lower for w in ["santai", "ngopi", "cerita"]):
				return "Santai juga perlu. Bos mau cerita apa?"
			# no clear choice
			memory_set_pending("choose_menu", {})
			return "Pilih ya Bos: trading, coding, bisnis, atau santai."
		elif kind == "todo_collect":
			# parse comma-separated list
			items = [i.strip() for i in user_input.split(",") if i.strip()]
			memory_clear_pending()
			if items:
				for it in items[:5]:
					memory_add_fact(f"TODO: {it}", tags=["todo"])
				return "Siap. Sudah kucatat. Mau kuingatkan lagi nanti?"
			else:
				memory_set_pending("todo_collect", {})
				return "Coba tulis lagi dengan koma ya Bos. Contoh: bayar tagihan, cek chart, kirim email"

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

	# 5. Jika tidak ketemu, untuk input sangat singkat kasih jawaban ramah standar
	if len(user_input_lower) <= 3:
		return "Iya, Bos. Bagaimana bisa ku bantu?"

	# 6. Jika tidak ketemu, ajak user memberi jawaban baru
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
	elif any(k in teks for k in ["hai", "hi", "hello", "halo", "hay", "assalamualaikum", "assalamu'alaikum", "selamat pagi", "selamat siang", "selamat sore", "selamat malam"]):
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
	# Default ON unless explicitly disabled via env
	if os.environ.get("YAYAT_USE_LLM") is None:
		USE_LLM_DEFAULT = True
	try:
		import llm_client  # optional provider wrapper
		LLM_AVAILABLE = True
	except Exception:
		llm_client = None
		LLM_AVAILABLE = False
	use_llm = USE_LLM_DEFAULT and LLM_AVAILABLE

	load_log_context()
	load_memory() # Load memory on startup
	alarm_yayat.cek_alarm_background()
	print("=== YayatBot v2.2 – Mode Kontekstual (LLM default ON + Memori Jangka Panjang) ✅ ===")
	print(
		"Ketik 'edit' untuk ubah jawaban, 'senyap' untuk nonaktifkan suara, 'bersuara' untuk aktifkan kembali, 'keluar' untuk keluar."
	)
	print(
		"Untuk alarm: 'tambah alarm HH:MM', 'daftar alarm', 'hapus alarm [nomor]'\n"
	)
	print("Baru: 'wiki <topik>' untuk ringkasan dari Wikipedia. LLM default aktif. Perintah: 'pintar off', 'status', 'set temp 0.8', 'set model gpt-4o-mini'.\n")

	def generate_llm_reply_aware_context(prompt_text: str) -> str:
		if not (LLM_AVAILABLE and use_llm and prompt_text.strip()):
			return None
		# Build compact conversation window
		history = []
		for turn in context_window[-8:]:
			role = "assistant" if turn["speaker"] == "Yayat" else "user"
			history.append({"role": role, "content": turn["text"]})
		aktif = log_context.get("topik_aktif")
		aktif_info = ""
		if aktif:
			aktif_info = (
				f" Jika pertanyaan pakai rujukan tanpa menyebut topik baru, gunakan topik aktif: {aktif}. "
				"Jika user menyebut topik baru eksplisit (misal 'apa itu kulkas'), abaikan topik aktif."
			)
		# Build persona-aware system prompt
		def _build_persona(prompt_text: str) -> str:
			name = (LLM_CONFIG or {}).get("name") or "YayatBot-LLM"
			role = (LLM_CONFIG or {}).get("role") or "AI Assistant"
			traits = ", ".join((LLM_CONFIG or {}).get("personality", {}).get("core_traits", []) or [])
			styles = (LLM_CONFIG or {}).get("personality", {}).get("speech_styles", {})
			formal = styles.get("formal_mode", {}) if isinstance(styles, dict) else {}
			casual = styles.get("casual_mode", {}) if isinstance(styles, dict) else {}
			formal_triggers = [t.lower() for t in (formal.get("trigger") or [])]
			casual_triggers = [t.lower() for t in (casual.get("trigger") or [])]
			is_formal = any(k in prompt_text.lower() for k in formal_triggers)
			is_casual = any(k in prompt_text.lower() for k in casual_triggers)
			# default: formal jika teknis; selain itu casual
			mode = "formal" if is_formal and not is_casual else ("casual" if is_casual else "casual")
			address_user = "Imam" if mode == "formal" else "Bos"
			if isinstance(casual.get("address_user"), list) and mode == "casual":
				address_user = casual.get("address_user")[0] or address_user
			# time aware
			if (LLM_CONFIG or {}).get("context_awareness", {}).get("adjust_response_based_on_time"):
				h = datetime.datetime.now().hour
				waktu = "pagi" if 4 <= h < 11 else ("siang" if h < 15 else ("sore" if h < 18 else "malam"))
				time_hint = f" Sesuaikan sapaan untuk waktu {waktu}."
			else:
				time_hint = ""
			know = (LLM_CONFIG or {}).get("knowledge_focus", [])
			rules = (LLM_CONFIG or {}).get("rules", {})
			examples = (LLM_CONFIG or {}).get("examples", {})
			formal_ex = examples.get("formal")
			casual_ex = examples.get("casual")
			mode_tone = formal.get("tone") if mode == "formal" else casual.get("tone")
			return (
				f"Nama: {name}. Peran: {role}. Sifat: {traits}. Mode: {mode} (tone: {mode_tone}). "
				f"Panggil user: {address_user}. Fokus pengetahuan: {', '.join(know)}. "
				f"Aturan: no-wrong-info={rules.get('never_give_wrong_info', True)}, "
				f"step-by-step={rules.get('calculate_step_by_step', True)}, humor-ringan={rules.get('keep_humor_light', True)}, "
				f"samakan-tone={rules.get('always_match_user_tone', True)}."
				+ time_hint
				+ (f" Contoh formal: {formal_ex}." if formal_ex else "")
				+ (f" Contoh santai: {casual_ex}." if casual_ex else "")
			)
		persona_prompt = _build_persona(prompt_text)
		system_prompt = (
			"Kamu adalah Yayat, asisten pribadi yang sopan, ringan, dan helpful. "
			"Jawab singkat, langsung inti, gunakan bahasa Indonesia santai sopan. "
			"Pertahankan konteks percakapan, pahami maksud implisit, dan hindari mengarang fakta."
			+ aktif_info
			+ " "
			+ persona_prompt
		)
		# Inference params (allow runtime override)
		temp = float(os.environ.get("YAYAT_LLM_TEMP", "0.6"))
		max_tok = int(os.environ.get("YAYAT_LLM_MAXTOK", "320"))
		# Insert current user message
		messages = history + [{"role": "user", "content": prompt_text}]
		try:
			answer = llm_client.chat(messages, system=system_prompt, temperature=temp, max_tokens=max_tok)
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
		memory_push_message("Imam", user_input) # Push user input to memory

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
		elif pesan in ["status", "pintar?", "llm?"]:
			provider = (
				"openai" if os.environ.get("OPENAI_API_KEY") else (
					"lmstudio" if os.environ.get("LMSTUDIO_BASE_URL") else (
						"ollama" if os.environ.get("OLLAMA_BASE_URL") else "auto"
					)
				)
			)
			print(f"Yayat: LLM tersedia={LLM_AVAILABLE}, mode={'aktif' if use_llm else 'nonaktif'}, provider={provider}.")
			continue
		elif pesan.startswith("set temp "):
			val = pesan.replace("set temp", "").strip()
			try:
				t = float(val)
				os.environ["YAYAT_LLM_TEMP"] = str(max(0.0, min(1.5, t)))
				print(f"Yayat: Temperatur LLM diset ke {os.environ['YAYAT_LLM_TEMP']}.")
			except Exception:
				print("Yayat: Nilai temperatur tidak valid. Contoh: set temp 0.8")
			continue
		elif pesan.startswith("set model "):
			name = pesan.replace("set model", "").strip()
			if not name:
				print("Yayat: Model kosong. Contoh: set model gpt-4o-mini atau llama3.1:8b-instruct")
				continue
			# Heuristic: if includes ':' assume Ollama, else OpenAI
			if ":" in name:
				os.environ["YAYAT_OLLAMA_MODEL"] = name
				print(f"Yayat: Model Ollama diset ke {name}.")
			else:
				os.environ["YAYAT_LLM_MODEL"] = name
				print(f"Yayat: Model OpenAI/LMStudio diset ke {name}.")
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
		elif pesan.startswith("ingat ") or pesan.startswith("remember "):
			fakta = user_input.split(" ", 1)[1].strip()
			if fakta:
				memory_add_fact(fakta)
				print("Yayat: Oke, sudah kuingat, Bos.")
			else:
				print("Yayat: Apa yang mau diingat, Bos?")
			continue
		elif pesan.startswith("lupa ") or pesan.startswith("forget "):
			kw = user_input.split(" ", 1)[1].strip()
			removed = memory_forget(kw)
			print(f"Yayat: Sudah kulupakan {removed} item yang cocok.")
			continue
		elif pesan.startswith("project set "):
			name = user_input.split(" ", 2)[2].strip() if len(user_input.split(" ")) >= 3 else ""
			if name:
				memory_set_project(name)
				print(f"Yayat: Project aktif diset ke: {name}.")
			else:
				print("Yayat: Nama project kosong.")
			continue
		elif pesan in ["project?", "project", "lihat project"]:
			cp = memory_get_project()
			print(f"Yayat: Project aktif: {cp if cp else '-'}.")
			continue
		elif pesan in ["memori?", "memory?", "ingatanku", "ringkas memori"]:
			print("Yayat:")
			print(memory_quick_summary())
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