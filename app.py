"""Servidor NOCRY: formulário privado via Discord Webhook."""
import os, re, time, json
from threading import Lock
from collections import defaultdict, deque
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import Flask, request, jsonify, send_from_directory

BASE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 8192
hits = defaultdict(deque)
CLASSES = {'Lutador','Mecânico','Arqueira','Pike','Assassina','Cavaleiro','Atalanta','Sacerdotisa','Mago','Xamã','Outra'}

def clean(value, limit):
    return str(value or '').strip()[:limit].replace('@', '＠').replace('`', "'")

_boss_cache = {'at': 0.0, 'data': None}
_boss_lock = Lock()

@app.get('/api/bosses')
def boss_api():
    """Consulta pública, com cache, sem expor credenciais ou sobrecarregar a origem."""
    with _boss_lock:
        now = time.monotonic()
        if _boss_cache['data'] is not None and now - _boss_cache['at'] < 45:
            return jsonify(_boss_cache['data'])
        try:
            req = Request('https://pweb.world/api/map-spawns', headers={
                'User-Agent': 'NOCRY-Guild-Website/1.0', 'Accept': 'application/json'})
            with urlopen(req, timeout=9) as resp:
                data = json.loads(resp.read(1024 * 1024).decode('utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('bosses'), list):
                raise ValueError('Formato de dados inesperado')
            _boss_cache.update(at=now, data=data)
            return jsonify(data)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            app.logger.warning('API de bosses indisponível', exc_info=True)
            if _boss_cache['data'] is not None:
                return jsonify(_boss_cache['data'])
            return jsonify(error='API de bosses indisponível no momento'), 502

@app.get('/')
def home():
    return send_from_directory(BASE, 'index.html')

@app.get('/assets/<path:name>')
def assets(name):
    return send_from_directory(BASE / 'assets', name)

@app.get('/ativos/<path:name>')
def ativos(name):
    return send_from_directory(BASE / 'ativos', name)

@app.post('/api/inscricao')
def inscricao():
    webhook = os.environ.get('DISCORD_RECRUTAMENTO_WEBHOOK', '').strip()
    if not webhook.startswith('https://discord.com/api/webhooks/') and not webhook.startswith('https://discordapp.com/api/webhooks/'):
        return jsonify(error='Recrutamento ainda não configurado. Avise a equipe NOCRY.'), 503
    ip = request.remote_addr or 'unknown'
    now = time.monotonic()
    recent = hits[ip]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    if len(recent) >= 3:
        return jsonify(error='Limite de inscrições atingido. Tente novamente mais tarde.'), 429
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Dados inválidos.'), 400
    if data.get('website'):
        return jsonify(ok=True), 200
    if data.get('consentimento') != 'on':
        return jsonify(error='Confirme a autorização para contato.'), 400
    nome = clean(data.get('personagem'), 50)
    classe = clean(data.get('classe'), 30)
    nivel = str(data.get('nivel', '')).strip()
    discord = clean(data.get('discord'), 60)
    whatsapp = re.sub(r'\D', '', str(data.get('whatsapp', '')))
    if not (2 <= len(nome) <= 50 and 2 <= len(discord) <= 60 and classe in CLASSES and nivel.isdecimal() and 1 <= int(nivel) <= 999 and 10 <= len(whatsapp) <= 13):
        return jsonify(error='Confira personagem, classe, nível, Discord e WhatsApp com DDD.'), 400
    import json
    payload = {
        'username': 'Recrutamento NOCRY',
        'allowed_mentions': {'parse': []},
        'embeds': [{
            'title': '⚔️ NOVO AVENTUREIRO · RECRUTAMENTO NOCRY',
            'description': 'Uma nova solicitação chegou ao clã! A liderança pode analisar os dados abaixo e entrar em contato com o jogador.',
            'color': 16081435,
            'fields': [
                {'name': '🎮 Personagem', 'value': nome, 'inline': True},
                {'name': '⚔️ Classe', 'value': classe, 'inline': True},
                {'name': '⭐ Nível', 'value': nivel, 'inline': True},
                {'name': '💬 Discord', 'value': discord, 'inline': True},
                {'name': '📱 WhatsApp', 'value': '+' + whatsapp if len(whatsapp) > 11 else whatsapp, 'inline': True},
                {'name': '📋 Situação', 'value': 'Aguardando contato da liderança', 'inline': False},
            ],
            'footer': {'text': 'NOCRY • Recrutamento | Priston Tale'}
        }]
    }
    try:
        req = Request(webhook, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'Content-Type':'application/json', 'User-Agent':'NOCRY-Site/1.0'}, method='POST')
        with urlopen(req, timeout=10) as response:
            if response.status not in (200, 204):
                raise ValueError('Discord não confirmou o envio')
    except (HTTPError, URLError, TimeoutError, ValueError):
        app.logger.exception('Falha no envio ao Discord')
        return jsonify(error='Discord indisponível no momento. Tente novamente.'), 502
    recent.append(now)
    return jsonify(ok=True), 200

# Portal persistente de personagens
from portal import setup as setup_portal
setup_portal(app)

# Mercado interno do clã (somente no portal de testes)
from market import market as market_blueprint
from portal import db as portal_db
app.register_blueprint(market_blueprint)
with app.app_context():
    portal_db.create_all()
    from sqlalchemy import text
    # Atualização aditiva, preservando anúncios existentes.
    for table in ('market_requests', 'market_offers'):
        with portal_db.engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS spec VARCHAR(20)'))
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1'))
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS fulfilled INTEGER NOT NULL DEFAULT 0'))

# Limpeza única dos registros de teste na primeira publicação pública.
# Preserva usuários, personagens, aprovações e demais dados do portal.
with app.app_context():
    from sqlalchemy import text as _market_sql
    with portal_db.engine.begin() as _conn:
        _conn.execute(_market_sql('CREATE TABLE IF NOT EXISTS market_launch_migrations (key VARCHAR(100) PRIMARY KEY)'))
        _first = _conn.execute(_market_sql("INSERT INTO market_launch_migrations (key) VALUES ('public_market_launch_20261029') ON CONFLICT (key) DO NOTHING RETURNING key")).scalar()
        if _first:
            for _table in ('market_matches', 'market_notices', 'market_offers', 'market_requests'):
                _conn.execute(_market_sql(f'DELETE FROM {_table}'))

# Atualização pontual do ranking fornecido pelos membros em 29/10/2026.
# Apenas nível/EXP dos personagens NOCRY existentes; preserva owner_id e aprovações.
with app.app_context():
    from sqlalchemy import text as _rank_sql
    _rank_rows = [["Agressor[AS]",84,39.4],["BilGun",84,37],["GEPETTO",84,36.5],["Opressor",84,35.3],["Mago",84,33.7],["KUNDO",84,30.4],["MaTchoLa",83,10.7],["zSilentEdge",83,8.4],["Snoopy",83,8.3],["BIGMAR",83,3.6],["Afoice",83,1.9],["Leitinho",82,98.1],["BuRgUeZiiNhA",82,95.6],["Datena",82,94.7],["RealTRAP",82,89.8],["TheStrangler",82,18.4],["VENLAFAXINA",82,9.4],["Salluri",81,87.3],["DanielVorcaro",81,85.3],["Maiorali",81,48.5],["isackinaks",81,30.8]]
    with portal_db.engine.begin() as _conn:
        _conn.execute(_rank_sql('CREATE TABLE IF NOT EXISTS market_launch_migrations (key VARCHAR(100) PRIMARY KEY)'))
        _apply_rank = _conn.execute(_rank_sql("INSERT INTO market_launch_migrations (key) VALUES ('nocry_rank_20261029_user_snapshot') ON CONFLICT (key) DO NOTHING RETURNING key")).scalar()
        if _apply_rank:
            for _name, _level, _exp in _rank_rows:
                _conn.execute(_rank_sql('UPDATE character SET level=:level, percent=:exp, updated_at=NOW() WHERE lower(name)=lower(:name) AND tag=:tag AND (level < :level OR (level = :level AND percent < :exp))'), {'name':_name,'level':_level,'exp':_exp,'tag':'NOCRY'})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT','5000')))
