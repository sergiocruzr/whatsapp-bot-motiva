import os
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

# ==== CONFIG ====
SHEET_URL = "https://api.sheetbest.com/sheets/c38f74e3-80be-4898-af8b-b44389ef6a91"
ASESOR_LINK = "https://wa.link/tx3hj3"
ASESOR_NUM = "+59162723944"

# OpenAI (opcional: solo fallback). Si no pones API key, el bot sigue funcionando.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
use_openai = bool(OPENAI_API_KEY)
if use_openai:
    from openai import OpenAI
    oai = OpenAI(api_key=OPENAI_API_KEY)

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

def cargar_curso_vigente() -> dict | None:
    try:
        resp = requests.get(SHEET_URL, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None
    except Exception:
        return None

def match(texto: str, palabras: list[str]) -> bool:
    t = (texto or "").lower()
    return any(p in t for p in palabras)

# ==== RUTAS ====

@app.route("/", methods=["GET"])
def home():
    return "🚀 Bot activo y esperando mensajes desde Twilio"

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = (request.values.get("Body", "") or "").strip()
    low = incoming_msg.lower()
    from_number = request.values.get("From", "")  # ej: whatsapp:+5917xxxxxx

    # 0) Manejo de audios (o si lo mencionan)
    if match(low, ["audio", "nota de voz", "mensaje de voz"]):
        msg = ("Gracias por tu mensaje 🙌. Estoy conectado desde una computadora y no puedo escuchar audios. "
               "¿Podrías escribir tu consulta por texto? Así puedo ayudarte mejor 😊")
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # 1) Cargar curso vigente desde el Sheet
    curso = cargar_curso_vigente()
    if not curso:
        tw = MessagingResponse(); tw.message("No se encontró información de cursos disponibles en este momento.")
        return str(tw)

    # 2) Tomar campos del Sheet
    texto_principal = (curso.get("Texto Principal") or "").strip()
    link_pdf = (curso.get("Link PDF") or "").strip()
    nombre = (curso.get("Curso") or "").strip()
    fecha_inicio = (curso.get("Fecha de Inicio") or "").strip()
    fechas_clases = (curso.get("Fechas de clases") or "").strip()
    duracion = (curso.get("Duración") or "").strip()
    horarios = (curso.get("Horarios") or "").strip()

    # Precio por país: por texto o por número
    col_txt = detectar_col_precio_por_texto(low)
    col_num = detectar_col_precio_por_numero(from_number)
    col_precio = col_txt or col_num
    precio = (curso.get(col_precio) or "").strip() or "Consulta por el valor en tu país."

    # 3) Primera respuesta de info general → Texto Principal + PDF (+ datos clave)
    if match(low, ["hola","info","información","informacion","detalles","brochure","pdf","curso","quiero saber","cómo es","como es"]):
        partes = [texto_principal] if texto_principal else []
        if link_pdf: partes.append(f"📄 PDF informativo: {link_pdf}")
        # Añadimos datos clave si están
        if nombre: partes.append(f"📚 Curso: {nombre}")
        if fecha_inicio: partes.append(f"📅 Inicio: {fecha_inicio}")
        if fechas_clases: partes.append(f"🗓️ Clases: {fechas_clases}")
        if duracion: partes.append(f"⏳ Duración: {duracion}")
        if horarios: partes.append(f"🕒 Horarios: {horarios}")
        if precio: partes.append(f"💰 Precio para tu país: {precio}")
        partes.append(f"¿Querés inscribirte? Escribí *Me quiero inscribir* y te contacto con un coordinador. 🤝")
        tw = MessagingResponse(); tw.message("\n\n".join([p for p in partes if p]))
        return str(tw)

    # 4) Intención de inscripción → derivar a asesor humano
    if match(low, ["me quiero inscribir","quiero inscribirme","cómo me inscribo","como me inscribo","dónde pago","donde pago","quiero pagar","me interesa inscribirme"]):
        msg = (f"¡Excelente! 🙌 Para completar tu inscripción, podés escribir a nuestro coordinador: "
               f"{ASESOR_LINK} o al número {ASESOR_NUM}. Si querés, también puedo ayudarte por aquí con los pasos. 😊")
        tw = MessagingResponse(); tw.message(msg); return str(tw)

    # 5) Precio directo
    if match(low, ["precio","valor","inscripción","inscripcion","cuánto cuesta","cuanto cuesta"]):
        tw = MessagingResponse(); tw.message(f"💰 Precio para tu país: {precio}")
        return str(tw)

    # 6) Si pide PDF explícitamente
    if match(low, ["pdf","brochure","info completa","información completa","informacion completa","archivo"]):
        if link_pdf:
            tw = MessagingResponse(); tw.message(f"📄 Aquí tienes el PDF informativo: {link_pdf}")
        else:
            tw = MessagingResponse(); tw.message("Puedo compartirte el PDF informativo apenas esté disponible. ¿Te paso la info por aquí mientras tanto?")
        return str(tw)

    # 7) Fallback: OpenAI con contexto del Sheet (opcional)
    if use_openai:
        contexto = []
        if texto_principal: contexto.append(f"Texto principal: {texto_principal}")
        if nombre: contexto.append(f"Curso: {nombre}")
        if fecha_inicio: contexto.append(f"Fecha de inicio: {fecha_inicio}")
        if fechas_clases: contexto.append(f"Fechas de clases: {fechas_clases}")
        if duracion: contexto.append(f"Duración: {duracion}")
        if horarios: contexto.append(f"Horarios: {horarios}")
        contexto.append(f"Precio: {precio}")
        if link_pdf: contexto.append(f"PDF: {link_pdf}")

        prompt = (
            "Responde únicamente usando esta información oficial (no inventes ni estimes nada):\n\n" +
            "\n".join(contexto) +
            f"\n\nConsulta del usuario: {incoming_msg}\n" +
            "Tono: cálido, profesional y cercano. No te identifiques como bot. Usa emojis con moderación."
        )

        try:
            resp = oai.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system",
                     "content": "Eres parte del equipo de coordinación de Motiva Educación. Respondes solo con la información provista. "
                                "No conviertas monedas ni inventes precios/fechas. Si hay interés de inscripción, ofrece el contacto humano: "
                                f"{ASESOR_LINK} / {ASESOR_NUM}."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
            )
            texto = resp.choices[0].message.content.strip()
            tw = MessagingResponse(); tw.message(texto)
            return str(tw)
        except Exception:
            pass  # si falla OpenAI, seguimos con el último recurso

    # 8) Último recurso
    tw = MessagingResponse()
    tw.message("¿Podrías contarme un poco más? Puedo enviarte el PDF informativo 📄 y el precio para tu país.")
    return str(tw)

# ==== RUN ====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor Flask funcionando en el puerto {port}")
    app.run(host="0.0.0.0", port=port)
