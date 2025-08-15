import os
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# ==== CONFIG ====
SHEET_URL = "https://api.sheetbest.com/sheets/c38f74e3-80be-4898-af8b-b44389ef6a91"
ASESOR_LINK = "https://wa.link/tx3hj3"
ASESOR_NUM = "+59162723944"

app = Flask(__name__)

# ==== DETECTAR PAÍS ====
def detectar_col_precio_por_numero(numero):
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

def detectar_col_precio_por_texto(texto):
    texto = texto.lower()
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
        "uruguay": "Valor Inscripción Uruguay"
    }
    for k, v in mapping.items():
        if k in texto:
            return v
    if any(p in texto for p in ["eeuu","ee.uu","ecuador","puerto rico","canadá","honduras","guatemala","venezuela","rd","república dominicana","panamá","panama"]):
        return "Inscripción Resto Países"
    return None

def cargar_curso_vigente():
    try:
        resp = requests.get(SHEET_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except:
        return None

def contiene(texto, palabras):
    texto = texto.lower()
    return any(p in texto for p in palabras)

# ==== RUTAS ====
@app.route("/", methods=["GET"])
def home():
    return "🚀 Bot activo y esperando mensajes desde Twilio"

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = (request.values.get("Body", "") or "").strip()
    from_number = request.values.get("From", "")

    # Detectar si es audio
    if contiene(incoming_msg, ["audio", "nota de voz", "mensaje de voz"]):
        msg = "Ups, ahora no puedo escuchar audios 🙈 Escríbeme por texto y te ayudo rápido."
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    curso = cargar_curso_vigente()
    if not curso:
        tw = MessagingResponse(); tw.message("No tengo info disponible del curso ahora 🙏")
        return str(tw)

    # Datos del curso
    texto_principal = (curso.get("Texto Principal") or "").strip()
    link_pdf = (curso.get("Link PDF") or "").strip()
    nombre = (curso.get("Curso") or "").strip()
    fecha_inicio = (curso.get("Fecha de Inicio") or "").strip()
    fechas_clases = (curso.get("Fechas de clases") or "").strip()
    duracion = (curso.get("Duración") or "").strip()
    horarios = (curso.get("Horarios") or "").strip()

    col_txt = detectar_col_precio_por_texto(incoming_msg)
    col_num = detectar_col_precio_por_numero(from_number)
    col_precio = col_txt or col_num
    precio = (curso.get(col_precio) or "").strip() or "Consulta por el valor en tu país."

    # Info inicial
    if contiene(incoming_msg, ["hola","info","información","informacion","detalles","brochure","pdf","curso","quiero saber","cómo es","como es"]):
        partes = []
        if texto_principal: partes.append(texto_principal)
        if link_pdf: partes.append(f"📄 PDF: {link_pdf}")
        partes.append(f"💰 Precio: {precio}")
        partes.append("Si querés anotarte, decime *Me quiero inscribir* y te paso con un coordinador 😉")
        tw = MessagingResponse(); tw.message("\n\n".join(partes))
        return str(tw)

    # Intención de inscripción
    if contiene(incoming_msg, ["me quiero inscribir","quiero inscribirme","cómo me inscribo","como me inscribo","dónde pago","donde pago","quiero pagar","me interesa inscribirme"]):
        msg = f"Genial 😃 Escribile directo a nuestro coordinador: {ASESOR_LINK} o al {ASESOR_NUM}"
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # Pregunta de precio
    if contiene(incoming_msg, ["precio","valor","inscripción","inscripcion","cuánto cuesta","cuanto cuesta"]):
        tw = MessagingResponse(); tw.message(f"💰 Precio: {precio}")
        return str(tw)

    # Pide PDF
    if contiene(incoming_msg, ["pdf","brochure","info completa","información completa","informacion completa","archivo"]):
        if link_pdf:
            tw = MessagingResponse(); tw.message(f"📄 PDF: {link_pdf}")
        else:
            tw = MessagingResponse(); tw.message("Aún no tengo el PDF, pero puedo mandarte la info por aquí 😉")
        return str(tw)

    # Respuesta por defecto
    tw = MessagingResponse()
    tw.message("¿Querés que te mande el PDF y precio de tu país? 📄💰")
    return str(tw)

# ==== RUN ====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor Flask funcionando en el puerto {port}")
    app.run(host="0.0.0.0", port=port)
