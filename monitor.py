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

def send_telegram(message: str):
    """Invia notifica Telegram in modo sicuro"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[-] ERRORE: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non configurati nei Secrets.")
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

def check_account(account_info: dict, is_daytime: bool):
    label = account_info["label"]
    user = account_info["user"]
    password = account_info["pass"]

    print(f"\n==================== CONTROLLO {label} ====================")
    if not user or not password:
        print(f"[-] Credenziali mancanti per {label} (Controlla i Secrets su GitHub).")
        return

    api = growattServer.GrowattApi()
    api.server_url = "https://server.growatt.com/"

    # 1. Tentativo di Login
    try:
        print(f"[*] Tentativo di login per utente: '{user}'...")
        login_res = api.login(user, password)
        print(f"[*] Risposta Login: {login_res}")

        # Estrazione user_id
        user_id = None
        if isinstance(login_res, dict):
            user_data = login_res.get("user")
            if isinstance(user_data, dict):
                user_id = user_data.get("id")
            user_id = user_id or login_res.get("userId") or login_res.get("uid")

        if not user_id:
            msg = f"⚠️ <b>Growatt Monitor ({label})</b>\nLogin fallito per <code>{user}</code>.\nVerifica username e password nei Secrets."
            send_telegram(msg)
            return

        print(f"[+] Login effettuato con successo! User ID: {user_id}")

    except Exception as e:
        print(f"[-] Eccezione durante il login di {label}: {e}")
        traceback.print_exc()
        send_telegram(f"⚠️ <b>Growatt Monitor ({label})</b>\nErrore connessione server Growatt: {e}")
        return

    # 2. Recupero Impianti
    try:
        plant_list_res = api.plant_list(user_id)
        print(f"[*] Dati impianti ricevuti: {plant_list_res}")

        plants = []
        if isinstance(plant_list_res, dict):
            plants = plant_list_res.get("data", [])
        elif isinstance(plant_list_res, list):
            plants = plant_list_res

        if not plants:
            print(f"[-] Nessun impianto trovato per l'utente {user}")
            return

    except Exception as e:
        print(f"[-] Errore recupero lista impianti: {e}")
        traceback.print_exc()
        return

    # 3. Controllo Inverter per ogni impianto
    for plant in plants:
        plant_id = plant.get("plantId") or plant.get("id")
        plant_name = plant.get("plantName", "Impianto")

        try:
            device_list = api.device_list(plant_id)
            print(f"[*] Dispositivi per impianto '{plant_name}': {device_list}")
            
            # Se la risposta è un dizionario con chiave 'data' o una lista
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

                # Controllo stato 'lost' / offline
                is_lost = dev.get("lost")
                if is_lost is True or str(is_lost).lower() == "true":
                    send_telegram(
                        f"🚨 <b>ALLARME: INVERTER OFFLINE</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"Stato: Inverter disconnesso o spento."
                    )
                    continue

                # Recupero dettagli dispositivo
                detail = {}
                try:
                    if "sph" in dev_type or "storage" in dev_type or "mix" in dev_type:
                        detail = api.sph_detail(sn)
                    elif "tlx" in dev_type or "min" in dev_type:
                        detail = api.mix_detail(sn)
                    else:
                        detail = api.inverter_detail(sn)
                except Exception as ex_detail:
                    print(f"[*] Tentativo lettura generica per {sn}: {ex_detail}")
                    try:
                        detail = api.inverter_detail(sn)
                    except Exception:
                        detail = {}

                print(f"[*] Dettagli per {sn}: {detail}")

                # Estrazione codice errore e potenza
                fault_code = detail.get("faultType") or detail.get("faultCode") or 0
                try:
                    pac = float(detail.get("pac") or detail.get("ppv") or detail.get("pacToGridTotal") or 0)
                except (ValueError, TypeError):
                    pac = 0.0

                # Verifica allarmi
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
                    print(f"[OK] {display_name} funzionante regolarmente ({pac} W)")

        except Exception as e:
            print(f"[-] Errore gestione impianto {plant_name}: {e}")
            traceback.print_exc()

def main():
    print(f"Avvio monitoraggio Growatt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    now_hour_utc = datetime.utcnow().hour
    # Indicativamente ore diurne in Italia (UTC tra le 6 e le 18)
    is_daytime = 6 <= now_hour_utc <= 18

    for acc in ACCOUNTS:
        check_account(acc, is_daytime)

    print("\n[+] Monitoraggio completato con successo.")

if __name__ == "__main__":
    main()
