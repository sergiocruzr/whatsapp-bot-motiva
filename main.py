from flask import Flask, request, jsonify, Response
import os, time, requests, csv
from io import StringIO

app = Flask(__name__)

SHEET_CSV_URL = os.getenv("SHEET_CSV_URL")

EXPECTED_HEADERS = [
    "Curso","Texto Principal","Link PDF","Fecha de Inicio","Fechas de clases","Duración","Horarios",
    "Inscripción Argentina","Inscripción Bolivia","Inscripción Chile","Inscripción Colombia",
    "Inscripción Costa Rica","Inscripción México","Inscripción Paraguay","Inscripción Perú",
    "Inscripción Uruguay","Inscripción Resto Países","FAQ",
]
# Acepta este alias que vi en tu hoja:
HEADER_SYNONYMS = {
    "Valor Inscripción Uruguay": "Inscripción Uruguay",
}

_cache = {"rows": [], "t": 0.0}
CACHE_SECONDS = 300

def fetch_sheet_rows(force=False):
    now = time.time()
    if not force and _cache["rows"] and now - _cache["t"] < CACHE_SECONDS:
        return _cache["rows"]
    if not SHEET_CSV_URL:
        _cache.update({"rows": [], "t": now})
        return []
    resp = requests.get(SHEET_CSV_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # corrige acentos
    reader = csv.DictReader(StringIO(resp.text))

    raw_headers = reader.fieldnames or []
    headers = [ (h or "").strip().lstrip("\ufeff") for h in raw_headers ]
    headers = [ HEADER_SYNONYMS.get(h, h) for h in headers ]
    if headers != EXPECTED_HEADERS:
        raise ValueError(f"Encabezados no coinciden. Esperado: {EXPECTED_HEADERS}. Recibido: {raw_headers}")

    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            nk = (k or "").strip().lstrip("\ufeff")
            nk = HEADER_SYNONYMS.get(nk, nk)
            clean[nk] = (v or "").strip()
        if clean.get("Curso"):
            rows.append(clean)
    _cache.update({"rows": rows, "t": now})
    return rows

@app.get("/health")
def health():
    return jsonify(ok=True, cached_rows=len(_cache["rows"]), cache_age_s=int(time.time()-_cache["t"]))

@app.route("/sheet_refresh", methods=["GET","POST"])
def sheet_refresh():
    fetch_sheet_rows(force=True)
    return jsonify(ok=True, refreshed=True, count=len(_cache["rows"]))

@app.get("/sheet_preview")
def sheet_preview():
    try:
        rows = fetch_sheet_rows()
        cursos = [r["Curso"] for r in rows]
        return jsonify(count=len(cursos), cursos=cursos)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# Webhook dummy (seguimos con lo básico por ahora)
@app.post("/whatsapp")
def whatsapp():
    twiml = "<?xml version='1.0' encoding='UTF-8'?><Response><Message>✅ Bot operativo. Escribe el nombre del curso.</Message></Response>"
    return Response(twiml, mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
