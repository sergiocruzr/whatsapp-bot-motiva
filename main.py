# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, time, csv, re, requests, unicodedata
from io import StringIO
from xml.sax.saxutils import escape as xml_escape

app = Flask(__name__)

# ========= Config =========
BRAND_NAME = os.getenv('BRAND_NAME', 'Motiva Educación')
BOT_NAME = os.getenv('BOT_NAME', 'Moti')
SHEET_CSV_URL = os.getenv('SHEET_CSV_URL')

# Twilio (solo para notificar al asesor en handoff)
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

# Asesor / derivación
ADVISOR_E164 = os.getenv('ADVISOR_E164', '+59162723944')
ADVISOR_WA_LINK = 'https://wa.me/{}'.format(ADVISOR_E164.replace('+', ''))
ADMIN_FORWARD_NUMBER = os.getenv('ADMIN_FORWARD_NUMBER', 'whatsapp:{}'.format(ADVISOR_E164))

# ========= Cache & sesión =========
_cache = {'rows': [], 't': 0.0, 'alias_idx': {}}
CACHE_SECONDS = 300
_sessions = {}  # { from_number: {'course': row, 't': epoch} }
SESSION_TTL = 60 * 60

# ========= Encabezados esperados =========
EXPECTED_HEADERS = [
    'Curso','Texto Principal','Link PDF','Fecha de Inicio','Fechas de clases','Duración','Horarios',
    'Inscripción Argentina','Inscripción Bolivia','Inscripción Chile','Inscripción Colombia',
    'Inscripción Costa Rica','Inscripción México','Inscripción Paraguay','Inscripción Perú',
    'Inscripción Uruguay','Inscripción Resto Países','FAQ'
]
HEADER_SYNONYMS = {
    'Valor Inscripción Uruguay': 'Inscripción Uruguay',
}

# ========= Precio por país =========
PREFIX2COL = {
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

# ========= Utils =========
def _fold(s):
    s = (s or '').lower()
    nf = unicodedata.normalize('NFD', s)
    return ''.join(c for c in nf if not unicodedata.combining(c))

def build_twiml(message):
    xml = "<?xml version='1.0' encoding='UTF-8'?><Response><Message>{}</Message></Response>".format(xml_escape(message))
    return Response(xml, mimetype='application/xml')

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
        raise ValueError('Faltan encabezados: {}. Recibido: {}'.format(missing, raw_headers))

    rows = []
    for row in reader:
        clean = {}
        for k, v in row.items():
            nk = (k or '').strip().lstrip('\ufeff')
            nk = HEADER_SYNONYMS.get(nk, nk)
            clean[nk] = (v or '').strip()
        if clean.get('Curso'):
            rows.append(clean)

    # Alias (columna opcional "Alias")
    alias_idx = {}
    for r in rows:
        alias_cell = (r.get('Alias') or '').strip()
        if not alias_cell:
            continue
        for a in re.split(r'[\n,;|/]+', alias_cell):
            a = a.strip()
            if a:
                alias_idx[_fold(a)] = r

    _cache['rows'] = rows
    _cache['t'] = now
    _cache['alias_idx'] = alias_idx
    return rows

def list_courses(rows):
    return [r.get('Curso', '').strip() for r in rows if r.get('Curso')]

def _best_row_by_query(rows, q_fold):
    # a) user text dentro del nombre
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and q_fold in _fold(name):
            return r
    # b) nombre dentro de user text
    for r in rows:
        name = (r.get('Curso') or '').strip()
        if name and _fold(name) in q_fold:
            return r
    # c) tokens (>=3 chars) con intersección
    words = set([w for w in re.findall(r'[a-z0-9áéíóúñ]+', q_fold) if len(w) >= 3 and w not in ('curso','cursos')])
    best, best_row = 0, None
    for r in rows:
        name = (r.get('Curso') or '')
        name_tokens = set([_fold(w) for w in re.findall(r'[a-z0-9áéíóúñ]+', name) if len(w) >= 3 and w not in ('curso','cursos')])
        score = len(words & name_tokens)
        if score > best:
            best, best_row = score, r
    return best_row if best >= 1 else None

def find_course(rows, user_text):
    q = _fold(user_text)
    # 1) alias
    for a, r in (_cache.get('alias_idx') or {}).items():
        if a and a in q:
            return r
    # 2) keyword + resto del texto
    m = re.search(r'(?:info|informacion|información|precio|horarios?|pdf|modalidad|metodolog(?:ía|ia))\s+(.+)$', q)
    if m:
        cand = m.group(1).strip()
        r = _best_row_by_query(rows, cand)
        if r: return r
    # 3) fallback
    return _best_row_by_query(rows, q)

def guess_price_col_from_number(from_number):
    num = (from_number or '').replace('whatsapp:', '').replace('+', '')
    for p in sorted(PREFIX2COL.keys(), key=lambda p: -len(p)):
        if num.startswith(p):
            return PREFIX2COL[p]
    return 'Inscripción Resto Países'

def pick_price_col(body_lower, from_number):
    for key, col in COUNTRY_WORD_TO_COL.items():
        if key in body_lower:
            return col
    return guess_price_col_from_number(from_number or '')

# ========= Intents =========
INTENTS = {
    'info': ['info','informacion','información','mas info','más info','detalles','ficha','sobre el curso'],
    'price': ['precio','costo','valor','arancel','inversion','inversión','inscrip','cuanto','cuánto','vale','pago'],
    'schedule': ['horario','horarios','hora','cronograma'],
    'modality': ['modalidad','online','virtual','en vivo','zoom','meet','videoconferencia'],
    'methodology': ['metodologia','metodología','metodo','método','como se cursa','cómo se cursa'],
    'start': ['inicio','empieza','empiezan','fecha de inicio'],
    'dates': ['fechas','calendario','cronograma'],
    'duration': ['duracion','duración','dura','carga horaria','horas'],
    'pdf': ['pdf','brochure','informativo','dossier','folleto'],
    'faq': ['faq','preguntas','dudas','consulta','general'],
    'enroll': ['me interesa','quiero inscribirme','inscribirme','como me inscribo','cómo me inscribo','quiero anotarme','quiero matricularme'],
    'recordings': ['grabada','grabadas','grabacion','grabación','grabaciones','repeticion','repetición','on demand','ondemand','ver despues','ver después','quedan grabadas'],
}
GREETINGS = ['hola','buenas','buenos dias','buenos días','buenas tardes','buenas noches','hey','que tal','qué tal']

def _has_any(text, words):
    return any(w in text for w in words)

def classify_intents(body_lower):
    flags = {k: False for k in INTENTS.keys()}
    for k, words in INTENTS.items():
        if _has_any(body_lower, words):
            flags[k] = True
    if re.search(r'precio\s+[a-záéíóúñ ]{3,}', body_lower):
        flags['price'] = True
    return flags

# ========= FAQ (simple y estable) =========
STOP = set("de del la las el los un una unos unas y o u a ante bajo con contra desde durante en entre hacia hasta mediante para por segun según sin sobre tras que como donde cuando cuanto ya no si hay".split())

def _faq_tokens(s):
    f = _fold(s)
    toks = [t for t in re.findall(r'[a-z0-9]+', f) if len(t) >= 3 and t not in STOP]
    return toks

def _parse_faq_blocks(faq_text):
    if not faq_text:
        return []
    text = faq_text.strip()
    pat = re.compile(r"Si preguntan:\s*(.+?)\s*Respuesta:\s*(.+?)(?=(?:\n\s*Si preguntan:)|\Z)", re.S | re.I)
    blocks = []
    for m in pat.finditer(text):
        qpart = m.group(1).strip()
        ans = m.group(2).strip()
        utterances = [u.strip(" \t\r\n.?!¡¿") for u in re.split(r"\s*/\s*|\n", qpart) if u.strip()]
        if utterances and ans:
            blocks.append((utterances, ans))
    return blocks

def answer_from_faq(row, user_text, threshold=0.45):
    faq = (row.get('FAQ') or '').strip()
    if not faq:
        return None
    blocks = _parse_faq_blocks(faq)
    if not blocks:
        return None
    qtok = set(_faq_tokens(user_text))
    if not qtok:
        return None
    best, best_ans = 0.0, None
    for utterances, ans in blocks:
        for u in utterances:
            utok = set(_faq_tokens(u))
            if not utok:
                continue
            overlap = len(qtok & utok)
            score = overlap / max(1, len(utok))
            if score > best:
                best, best_ans = score, ans
    if best >= threshold or (best >= 0.33 and len(qtok) >= 2):
        return best_ans
    return None

# ========= Respuestas =========
def course_card(row, from_number, body_lower=''):
    parts = []
    parts.append('Hola, gracias por contactarnos 🙌 Soy *{}* (asistente de {}).'.format(BOT_NAME, BRAND_NAME))
    parts.append('Te paso la información del curso:')

    titulo = row.get('Curso', '')
    if titulo: parts.append('🎓 *{}*'.format(titulo))

    txt = row.get('Texto Principal', '')
    if txt: parts.append(txt)

    fi = row.get('Fecha de Inicio', '')
    if fi: parts.append('📅 *Inicio:* {}'.format(fi))

    fechas = row.get('Fechas de clases', '')
    if fechas: parts.append('🗓️ *Fechas de clases:* {}'.format(fechas))

    dur = row.get('Duración', '')
    if dur: parts.append('⏳ *Duración:* {}'.format(dur))

    hor = row.get('Horarios', '')
    if hor: parts.append('🕒 *Horarios:* {}'.format(hor))

    modalidad = row.get('Modalidad', '') or row.get('modalidad', '')
    if modalidad: parts.append('🎥 *Modalidad:* {}'.format(modalidad))
    metodologia = row.get('Metodología', '') or row.get('Metodologia', '') or row.get('metodología', '')
    if metodologia: parts.append('🧩 *Metodología:* {}'.format(metodologia))

    price_col = pick_price_col(body_lower, from_number)
    precio = row.get(price_col, '') or row.get('Inscripción Resto Países', '')
    if precio:
        parts.append('💳 *Inscripción ({}):* {}'.format(price_col.replace('Inscripción ', ''), precio))

    pdf = row.get('Link PDF', '')
    if pdf: parts.append('📄 *PDF informativo:* {}'.format(pdf))

    parts.append('Si deseas *inscribirte*, dime "*me interesa*" y te conecto con un asesor humano 🤝')
    return '\n\n'.join(parts)

def answer_for_intents(row, intents, body_lower, from_number):
    answers = []

    if intents.get('price'):
        col = pick_price_col(body_lower, from_number)
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

    # FAQ (solo si aún no contestamos algo específico)
    if not answers:
        faq_ans = answer_from_faq(row, body_lower)
        if faq_ans:
            answers.append('ℹ️ ' + faq_ans)

    # Si sigue sin nada y pidió "info" → ficha completa
    if intents.get('info') and not answers:
        answers.append(course_card(row, from_number, body_lower))

    return '\n\n'.join([a for a in answers if a])

# ========= Handoff =========
def detect_enroll(body_lower):
    keys = ['me interesa','quiero inscribirme','inscribirme','como me inscribo','cómo me inscribo','quiero anotarme','quiero matricularme']
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

def set_session_course(from_number, row):
    _sessions[from_number] = {'course': row, 't': time.time()}

def get_session_course(from_number):
    s = _sessions.get(from_number)
    if not s: return None
    if time.time() - s.get('t', 0) > SESSION_TTL:
        _sessions.pop(from_number, None)
        return None
    return s.get('course')

# ========= Rutas =========
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

# Acepta GET y POST; usa request.values
@app.route('/whatsapp', methods=['GET', 'POST'])
def whatsapp_webhook():
    try:
        from_number = request.values.get('From', '')
        body = (request.values.get('Body', '') or '').strip()
        rows = fetch_sheet_rows()
        body_l = _fold(body)

        # 0) saludo o vacío → saludo + lista cursos
        if not body or body_l in GREETINGS or any(body_l.startswith(g) for g in GREETINGS):
            cursos = list_courses(rows)
            if cursos:
                msg = (
                    'Hola, gracias por contactarnos 🙌 Soy *{}*.\n'
                    'Indícame el *nombre del curso* del que deseas información y te paso los detalles.\n\n'
                    '*Cursos:*\n- '.format(BOT_NAME)
                ) + '\n- '.join(cursos)
            else:
                msg = 'Hola, gracias por contactarnos 🙌 Soy *{}*. Aún no encuentro cursos publicados.'.format(BOT_NAME)
            return build_twiml(msg)

        # 1) interés de inscripción
        if detect_enroll(body_l):
            row_for_forward = find_course(rows, body) or get_session_course(from_number)
            cname = row_for_forward.get('Curso') if row_for_forward else None
            sent = send_admin_forward(from_number, body, course_name=cname)
            human = 'Ya avisé a nuestro asesor ✅.' if sent else 'Te conecto con nuestro asesor.'
            reply = (
                '¡Genial! 🙌 {} En breve te escribirá.\n\n'
                'Si prefieres, contáctalo ahora:\n'
                '📲 {}  ({})'
            ).format(human, ADVISOR_E164, ADVISOR_WA_LINK)
            return build_twiml(reply)

        # 2) detectar curso (mensaje actual) o usar sesión previa
        row = find_course(rows, body)
        if row:
            set_session_course(from_number, row)
        else:
            row = get_session_course(from_number)

        # 3) si hay curso, responde intención o ficha
        if row:
            intents = classify_intents(body_l)
            specific = answer_for_intents(row, intents, body_l, from_number)
            if specific:
                return build_twiml('Aquí tienes:\n\n' + specific)
            # si no hubo intención clara, manda ficha completa (mejor que quedarse en blanco)
            return build_twiml(course_card(row, from_number, body_l))

        # 4) si NO hay curso y piden algo específico → pedir curso o derivar
        intents = classify_intents(body_l)
        if any(v for k, v in intents.items() if k not in ['info','faq']) or intents.get('info') or intents.get('faq'):
            msg = (
                'Para darte esa info al toque, indícame primero el *nombre del curso*. '
                'O si prefieres, te conecto con nuestro asesor humano 😊\n\n'
                '📲 {}  ({})'
            ).format(ADVISOR_E164, ADVISOR_WA_LINK)
            return build_twiml(msg)

        # 5) fallback: pedir curso
        cursos = list_courses(rows)
        if cursos:
            return build_twiml('Para ayudarte mejor, dime el *nombre del curso*.\n\n*Cursos:*\n- ' + '\n- '.join(cursos))
        return build_twiml('Por ahora no encuentro cursos publicados en {}.'.format(BRAND_NAME))

    except Exception as e:
        print('[ERROR /whatsapp]', e)
        return build_twiml('Ups, tuve un detalle al procesar tu mensaje. ¿Puedes intentar de nuevo? 🙏')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
