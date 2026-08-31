import os
import requests
import growattServer
from datetime import datetime

# Token Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Lista dei due account (Nome etichetta, Username, Password)
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
    """Invia un messaggio sul tuo Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Errore: Token Telegram o Chat ID mancanti.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Errore invio Telegram: {e}")

def check_account(account_info: dict, is_daytime: bool):
    label = account_info["label"]
    user = account_info["user"]
    password = account_info["pass"]

    if not user or not password:
        print(f"Credenziali non configurate per {label}")
        return

    api = growattServer.GrowattApi()
    try:
        login_response = api.login(user, password)
        user_id = login_response.get("user", {}).get("id") or login_response.get("userId")
        print(f"[{label}] Login Growatt effettuato per user: {user}")
    except Exception as e:
        send_telegram(
            f"⚠️ <b>Growatt Monitor ({label})</b>\n"
            f"Impossibile effettuare il login all'account <code>{user}</code>.\n"
            f"Errore: {e}"
        )
        return

    try:
        plant_list = api.plant_list(user_id)
    except Exception as e:
        print(f"[{label}] Errore nel recupero lista impianti: {e}")
        return

    for plant in plant_list.get("data", []):
        plant_id = plant.get("plantId")
        plant_name = plant.get("plantName", "Senza nome")
        devices = api.device_list(plant_id)

        for dev in devices:
            sn = dev.get("deviceSn")
            dev_type = dev.get("deviceType")
            dev_alias = dev.get("deviceAilas") or dev.get("deviceAlias") or sn
            display_name = f"{label} ({plant_name}) - {dev_alias}"

            try:
                # Se il dispositivo risulta offline/disconnesso
                is_lost = dev.get("lost")
                if is_lost is True:
                    send_telegram(
                        f"🚨 <b>ALLARME: INVERTER OFFLINE</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"Stato: L'inverter non comunica con la rete o è spento."
                    )
                    continue

                # Lettura dettagli (SPH / Storage o inverter fotovoltaico standard)
                if dev_type == "storage" or "sph" in str(dev_type).lower():
                    detail = api.sph_detail(sn)
                else:
                    detail = api.inverter_detail(sn)

                # Controllo stato guasti / errori
                fault_code = detail.get("faultType") or 0
                pac = float(detail.get("pac") or detail.get("ppv") or 0)  # Potenza istantanea in Watt

                # Se viene segnalato un codice di errore
                if fault_code not in [0, "0", None]:
                    send_telegram(
                        f"⚠️ <b>ALLARME: ANOMALIA / ERRORE INVERTER</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"⚠️ <b>Codice Errore: {fault_code}</b>\n"
                        f"⚡ Potenza Attuale: <b>{pac} W</b>"
                    )

                # Se è giorno (es. 08:00 - 19:00) e la produzione è ferma a 0W
                elif is_daytime and pac == 0:
                    send_telegram(
                        f"⚠️ <b>AVVISO: PRODUZIONE ZERO DI GIORNO</b>\n\n"
                        f"📍 <b>{display_name}</b>\n"
                        f"🔢 Seriale: <code>{sn}</code>\n"
                        f"⚡ L'inverter è connesso ma eroga <b>0 W</b> in pieno giorno."
                    )
                else:
                    print(f"[{label}] Inverter {sn} OK - Potenza: {pac} W")

            except Exception as e:
                print(f"[{label}] Errore lettura dettagli SN {sn}: {e}")

def main():
    now_hour = datetime.now().hour
    # Ore diurne (tra le 8:00 e le 19:00)
    is_daytime = 8 <= now_hour <= 19

    for acc in ACCOUNTS:
        check_account(acc, is_daytime)

if __name__ == "__main__":
    main()
