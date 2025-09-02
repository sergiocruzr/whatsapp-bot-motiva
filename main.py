# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, time, csv, re, requests, unicodedata
from io import StringIO
from xml.sax.saxutils import escape as xml_escape

app = Flask(__name__)

# ===== Config =====
BRAND_NAME = os.getenv('BRAND_NAME', 'Motiva Educación')
BOT_NAME = os.getenv('BOT_NAME', 'Moti')

SHEET_CSV_URL = os.getenv('SHEET_CSV_URL')

# Para notificar al asesor automáticamente (Twilio a WhatsApp del asesor)
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

# Número del asesor para handoff (MOSTRAR y también notificarle)
# 1) Mostrar al usuario (link clickeable)
ADVISOR_E164 = os.getenv('ADVISOR_E164', '+59162723944')  # sin "whatsapp:" para link wa.me
ADVISOR_WA_LINK = 'https://wa.me/{}'.format(ADVISOR_E164.replace('+',''))

# 2) Notificación automática al asesor (Twilio REST necesita el prefijo "whatsapp:")
ADMIN_FORWARD_NUMBER = os.getenv('ADMIN_FORWARD_NUMBER', 'whatsapp:{}'.format(ADVISOR_E164))

# ===== Cache =====
_cache = {'rows': [], 't': 0.0, 'alias_idx': {}}
CACHE_SECONDS = 300

# ===== Encabezados requeridos (Alias opcional) =====
EXPECTED_HEADERS = [
    'Curso','Texto Principal','Link PDF','Fecha de Inicio','Fechas de clases','Duración','Horarios',
    'Inscripción Argentina','Inscripción Bolivia','Inscripción Chile','Inscripción Colombia',
    'Inscripción Costa Rica','Inscripción México','Inscripción Paraguay','Inscripción Perú',
    'Inscripción Uruguay','Inscripción Resto Países','FAQ'
]
HEADER_SYNONYMS = {
    'Valor Inscripción Uruguay': 'Inscripción Uruguay',
}

# ===== Prefijo telefónico -> columna de precio =====
COUNTRY_PRICE_COLUMN = {
    '506': 'Inscripción Costa Rica',
    '598': 'Inscripción Uruguay',
    '595': 'Inscripción Paraguay',
    '591': 'Inscripción Bolivia',
    '54':  'Inscripción Argentina',
    '56':  'Inscripción Chile',
    '57':  'Inscripción Colombia',
    '52':  'Inscripción México',
    '51':  'Inscripción Perú',
}
# Palabras país en el texto -> columna de precio
COUNTRY_WORD_TO_COL = {
    'argentina': 'Inscripción Argentina',
    'bolivia': 'Inscripción Bolivia',
    'chile': 'Inscripción Chile',
    'colombia': 'Inscripción Colombia',
    'costa rica': 'Inscripción Costa Rica',
    'mexico': 'Inscripción México',
    'méxico': 'Inscripción México',
    'paraguay': 'Inscripción Paraguay',
    'peru': 'Inscripción Perú',
    'perú': 'Inscripción Perú',
    'uruguay': 'Inscripción Uruguay',
    'resto': 'Inscripción Resto Países',
}

# ===== Utils =====
def _fold(s):
    s = (s or '').lower()
    nf = unicodedata.normalize('NFD', s)
    return ''.join(c for c in nf if not unicodedata.combining(c))

def build_twiml(message):
    xml = "<?xml version='1.0' encoding='UTF-8'?><Response><Message>{}</Message></Response>".format(xml_escape(message))
    return Response(xml, mimetype='application/xml')

def _has_any(text, keywords):
    return any(k in text for k in keywords)

# ===== Sheet =====
def _rebuild_alias_index(rows):
    idx = {}
    for r in rows:
        alias_cell = (r.get('Alias') or '').strip()
        if not alias_cell:
            continue
        parts = re.split(r'[\n,;|/]+', alias_cell)  # coma, ;, |, / o salto de linea
        for a in parts:
            a = a.strip()
            if not a:
                continue
            idx[_fold(a)] = r
    return idx

def fetch_sheet_rows(force=False):
    now = time.time()
    if not force and _cache['rows'] and now - _cache['t'] < CACHE_SECONDS:
        return _cache['rows']
    if not SHEET_CSV_URL:
        _cache.update({'rows': [], 't': now, 'alias_idx': {}})
        return []

    resp = requests.get(SHEET_CSV_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    reader = csv.DictReader(StringIO(resp.text))

    raw_headers = reader.fieldnames or []
    headers = [(h or '').strip().lstrip('\ufeff') for h in raw_headers]
    headers = [HEADER_SYNONYMS.get(h, h) for h in headers]

    missing = [h for h in EXPECTED_HEADERS if h not in headers]
    if missing:
        raise ValueError('Faltan encabezados requeridos: {}. Recibido: {}'.format(missing, raw_headers))

    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            nk = (k or '').strip().lstrip('\ufeff')
            nk = HEADER_SYNONYMS.get(nk, nk)
            clean[nk] = (v or '').strip()
        if clean.get('Curso'):
            rows.append(clean)

    _cache['rows'] = rows
    _cache['t'] = now
    _cache['alias_idx'] = _rebuild_alias_index(rows)
    return rows

def list_courses(rows):
    return [r.get('Curso', '').strip() for r in rows if r.get('Curso')]

# ===== Matching de curso =====
def _best_row_by_query(rows, q_fold):
    # (1) Texto del usuario dentro del nombre del curso
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and q_fold in _fold(name):
            return r
    # (2) Nombre completo dentro del texto del usuario
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and _fold(name) in q_fold:
            return r
    # (3) Intersección de tokens >=3 chars
    words = [w for w in re.findall(r'[a-z0-9áéíóúñ]+', q_fold) if len(w) >= 3]
    words = set(words)
    best, best_row = 0, None
    for r in rows:
        name = (r.get('Curso') or '')
        name_tokens = [_fold(w) for w in re.findall(r'[a-z0-9áéíóúñ]+', name.lower()) if len(w) >= 3]
        score = len(words & set(name_tokens))
        if score > best:
            best, best_row = score, r
    return best_row if best >= 1 else None

def find_course(rows, user_text):
    q_fold = _fold(user_text)
    # (A) alias desde la hoja
    for a, r in (_cache.get('alias_idx') or {}).items():
        if a and a in q_fold:
            print('[ALIAS HIT]', a, '->', r.get('Curso'))
            return r
    # (B) patrones tipo "info|precio|horario|pdf <algo>"
    m = re.search(r'(?:info|precio|horarios?|pdf)\s+(.+)$', q_fold)
    if m:
        cand = m.group(1).strip()
        r = _best_row_by_query(rows, cand)
        if r:
            print('[BEST MATCH after keyword]', cand, '->', r.get('Curso'))
            return r
    # (C) fallback general
    r = _best_row_by_query(rows, q_fold)
    if r:
        print('[BEST MATCH]', q_fold, '->', r.get('Curso'))
    return r

def guess_country_price_column(from_number):
    num = (from_number or '').replace('whatsapp:', '').replace('+', '')
    for p in sorted(COUNTRY_PRICE_COLUMN.keys(), key=lambda p: -len(p)):
        if num.startswith(p):
            return COUNTRY_PRICE_COLUMN[p]
    return 'Inscripción Resto Países'

def pick_price_column_from_text(body_lower, from_number):
    # 1) si el usuario dice el país
    for key, col in COUNTRY_WORD_TO_COL.items():
        if key in body_lower:
            return col
    # 2) si no, tratamos de inferir por prefijo del número
    return guess_country_price_column(from_number or '')

# ===== Respuestas =====
def course_card(row, from_number):
    """Bloque estándar de información del curso (usa columnas del sheet)."""
    partes = []
    titulo = row.get('Curso', '')
    if titulo:
        partes.append('🎓 *{}* — {}'.format(titulo, BRAND_NAME))

    txt = row.get('Texto Principal', '')
    if txt:
        partes.append(txt)

    fi = row.get('Fecha de Inicio', '')
    if fi:
        partes.append('📅 *Inicio:* {}'.format(fi))

    fechas = row.get('Fechas de clases', '')
    if fechas:
        partes.append('🗓️ *Fechas de clases:* {}'.format(fechas))

    dur = row.get('Duración', '')
    if dur:
        partes.append('⏳ *Duración:* {}'.format(dur))

    hor = row.get('Horarios', '')
    if hor:
        partes.append('🕒 *Horarios:* {}'.format(hor))

    # Si hay Modalidad / Metodología como columnas, úsalas
    modalidad = row.get('Modalidad', '') or row.get('modalidad', '')
    if modalidad:
        partes.append('🎥 *Modalidad:* {}'.format(modalidad))
    metodologia = row.get('Metodología', '') or row.get('Metodologia', '') or row.get('metodología', '')
    if metodologia:
        partes.append('🧩 *Metodología:* {}'.format(metodologia))

    # Precio por país (según texto o número)
    price_col = pick_price_column_from_text('', from_number)  # si no hay país en el texto, infiere por número
    precio = row.get(price_col, '') or row.get('Inscripción Resto Países', '')
    if precio:
        partes.append('💳 *Inscripción ({}):* {}'.format(price_col.replace('Inscripción ', ''), precio))

    pdf = row.get('Link PDF', '')
    if pdf:
        partes.append('📄 *PDF informativo:* {}'.format(pdf))

    partes.append('Si deseas *inscribirte*, respóndeme: *me interesa* o *quiero inscribirme* y te conecto con un asesor humano. 🤝')
    return '\n\n'.join(partes)

def answer_specific(row, body_lower, from_number):
    """Respuestas específicas si el usuario las pide por palabra clave."""
    # Precio [país]
    if _has_any(body_lower, ['precio','costo','valor','inscrip']):
        col = pick_price_column_from_text(body_lower, from_number)
        precio = row.get(col, '') or row.get('Inscripción Resto Países', '')
        if precio:
            return '💳 *Inscripción ({}):* {}'.format(col.replace('Inscripción ', ''), precio)
        return '💳 Para darte el valor exacto, indícame tu país (ej.: "precio Bolivia").'

    # Horarios
    if _has_any(body_lower, ['horario','horarios','hora','clase','clases']):
        if row.get('Horarios'):
            return '🕒 *Horarios:* {}'.format(row['Horarios'])
        return '🕒 En el PDF tienes los horarios detallados.'

    # Modalidad / Metodología
    if _has_any(body_lower, ['modalidad','metodolog','metodología','metodologia','online','virtual','en vivo','zoom','meet']):
        modalidad = row.get('Modalidad', '') or row.get('modalidad', '')
        metodologia = row.get('Metodología', '') or row.get('Metodologia', '') or row.get('metodología', '')
        piezas = []
        if modalidad:
            piezas.append('🎥 *Modalidad:* {}'.format(modalidad))
        if metodologia:
            piezas.append('🧩 *Metodología:* {}'.format(metodologia))
        if not piezas:
            piezas.append('🎥 Modalidad *en vivo* por videoconferencia (clases síncronas).')
        return '\n'.join(piezas)

    # Inicio / Fechas / Duración / PDF
    if _has_any(body_lower, ['inicio','empieza','empiezan','fecha de inicio']):
        if row.get('Fecha de Inicio'):
            return '📅 *Inicio:* {}'.format(row['Fecha de Inicio'])
    if _has_any(body_lower, ['fechas','calendario']):
        if row.get('Fechas de clases'):
            return '🗓️ *Fechas de clases:* {}'.format(row['Fechas de clases'])
    if _has_any(body_lower, ['duración','duracion','dura']):
        if row.get('Duración'):
            return '⏳ *Duración:* {}'.format(row['Duración'])
    if _has_any(body_lower, ['pdf','brochure','informativo']):
        if row.get('Link PDF'):
            return '📄 *PDF informativo:* {}'.format(row['Link PDF'])

    # FAQ general (si existe)
    if _has_any(body_lower, ['faq','preguntas','dudas','consulta']):
        faq = (row.get('FAQ') or '').strip()
        if faq:
            return 'ℹ️ *FAQ:* {}'.format(faq)

    return None  # si no pidió algo específico

def detect_intent_enroll(body_lower):
    keys = [
        'me interesa','quiero inscribirme','inscribirme','como me inscribo','cómo me inscribo',
        'inscripcion','inscripción','quiero anotarme','quiero matricularme'
    ]
    return any(k in body_lower for k in keys)

def send_admin_forward(user_from, user_body, course_name=None):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER and ADMIN_FORWARD_NUMBER):
        return False
    try:
        url = 'https://api.twilio.com/2010-04-01/Accounts/{}/Messages.json'.format(TWILIO_ACCOUNT_SID)
        parts = [
            'Nuevo lead para {}'.format(BRAND_NAME),
            'Desde: {}'.format(user_from),
            'Mensaje: {}'.format(user_body)
        ]
        if course_name:
            parts.append('Curso: {}'.format(course_name))
        data = {'From': TWILIO_WHATSAPP_NUMBER, 'To': ADMIN_FORWARD_NUMBER, 'Body': '\n'.join(parts)}
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print('[ERROR send_admin_forward]', e)
        return False

# ===== Rutas =====
@app.get('/health')
def health():
    age = int(time.time() - _cache['t']) if _cache['t'] else None
    return jsonify(ok=True, brand=BRAND_NAME, bot=BOT_NAME, cached_rows=len(_cache['rows']), cache_age_s=age)

@app.route('/sheet_refresh', methods=['GET','POST'])
def sheet_refresh():
    fetch_sheet_rows(force=True)
    return jsonify(ok=True, refreshed=True, count=len(_cache['rows']))

@app.get('/sheet_preview')
def sheet_preview():
    try:
        rows = fetch_sheet_rows()
        return jsonify(ok=True, count=len(rows), cursos=list_courses(rows))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# Acepta GET y POST; usa request.values para leer Body/From desde querystring o form
@app.route('/whatsapp', methods=['GET', 'POST'])
def whatsapp_webhook():
    try:
        from_number = request.values.get('From', '')
        body = (request.values.get('Body', '') or '').strip()
        print('[INBOUND]', from_number, body)

        rows = fetch_sheet_rows()

        # saludo / sin body -> presentación con lista de cursos
        if not body:
            cursos = list_courses(rows)
            if cursos:
                msg = 'Hola 👋, soy *{}*, asistente de {}. Estoy para ayudarte con la información general de los cursos.\n\n*Cursos:*\n- '.format(BOT_NAME, BRAND_NAME) + '\n- '.join(cursos) + '\n\nPuedes escribirme, por ejemplo: "info [nombre del curso]" o "precio [país] [curso]".'
            else:
                msg = 'Hola 👋, soy *{}*, asistente de {}. Aún no encuentro cursos publicados.'.format(BOT_NAME, BRAND_NAME)
            return build_twiml(msg)

        body_fold = _fold(body)

        # intención de inscripción -> notificar asesor y dar contacto directo
        if detect_intent_enroll(body_fold):
            # tratar de inferir el curso mencionado para pasar contexto al asesor
            row_for_forward = find_course(rows, body)
            course_name = row_for_forward.get('Curso') if row_for_forward else None
            sent = send_admin_forward(from_number, body, course_name=course_name)
            human = 'Ya avisé a nuestro asesor ✅.' if sent else 'Te conecto con nuestro asesor.'
            reply = (
                '¡Genial! 🙌 {} En breve te escribirá.\n\n'
                'Si prefieres, puedes contactarlo ahora mismo aquí:\n'
                '📲 {}  ({})'
            ).format(human, ADVISOR_E164, ADVISOR_WA_LINK)
            return build_twiml(reply)

        # encontrar curso
        row = find_course(rows, body)
        print('[MATCH]', 'row=' + (row.get('Curso','') if row else 'None'))

        if row:
            # si pidió algo específico (precio/horarios/pdf/faq...), responde eso
            specific = answer_specific(row, body_fold, from_number)
            if specific:
                return build_twiml(specific)
            # si no, envía la ficha completa
            return build_twiml(course_card(row, from_number))

        # no matcheó ningún curso
        cursos = list_courses(rows)
        if cursos:
            return build_twiml('Para ayudarte mejor, dime el *nombre del curso*.\n\n*Cursos:*\n- ' + '\n- '.join(cursos))
        return build_twiml('Por ahora no encuentro cursos publicados en {}.'.format(BRAND_NAME))

    except Exception as e:
        print('[ERROR /whatsapp]', e)
        return build_twiml('Ups, tuve un detalle al procesar tu mensaje. ¿Puedes intentar de nuevo? 🙏')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
