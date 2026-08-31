import os
import sys
import traceback
import requests
import growattServer
from datetime import datetime

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

# Header realistici per evitare il blocco 403 (Cloudflare/WAF)
CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://server.growatt.com",
    "Referer": "https://server.growatt.com/login",
    "X-Requested-With": "XMLHttpRequest"
}

def send_telegram(message: str):
    """Invia notifica Telegram in modo sicuro"""
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
            print("[+] Notifica Telegram inviata con successo.")
    except Exception as e:
        print(f"[-] Eccezione invio Telegram: {e}")

def create_growatt_session(server_url: str):
    api = growattServer.GrowattApi()
    api.server_url = server_url
    api.session.headers.update(CUSTOM_HEADERS)
    return api

def check_account(account_info: dict, is_daytime: bool):
    label = account_info["label"]
    user = (account_info["user"] or "").strip()
    password = (account_info["pass"] or "").strip()

    print(f"\n==================== CONTROLLO {label} ====================")
    if not user or not password:
        print(f"[-] Credenziali mancanti per {label} (Controlla i Secrets su GitHub).")
        return

    # Proviamo prima il server principale e poi il server API se il primo fallisce
    servers = ["https://server.growatt.com/", "https://server-api.growatt.com/"]
    api = None
    login_res = None
    last_error = None

    for srv in servers:
        try:
            print(f"[*] Tentativo di login per '{user}' su {srv}...")
            temp_api = create_growatt_session(srv)
            res = temp_api.login(user, password)
            if res and (isinstance(res, dict) or isinstance(res, list)):
                api = temp_api
                login_res = res
                print(f"[+] Login riuscito su {srv}!")
                break
        except Exception as e:
            print(f"[-] Tentativo su {srv} fallito: {e}")
            last_error = e

    if not api or not login_res:
        send_telegram(
            f"⚠️ <b>Growatt Monitor ({label})</b>\n"
            f"Login fallito per <code>{user}</code>.\n"
            f"Dettagli: {last_error}"
        )
        return

    # Estrazione user_id
    user_id = None
    if isinstance(login_res, dict):
        user_data = login_res.get("user")
        if isinstance(user_data, dict):
            user_id = user_data.get("id")
        user_id = user_id or login_res.get("userId") or login_res.get("uid")

    if not user_id:
        # Se la risposta non contiene un id valido (es. credenziali errate)
        msg_err = login_res.get("msg") if isinstance(login_res, dict) else "Credenziali errate"
        send_telegram(f"⚠️ <b>Growatt Monitor ({label})</b>\nLogin non riuscito per <code>{user}</code>: {msg_err}")
        return

    print(f"[+] User ID autenticato: {user_id}")

    # 2. Recupero Impianti
    try:
        plant_list_res = api.plant_list(user_id)
        plants = plant_list_res.get("data", []) if isinstance(plant_list_res, dict) else plant_list_res
        if not plants:
            print(f"[-] Nessun impianto trovato per l'utente {user}")
            return
    except Exception as e:
        print(f"[-] Errore recupero lista impianti: {e}")
        traceback.print_exc()
        return

    # 3. Controllo Dispositivi
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
                if is_lost is True or str(is_lost).lower() == "true":
                    send_telegram(
                        f"🚨 <b>ALLARME: INVERTER OFFLINE</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"Stato: Inverter non raggiungibile o spento."
                    )
                    continue

                # Lettura dettagli
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

                # Segnalazione guasti o produzione ferma di giorno
                if fault_code not in [0, "0", None, ""]:
                    send_telegram(
                        f"⚠️ <b>ALLARME: ERRORE INVERTER</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"⚠️ <b>Codice Errore: {fault_code}</b>\n"
                        f"⚡ Potenza: <b>{pac} W</b>"
                    )
                elif is_daytime and pac == 0:
                    send_telegram(
                        f"⚠️ <b>AVVISO: PRODUZIONE ZERO</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"⚡ Produzione a <b>0 W</b> in pieno giorno."
                    )
                else:
                    print(f"[OK] {display_name} -> Operativo ({pac} W)")

        except Exception as e:
            print(f"[-] Errore gestione impianto {plant_name}: {e}")

def main():
    print(f"Avvio monitoraggio Growatt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    now_hour_utc = datetime.utcnow().hour
    # Ore diurne in Italia (circa 06:00 - 18:00 UTC)
    is_daytime = 6 <= now_hour_utc <= 18

    for acc in ACCOUNTS:
        check_account(acc, is_daytime)

    print("\n[+] Controllo completato.")

if __name__ == "__main__":
    main()
