from flask import Flask, request, Response, jsonify
import os

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.post("/whatsapp")
def whatsapp():
    # Responde algo fijo para confirmar que el webhook funciona
    twiml = "<?xml version='1.0' encoding='UTF-8'?><Response><Message>✅ Bot operativo. Escribe el nombre del curso.</Message></Response>"
    return Response(twiml, mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
