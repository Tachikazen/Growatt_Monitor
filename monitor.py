import os
import sys
import json
import time
import calendar
import traceback
import requests
import growattServer
from datetime import datetime
from zoneinfo import ZoneInfo

# Token Telegram (prelevati dai Secrets)
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

# Parametri Economici di Default (modificabili liberamente)
PRICE_KWH_SAVED = float(os.getenv("PRICE_KWH_SAVED", "0.25"))  # Risparmio in bolletta (€/kWh autoconsumato)
PRICE_KWH_GRID = float(os.getenv("PRICE_KWH_GRID", "0.10"))    # Rimborso GSE / Ritiro Dedicato (€/kWh immesso)

# Coordinate geografiche indicative per il meteo (Nord/Centro Italia)
LATITUDE = float(os.getenv("LATITUDE", "45.46"))
LONGITUDE = float(os.getenv("LONGITUDE", "9.19"))

STATE_FILE = "state.json"
TZ_ROME = ZoneInfo("Europe/Rome")

# Dizionario Ufficiale Codici Errore e Guasti Growatt SPH / MIN / MIC
GROWATT_FAULT_CODES = {
    201: ("Corrente di Dispersione Elevata (Leakage Current High)", "Rilevata corrente di fuga verso terra superiore alla norma."),
    202: ("Guasto Sensore Corrente (Sensor Fault)", "Anomalia nel sensore di lettura corrente interna."),
    203: ("Sovracorrente Transitoria (Transient Overcurrent)", "Picco improvviso di corrente AC/DC oltre i limiti di sicurezza."),
    300: ("Tensione Rete Enel Fuori Limite (AC V Outrange)", "La rete elettrica Enel ha superato la soglia di voltaggio massima o minima consentita."),
    302: ("Frequenza Rete Enel Fuori Limite (AC F Outrange)", "La frequenza della rete Enel è instabile (diversa da 50 Hz)."),
    303: ("Sovraccarico Uscita EPS (EPS Overload)", "I carichi collegati all'uscita di emergenza/backup superano la potenza massima."),
    304: ("Errore Comunicazione BMS Batteria (BMS Open)", "Interrotta la linea di comunicazione dati CAN/RS485 tra inverter e batteria."),
    401: ("Blocco Comunicazione Interna (DSP/COM Fault)", "Mancata risposta della scheda di controllo/porta di comunicazione interna."),
    402: ("Sovratensione Bus DC (Bus Voltage High)", "Tensione del bus DC interno oltre la soglia di sicurezza."),
    403: ("Tensione Batteria Bassa (Bat Voltage Low)", "La batteria è scesa al di sotto della tensione minima operativa."),
    404: ("Guasto Relè di Rete (Grid Relay Fault)", "I relè di connessione alla rete elettrica non commutano correttamente."),
    405: ("Guasto Relè Interno (Relay Fault)", "Anomalia nei relè di scambio interni all'inverter."),
    406: ("Auto-Test Fallito (Auto Test Fault)", "Il controllo automatico di sicurezza iniziale non è andato a buon fine."),
    407: ("Guasto Sensore HCT (HCT Fault)", "Anomalia nel sensore di corrente ad effetto Hall."),
    408: ("Sovratemperatura Interna (Temp Over)", "Temperatura interna dell'inverter troppo elevata. Verificare ventilazione e dissipatore."),
    409: ("Basso Isolamento PV (PV Isolation Low)", "Dispersione verso terra delle stringhe fotovoltaiche (frequente con pioggia o umidità elevata)."),
    410: ("Sovratemperatura Induttanza (Inductor Temp Over)", "Surriscaldamento degli induttori interni di potenza."),
    411: ("Sovratensione Stringa PV (PV Voltage High)", "La tensione a vuoto (Voc) dei pannelli solari ha superato il limite massimo dell'inverter."),
    417: ("Guasto DSP Principale (DSP Fault)", "Anomalia nel microprocessore di calcolo principale DSP."),
    418: ("Versione DSP non Compatibile (DSP Version Error)", "Incongruenza tra le versioni firmware DSP e HMI."),
    420: ("Guasto Sensore NTC (NTC Fault)", "Sensore di rilevamento temperatura interno interrotto o in corto."),
    425: ("Batteria Sottotensione Critica (Bat Under Voltage)", "La batteria ha raggiunto una scarica profonda e richiede ricarica di soccorso.")
}

MONTH_NAMES_IT = {
    1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile",
    5: "Maggio", 6: "Giugno", 7: "Luglio", 8: "Agosto",
    9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
}

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
                content = f.read().strip()
                if content:
                    return json.loads(content)
        except Exception as e:
            print(f"[-] Errore lettura {STATE_FILE}: {e}")
    return {}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[+] Stato salvato in {STATE_FILE}.")
    except Exception as e:
        print(f"[-] Errore salvataggio {STATE_FILE}: {e}")

def format_duration(seconds: float) -> str:
    mins = int(max(1, seconds // 60))
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

def safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        return float(str(val).replace("W", "").replace("kW", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return default

def get_fault_description(fault_code) -> tuple:
    """Restituisce (Nome errore, Spiegazione dettagliata)"""
    try:
        code_int = int(fault_code)
    except (ValueError, TypeError):
        code_int = -1

    if code_int in GROWATT_FAULT_CODES:
        return GROWATT_FAULT_CODES[code_int]
    return (f"Codice Guasto: {fault_code}", "Anomalia rilevata dall'inverter. Consultare il manuale tecnico Growatt.")

def send_telegram(message: str, chat_id: str = None):
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not target_chat:
        print("[-] ERRORE: TELEGRAM_TOKEN o TELEGRAM_CHAT_ID mancanti.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
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

def create_growatt_session():
    api = growattServer.GrowattApi()
    api.server_url = "https://server.growatt.com/"
    api.session.headers.update(CUSTOM_HEADERS)
    return api

def get_solar_forecast():
    """Recupera la previsione solare e meteo per domani da Open-Meteo"""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={LATITUDE}&longitude={LONGITUDE}&daily=weathercode,temperature_2m_max,sunshine_duration"
            f"&timezone=Europe%2FRome&forecast_days=2"
        )
        res = requests.get(url, timeout=10)
        data = res.json()
        daily = data.get("daily", {})

        wcode = daily.get("weathercode", [0, 0])[1]
        t_max = daily.get("temperature_2m_max", [0, 0])[1]
        sun_sec = daily.get("sunshine_duration", [0, 0])[1]
        sun_hours = round(sun_sec / 3600, 1)

        if wcode == 0:
            desc = "☀️ Sereno"
            estimate = "Ottima (~28-34 kWh)"
        elif wcode in [1, 2]:
            desc = "🌤️ Poco nuvoloso"
            estimate = "Buona (~22-28 kWh)"
        elif wcode == 3:
            desc = "☁️ Coperto / Nuvoloso"
            estimate = "Media (~14-20 kWh)"
        elif wcode in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
            desc = "🌧️ Pioggia"
            estimate = "Bassa (~6-12 kWh)"
        elif wcode in [71, 73, 75, 85, 86]:
            desc = "🌨️ Neve"
            estimate = "Minima (~3-8 kWh)"
        elif wcode in [95, 96, 99]:
            desc = "⛈️ Temporali"
            estimate = "Variabile/Bassa (~8-15 kWh)"
        else:
            desc = "⛅ Variabile"
            estimate = "Media (~18-24 kWh)"

        return f"{desc} (~{sun_hours}h di sole, max {t_max}°C) | Stima: <b>{estimate}</b>"
    except Exception as e:
        print(f"[-] Errore previsioni meteo: {e}")
        return ""

def check_account(account_info: dict, is_daytime: bool, state: dict, live_data: list, daily_stats: list):
    label = account_info["label"]
    user = (account_info["user"] or "").strip()
    password = (account_info["pass"] or "").strip()

    print(f"\n==================== CONTROLLO {label} ====================")
    if not user or not password:
        print(f"[-] Credenziali mancanti per {label}.")
        return

    api = create_growatt_session()
    try:
        login_res = api.login(user, password)
    except Exception as e:
        print(f"[-] Errore login: {e}")
        return

    user_id = None
    if isinstance(login_res, dict):
        user_data = login_res.get("user")
        if isinstance(user_data, dict):
            user_id = user_data.get("id")
        user_id = user_id or login_res.get("userId") or login_res.get("uid")

    if not user_id:
        print(f"[-] Login fallito per user {user}")
        return

    try:
        plant_list_res = api.plant_list(user_id)
        plants = plant_list_res.get("data", []) if isinstance(plant_list_res, dict) else plant_list_res
    except Exception as e:
        print(f"[-] Errore lista impianti: {e}")
        return

    for plant in plants:
        plant_id = str(plant.get("plantId") or plant.get("id"))
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
                dev_alias = dev.get("deviceAilas") or dev.get("deviceAlias") or sn
                display_name = f"{label} ({plant_name}) - {dev_alias}"

                is_lost_dev = dev.get("lost")
                lost_from_list = (is_lost_dev is True or str(is_lost_dev).lower() == "true")

                sys_status = {}
                totals = {}
                try:
                    sys_status = api.mix_system_status(sn, plant_id)
                except Exception as ex:
                    print(f"[-] Errore mix_system_status per {sn}: {ex}")

                try:
                    totals = api.mix_totals(sn, plant_id)
                except Exception as ex:
                    print(f"[-] Errore mix_totals per {sn}: {ex}")

                ppv_kw = safe_float(sys_status.get("ppv") or sys_status.get("storagePpv"))
                p_pv1_kw = safe_float(sys_status.get("pPv1"))
                p_pv2_kw = safe_float(sys_status.get("pPv2"))
                solar_kw = ppv_kw if ppv_kw > 0 else (p_pv1_kw + p_pv2_kw)
                solar_w = round(solar_kw * 1000.0, 1)

                p_grid_w = round(safe_float(sys_status.get("pactogrid")) * 1000.0, 1)
                p_load_w = round(safe_float(sys_status.get("pLocalLoad")) * 1000.0, 1)
                
                soc_raw = sys_status.get("SOC") or dev.get("capacity")
                soc = safe_float(soc_raw) if soc_raw is not None else None
                v_bat = safe_float(sys_status.get("vBat"))

                inv_status = str(sys_status.get("status") or dev.get("deviceStatus") or "").strip()
                status_desc = str(sys_status.get("lost") or "").strip()
                fault_code = sys_status.get("proPto") or dev.get("proPto") or 0

                e_today = safe_float(totals.get("epvToday") or plant.get("todayEnergy") or dev.get("eToday"))
                e_to_grid = safe_float(totals.get("etoGridToday"))
                e_to_user = safe_float(totals.get("elocalLoadToday"))
                e_charge = safe_float(totals.get("echargetoday") or dev.get("eChargeToday"))
                e_discharge = safe_float(totals.get("edischarge1Today"))

                if e_to_user == 0.0 and e_today > 0 and e_today >= e_to_grid:
                    e_to_user = round(e_today - e_to_grid, 2)

                has_telemetry = bool(sys_status and (solar_w > 0 or p_load_w > 0 or p_grid_w > 0 or inv_status in ["1", "5", "normal"]))
                is_offline = lost_from_list and not has_telemetry

                if sn not in state:
                    state[sn] = {}
                
                if soc is not None and soc > 0:
                    state[sn]["last_known_soc"] = soc

                live_item = {
                    "sn": sn,
                    "label": display_name,
                    "solar_w": solar_w,
                    "p_grid_w": p_grid_w,
                    "p_load_w": p_load_w,
                    "soc": soc,
                    "v_bat": v_bat,
                    "e_today": e_today,
                    "e_to_grid": e_to_grid,
                    "e_to_user": e_to_user,
                    "e_charge": e_charge,
                    "e_discharge": e_discharge,
                    "is_offline": is_offline,
                    "last_known_soc": state[sn].get("last_known_soc")
                }
                live_data.append(live_item)
                daily_stats.append(live_item)

                print(f"[+] {display_name}:")
                print(f"    - Solare: {solar_w} W (P1: {round(p_pv1_kw*1000)}W, P2: {round(p_pv2_kw*1000)}W)")
                print(f"    - Rete: {p_grid_w} W | Casa: {p_load_w} W | Batteria: {soc}% ({v_bat}V)")
                print(f"    - Energia Oggi: {e_today} kWh (Rete: {e_to_grid} kWh, Casa: {e_to_user} kWh)")
                print(f"    - Stato: '{inv_status}' ({status_desc}) | Guasto: {fault_code} | Lost: {lost_from_list}")

                inverter_state = state.get(sn, {})
                was_in_error = inverter_state.get("in_error", False)
                zero_count = inverter_state.get("zero_count", 0)
                now_ts = time.time()

                current_error_type = None
                current_error_title = ""
                current_error_msg = ""

                if is_offline:
                    current_error_type = "OFFLINE"
                    current_error_title = "🚨 <b>ALLARME: INVERTER OFFLINE</b>"
                    current_error_msg = "L'inverter non comunica con la rete o la sezione Storage è spenta."
                    zero_count = 0
                elif fault_code not in [0, "0", None, "", "00"]:
                    fault_name, fault_detail = get_fault_description(fault_code)
                    current_error_type = f"ERRORE {fault_code}"
                    current_error_title = f"⚠️ <b>ALLARME GUASTO: {fault_name} (Codice {fault_code})</b>"
                    current_error_msg = f"🛑 <b>Dettaglio Errore:</b>\n{fault_detail}"
                    zero_count = 0
                elif is_daytime and solar_w == 0 and inv_status not in ["1", "5", "Normal", "normal"]:
                    zero_count += 1
                    if zero_count >= 3:
                        current_error_type = "PRODUZIONE_ZERO"
                        current_error_title = "⚠️ <b>AVVISO: PRODUZIONE SOLARE A ZERO</b>"
                        current_error_msg = f"Produzione solare ferma a 0 W in pieno giorno per oltre {zero_count * 10} minuti consecutivi."
                else:
                    zero_count = 0

                if current_error_type:
                    if not was_in_error:
                        state[sn].update({
                            "in_error": True,
                            "error_type": current_error_type,
                            "start_ts": now_ts,
                            "display_name": display_name,
                            "zero_count": zero_count
                        })
                        start_time_str = get_italian_time_str(now_ts)
                        send_telegram(
                            f"{current_error_title}\n\n"
                            f"📍 <b>{display_name}</b>\n"
                            f"🔢 Seriale: <code>{sn}</code>\n\n"
                            f"{current_error_msg}\n\n"
                            f"⚡ Potenza Attuale: <b>{solar_w} W</b>\n"
                            f"🕒 Inizio anomalia: <b>{start_time_str}</b>"
                        )
                    else:
                        state[sn]["zero_count"] = zero_count
                        dur_str = format_duration(now_ts - inverter_state.get("start_ts", now_ts))
                        print(f"[-] {display_name} ancora in anomalia ({current_error_type}) da {dur_str}.")
                else:
                    if was_in_error:
                        start_ts = inverter_state.get("start_ts", now_ts)
                        duration_str = format_duration(now_ts - start_ts)
                        start_time_str = get_italian_time_str(start_ts)
                        end_time_str = get_italian_time_str(now_ts)
                        prev_err = inverter_state.get("error_type", "OFFLINE/ANOMALIA")

                        battery_str = f"\n🔋 Batteria: <b>{soc:.0f}%</b>" if soc is not None and soc > 0 else ""

                        print(f"[+] Invio ripristino per {display_name} (durata: {duration_str})")
                        send_telegram(
                            f"✅ <b>RIPRISTINO: INVERTER TORNATO OPERATIVO</b>\n\n"
                            f"📍 <b>{display_name}</b>\n"
                            f"🔢 Seriale: <code>{sn}</code>\n"
                            f"🟢 Lo stato di <b>{prev_err}</b> è rientrato!\n"
                            f"⏱️ <b>Durata anomalia:</b> {duration_str} (dalle {start_time_str} alle {end_time_str})\n"
                            f"⚡ Potenza Solare Attuale: <b>{solar_w} W</b>"
                            f"{battery_str}"
                        )

                        state[sn].update({
                            "in_error": False,
                            "last_resolved": now_ts,
                            "zero_count": 0
                        })
                    else:
                        state[sn]["zero_count"] = zero_count
                        state[sn]["in_error"] = False

        except Exception as e:
            print(f"[-] Errore lettura {plant_name}: {e}")
            traceback.print_exc()

def format_monthly_report(now_rome: datetime, month_key: str, month_data: dict) -> str:
    month_num = int(month_key.split("-")[1])
    year_num = int(month_key.split("-")[0])
    month_name = MONTH_NAMES_IT.get(month_num, month_key)

    tot_prod = 0.0
    tot_grid = 0.0
    tot_user = 0.0

    msg = f"🏆 <b>REPORT MENSILE FOTOVOLTAICO - {month_name.upper()} {year_num}</b>\n"
    msg += f"📅 <i>Riepilogo energetico ed economico</i>\n\n"

    for sn, d in month_data.items():
        label = d.get("label", sn)
        prod = d.get("production", 0.0)
        grid = d.get("to_grid", 0.0)
        user = d.get("to_user", 0.0)
        charge = d.get("charge", 0.0)
        num_days = len(d.get("days", [])) or 1

        avg_daily = prod / num_days if num_days > 0 else 0.0
        pct_user = round((user / prod * 100), 1) if prod > 0 else 0
        pct_grid = round((grid / prod * 100), 1) if prod > 0 else 0

        val_saved = user * PRICE_KWH_SAVED
        val_grid = grid * PRICE_KWH_GRID
        val_tot = val_saved + val_grid

        tot_prod += prod
        tot_grid += grid
        tot_user += user

        msg += f"📍 <b>{label}</b>\n"
        msg += f"☀️ Produzione Mese: <b>{prod:.2f} kWh</b> (Media: {avg_daily:.1f} kWh/gg)\n"
        msg += f"🏠 Autoconsumo: <b>{user:.2f} kWh</b> ({pct_user}%)\n"
        msg += f"🔌 Immessa in Rete: <b>{grid:.2f} kWh</b> ({pct_grid}%)\n"
        if charge > 0:
            msg += f"🔋 Batteria: <b>{charge:.1f} kWh</b> accumulati\n"
        msg += f"💰 Valore Economico: <b>~{val_tot:.2f} €</b> (Risparmio: {val_saved:.2f}€ | GSE: {val_grid:.2f}€)\n\n"

    if len(month_data) > 1:
        tot_pct_user = round((tot_user / tot_prod * 100), 1) if tot_prod > 0 else 0
        tot_pct_grid = round((tot_grid / tot_prod * 100), 1) if tot_prod > 0 else 0
        tot_val_saved = tot_user * PRICE_KWH_SAVED
        tot_val_grid = tot_grid * PRICE_KWH_GRID
        tot_val = tot_val_saved + tot_val_grid

        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "🌟 <b>TOTALE MENSILE COMPLESSIVO</b>\n"
        msg += f"☀️ Produzione Totale: <b>{tot_prod:.2f} kWh</b>\n"
        msg += f"🏠 Autoconsumo Totale: <b>{tot_user:.2f} kWh</b> ({tot_pct_user}%)\n"
        msg += f"🔌 Immissione Totale: <b>{tot_grid:.2f} kWh</b> ({tot_pct_grid}%)\n"
        msg += f"💶 <b>VALORE TOTALE GENERATO: ~{tot_val:.2f} €</b>\n"
        msg += f"   <i>(Risparmio in bolletta: ~{tot_val_saved:.2f} € | Rimborso GSE: ~{tot_val_grid:.2f} €)</i>\n"

    return msg

def handle_reports(now_rome: datetime, daily_stats: list, state: dict):
    today_str = now_rome.strftime("%Y-%m-%d")
    month_key = now_rome.strftime("%Y-%m")
    last_daily_date = state.get("last_daily_report_date")
    last_monthly_date = state.get("last_monthly_report_date")

    if "monthly" not in state:
        state["monthly"] = {}
    if month_key not in state["monthly"]:
        state["monthly"][month_key] = {}

    # 1. REPORT GIORNALIERO (alle 21:00 o successive)
    if now_rome.hour >= 21 and last_daily_date != today_str and daily_stats:
        print("[*] Generazione del Report Giornaliero...")
        
        for item in daily_stats:
            sn = item["sn"]
            if sn not in state["monthly"][month_key]:
                state["monthly"][month_key][sn] = {
                    "label": item["label"],
                    "production": 0.0,
                    "to_grid": 0.0,
                    "to_user": 0.0,
                    "charge": 0.0,
                    "discharge": 0.0,
                    "days": []
                }
            m_entry = state["monthly"][month_key][sn]
            m_entry["label"] = item["label"]
            if today_str not in m_entry["days"]:
                m_entry["days"].append(today_str)
                m_entry["production"] = round(m_entry["production"] + item["e_today"], 2)
                m_entry["to_grid"] = round(m_entry["to_grid"] + item["e_to_grid"], 2)
                m_entry["to_user"] = round(m_entry["to_user"] + item["e_to_user"], 2)
                m_entry["charge"] = round(m_entry["charge"] + item.get("e_charge", 0.0), 2)
                m_entry["discharge"] = round(m_entry["discharge"] + item.get("e_discharge", 0.0), 2)

        tot_production = sum(item["e_today"] for item in daily_stats)
        tot_grid = sum(item["e_to_grid"] for item in daily_stats)
        tot_user = sum(item["e_to_user"] for item in daily_stats)

        tot_val_saved = tot_user * PRICE_KWH_SAVED
        tot_val_grid = tot_grid * PRICE_KWH_GRID
        tot_val_day = tot_val_saved + tot_val_grid

        date_formatted = now_rome.strftime("%d/%m/%Y")
        msg = f"📊 <b>REPORT GIORNALIERO FOTOVOLTAICO</b>\n📅 <i>{date_formatted}</i>\n\n"

        for item in daily_stats:
            prod = item["e_today"]
            grid = item["e_to_grid"]
            user = item["e_to_user"]
            
            pct_user = round((user / prod * 100), 1) if prod > 0 else 0
            pct_grid = round((grid / prod * 100), 1) if prod > 0 else 0

            msg += f"📍 <b>{item['label']}</b>\n"
            msg += f"☀️ Produzione: <b>{prod:.2f} kWh</b>\n"
            msg += f"🏠 Autoconsumo: <b>{user:.2f} kWh</b> ({pct_user}%)\n"
            msg += f"🔌 Immessa in Rete: <b>{grid:.2f} kWh</b> ({pct_grid}%)\n"

            soc_val = item.get("soc")
            is_offline = item.get("is_offline", False)
            last_soc = item.get("last_known_soc")

            if is_offline:
                if last_soc is not None:
                    msg += f"🔋 Batteria: <b>{last_soc:.0f}%</b> <i>(ultimo valore noto - Offline)</i>\n"
                else:
                    msg += f"🔋 Batteria: <i>(Disconnesso)</i>\n"
            elif soc_val is not None and soc_val > 0:
                msg += f"🔋 Batteria: <b>{soc_val:.0f}%</b>"
                if item.get("e_charge", 0) > 0 or item.get("e_discharge", 0) > 0:
                    msg += f" (Carica: {item['e_charge']:.1f} kWh | Scarica: {item['e_discharge']:.1f} kWh)"
                msg += "\n"
            elif last_soc is not None:
                msg += f"🔋 Batteria: <b>{last_soc:.0f}%</b> <i>(ultimo valore noto)</i>\n"

            msg += "\n"

        if len(daily_stats) > 1:
            tot_pct_user = round((tot_user / tot_production * 100), 1) if tot_production > 0 else 0
            tot_pct_grid = round((tot_grid / tot_production * 100), 1) if tot_production > 0 else 0
            
            msg += "━━━━━━━━━━━━━━━━━━━━\n"
            msg += "🌟 <b>TOTALE COMPLESSIVO</b>\n"
            msg += f"☀️ Produzione Totale: <b>{tot_production:.2f} kWh</b>\n"
            msg += f"🏠 Autoconsumo Totale: <b>{tot_user:.2f} kWh</b> ({tot_pct_user}%)\n"
            msg += f"🔌 Immissione Totale: <b>{tot_grid:.2f} kWh</b> ({tot_pct_grid}%)\n"
            msg += f"💶 <b>Valore Economico Oggi: ~{tot_val_day:.2f} €</b>\n"
            msg += f"   <i>(Risparmio bolletta: ~{tot_val_saved:.2f} € | Rimborso GSE: ~{tot_val_grid:.2f} €)</i>\n\n"

        forecast_str = get_solar_forecast()
        if forecast_str:
            msg += f"🌤️ <b>Previsione Solare Domani:</b>\n{forecast_str}\n"

        send_telegram(msg)
        state["last_daily_report_date"] = today_str
        print("[+] Report Giornaliero inviato!")

        # 2. REPORT MENSILE (Inviato l'ultimo giorno del mese alle 21:00)
        last_day_of_month = calendar.monthrange(now_rome.year, now_rome.month)[1]
        if now_rome.day == last_day_of_month and last_monthly_date != month_key:
            print("[*] Generazione del Report Mensile...")
            monthly_msg = format_monthly_report(now_rome, month_key, state["monthly"][month_key])
            send_telegram(monthly_msg)
            state["last_monthly_report_date"] = month_key
            print("[+] Report Mensile inviato con successo!")

def handle_telegram_commands(live_data: list, state: dict):
    """Gestisce i comandi interattivi inviati dall'utente al Bot Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    last_update_id = state.get("last_telegram_update_id", 0)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=3"

    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return
        updates = res.json().get("result", [])
        if not updates:
            return

        for update in updates:
            up_id = update.get("update_id", 0)
            if up_id > last_update_id:
                state["last_telegram_update_id"] = up_id

            msg_obj = update.get("message", {})
            chat_id = str(msg_obj.get("chat", {}).get("id", ""))
            text = str(msg_obj.get("text", "")).strip().lower()

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            print(f"[+] Ricevuto comando Telegram: '{text}' da chat {chat_id}")

            if text.startswith("/status") or text.startswith("/ora") or text.startswith("/live"):
                tot_w = sum(x.get("solar_w", 0) for x in live_data)
                tot_grid = sum(x.get("p_grid_w", 0) for x in live_data)
                tot_load = sum(x.get("p_load_w", 0) for x in live_data)
                
                resp = f"⚡ <b>STATO IN TEMPO REALE</b> ({get_italian_time_str()})\n\n"
                for item in live_data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    if item.get("is_offline"):
                        resp += "🔴 <i>Inverter OFFLINE</i>\n\n"
                    else:
                        resp += f"☀️ Solare: <b>{item['solar_w']} W</b>\n"
                        resp += f"🏠 Casa: <b>{item['p_load_w']} W</b> | 🔌 Rete: <b>{item['p_grid_w']} W</b>\n"
                        soc = item.get("soc")
                        if soc is not None:
                            resp += f"🔋 Batteria: <b>{soc:.0f}%</b> ({item.get('v_bat')}V)\n"
                        resp += "\n"

                if len(live_data) > 1:
                    resp += "━━━━━━━━━━━━━━━━━━━━\n"
                    resp += f"☀️ <b>Produzione Totale: {tot_w:.0f} W</b>\n"
                    resp += f"🏠 Consumo Casa: {tot_load:.0f} W | 🔌 Immessa: {tot_grid:.0f} W\n"
                send_telegram(resp, chat_id)

            elif text.startswith("/batteria") or text.startswith("/batt"):
                resp = f"🔋 <b>STATO BATTERIE</b> ({get_italian_time_str()})\n\n"
                for item in live_data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    soc = item.get("soc")
                    last_soc = item.get("last_known_soc")
                    if soc is not None and not item.get("is_offline"):
                        resp += f"🔋 Carica Attuale: <b>{soc:.0f}%</b>\n"
                        resp += f"⚡ Tensione: <b>{item.get('v_bat')} V</b>\n"
                        resp += f"📥 Caricata oggi: <b>{item.get('e_charge', 0):.1f} kWh</b>\n"
                        resp += f"📤 Scaricata oggi: <b>{item.get('e_discharge', 0):.1f} kWh</b>\n\n"
                    elif last_soc is not None:
                        resp += f"🔋 Carica: <b>{last_soc:.0f}%</b> <i>(ultimo valore noto - Offline)</i>\n\n"
                    else:
                        resp += "🔴 <i>Dato non disponibile (Offline)</i>\n\n"
                send_telegram(resp, chat_id)

            elif text.startswith("/oggi") or text.startswith("/report"):
                tot_prod = sum(x.get("e_today", 0) for x in live_data)
                tot_grid = sum(x.get("e_to_grid", 0) for x in live_data)
                tot_user = sum(x.get("e_to_user", 0) for x in live_data)
                val_day = (tot_user * PRICE_KWH_SAVED) + (tot_grid * PRICE_KWH_GRID)

                resp = f"📊 <b>REPORT PARZIALE DI OGGI</b> ({get_italian_time_str()})\n\n"
                for item in live_data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    resp += f"☀️ Prodotto: <b>{item.get('e_today', 0):.2f} kWh</b>\n"
                    resp += f"🏠 Autoconsumo: <b>{item.get('e_to_user', 0):.2f} kWh</b>\n"
                    resp += f"🔌 In Rete: <b>{item.get('e_to_grid', 0):.2f} kWh</b>\n\n"

                if len(live_data) > 1:
                    resp += "━━━━━━━━━━━━━━━━━━━━\n"
                    resp += f"☀️ <b>Totale Prodotto: {tot_prod:.2f} kWh</b>\n"
                    resp += f"🏠 Autoconsumo: {tot_user:.2f} kWh | 🔌 In Rete: {tot_grid:.2f} kWh\n"
                    resp += f"💶 <b>Valore generato finora: ~{val_day:.2f} €</b>\n"
                send_telegram(resp, chat_id)

            elif text.startswith("/help") or text.startswith("/start"):
                help_msg = (
                    "🤖 <b>COMANDI DISPONIBILI DEL BOT</b>\n\n"
                    "⚡ <b>/status</b> - Potenza in tempo reale, consumi e rete\n"
                    "📊 <b>/oggi</b> - Produzione parziale e valore economico odierno\n"
                    "🔋 <b>/batteria</b> - Percentuale e salute accumulatori\n"
                    "ℹ️ <b>/help</b> - Mostra questo messaggio di aiuto"
                )
                send_telegram(help_msg, chat_id)

    except Exception as e:
        print(f"[-] Errore gestione comandi Telegram: {e}")

def main():
    now_rome = datetime.now(TZ_ROME)
    print(f"Avvio monitoraggio Growatt (Ora Italiana: {now_rome.strftime('%Y-%m-%d %H:%M:%S')})")
    is_daytime = 9 <= now_rome.hour < 18

    state = load_state()
    live_data = []
    daily_stats = []

    for acc in ACCOUNTS:
        check_account(acc, is_daytime, state, live_data, daily_stats)

    # 1. Controllo e invio Report Giornaliero e Mensile
    handle_reports(now_rome, daily_stats, state)

    # 2. Gestione eventuali comandi interattivi ricevuti su Telegram
    handle_telegram_commands(live_data, state)

    save_state(state)
    print("\n[+] Controllo completato con successo.")

if __name__ == "__main__":
    main()
