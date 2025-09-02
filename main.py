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

# Twilio para notificar al asesor (opcional)
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

# Asesor (mostrado al usuario y usado para notificación)
ADVISOR_E164 = os.getenv('ADVISOR_E164', '+59162723944')   # visible al usuario
ADVISOR_WA_LINK = 'https://wa.me/{}'.format(ADVISOR_E164.replace('+',''))
ADMIN_FORWARD_NUMBER = os.getenv('ADMIN_FORWARD_NUMBER', 'whatsapp:{}'.format(ADVISOR_E164))  # para Twilio REST

# ===== Cache hoja y memoria simple por usuario =====
_cache = {'rows': [], 't': 0.0, 'alias_idx': {}}
CACHE_SECONDS = 300

_sessions = {}  # { from_number: {'course': <row>, 't': <epoch>} }
SESSION_TTL = 60*60  # 1 hora

# ===== Encabezados requeridos (Alias es opcional) =====
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

# ===== Sinónimos para tokens (FAQ/fuzzy) =====
# Normaliza palabras a raíces equivalentes para mejorar coincidencias
TOKEN_SYNONYMS = {
    # grabadas / grabación
    'grabada': 'grabadas', 'grabadas': 'grabadas', 'grabado': 'grabadas', 'grabados': 'grabadas',
    'grabacion': 'grabadas', 'grabaciones': 'grabadas', 'grabar': 'grabadas', 'repeticion': 'grabadas',
    'repeticiones': 'grabadas', 'ondemand': 'grabadas', 'on': 'grabadas', 'demand': 'grabadas',
    'despues': 'grabadas', 'después': 'grabadas',
    # precio
    'costo': 'precio', 'valor': 'precio', 'arancel': 'precio', 'inversion': 'precio', 'inversión': 'precio', 'pago': 'precio',
    # horarios
    'hora': 'horarios', 'clase': 'horarios', 'clases': 'horarios', 'cronograma': 'horarios',
    # modalidad / metodología
    'metodologia': 'metodologia', 'metodología': 'metodologia', 'metodo': 'metodologia', 'método': 'metodologia',
}

def _normalize_token(tok):
    return TOKEN_SYNONYMS.get(tok, tok)

# ===== Hoja =====
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

# ===== Matching curso =====
def _best_row_by_query(rows, q_fold):
    # (1) texto del usuario dentro del nombre del curso
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and q_fold in _fold(name):
            return r
    # (2) nombre completo dentro del texto del usuario
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and _fold(name) in q_fold:
            return r
    # (3) intersección de tokens >=3
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
    # (A) alias
    for a, r in (_cache.get('alias_idx') or {}).items():
        if a and a in q_fold:
            print('[ALIAS HIT]', a, '->', r.get('Curso'))
            return r
    # (B) "info|precio|horario|pdf|modalidad|metodología <algo>"
    m = re.search(r'(?:info|informacion|información|precio|horarios?|pdf|modalidad|metodolog(?:ía|ia))\s+(.+)$', q_fold)
    if m:
        cand = m.group(1).strip()
        r = _best_row_by_query(rows, cand)
        if r:
            print('[BEST MATCH after keyword]', cand, '->', r.get('Curso'))
            return r
    # (C) fallback
    r = _best_row_by_query(rows, q_fold)
    if r:
        print('[BEST MATCH]', q_fold, '->', r.get('Curso'))
    return r

# ===== Precio por país =====
def guess_country_price_column(from_number):
    num = (from_number or '').replace('whatsapp:', '').replace('+', '')
    for p in sorted(COUNTRY_PRICE_COLUMN.keys(), key=lambda p: -len(p)):
        if num.startswith(p):
            return COUNTRY_PRICE_COLUMN[p]
    return 'Inscripción Resto Países'

def pick_price_column_from_text(body_lower, from_number):
    for key, col in COUNTRY_WORD_TO_COL.items():
        if key in body_lower:
            return col
    return guess_country_price_column(from_number or '')

# ===== Intenciones con sinónimos =====
INTENTS = {
    'info': ['info','informacion','información','mas info','más info','detalles','ficha','sobre el curso'],
    'price': ['precio','costo','valor','arancel','inversion','inversión','inscrip','cuanto','cuánto','vale','pago'],
    'schedule': ['horario','horarios','hora','clase','clases','cronograma'],
    'modality': ['modalidad','online','virtual','en vivo','zoom','meet','videoconferencia'],
    'methodology': ['metodologia','metodología','metodo','método','como se cursa','cómo se cursa'],
    'start': ['inicio','empieza','empiezan','fecha de inicio'],
    'dates': ['fechas','calendario','cronograma'],
    'duration': ['duracion','duración','dura','carga horaria','horas'],
    'pdf': ['pdf','brochure','informativo','dossier','folleto'],
    'faq': ['faq','preguntas','dudas','consulta','general'],
    'enroll': ['me interesa','quiero inscribirme','inscribirme','como me inscribo','cómo me inscribo','quiero anotarme','quiero matricularme'],
    # grabadas (keywords directas por si no matchea FAQ)
    'recordings': ['grabada','grabadas','grabacion','grabación','grabaciones','repeticion','repetición','on demand','ondemand','ver despues','ver después','quedan grabadas'],
}

def classify_intents(body_lower):
    flags = {k: False for k in INTENTS.keys()}
    for k, words in INTENTS.items():
        if _has_any(body_lower, words):
            flags[k] = True
    if re.search(r'precio\s+[a-záéíóúñ ]{3,}', body_lower):
        flags['price'] = True
    return flags

# ===== FAQ por similitud =====
def _faq_parse_blocks(faq_text):
    """
    Devuelve lista de (utterances[], answer) según formato:
      Si preguntan: ... / ... / ...
      Respuesta: ...
    """
    if not faq_text:
        return []
    text = faq_text.strip()
    pattern = re.compile(r"Si preguntan:\s*(.+?)\s*Respuesta:\s*(.+?)(?=(?:\n\s*Si preguntan:)|\Z)", re.S | re.I)
    blocks = []
    for m in pattern.finditer(text):
        qpart = m.group(1).strip()
        ans = m.group(2).strip()
        utterances = [u.strip(" \t\r\n.?!¡¿") for u in re.split(r"\s*/\s*|\n", qpart) if u.strip()]
        if utterances and ans:
            blocks.append((utterances, ans))
    return blocks

def _faq_tokens(s):
    toks = [w for w in re.findall(r"[a-z0-9áéíóúñ]+", _fold(s)) if len(w) >= 3]
    toks = [_normalize_token(w) for w in toks]
    return toks

def answer_from_faq(row, user_text):
    faq_text = (row.get("FAQ") or "").strip()
    if not faq_text:
        return None
    blocks = _faq_parse_blocks(faq_text)
    if not blocks:
        return None

    qtok = set(_faq_tokens(user_text))
    if not qtok:
        return None

    best_score, best_ans = 0.0, None
    for utterances, ans in blocks:
        for u in utterances:
            utok = set(_faq_tokens(u))
            if not utok:
                continue
            overlap = len(qtok & utok)
            score = overlap / max(1, len(utok))
            if score > best_score:
                best_score, best_ans = score, ans

    # Umbral flexible (mejor con sinónimos normalizados)
    if best_score >= 0.5 or (best_score >= 0.34 and len(qtok) >= 2):
        # Respuesta más "amigable"
        return '😊 ' + best_ans
    return None

# ===== Respuestas =====
def course_card(row, from_number, body_lower=''):
    partes = []
    partes.append('Hola! Soy *{}* 🤖 de {}. Te paso los datos del curso:'.format(BOT_NAME, BRAND_NAME))

    titulo = row.get('Curso', '')
    if titulo:
        partes.append('🎓 *{}*'.format(titulo))

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

    modalidad = row.get('Modalidad', '') or row.get('modalidad', '')
    if modalidad:
        partes.append('🎥 *Modalidad:* {}'.format(modalidad))
    metodologia = row.get('Metodología', '') or row.get('Metodologia', '') or row.get('metodología', '')
    if metodologia:
        partes.append('🧩 *Metodología:* {}'.format(metodologia))

    price_col = pick_price_column_from_text(body_lower, from_number)
    precio = row.get(price_col, '') or row.get('Inscripción Resto Países', '')
    if precio:
        partes.append('💳 *Inscripción ({}):* {}'.format(price_col.replace('Inscripción ', ''), precio))

    pdf = row.get('Link PDF', '')
    if pdf:
        partes.append('📄 *PDF informativo:* {}'.format(pdf))

    partes.append('Si deseas *inscribirte*, dime "*me interesa*" y te conecto con un asesor humano 🤝')
    return '\n\n'.join(partes)

def answer_for_intents(row, intents, body_lower, from_number):
    answers = []

    if intents.get('price'):
        col = pick_price_column_from_text(body_lower, from_number)
        precio = row.get(col, '') or row.get('Inscripción Resto Países', '')
        if precio:
            answers.append('💳 *Inscripción ({}):* {}'.format(col.replace('Inscripción ', ''), precio))
        else:
            answers.append('💳 Para darte el valor exacto, indícame tu país (ej.: "precio Bolivia").')

    if intents.get('schedule'):
        val = (row.get('Horarios') or '').strip()
        if val: answers.append('🕒 *Horarios:* {}'.format(val))

    if intents.get('modality'):
        val = (row.get('Modalidad') or row.get('modalidad') or '').strip()
        if val: answers.append('🎥 *Modalidad:* {}'.format(val))
        else:   answers.append('🎥 Modalidad en vivo por videoconferencia (clases síncronas).')

    if intents.get('methodology'):
        val = (row.get('Metodología') or row.get('Metodologia') or row.get('metodología') or '').strip()
        if val: answers.append('🧩 *Metodología:* {}'.format(val))

    if intents.get('start'):
        val = (row.get('Fecha de Inicio') or '').strip()
        if val: answers.append('📅 *Inicio:* {}'.format(val))

    if intents.get('dates'):
        val = (row.get('Fechas de clases') or '').strip()
        if val: answers.append('🗓️ *Fechas de clases:* {}'.format(val))

    if intents.get('duration'):
        val = (row.get('Duración') or '').strip()
        if val: answers.append('⏳ *Duración:* {}'.format(val))

    if intents.get('pdf'):
        val = (row.get('Link PDF') or '').strip()
        if val: answers.append('📄 *PDF informativo:* {}'.format(val))

    # Si detectamos palabras de grabación explícitas, intenta FAQ primero
    if intents.get('recordings'):
        faq_ans = answer_from_faq(row, body_lower)
        if faq_ans:
            answers.append('▶️ ' + faq_ans)
        else:
            # fallback amistoso si el FAQ no tiene
            answers.append('▶️ Sí, solemos dejar las clases grabadas para que puedas verlas luego. (Confírmame si te interesa y te paso el detalle)')

    if intents.get('faq') and not answers:
        faq = (row.get('FAQ') or '').strip()
        if faq:
            answers.append('ℹ️ *FAQ:* {}'.format(faq))

    if intents.get('info') and not answers:
        answers.append(course_card(row, from_number, body_lower))

    # Si aún no hay respuesta específica, intenta FAQ por similitud (amigable)
    if not answers:
        faq_ans = answer_from_faq(row, body_lower)
        if faq_ans:
            return faq_ans

    return '\n\n'.join([a for a in answers if a])

# ===== Handoff =====
def detect_intent_enroll(body_lower):
    return _has_any(body_lower, INTENTS['enroll'])

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

# ===== Sesiones =====
def set_session_course(from_number, row):
    _sessions[from_number] = {'course': row, 't': time.time()}

def get_session_course(from_number):
    sess = _sessions.get(from_number)
    if not sess:
        return None
    if time.time() - sess.get('t', 0) > SESSION_TTL:
        _sessions.pop(from_number, None)
        return None
    return sess.get('course')

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

        # saludo / sin body: presentación + lista
        if not body:
            cursos = list_courses(rows)
            if cursos:
                msg = 'Hola 👋, soy *{}*, asistente de {}. Estoy para ayudarte con la información general.\n\n*Cursos:*\n- '.format(BOT_NAME, BRAND_NAME) + '\n- '.join(cursos) + '\n\nPuedes escribirme, por ejemplo: "info [nombre del curso]" o "precio [país] [curso]".'
            else:
                msg = 'Hola 👋, soy *{}*, asistente de {}. Aún no encuentro cursos publicados.'.format(BOT_NAME, BRAND_NAME)
            return build_twiml(msg)

        body_fold = _fold(body)

        # Inscripción
        if detect_intent_enroll(body_fold):
            row_for_forward = find_course(rows, body) or get_session_course(from_number)
            course_name = row_for_forward.get('Curso') if row_for_forward else None
            sent = send_admin_forward(from_number, body, course_name=course_name)
            human = 'Ya avisé a nuestro asesor ✅.' if sent else 'Te conecto con nuestro asesor.'
            reply = (
                '¡Genial! 🙌 {} En breve te escribirá.\n\n'
                'Si prefieres, contáctalo ahora:\n'
                '📲 {}  ({})'
            ).format(human, ADVISOR_E164, ADVISOR_WA_LINK)
            return build_twiml(reply)

        # Intentos + curso
        intents = classify_intents(body_fold)
        row = find_course(rows, body)

        if row:
            set_session_course(from_number, row)
        else:
            row = get_session_course(from_number)

        print('[MATCH]', 'row=' + (row.get('Curso','') if row else 'None'), 'intents=', intents)

        # Si hay curso
        if row:
            specific = answer_for_intents(row, intents, body_fold, from_number)
            if specific:
                return build_twiml('Aquí tienes:\n\n' + specific)

            # Si NO pidió "info" explícita y no hubo match, deriva al asesor
            if not intents.get('info'):
                msg = (
                    'Para esa consulta puntual, te conecto con nuestro asesor humano 😊\n\n'
                    '📲 {}  ({})\n\n'
                    'Si quieres, también puedo pasarte la ficha completa del curso. Escribe: "info".'
                ).format(ADVISOR_E164, ADVISOR_WA_LINK)
                return build_twiml(msg)

            # Pidió "info": ficha completa
            return build_twiml(course_card(row, from_number, body_fold))

        # Si NO hay curso y pidió algo específico (precio/horarios/etc.) -> derivar
        if any(v for k, v in intents.items() if k not in ['info','faq']) or intents.get('info') or intents.get('faq'):
            msg = (
                'Para darte esa info al toque, te conecto con nuestro asesor humano. 😊\n\n'
                '📲 {}  ({})\n\n'
                'Si prefieres seguir por aquí, dime el *nombre del curso*.'
            ).format(ADVISOR_E164, ADVISOR_WA_LINK)
            return build_twiml(msg)

        # Caso general sin curso ni intención clara: listar cursos
        cursos = list_courses(rows)
        if cursos:
            return build_twiml('Para ayudarte mejor, dime el *nombre del curso*.\n\n*Cursos:*\n- ' + '\n- '.join(cursos))
        return build_twiml('Por ahora no encuentro cursos publicados en {}.'.format(BRAND_NAME))

    except Exception as e:
        print('[ERROR /whatsapp]', e)
        return build_twiml('Ups, tuve un detalle al procesar tu mensaje. ¿Puedes intentar de nuevo? 🙏')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
