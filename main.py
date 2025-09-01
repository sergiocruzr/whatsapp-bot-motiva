from flask import Flask, request, jsonify, Response
import os, time, csv, re, requests, unicodedata
from io import StringIO
from xml.sax.saxutils import escape as xml_escape

app = Flask(__name__)

# ===== Config =====
BRAND_NAME = os.getenv("BRAND_NAME", "Motiva Educación")
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")  # whatsapp:+14155238886
ADMIN_FORWARD_NUMBER = os.getenv("ADMIN_FORWARD_NUMBER")      # whatsapp:+5917XXXXXXX

# ===== Cache =====
_cache = {"rows": [], "t": 0.0}
CACHE_SECONDS = 300

EXPECTED_HEADERS = [
    "Curso","Texto Principal","Link PDF","Fecha de Inicio","Fechas de clases","Duración","Horarios",
    "Inscripción Argentina","Inscripción Bolivia","Inscripción Chile","Inscripción Colombia",
    "Inscripción Costa Rica","Inscripción México","Inscripción Paraguay","Inscripción Perú",
    "Inscripción Uruguay","Inscripción Resto Países","FAQ",
]
HEADER_SYNONYMS = {
    "Valor Inscripción Uruguay": "Inscripción Uruguay",
}

COUNTRY_PRICE_COLUMN = {
    "506": "Inscripción Costa Rica",
    "598": "Inscripción Uruguay",
    "595": "Inscripción Paraguay",
    "591": "Inscripción Bolivia",
    "54":  "Inscripción Argentina",
    "56":  "Inscripción Chile",
    "57":  "Inscripción Colombia",
    "52":  "Inscripción México",
    "51":  "Inscripción Perú",
}

# ===== Utils =====
def _fold(s: str) -> str:
    s = (s or "").lower()
    nfkd = unicodedata.normalize('NFD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

def build_twiml(message: str) -> Response:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?><Response><Message>{xml_escape(message)}</Message></Response>"""
    return Response(xml, mimetype="application/xml")

# ===== Sheet =====
def fetch_sheet_rows(force: bool = False):
    now = time.time()
    if not force and _cache["rows"] and now - _cache["t"] < CACHE_SECONDS:
        return _cache["rows"]
    if not SHEET_CSV_URL:
        _cache.update({"rows": [], "t": now})
        return []
    resp = requests.get(SHEET_CSV_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    reader = csv.DictReader(StringIO(resp.text))

    raw_headers = reader.fieldnames or []
    headers = [(h or "").strip().lstrip('﻿') for h in raw_headers]
    headers = [HEADER_SYNONYMS.get(h, h) for h in headers]
    if headers != EXPECTED_HEADERS:
        raise ValueError(f"Encabezados no coinciden. Esperado: {EXPECTED_HEADERS}. Recibido: {raw_headers}")

    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            nk = (k or "").strip().lstrip('﻿')
            nk = HEADER_SYNONYMS.get(nk, nk)
            clean[nk] = (v or "").strip()
        if clean.get("Curso"):
            rows.append(clean)
    _cache.update({"rows": rows, "t": now})
    return rows

# ===== Logic =====
def list_courses(rows):
    return [r.get("Curso", "").strip() for r in rows if r.get("Curso")]

def find_course(rows, text: str):
    t = _fold(text)
    for r in rows:
        name = (r.get("Curso") or "").strip()
        if name and (t in _fold(name) or _fold(name) in t):
            return r
    words = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]+", text) if len(w) >= 3]
    words_fold = set(_fold(w) for w in words)
    best_score, best_row = 0, None
    for r in rows:
        name = (r.get("Curso") or "")
        name_tokens = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]+", name) if len(w) >= 3]
        name_fold_set = set(_fold(w) for w in name_tokens)
        score = len(words_fold & name_fold_set)
        if score > best_score:
            best_score, best_row = score, r
    if best_score >= 1:
        return best_row
    return None

def guess_country_price_column(from_number: str) -> str:
    num = (from_number or "").replace("whatsapp:", "").replace("+", "")
    for p in sorted(COUNTRY_PRICE_COLUMN.keys(), key=lambda p: -len(p)):
        if num.startswith(p):
            return COUNTRY_PRICE_COLUMN[p]
    return "Inscripción Resto Países"

def first_response_for_course(row, from_number: str) -> str:
    titulo = row.get("Curso", "")
    texto = row.get("Texto Principal", "")
    pdf = row.get("Link PDF", "")
    fecha_inicio = row.get("Fecha de Inicio", "")
    duracion = row.get("Duración", "")
    horarios = row.get("Horarios", "")

    price_col = guess_country_price_column(from_number)
    precio = row.get(price_col, "")

    partes = []
    if titulo:
        partes.append(f"*{titulo}* — {BRAND_NAME}")
    if texto:
        partes.append(texto)
    if fecha_inicio:
        partes.append(f"📅 *Inicio:* {fecha_inicio}")
    if duracion:
        partes.append(f"⏳ *Duración:* {duracion}")
    if horarios:
        partes.append(f"🕒 *Horarios:* {horarios}")
    if precio:
        partes.append(f"💳 *Inscripción ({price_col.replace('Inscripción ', '')}):* {precio}")
    if pdf:
        partes.append(f"📄 *PDF informativo:* {pdf}")
    partes.append("Si deseas *inscribirte*, responde: *me interesa* o *quiero inscribirme* y te derivo con un coordinador humano.")
    return '

'.join(partes)

def answer_faq(row, body_lower: str):
    out = []
    if any(k in body_lower for k in ["precio", "costo", "vale", "valor", "cuanto", "cuánto", "inscrip"]):
        out.append("💳 *Inscripción:* indícame tu país para darte el precio exacto, o dime 'precio [país]'.")
    if any(k in body_lower for k in ["horario", "horarios", "hora", "clase", "clases"]):
        if row.get("Horarios"):
            out.append(f"🕒 *Horarios:* {row['Horarios']}")
    if any(k in body_lower for k in ["modalidad", "metodolog", "online", "virtual", "en vivo", "zoom", "meet"]):
        out.append("🎥 Modalidad *en vivo* por videoconferencia (clases síncronas).")
    faq = (row.get("FAQ") or "").strip()
    if faq:
        out.append(f"ℹ️ *FAQ:* {faq}")
    return '

'.join(out) if out else None

def detect_intent_enroll(body_lower: str) -> bool:
    keys = ["me interesa", "quiero inscribirme", "inscribirme", "como me inscribo", "inscripción", "inscribirme ya"]
    return any(k in body_lower for k in keys)

def send_admin_forward(user_from: str, user_body: str, course_name: str = None) -> bool:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER and ADMIN_FORWARD_NUMBER):
        return False
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        parts = [f"Nuevo lead para {BRAND_NAME}", f"Desde: {user_from}", f"Mensaje: {user_body}"]
        if course_name:
            parts.append(f"Curso: {course_name}")
        body = '
'.join(parts)
        data = {"From": TWILIO_WHATSAPP_NUMBER, "To": ADMIN_FORWARD_NUMBER, "Body": body}
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print("[ERROR send_admin_forward]", e)
        return False

# ===== Routes =====
@app.get("/health")
def health():
    return jsonify(ok=True, brand=BRAND_NAME, cached_rows=len(_cache["rows"]), cache_age_s=int(time.time()-_cache["t"]))

@app.route("/sheet_refresh", methods=["GET","POST"])
def sheet_refresh():
    fetch_sheet_rows(force=True)
    return jsonify(ok=True, refreshed=True, count=len(_cache["rows"]))

@app.get("/sheet_preview")
def sheet_preview():
    try:
        rows = fetch_sheet_rows()
        return jsonify(count=len(rows), cursos=list_courses(rows))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.post("/whatsapp")
def whatsapp_webhook():
    try:
        from_number = request.form.get("From", "")
        body = (request.form.get("Body", "") or "").strip()
        print("[INBOUND] From=", from_number, "Body=", body)

        rows = fetch_sheet_rows()
        if not body:
            cursos = list_courses(rows)
            if cursos:
                msg = 'Hola 👋 Soy el asistente de {}. ¿Sobre qué curso deseas info?

*Cursos:*
- '.format(BRAND_NAME) + '
- '.join(cursos)
            else:
                msg = "Hola 👋 Soy el asistente de {}. No encuentro cursos publicados aún.".format(BRAND_NAME)
            return build_twiml(msg)

        body_lower = _fold(body)

        if detect_intent_enroll(body_lower):
            send_admin_forward(from_number, body)
            return build_twiml("¡Excelente! 🙌 Te conecto con un coordinador humano para continuar con tu inscripción.")

        row = find_course(rows, body)
        if row:
            faq_ans = answer_faq(row, body_lower)
            if faq_ans:
                return build_twiml(faq_ans)
            resp = first_response_for_course(row, from_number)
            return build_twiml(resp)

        cursos = list_courses(rows)
        if cursos:
            return build_twiml('Para ayudarte mejor, dime el *nombre del curso*.

*Cursos:*
- ' + '
- '.join(cursos))
        return build_twiml("Por ahora no encuentro cursos publicados en {}.".format(BRAND_NAME))

    except Exception as e:
        print("[ERROR /whatsapp]", e)
        return build_twiml("Ocurrió un detalle al procesar tu mensaje. Intenta nuevamente en un momento, por favor.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
