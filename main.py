import os
import csv
import time
import requests
from io import StringIO
from flask import Flask, request, jsonify, Response
from xml.sax.saxutils import escape as xml_escape

app = Flask(__name__)

# ====== CONFIG ======
BRAND_NAME = os.getenv("BRAND_NAME", "Motiva Educación")
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL")
# Si usas Sheet.best más adelante, podrías leer SHEET_BEST_URL

# Twilio (para notificar al coordinador humano)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")  # Ej: whatsapp:+14155238886
ADMIN_FORWARD_NUMBER = os.getenv("ADMIN_FORWARD_NUMBER")      # Ej: whatsapp:+5917XXXXXXX

# ====== CACHE DEL SHEET ======
_sheet_cache = {
    "rows": [],
    "fetched_at": 0.0,
}
CACHE_SECONDS = 300  # 5 minutos

# Mapeo de prefijos a columna de precios por país (E.164 sin el '+')
COUNTRY_PRICE_COLUMN = {
    "506": "Inscripción Costa Rica",  # CR
    "598": "Inscripción Uruguay",    # UY
    "595": "Inscripción Paraguay",   # PY
    "591": "Inscripción Bolivia",    # BO
    "54":  "Inscripción Argentina",  # AR
    "56":  "Inscripción Chile",      # CL
    "57":  "Inscripción Colombia",   # CO
    "52":  "Inscripción México",     # MX
    "51":  "Inscripción Perú",       # PE
}
# Nota: usamos el más largo que calce. Si no hay match, usamos "Inscripción Resto Países".

# Encabezados esperados EXACTOS en el CSV
EXPECTED_HEADERS = [
    "Curso",
    "Texto Principal",
    "Link PDF",
    "Fecha de Inicio",
    "Fechas de clases",
    "Duración",
    "Horarios",
    "Inscripción Argentina",
    "Inscripción Bolivia",
    "Inscripción Chile",
    "Inscripción Colombia",
    "Inscripción Costa Rica",
    "Inscripción México",
    "Inscripción Paraguay",
    "Inscripción Perú",
    "Inscripción Uruguay",
    "Inscripción Resto Países",
    "FAQ",
]

# Sinónimos permitidos en encabezados (por si la hoja tiene nombres alternativos)
HEADER_SYNONYMS = {
    "Valor Inscripción Uruguay": "Inscripción Uruguay",
}


def fetch_sheet_rows(force: bool = False):
    """Descarga el CSV y lo cachea como lista de diccionarios."""
    now = time.time()
    if not force and _sheet_cache["rows"] and now - _sheet_cache["fetched_at"] < CACHE_SECONDS:
        return _sheet_cache["rows"]

    if not SHEET_CSV_URL:
        _sheet_cache["rows"] = []
        _sheet_cache["fetched_at"] = now
        return []

    resp = requests.get(SHEET_CSV_URL, timeout=15)
    resp.raise_for_status()
    # Fuerza decodificación correcta en UTF-8 para tildes/acentos
    resp.encoding = "utf-8"

    content = resp.text
    csv_io = StringIO(content)
    reader = csv.DictReader(csv_io)

    # Validación de encabezados
    raw_headers = reader.fieldnames or []
    # Normaliza espacios, BOM y aplica sinónimos
    headers = [ (h or "").strip().lstrip('﻿') for h in raw_headers ]
    headers = [ HEADER_SYNONYMS.get(h, h) for h in headers ]
    if headers != EXPECTED_HEADERS:
        raise ValueError(
            f"Encabezados no coinciden. Esperado: {EXPECTED_HEADERS}. Recibido: {raw_headers}"
        )

    rows = []
    for row in reader:
        # Normaliza espacios
        clean = {}
        for k, v in row.items():
            nk = (k or "").strip().lstrip('﻿')
            nk = HEADER_SYNONYMS.get(nk, nk)
            clean[nk] = (v or "").strip()
        # Solo filas con nombre de curso
        if clean.get("Curso"):
            rows.append(clean)

    _sheet_cache["rows"] = rows
    _sheet_cache["fetched_at"] = now
    return rows


def list_courses(rows):
    return [r.get("Curso", "").strip() for r in rows if r.get("Curso")]


def find_course(rows, text: str):
    """Devuelve la fila del curso cuyo nombre aparezca dentro del texto (insensible a mayúsculas)."""
    t = text.lower()
    candidates = []
    for r in rows:
        name = (r.get("Curso") or "").strip()
        if not name:
            continue
        if name.lower() in t:
            candidates.append(r)
    if len(candidates) == 1:
        return candidates[0]
    # Si no hay match estricto, intenta por palabras clave (cualquier palabra de 4+ letras)
    words = [w for w in t.replace("\n", " ").split(" ") if len(w) >= 4]
    score = []
    for r in rows:
        name = (r.get("Curso") or "").lower()
        s = sum(1 for w in words if w in name)
        if s:
            score.append((s, r))
    if score:
        score.sort(key=lambda x: x[0], reverse=True)
        top_s, top_r = score[0]
        # Acepta si el score es razonable
        if top_s >= 1:
            return top_r
    return None


def guess_country_price_column(from_number: str) -> str:
    """Determina la columna de precio según el prefijo del número E.164 (sin 'whatsapp:')."""
    # from_number viene como 'whatsapp:+5917xxxxxxx'
    num = from_number.replace("whatsapp:", "").replace("+", "")
    # probar prefijos del más largo al más corto
    prefijos = sorted(COUNTRY_PRICE_COLUMN.keys(), key=lambda p: -len(p))
    for p in prefijos:
        if num.startswith(p):
            return COUNTRY_PRICE_COLUMN[p]
    return "Inscripción Resto Países"


def build_twiml(message: str) -> Response:
    xml = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{xml_escape(message)}</Message></Response>"
    return Response(xml, mimetype="application/xml")


def send_admin_forward(user_from: str, user_body: str, course_name: str = None):
    """Envía un WhatsApp al coordinador con los datos del lead usando la API REST de Twilio."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER and ADMIN_FORWARD_NUMBER):
        return False
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        parts = [
            f"Nuevo lead para {BRAND_NAME}",
            f"Desde: {user_from}",
            f"Mensaje: {user_body}",
        ]
        if course_name:
            parts.append(f"Curso detectado: {course_name}")
        body = "\n".join(parts)
        data = {
            "From": TWILIO_WHATSAPP_NUMBER,
            "To": ADMIN_FORWARD_NUMBER,
            "Body": body,
        }
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=15)
        resp.raise_for_status()
        return True
    except Exception:
        return False


def first_response_for_course(row, from_number):
    """Arma la primera respuesta con Texto Principal + PDF y datos clave."""
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

    partes.append("Si deseas *inscribirte*, responde: *me interesa* o *quiero inscribirme* y te derivaré con un coordinador humano.")
    return "\n\n".join(partes)


def answer_faq(row, body_lower):
    """Respuestas básicas a palabras clave usando columnas estándar."""
    out = []
    if any(k in body_lower for k in ["precio", "costo", "vale", "inscrip"]):
        out.append("💳 *Inscripción:* indícame tu país para darte el precio exacto, o dime 'precio [país]'.")
    if any(k in body_lower for k in ["horario", "hora"]):
        if row.get("Horarios"):
            out.append(f"🕒 *Horarios:* {row['Horarios']}")
    if any(k in body_lower for k in ["modalidad", "metodolog", "online", "virtual", "en vivo"]):
        out.append("🎥 Modalidad *en vivo* por videoconferencia (clases síncronas).")
    faq = row.get("FAQ", "").strip()
    if faq:
        out.append(f"ℹ️ *FAQ:* {faq}")
    return "\n\n".join(out) if out else None


def detect_intent_enroll(body_lower):
    keys = [
        "me interesa",
        "quiero inscribirme",
        "inscribirme",
        "quiero inscribir",
        "como me inscribo",
        "inscripción",
        "inscribirme ya",
    ]
    return any(k in body_lower for k in keys)


# ====== ROUTES ======
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "brand": BRAND_NAME,
        "cached_rows": len(_sheet_cache.get("rows", [])),
        "cache_age_s": int(time.time() - _sheet_cache.get("fetched_at", 0.0)),
    })


@app.get("/sheet_preview")
def sheet_preview():
    try:
        rows = fetch_sheet_rows()
        cursos = list_courses(rows)
        return jsonify({"count": len(cursos), "cursos": cursos})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/sheet_refresh", methods=["GET", "POST"])
def sheet_refresh():
    fetch_sheet_rows(force=True)
    return jsonify({"ok": True, "refreshed": True, "count": len(_sheet_cache["rows"])})


@app.post("/whatsapp")
def whatsapp_webhook():
    # Twilio envía application/x-www-form-urlencoded
    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    # Carga de cursos
    try:
        rows = fetch_sheet_rows()
    except Exception as e:
        msg = f"Lo siento, hubo un problema cargando la información del curso. Intenta más tarde. (detalle: {e})"
        return build_twiml(msg)

    if not body:
        # Mensaje vacío, lista cursos
        cursos = list_courses(rows)
        if cursos:
            msg = "Hola 👋 Soy el asistente de {brand}. ¿Sobre qué curso deseas info?\n\n*Cursos disponibles:*\n- ".format(brand=BRAND_NAME) + "\n- ".join(cursos)
        else:
            msg = f"Hola 👋 Soy el asistente de {BRAND_NAME}. No encuentro cursos publicados aún."
        return build_twiml(msg)

    body_lower = body.lower()

    # Detección de intención de inscripción → handoff humano
    if detect_intent_enroll(body_lower):
        send_admin_forward(from_number, body)
        msg = (
            "¡Excelente! 🙌 Te conecto con un coordinador humano de {brand} para continuar con tu inscripción. "
            "En breve te escriben por este mismo chat."
        ).format(brand=BRAND_NAME)
        return build_twiml(msg)

    # Intento detectar curso
    row = find_course(rows, body)

    if row:
        # Si pregunta algo específico (precio, horarios, etc.)
        faq_ans = answer_faq(row, body_lower)
        if faq_ans:
            return build_twiml(faq_ans)
        # Primera respuesta estándar
        resp = first_response_for_course(row, from_number)
        return build_twiml(resp)

    # Si no detecto curso, ofrezco la lista
    cursos = list_courses(rows)
    if cursos:
        msg = (
            "Para ayudarte mejor, dime el *nombre del curso*.\n\n*Cursos disponibles:*\n- "
            + "\n- ".join(cursos)
        )
    else:
        msg = f"Por ahora no encuentro cursos publicados en {BRAND_NAME}."
    return build_twiml(msg)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
