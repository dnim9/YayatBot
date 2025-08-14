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


def memory_add_fact(text: str, tags=None, meta=None):
	if not text:
		return
	entry = {
		"text": text.strip(),
		"tags": tags or [],
		"meta": meta or {},
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
		# Coba tarik konteks dari memori
		hits = retrieve_from_memory(query, top_k=3)
		if hits:
			ringkas = "; ".join([h.get("text") for h in hits if h.get("type") == "fact"])
			if ringkas:
				return f"Ringkas konteks: {ringkas}"
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

	# 4b. Retrieval dari memori sebelum minta ajar
	hits = retrieve_from_memory(user_input_lower, top_k=3)
	if hits:
		bag = []
		for h in hits:
			if h.get("type") == "fact":
				bag.append(f"- {h.get('text')}")
			else:
				bag.append(f"- {h.get('speaker')}: {h.get('text')}")
		return "Ini yang relevan dari memori:\n" + "\n".join(bag)

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


def normalize_text(teks: str) -> str:
	if not teks:
		return ""
	t = teks.lower().strip()
	# Normalize spaced forms
	t = t.replace("di mana", "dimana").replace("ke mana", "kemana")
	# Replace common slang/shortcuts
	replacements = {
		"gk": "tidak",
		"ga": "tidak",
		"gak": "tidak",
		"ngga": "tidak",
		"nggak": "tidak",
		"tdk": "tidak",
		"yg": "yang",
		"dgn": "dengan",
		"dg": "dengan",
		"jgn": "jangan",
		"hrs": "harus",
		"bgt": "banget",
		"bgs": "bagus",
		"btw": "ngomong-ngomong",
		"udh": "sudah",
		"udah": "sudah",
		"sdh": "sudah",
		"sm": "sama",
		"smua": "semua",
		"skrg": "sekarang",
		"bsk": "besok",
		"kmrn": "kemarin",
	}
	for a, b in replacements.items():
		t = t.replace(f" {a} ", f" {b} ")
	# Remove filler/particles at ends
	fillers = ["dong", "nih", "deh", "lah", "ya", "kok", "sih", "kan"]
	for f in fillers:
		if t.endswith(f" {f}"):
			t = t[: -len(f) - 1]
	# Collapse multiple spaces
	t = " ".join(t.split())
	return t

# --- Synonyms and token normalization ---
EQUIV_SETS = [
	{"apa", "arti", "maksud", "pengertian", "definisi", "meaning"},
	{"bagaimana", "gimana", "gmn"},
	{"dimana", "kemana", "mana"},
	{"siapa"},
]

def _normalize_token_simple(tok: str) -> str:
	# strip common clitics/suffix
	for suf in ["nya", "kah", "lah", "tah"]:
		if tok.endswith(suf) and len(tok) > len(suf) + 2:
			return tok[: -len(suf)]
	return tok

def expand_query_tokens(tokens: list) -> list:
	expanded = set()
	for t in tokens:
		tn = _normalize_token_simple(t)
		added = False
		for s in EQUIV_SETS:
			if tn in s:
				expanded |= s
				added = True
				break
		if not added:
			expanded.add(tn)
	return list(expanded)


def _tokenize(text: str) -> list:
	return [_normalize_token_simple(w) for w in normalize_text(text).split() if len(w) > 1]


def _bm25_score(query_tokens: list, doc_tokens_list: list) -> float:
	# Simplified BM25-ish: TF * IDF with smoothing
	if not query_tokens or not doc_tokens_list:
		return 0.0
	import math
	doc_len = len(doc_tokens_list)
	if doc_len == 0:
		return 0.0
	# term frequencies
	tf = {}
	for tok in doc_tokens_list:
		tf[tok] = tf.get(tok, 0) + 1
	# idf approximated by rarity in query
	idf = {}
	for t in set(query_tokens):
		idf[t] = 1.5  # constant boost for matched terms
	score = 0.0
	for t in query_tokens:
		score += (tf.get(t, 0) / (0.5 + 0.5 * doc_len)) * idf.get(t, 0)
	return score


def retrieve_from_memory(query: str, top_k: int = 3) -> list:
	"""Return top facts/messages matching query using simple BM25-ish scoring."""
	qt = expand_query_tokens(_tokenize(query))
	candidates = []
	for f in memory.get("facts", []):
		tokens = _tokenize(f.get("text", ""))
		s = _bm25_score(qt, tokens)
		if s > 0:
			candidates.append((s, {"type": "fact", "text": f.get("text"), "ts": f.get("ts"), "meta": f.get("meta", {}), "tags": f.get("tags", [])}))
	for m in memory.get("last_messages", [])[-40:]:
		tokens = _tokenize(m.get("text", ""))
		s = _bm25_score(qt, tokens)
		if s > 0:
			candidates.append((s, {"type": "message", "speaker": m.get("speaker"), "text": m.get("text"), "ts": m.get("ts")}))
	candidates.sort(key=lambda x: x[0], reverse=True)
	return [c[1] for c in candidates[:top_k]]

# --- Wiki indexing into memory ---
def index_wiki_title_into_memory(title: str, lang: str = "id", max_sections: int = 12, max_chars: int = 500):
	sections = get_wikipedia_sections(title, lang=lang)
	if not sections:
		return 0
	url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.strip().replace(' ', '_'))}"
	# Avoid duplicate indexing if already present
	marker = f"wiki:{title.lower()}"
	if any(marker in (f.get("tags") or []) for f in memory.get("facts", [])):
		return 0
	count = 0
	for s in sections[:max_sections]:
		text = (s.get("text") or "").strip()
		if not text:
			continue
		clip = text[:max_chars]
		memory_add_fact(
			f"WIKI[{title}] {s.get('title')}: {clip}",
			tags=["wiki", marker],
			meta={"source": "wikipedia", "title": title, "lang": lang, "url": url, "section": s.get("title")},
		)
		count += 1
	return count


def clear_wiki_index_from_memory(title: str):
	marker = f"wiki:{(title or '').lower()}"
	facts = memory.get("facts", [])
	new_facts = [f for f in facts if marker not in (f.get("tags") or [])]
	removed = len(facts) - len(new_facts)
	memory["facts"] = new_facts
	save_memory()
	return removed


def _strip_html(html: str) -> str:
	try:
		import re
		text = re.sub(r"<[^>]+>", " ", html or "")
		text = re.sub(r"\s+", " ", text).strip()
		return text
	except Exception:
		return html or ""


def get_wiktionary_definition(term: str, lang: str = "id"):
	try:
		encoded = urllib.parse.quote(term.strip().replace(" ", "_"))
		url = f"https://{lang}.wiktionary.org/api/rest_v1/page/definition/{encoded}"
		data = _http_get_json(url)
		if not data:
			return None
		# The API returns dict keyed by language codes
		entries = data.get(lang) or next(iter(data.values()), None)
		if not entries:
			return None
		defs = []
		for entry in entries:
			for d in entry.get("definitions", []):
				val = (d.get("definition") or "").strip()
				if val:
					defs.append(val)
		if not defs:
			return None
		teks = defs[0]
		return {"title": term, "extract": teks, "url": f"https://{lang}.wiktionary.org/wiki/{encoded}", "lang": lang, "source": "Wiktionary"}
	except Exception:
		return None


def search_wikipedia_pages(query: str, lang: str = "id", limit: int = 5):
	try:
		params = urllib.parse.urlencode({"q": query, "limit": str(limit)})
		url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page?{params}"
		data = _http_get_json(url)
		if not data:
			return []
		pages = data.get("pages") or []
		results = []
		for p in pages:
			title = p.get("title") or p.get("key")
			if title:
				desc = (p.get("description") or p.get("excerpt") or "").strip()
				results.append({"title": title, "description": _strip_html(desc)})
		return results
	except Exception:
		return []


def get_wikipedia_sections(title: str, lang: str = "id"):
	try:
		encoded = urllib.parse.quote(title.strip().replace(" ", "_"))
		url = f"https://{lang}.wikipedia.org/api/rest_v1/page/mobile-sections/{encoded}"
		data = _http_get_json(url)
		if not data:
			return []
		sections = []
		lead = (data.get("lead") or {}).get("sections") or []
		for s in lead:
			text = _strip_html(s.get("text") or "")
			if text:
				sections.append({"title": s.get("line") or title, "text": text})
		remaining = (data.get("remaining") or {}).get("sections") or []
		for s in remaining:
			text = _strip_html(s.get("text") or "")
			if text:
				sections.append({"title": s.get("line") or title, "text": text})
		return sections
	except Exception:
		return []


def prepare_wiki_session(title: str, lang: str = "id"):
	sections = get_wikipedia_sections(title, lang=lang)
	log_context["wiki_session"] = {
		"title": title,
		"lang": lang,
		"sections": sections,
		"cursor": 0,
	}
	save_log_context()


def wiki_session_next() -> str:
	sess = log_context.get("wiki_session") or {}
	sections = sess.get("sections") or []
	cursor = sess.get("cursor") or 0
	if cursor >= len(sections):
		return "Belum ada detail lain, Bos."
	item = sections[cursor]
	log_context["wiki_session"]["cursor"] = cursor + 1
	save_log_context()
	return f"{item.get('title')}: {item.get('text')[:700]}" + ("..." if len(item.get('text')) > 700 else "")


def wiki_search_then_summary(query: str, lang: str = "id"):
	results = search_wikipedia_pages(query, lang=lang, limit=5)
	if not results:
		return None
	best = results[0]
	info = get_wikipedia_summary(best["title"], lang=lang)
	return info


# --- Bagian Utama Skrip ---
if __name__ == "__main__":
	# LLM dinonaktifkan (default OFF permanen)
	USE_LLM_DEFAULT = False
	llm_client = None
	LLM_AVAILABLE = False
	use_llm = False

	load_log_context()
	load_memory() # Load memory on startup
	alarm_yayat.cek_alarm_background()
	print("=== YayatBot v2.3 – Small Talk & Follow-up Lebih Cerdas (Tanpa LLM) ✅ ===")
	print(
		"Ketik 'edit' untuk ubah jawaban, 'senyap' untuk nonaktifkan suara, 'bersuara' untuk aktifkan kembali, 'keluar' untuk keluar."
	)
	print(
		"Untuk alarm: 'tambah alarm HH:MM', 'daftar alarm', 'hapus alarm [nomor]'. Perintah memori: 'ingat ...', 'lupa ...', 'project set ...', 'memori?'\n"
	)
	print("Baru: 'wiki <topik>' untuk ringkasan dari Wikipedia. Mode LLM dinonaktifkan.\n")

	def generate_llm_reply_aware_context(prompt_text: str) -> str:
		# LLM dimatikan
		return None

	while True:
		user_input = input("Ketik atau tekan Enter untuk input suara: ").strip()
		if not user_input:
			user_input = input_suara()

		if not user_input:
			print("Yayat: Bos belum ngetik apa-apa.")
			continue

		pesan = normalize_text(user_input)
		simpan_log("Imam", user_input)
		update_context("Imam", user_input)
		memory_push_message("Imam", user_input) # Push user input to memory

		# --- Logika Perintah Khusus (lebih fleksibel) ---
		if pesan in ["pintar on", "llm on", "mode pintar on"]:
			print("Yayat: Mode LLM dinonaktifkan. Fokus ke small talk & follow-up lokal.")
			continue
		elif pesan in ["pintar off", "llm off", "mode pintar off"]:
			print("Yayat: Mode LLM memang sudah off, Bos.")
			continue
		elif pesan in ["status", "pintar?", "llm?"]:
			print("Yayat: LLM OFF. Menggunakan engine lokal untuk percakapan kontekstual.")
			continue
		elif pesan.startswith("set temp "):
			print("Yayat: Pengaturan temperatur diabaikan karena LLM OFF.")
			continue
		elif pesan.startswith("set model "):
			print("Yayat: Pengaturan model diabaikan karena LLM OFF.")
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
		elif pesan.startswith("wiki cari "):
			q = pesan.replace("wiki cari", "").strip()
			info = wiki_search_then_summary(q, lang="id")
			if info:
				set_topik_aktif_dari_wiki(info)
				prepare_wiki_session(info["title"], lang=info.get("lang", "id"))
				teks = f"{info['title']}: {info['extract']} (sumber: Wikipedia {info['lang']})"
				print("Yayat:", teks)
				simpan_log("Yayat", teks)
				update_context("Yayat", teks)
			else:
				print("Yayat: Tidak ketemu hasil yang cocok di Wikipedia.")
			continue
		elif pesan in ["wiki lanjut", "lanjut wiki", "detail wiki"]:
			teks = wiki_session_next()
			print("Yayat:", teks)
			simpan_log("Yayat", teks)
			update_context("Yayat", teks)
			continue
		elif pesan.startswith("arti ") or pesan.startswith("definisi "):
			term = pesan.split(" ", 1)[1].strip()
			res = get_wiktionary_definition(term, lang="id")
			if res:
				teks = f"{res['title']}: {res['extract']} (sumber: {res['source']})"
				print("Yayat:", teks)
				simpan_log("Yayat", teks)
				update_context("Yayat", teks)
			else:
				print("Yayat: Belum nemu arti di Wiktionary, Bos.")
			continue
		elif pesan.startswith("wiki index "):
			title = pesan.replace("wiki index", "").strip()
			if not title:
				print("Yayat: Judul kosong, Bos.")
				continue
			added = index_wiki_title_into_memory(title, lang="id")
			print(f"Yayat: Sudah diindeks {added} bagian dari Wikipedia untuk '{title}'.")
			continue
		elif pesan.startswith("wiki clear "):
			title = pesan.replace("wiki clear", "").strip()
			removed = clear_wiki_index_from_memory(title)
			print(f"Yayat: {removed} potongan dihapus dari memori untuk '{title}'.")
			continue
		elif pesan.startswith("cari memori "):
			q = pesan.replace("cari memori", "").strip()
			hits = retrieve_from_memory(q, top_k=5)
			if not hits:
				print("Yayat: Tidak ada yang cocok di memori.")
				continue
			print("Yayat: Yang relevan di memori:")
			for h in hits:
				if h.get("type") == "fact":
					print(f"- {h.get('text')}")
				else:
					print(f"- {h.get('speaker')}: {h.get('text')}")
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