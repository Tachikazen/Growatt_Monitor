import os
import json
import requests
import growattServer
from http.server import BaseHTTPRequestHandler
from datetime import datetime
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ACCOUNTS = [
    {
        "label": "Impianto 1",
        "user": os.getenv("GROWATT_USER_1", "KWK1CKJ4NT"),
        "pass": os.getenv("GROWATT_PASS_1", "CKJ4NT")
    },
    {
        "label": "Impianto 2",
        "user": os.getenv("GROWATT_USER_2", "KWK1CKJ4NW"),
        "pass": os.getenv("GROWATT_PASS_2", "CKJ4NW")
    }
]

PRICE_KWH_SAVED = 0.25
PRICE_KWH_GRID = 0.10
TZ_ROME = ZoneInfo("Europe/Rome")

CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://server.growatt.com",
    "Referer": "https://server.growatt.com/login",
    "X-Requested-With": "XMLHttpRequest"
}

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

def safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        return float(str(val).replace("W", "").replace("kW", "").replace("%", "").strip())
    except Exception:
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

def get_live_data():
    results = []
    for account_info in ACCOUNTS:
        label = account_info["label"]
        user = account_info["user"]
        pwd = account_info["pass"]

        if not user or not pwd:
            continue

        api = growattServer.GrowattApi()
        api.server_url = "https://server.growatt.com/"
        api.session.headers.update(CUSTOM_HEADERS)

        try:
            login_res = api.login(user, pwd)
            user_id = None
            if isinstance(login_res, dict):
                user_data = login_res.get("user")
                if isinstance(user_data, dict):
                    user_id = user_data.get("id")
                user_id = user_id or login_res.get("userId") or login_res.get("uid")

            if not user_id:
                continue

            plants_res = api.plant_list(user_id)
            plants = plants_res.get("data", []) if isinstance(plants_res, dict) else plants_res
            if not isinstance(plants, list):
                plants = [plants]

            for plant in plants:
                plant_id = str(plant.get("plantId") or plant.get("id"))
                plant_name = plant.get("plantName", "Impianto")

                devs = api.device_list(plant_id)
                devices = devs.get("data", []) if isinstance(devs, dict) else devs
                if not isinstance(devices, list):
                    devices = [devices]

                for dev in devices:
                    if not isinstance(dev, dict):
                        continue
                    sn = dev.get("deviceSn") or dev.get("sn")
                    alias = dev.get("deviceAilas") or dev.get("deviceAlias") or sn
                    display_name = f"{label} ({plant_name}) - {alias}"

                    is_lost_dev = dev.get("lost")
                    lost_from_list = bool(is_lost_dev is True or str(is_lost_dev).lower() == "true")

                    sys_status = {}
                    totals = {}
                    try:
                        sys_status = api.mix_system_status(sn, plant_id)
                    except Exception:
                        pass
                    try:
                        totals = api.mix_totals(sn, plant_id)
                    except Exception:
                        pass

                    ppv_kw = safe_float(sys_status.get("ppv") or sys_status.get("storagePpv"))
                    p_pv1_kw = safe_float(sys_status.get("pPv1"))
                    p_pv2_kw = safe_float(sys_status.get("pPv2"))
                    solar_w = round((ppv_kw if ppv_kw > 0 else (p_pv1_kw + p_pv2_kw)) * 1000.0, 1)

                    p_grid_w = round(safe_float(sys_status.get("pactogrid")) * 1000.0, 1)
                    p_load_w = round(safe_float(sys_status.get("pLocalLoad")) * 1000.0, 1)
                    soc = safe_float(sys_status.get("SOC") or dev.get("capacity"))
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

                    results.append({
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
                        "fault_code": fault_code,
                        "inv_status": inv_status,
                        "is_offline": is_offline
                    })
        except Exception as e:
            print(f"Error {label}: {e}")

    return results

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            update = json.loads(post_data.decode('utf-8'))
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = str(msg.get("text", "")).strip().lower()

            # Autorizza solo la tua chat
            if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                self.send_response(200)
                self.end_headers()
                return

            now_str = datetime.now(TZ_ROME).strftime("%H:%M")

            if text.startswith("/status") or text.startswith("/ora") or text.startswith("/live"):
                data = get_live_data()
                tot_w = sum(x.get("solar_w", 0) for x in data)
                tot_grid = sum(x.get("p_grid_w", 0) for x in data)
                tot_load = sum(x.get("p_load_w", 0) for x in data)

                resp = f"⚡ <b>STATO IN TEMPO REALE</b> ({now_str})\n\n"
                for item in data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    fault_code = item.get("fault_code", 0)
                    if item.get("is_offline"):
                        resp += "🔴 <b>Stato:</b> <i>OFFLINE (Disconnesso)</i>\n"
                    elif fault_code not in [0, "0", None, "", "00"]:
                        fault_name, fault_detail = get_fault_description(fault_code)
                        resp += f"⚠️ <b>ALLARME GUASTO: {fault_name} (Codice {fault_code})</b>\n"
                        resp += f"   🛑 <i>{fault_detail}</i>\n"
                    else:
                        resp += "🟢 <b>Stato:</b> <i>Operativo</i>\n"

                    if not item.get("is_offline"):
                        resp += f"☀️ Solare: <b>{item['solar_w']} W</b>\n"
                        resp += f"🏠 Casa: <b>{item['p_load_w']} W</b> | 🔌 Rete: <b>{item['p_grid_w']} W</b>\n"
                        soc = item.get("soc")
                        if soc is not None:
                            resp += f"🔋 Batteria: <b>{soc:.0f}%</b> ({item['v_bat']}V)\n"
                    resp += "\n"

                if len(data) > 1:
                    resp += "━━━━━━━━━━━━━━━━━━━━\n"
                    resp += f"☀️ <b>Produzione Totale: {tot_w:.0f} W</b>\n"
                    resp += f"🏠 Consumo Casa: {tot_load:.0f} W | 🔌 Immessa: {tot_grid:.0f} W\n"

                self.reply_telegram(chat_id, resp)

            elif text.startswith("/batteria") or text.startswith("/batt"):
                data = get_live_data()
                resp = f"🔋 <b>STATO BATTERIE</b> ({now_str})\n\n"
                for item in data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    resp += f"🔋 Carica Attuale: <b>{item['soc']:.0f}%</b>\n"
                    resp += f"⚡ Tensione: <b>{item['v_bat']} V</b>\n"
                    resp += f"📥 Caricata oggi: <b>{item['e_charge']:.1f} kWh</b>\n"
                    resp += f"📤 Scaricata oggi: <b>{item['e_discharge']:.1f} kWh</b>\n\n"
                self.reply_telegram(chat_id, resp)

            elif text.startswith("/oggi") or text.startswith("/report"):
                data = get_live_data()
                tot_prod = sum(x.get("e_today", 0) for x in data)
                tot_grid = sum(x.get("e_to_grid", 0) for x in data)
                tot_user = sum(x.get("e_to_user", 0) for x in data)
                val_day = (tot_user * PRICE_KWH_SAVED) + (tot_grid * PRICE_KWH_GRID)

                resp = f"📊 <b>REPORT PARZIALE DI OGGI</b> ({now_str})\n\n"
                for item in data:
                    resp += f"📍 <b>{item['label']}</b>\n"
                    resp += f"☀️ Prodotto: <b>{item['e_today']:.2f} kWh</b>\n"
                    resp += f"🏠 Autoconsumo: <b>{item['e_to_user']:.2f} kWh</b>\n"
                    resp += f"🔌 In Rete: <b>{item['e_to_grid']:.2f} kWh</b>\n\n"

                if len(data) > 1:
                    resp += "━━━━━━━━━━━━━━━━━━━━\n"
                    resp += f"☀️ <b>Totale Prodotto: {tot_prod:.2f} kWh</b>\n"
                    resp += f"🏠 Autoconsumo: {tot_user:.2f} kWh | 🔌 In Rete: {tot_grid:.2f} kWh\n"
                    resp += f"💶 <b>Valore generato finora: ~{val_day:.2f} €</b>\n"
                self.reply_telegram(chat_id, resp)

            elif text.startswith("/help") or text.startswith("/start"):
                help_msg = (
                    "🤖 <b>COMANDI DISPONIBILI DEL BOT</b>\n\n"
                    "⚡ <b>/status</b> - Potenza in tempo reale, consumi e rete\n"
                    "📊 <b>/oggi</b> - Produzione parziale e valore economico odierno\n"
                    "🔋 <b>/batteria</b> - Percentuale e salute accumulatori\n"
                    "ℹ️ <b>/help</b> - Mostra questo messaggio di aiuto"
                )
                self.reply_telegram(chat_id, help_msg)

        except Exception as e:
            print(f"Error handling webhook: {e}")

        self.send_response(200)
        self.end_headers()

    def reply_telegram(self, chat_id, text):
        if not TELEGRAM_TOKEN or not chat_id:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram reply error: {e}")
