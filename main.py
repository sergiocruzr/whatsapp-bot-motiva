import os
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# ==== CONFIG ====
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1qFZvzPdZetlMEWzNzxWptYsSMroQR7P88ShyfbORVOE/edit?usp=drive_link")
ASESOR_LINK = "https://wa.link/tx3hj3"
ASESOR_NUM = "+59162723944"

app = Flask(__name__)

# ==== UTILIDADES ====

def detectar_col_precio_por_numero(numero: str) -> str:
    if not numero:
        return "Inscripción Resto Países"
    if numero.startswith("whatsapp:+591"): return "Inscripción Bolivia"
    if numero.startswith("whatsapp:+54"):  return "Inscripción Argentina"
    if numero.startswith("whatsapp:+56"):  return "Inscripción Chile"
    if numero.startswith("whatsapp:+57"):  return "Inscripción Colombia"
    if numero.startswith("whatsapp:+506"): return "Inscripción Costa Rica"
    if numero.startswith("whatsapp:+52"):  return "Valor Inscripción México"
    if numero.startswith("whatsapp:+595"): return "Valor Inscripción Paraguay"
    if numero.startswith("whatsapp:+51"):  return "Valor Inscripción Perú"
    if numero.startswith("whatsapp:+598"): return "Valor Inscripción Uruguay"
    return "Inscripción Resto Países"

def detectar_col_precio_por_texto(texto: str) -> str | None:
    t = (texto or "").lower()
    mapping = {
        "argentina": "Inscripción Argentina",
        "bolivia": "Inscripción Bolivia",
        "chile": "Inscripción Chile",
        "colombia": "Inscripción Colombia",
        "costa rica": "Inscripción Costa Rica",
        "méxico": "Valor Inscripción México",
        "mexico": "Valor Inscripción México",
        "paraguay": "Valor Inscripción Paraguay",
        "perú": "Valor Inscripción Perú",
        "peru": "Valor Inscripción Perú",
        "uruguay": "Valor Inscripción Uruguay",
    }
    for k, v in mapping.items():
        if k in t:
            return v
    if any(p in t for p in [
        "eeuu","ee.uu","ecuador","puerto rico","canadá","honduras",
        "guatemala","venezuela","rd","república dominicana","panamá","panama"
    ]):
        return "Inscripción Resto Países"
    return None

def nombre_pais_desde_col_precio(col_precio: str) -> str:
    mapa = {
        "Inscripción Bolivia": "Bolivia",
        "Inscripción Argentina": "Argentina",
        "Inscripción Chile": "Chile",
        "Inscripción Colombia": "Colombia",
        "Inscripción Costa Rica": "Costa Rica",
        "Valor Inscripción México": "México",
        "Valor Inscripción Paraguay": "Paraguay",
        "Valor Inscripción Perú": "Perú",
        "Valor Inscripción Uruguay": "Uruguay",
        "Inscripción Resto Países": "otros países",
    }
    return mapa.get(col_precio, "tu país")

def cargar_curso_vigente() -> dict | None:
    try:
        resp = requests.get(SHEET_URL, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except Exception:
        return None

def contiene(texto: str, palabras: list[str]) -> bool:
    t = (texto or "").lower()
    return any(p in t for p in palabras)

def faq_respuesta(faq_text: str, pregunta: str) -> str | None:
    if not faq_text:
        return None
    t = (pregunta or "").lower()
    lineas = [l.strip(" •-") for l in faq_text.splitlines() if l.strip()]
    claves = [
        "certific", "licenc", "entren", "modal", "plataforma", "pago", "precio",
        "profes", "fecha", "horario", "pdf", "temario", "duración", "duracion"
    ]
    for l in lineas:
        l_low = l.lower()
        if any(k in l_low for k in claves) and any(w in t for w in l_low.split()):
            return l
    if "faq" in t:
        joined = "\n• " + "\n• ".join(lineas[:6])
        return f"Algunos puntos clave:\n{joined}"
    return None

# ==== RUTAS ====

@app.route("/", methods=["GET"])
def home():
    return "🚀 Bot activo y esperando mensajes desde Twilio"
@app.route("/debug/sheet", methods=["GET"])
def debug_sheet():
    try:
        url = os.environ.get("SHEET_URL","")
        r = requests.get(url, timeout=12)
        return {
            "sheet_url_used": url,
            "status": r.status_code,
            "preview": r.text[:800]
        }, 200
    except Exception as e:
        return {"sheet_url_used": os.environ.get("SHEET_URL",""), "error": str(e)}, 500

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = (request.values.get("Body", "") or "").strip()
    low = incoming_msg.lower()
    from_number = request.values.get("From", "")

    # 0) Audio
    if contiene(low, ["audio", "nota de voz", "mensaje de voz"]):
        tw = MessagingResponse()
        tw.message("Ups, ahora no puedo escuchar audios 🙈 Escribime por texto y te ayudo rápido.")
        return str(tw)

    # 1) Cargar curso (vigente)
    curso = cargar_curso_vigente()
    if not curso:
        tw = MessagingResponse(); tw.message("No tengo info del curso ahora 🙏")
        return str(tw)

    # 2) Campos clave
    texto_principal = (curso.get("Texto Principal") or "").strip()
    link_pdf = (curso.get("Link PDF") or "").strip()
    nombre = (curso.get("Curso") or "").strip()
    fecha_inicio = (curso.get("Fecha de Inicio") or "").strip()
    fechas = (curso.get("Fechas de clases") or "").strip()
    duracion = (curso.get("Duración") or "").strip()
    horarios = (curso.get("Horarios") or "").strip()
    faq = (curso.get("FAQ") or "").strip()

    # Precio por país
    col_txt = detectar_col_precio_por_texto(low)
    col_num = detectar_col_precio_por_numero(from_number)
    col_precio = col_txt or col_num
    precio = (curso.get(col_precio) or "").strip() or "Consulta por el valor en tu país."

    # --- NUEVO: manejo de saludos (no mandar aún la info del curso) ---
    if contiene(low, ["hola","holaa","buenas","buenos días","buenos dias","buenas tardes","buenas noches","qué tal","que tal","hey","ola"]):
        # guía suave para que el usuario diga el curso
        sugerencia = f"¿Te interesa *{nombre}* o tenés otro curso en mente?"
        tw = MessagingResponse()
        tw.message(f"¡Hola! 👋 ¿Sobre qué curso te paso info? {sugerencia}")
        return str(tw)

    # 3) Intención: ¿De qué país son?
    if contiene(low, [
        "de qué país son","de que pais son","de qué país es","de que pais es",
        "de dónde son","de donde son","de dónde operan","de donde operan",
        "de qué país","de que pais"
    ]):
        pais_detectado = nombre_pais_desde_col_precio(col_precio)
        if pais_detectado not in ("tu país", "otros países"):
            msg = (f"Somos Motiva Educación 👋 Trabajamos 100% online. "
                   f"Si querés, te paso precio y formas de pago de {pais_detectado}.")
        else:
            msg = ("Somos Motiva Educación 👋 Trabajamos 100% online para Hispanoamérica. "
                   "Decime tu país y te paso precio y formas de pago locales.")
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # 4) Info del curso (breve) — ya no incluye 'hola' como disparador
    if contiene(low, ["info","información","informacion","detalles","brochure","pdf","curso","quiero saber","cómo es","como es"]):
        partes = []
        if texto_principal: partes.append(texto_principal)
        if link_pdf: partes.append(f"📄 PDF: {link_pdf}")
        if precio: partes.append(f"💰 Precio: {precio}")
        tw = MessagingResponse(); tw.message("\n\n".join(partes + ["¿Te paso pasos para inscribirte? 😉"]))
        return str(tw)

    # 5) Precio directo
    if contiene(low, ["precio","valor","inscripción","inscripcion","cuánto cuesta","cuanto cuesta"]):
        tw = MessagingResponse(); tw.message(f"💰 Precio: {precio}")
        return str(tw)

    # 6) PDF directo
    if contiene(low, ["pdf","brochure","info completa","información completa","informacion completa","archivo"]):
        msg = f"📄 PDF: {link_pdf}" if link_pdf else "Aún no tengo el PDF listo, pero te paso la info por aquí 😉"
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # 7) Fechas/horarios
    if contiene(low, ["fecha","fechas","calendario","horario","horarios","cuándo","cuando"]):
        resumen = []
        if fecha_inicio: resumen.append(f"📅 Inicio: {fecha_inicio}")
        if fechas: resumen.append(f"🗓️ Clases: {fechas}")
        if duracion: resumen.append(f"⏳ Duración: {duracion}")
        if horarios: resumen.append(f"🕒 Horarios: {horarios}")
        if not resumen: resumen.append("Te paso el calendario en el PDF 📄")
        tw = MessagingResponse(); tw.message("\n".join(resumen))
        return str(tw)

    # 8) Inscripción → coordinador
    if contiene(low, ["me quiero inscribir","quiero inscribirme","cómo me inscribo","como me inscribo","dónde pago","donde pago","quiero pagar","me interesa inscribirme"]):
        msg = f"¡De una! 😃 Escribile al coord.: {ASESOR_LINK} o al {ASESOR_NUM}"
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # 9) FAQ
    posible = faq_respuesta(faq, low)
    if posible:
        tw = MessagingResponse(); tw.message(posible)
        return str(tw)

    # 10) Último recurso
    tw = MessagingResponse()
    tw.message("¿Sobre qué curso te paso info? Si querés, te envío PDF y precio de tu país 📄💰")
    return str(tw)

# ==== RUN ====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor Flask funcionando en el puerto {port}")
    app.run(host="0.0.0.0", port=port)
if __name__ == "__main__":
    print("SHEET_URL =", os.environ.get("SHEET_URL","(no definida)"))
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor Flask funcionando en el puerto {port}")
    app.run(host="0.0.0.0", port=port)






