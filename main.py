import requests
import os
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI

# Cliente OpenAI
client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "🚀 Bot activo y esperando mensajes desde Twilio"

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    incoming_msg = request.values.get("Body", "").strip().lower()
    from_number = request.values.get("From", "")
    print(f"📩 Mensaje recibido: {incoming_msg}")
    print(f"📱 Número: {from_number}")

    # Detectar país por número
    def detectar_columna_pais(numero):
        if numero.startswith("whatsapp:+591"):
            return "Inscripción Bolivia"
        elif numero.startswith("whatsapp:+54"):
            return "Inscripción Argentina"
        elif numero.startswith("whatsapp:+56"):
            return "Inscripción Chile"
        elif numero.startswith("whatsapp:+57"):
            return "Inscripción Colombia"
        elif numero.startswith("whatsapp:+506"):
            return "Inscripción Costa Rica"
        elif numero.startswith("whatsapp:+52"):
            return "Valor Inscripción México"
        elif numero.startswith("whatsapp:+595"):
            return "Valor Inscripción Paraguay"
        elif numero.startswith("whatsapp:+51"):
            return "Valor Inscripción Perú"
        elif numero.startswith("whatsapp:+598"):
            return "Valor Inscripción Uruguay"
        else:
            return "Inscripción Resto Países"

    # Detectar país en mensaje de texto
    def detectar_columna_por_mensaje(texto):
        paises = {
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
        for clave, columna in paises.items():
            if clave in texto:
                return columna
        return None

    columna_detectada = detectar_columna_pais(from_number)
    columna_en_mensaje = detectar_columna_por_mensaje(incoming_msg)
    columna_pais = columna_en_mensaje if columna_en_mensaje else columna_detectada

    # Leer curso desde Google Sheets (vía Sheet.best)
    sheet_url = "https://api.sheetbest.com/sheets/c38f74e3-80be-4898-af8b-b44389ef6a91"
    response = requests.get(sheet_url)
    cursos = response.json()

    if not cursos:
        respuesta_final = "No se encontró información de cursos disponibles en este momento."
    else:
        curso = cursos[0]  # Curso más reciente o vigente
        texto_principal = curso.get("Texto Principal", "")
        link_pdf = curso.get("Link PDF", "")
        precio = curso.get(columna_pais, "Consulta por el valor en tu país.")

        # Si envió un audio o lo menciona
        if "audio" in incoming_msg:
            respuesta_final = "Gracias por tu mensaje 🙌, pero estoy conectado desde una computadora y no puedo escuchar audios. ¿Podrías escribirme tu consulta por texto? Estaré encantado de ayudarte 😊"
        else:
            # Preparar prompt completo para OpenAI
            prompt_base = f"""
🧠 Información general del curso:

{texto_principal}

📄 PDF informativo: {link_pdf}

💰 Precio según país: {precio}

Consulta del usuario: {incoming_msg}

Responde como parte del equipo de coordinación de Motiva Educación. Usa un tono cálido, profesional y cercano. Si el usuario muestra interés en inscribirse, sugiérele contactar al coordinador al número +59162723944 o al enlace https://wa.link/tx3hj3. 
No te identifiques como bot. Si el usuario pregunta por certificado, plataforma, pagos o modalidad, responde según las reglas internas de la institución.
"""

            openai_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "Eres parte del equipo académico de Motiva Educación. Responde con claridad, calidez y profesionalismo. No inventes precios ni conviertas monedas. Nunca digas que eres un bot. Usa emojis amigables cuando sea útil."
                    },
                    {
                        "role": "user",
                        "content": prompt_base
                    }
                ]
            )

            respuesta_final = openai_response.choices[0].message.content.strip()

    # Enviar la respuesta
    twilio_resp = MessagingResponse()
    twilio_resp.message(respuesta_final)
    return str(twilio_resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor Flask funcionando en el puerto {port}")
    app.run(host="0.0.0.0", port=port)
