import os
import sys
import json
import time
import traceback
import requests
import growattServer
from datetime import datetime
from zoneinfo import ZoneInfo

# Token Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ACCOUNTS = [
    {
        "label": "Impianto 1",
        "user": os.getenv("GROWATT_USER_1"),
        "pass": os.getenv("GROWATT_PASS_1")
    },
    {
        "label": "Impianto 2",
        "user": os.getenv("GROWATT_USER_2"),
        "pass": os.getenv("GROWATT_PASS_2")
    }
]

STATE_FILE = "state.json"
TZ_ROME = ZoneInfo("Europe/Rome")

CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://server.growatt.com",
    "Referer": "https://server.growatt.com/login",
    "X-Requested-With": "XMLHttpRequest"
}

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[-] Errore lettura {STATE_FILE}: {e}")
    return {}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[-] Errore salvataggio {STATE_FILE}: {e}")

def format_duration(seconds: float) -> str:
    mins = int(seconds // 60)
    hours = mins // 60
    rem_mins = mins % 60
    if hours > 0:
        return f"{hours}h {rem_mins}m"
    return f"{mins} minuti"

def get_italian_time_str(epoch_timestamp: float = None) -> str:
    if epoch_timestamp:
        dt = datetime.fromtimestamp(epoch_timestamp, tz=TZ_ROME)
    else:
        dt = datetime.now(TZ_ROME)
    return dt.strftime("%H:%M")

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[-] ERRORE: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID mancanti.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=15)
        if res.status_code != 200:
            print(f"[-] Errore invio Telegram HTTP {res.status_code}: {res.text}")
        else:
            print("[+] Notifica Telegram inviata.")
    except Exception as e:
        print(f"[-] Eccezione invio Telegram: {e}")

def create_growatt_session(server_url: str):
    api = growattServer.GrowattApi()
    api.server_url = server_url
    api.session.headers.update(CUSTOM_HEADERS)
    return api

def check_account(account_info: dict, is_daytime: bool, state: dict):
    label = account_info["label"]
    user = (account_info["user"] or "").strip()
    password = (account_info["pass"] or "").strip()

    print(f"\n==================== CONTROLLO {label} ====================")
    if not user or not password:
        print(f"[-] Credenziali mancanti per {label}.")
        return

    servers = ["https://server.growatt.com/", "https://server-api.growatt.com/"]
    api = None
    login_res = None
    last_error = None

    for srv in servers:
        try:
            print(f"[*] Tentativo login su {srv}...")
            temp_api = create_growatt_session(srv)
            res = temp_api.login(user, password)
            if res and (isinstance(res, dict) or isinstance(res, list)):
                api = temp_api
                login_res = res
                print(f"[+] Login riuscito su {srv}!")
                break
        except Exception as e:
            last_error = e

    if not api or not login_res:
        print(f"[-] Impossibile effettuare il login: {last_error}")
        return

    user_id = None
    if isinstance(login_res, dict):
        user_data = login_res.get("user")
        if isinstance(user_data, dict):
            user_id = user_data.get("id")
        user_id = user_id or login_res.get("userId") or login_res.get("uid")

    if not user_id:
        return

    try:
        plant_list_res = api.plant_list(user_id)
        plants = plant_list_res.get("data", []) if isinstance(plant_list_res, dict) else plant_list_res
    except Exception as e:
        print(f"[-] Errore lista impianti: {e}")
        return

    for plant in plants:
        plant_id = plant.get("plantId") or plant.get("id")
        plant_name = plant.get("plantName", "Impianto")

        try:
            device_list = api.device_list(plant_id)
            devices = device_list.get("data", []) if isinstance(device_list, dict) else device_list
            if not isinstance(devices, list):
                devices = [devices]

            for dev in devices:
                if not isinstance(dev, dict):
                    continue

                sn = dev.get("deviceSn") or dev.get("sn")
                dev_type = str(dev.get("deviceType", "")).lower()
                dev_alias = dev.get("deviceAilas") or dev.get("deviceAlias") or sn
                display_name = f"{label} ({plant_name}) - {dev_alias}"

                # Controllo offline / lost
                is_lost = dev.get("lost")
                is_offline = (is_lost is True or str(is_lost).lower() == "true")

                detail = {}
                try:
                    if "sph" in dev_type or "storage" in dev_type or "mix" in dev_type:
                        detail = api.sph_detail(sn)
                    elif "tlx" in dev_type or "min" in dev_type:
                        detail = api.mix_detail(sn)
                    else:
                        detail = api.inverter_detail(sn)
                except Exception:
                    try:
                        detail = api.inverter_detail(sn)
                    except Exception:
                        detail = {}

                fault_code = detail.get("faultType") or detail.get("faultCode") or 0
                try:
                    pac = float(detail.get("pac") or detail.get("ppv") or detail.get("pacToGridTotal") or 0)
                except (ValueError, TypeError):
                    pac = 0.0

                # Verifica anomalia corrente
                current_error_type = None
                current_error_msg = ""

                if is_offline:
                    current_error_type = "OFFLINE"
                    current_error_msg = "L'inverter non comunica o la sezione Storage è disconnessa."
                elif fault_code not in [0, "0", None, ""]:
                    current_error_type = f"ERRORE {fault_code}"
                    current_error_msg = f"Codice Guasto: <code>{fault_code}</code>"
                elif is_daytime and pac == 0:
                    current_error_type = "PRODUZIONE_ZERO"
                    current_error_msg = "Produzione a 0 W nella fascia 09:00 - 18:00."

                inverter_state = state.get(sn, {})
                was_in_error = inverter_state.get("in_error", False)
                now_ts = time.time()

                if current_error_type:
                    if not was_in_error:
                        # NUOVO ERRORE
                        state[sn] = {
                            "in_error": True,
                            "error_type": current_error_type,
                            "start_ts": now_ts,
                            "display_name": display_name
                        }
                        start_time_str = get_italian_time_str(now_ts)
                        send_telegram(
                            f"🚨 <b>ALLARME: {current_error_type}</b>\n\n"
                            f"📍 <b>{display_name}</b>\n"
                            f"🔢 Seriale: <code>{sn}</code>\n"
                            f"📝 {current_error_msg}\n"
                            f"🕒 Inizio anomalia: <b>{start_time_str}</b>"
                        )
                    else:
                        dur_str = format_duration(now_ts - inverter_state.get("start_ts", now_ts))
                        print(f"[-] {display_name} ancora in anomalia ({current_error_type}) da {dur_str}.")
                else:
                    if was_in_error:
                        # RIPRISTINO
                        start_ts = inverter_state.get("start_ts", now_ts)
                        duration_str = format_duration(now_ts - start_ts)
                        start_time_str = get_italian_time_str(start_ts)
                        end_time_str = get_italian_time_str(now_ts)
                        prev_err = inverter_state.get("error_type", "ANOMALIA")

                        send_telegram(
                            f"✅ <b>RIPRISTINO: INVERTER OPERATIVO</b>\n\n"
                            f"📍 <b>{display_name}</b>\n"
                            f"🔢 Seriale: <code>{sn}</code>\n"
                            f"🟢 Lo stato di <b>{prev_err}</b> è rientrato!\n"
                            f"⏱️ <b>Durata anomalia:</b> {duration_str} (dalle {start_time_str} alle {end_time_str})\n"
                            f"⚡ Potenza Attuale: <b>{pac} W</b>"
                        )

                        state[sn] = {
                            "in_error": False,
                            "last_resolved": now_ts
                        }
                    else:
                        print(f"[OK] {display_name} regolare ({pac} W)")

        except Exception as e:
            print(f"[-] Errore lettura {plant_name}: {e}")

def main():
    now_rome = datetime.now(TZ_ROME)
    print(f"Avvio monitoraggio Growatt (Ora Italiana: {now_rome.strftime('%Y-%m-%d %H:%M:%S')})")
    
    # Fascia diurna attiva tra le 09:00 e le 18:00 (ora italiana)
    is_daytime = 9 <= now_rome.hour < 18
    print(f"Fascia controllo produzione 0W attiva (09-18): {is_daytime}")

    state = load_state()

    for acc in ACCOUNTS:
        check_account(acc, is_daytime, state)

    save_state(state)
    print("\n[+] Controllo completato.")

if __name__ == "__main__":
    main()
