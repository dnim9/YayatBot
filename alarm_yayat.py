import json
import os
import threading
import time
import datetime
import shutil

ALARM_FILE = "yayat_alarm_list.json"
_THREAD_STARTED = False
_THREAD_LOCK = threading.Lock()


def _ensure_file():
    if not os.path.exists(ALARM_FILE):
        with open(ALARM_FILE, "w", encoding="utf-8") as f:
            json.dump({"alarms": []}, f, ensure_ascii=False, indent=2)


def _load() -> dict:
    _ensure_file()
    try:
        with open(ALARM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"alarms": []}


def _save(data: dict):
    with open(ALARM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _is_termux() -> bool:
    return (
        os.environ.get("TERMUX_VERSION") is not None
        or os.path.exists("/data/data/com.termux/files/usr/bin")
    )


def _notify_alarm(title: str, body: str):
    # Termux first
    if _command_exists("termux-vibrate"):
        os.system("termux-vibrate -d 1500 -f")
    if _command_exists("termux-toast"):
        os.system(f'termux-toast "{title}: {body}"')
    if _command_exists("termux-tts-speak"):
        safe = body.replace('"', "")
        os.system(f'termux-tts-speak -l id -p 0.2 -r 1.2 "{safe}"')
        return
    # Linux desktop notify
    if _command_exists("notify-send"):
        os.system(f'notify-send "{title}" "{body}"')
        return
    # Fallback: print
    print(f"[ALARM] {title} - {body}")


def _parse_time_and_label(waktu: str):
    waktu = (waktu or "").strip()
    if not waktu:
        return None, None
    parts = waktu.split()
    if not parts:
        return None, None
    time_part = parts[0]
    label = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
    # validate HH:MM
    try:
        hh, mm = time_part.split(":", 1)
        if len(hh) != 2 or len(mm) != 2:
            return None, None
        hour = int(hh)
        minute = int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None, None
    except Exception:
        return None, None
    return f"{hh}:{mm}", label


def tambah_alarm(waktu: str) -> str:
    waktu_valid, label = _parse_time_and_label(waktu)
    if not waktu_valid:
        return "Format waktu tidak valid. Gunakan HH:MM, contoh: 07:30"
    data = _load()
    for a in data.get("alarms", []):
        if a.get("time") == waktu_valid and a.get("enabled", True):
            return f"Alarm untuk {waktu_valid} sudah ada, Bos."
    alarm_obj = {
        "time": waktu_valid,
        "label": label,
        "enabled": True,
        "last_triggered_date": None,
    }
    data.setdefault("alarms", []).append(alarm_obj)
    _save(data)
    return f"Alarm ditambahkan untuk {waktu_valid}{' (' + label + ')' if label else ''}."


def lihat_alarm() -> str:
    data = _load()
    alarms = data.get("alarms", [])
    if not alarms:
        return "Belum ada alarm, Bos."
    lines = ["Daftar alarm:"]
    for idx, a in enumerate(alarms, start=1):
        status = "aktif" if a.get("enabled", True) else "nonaktif"
        label = a.get("label", "")
        if label:
            lines.append(f"{idx}. {a.get('time')} - {label} [{status}]")
        else:
            lines.append(f"{idx}. {a.get('time')} [{status}]")
    return "\n".join(lines)


def hapus_alarm(nomor: str) -> str:
    nomor = (nomor or "").strip()
    data = _load()
    alarms = data.get("alarms", [])
    if not alarms:
        return "Belum ada alarm yang bisa dihapus, Bos."
    # Coba berdasarkan index
    try:
        idx = int(nomor) - 1
        if 0 <= idx < len(alarms):
            removed = alarms.pop(idx)
            _save(data)
            return f"Alarm {removed.get('time')} dihapus."
    except Exception:
        pass
    # Coba berdasarkan jam (HH:MM)
    waktu_valid, _ = _parse_time_and_label(nomor)
    if waktu_valid:
        new_alarms = [a for a in alarms if a.get("time") != waktu_valid]
        if len(new_alarms) != len(alarms):
            data["alarms"] = new_alarms
            _save(data)
            return f"Alarm {waktu_valid} dihapus."
    return "Nomor atau format waktu tidak valid, Bos."


def _scheduler_loop():
    while True:
        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            hhmm_now = now.strftime("%H:%M")
            data = _load()
            changed = False
            for alarm in data.get("alarms", []):
                if not alarm.get("enabled", True):
                    continue
                if alarm.get("time") == hhmm_now:
                    if alarm.get("last_triggered_date") != today_str:
                        label = alarm.get("label") or "Waktunya!"
                        _notify_alarm("Alarm Yayat", f"{alarm.get('time')} {label}")
                        alarm["last_triggered_date"] = today_str
                        changed = True
            if changed:
                _save(data)
        except Exception:
            # Keep scheduler alive
            pass
        # Check every 20s to avoid missing minute boundary
        time.sleep(20)


def cek_alarm_background():
    global _THREAD_STARTED
    with _THREAD_LOCK:
        if _THREAD_STARTED:
            return
        t = threading.Thread(target=_scheduler_loop, daemon=True)
        t.start()
        _THREAD_STARTED = True