"""NOCRY portal: persistent accounts, character ownership approvals, Discord OAuth and reminders."""
import os, re, json, secrets, smtplib, ssl, time, hashlib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from flask import Blueprint, request, jsonify, session, redirect, current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

portal = Blueprint('portal', __name__)
db = SQLAlchemy()
TAGS = ('NOCRY','N0CRY','NOWCRY')
CLASSES = ('Lutador','Mecânico','Arqueira','Pike','Assassina','Cavaleiro','Atalanta','Sacerdotisa','Mago','Xamã','Outra')

def utcnow(): return datetime.now(timezone.utc)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False, unique=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    discord_id = db.Column(db.String(32), unique=True, nullable=True)
    discord_name = db.Column(db.String(80), nullable=True)
    reminders = db.Column(db.Boolean, default=False, nullable=False)
    last_reminder = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

class Character(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    name_key = db.Column(db.String(50), nullable=False, unique=True)
    klass = db.Column(db.String(25), nullable=False)
    level = db.Column(db.Integer, nullable=False)
    percent = db.Column(db.Float, nullable=False, default=0)
    tag = db.Column(db.String(10), nullable=False, default='NOCRY')
    role = db.Column(db.String(30), nullable=False, default='')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Claim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(12), nullable=False, default='pending')
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    __table_args__ = (db.UniqueConstraint('character_id','user_id', name='uq_claim_character_user'),)

class LoginAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

def setup(app):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///' + str(Path(app.root_path) / 'nocry_dev.db')).replace('postgres://','postgresql://',1)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', '')
    if not app.config['SECRET_KEY']:
        raise RuntimeError('Configure SECRET_KEY no Render antes de iniciar o portal.')
    app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV')!='development',PERMANENT_SESSION_LIFETIME=timedelta(days=7),MAX_CONTENT_LENGTH=32768)
    db.init_app(app)
    app.register_blueprint(portal)
    with app.app_context():
        db.create_all()
        seed_file=Path(app.root_path)/'initial_members.json'
        if seed_file.exists():
            for row in json.loads(seed_file.read_text(encoding='utf-8')):
                key=row['nome'].strip().casefold()
                if not db.session.query(Character.id).filter_by(name_key=key).first():
                    db.session.add(Character(name=row['nome'],name_key=key,klass=row['classe'],level=int(row['nivel']),percent=0,tag=row.get('tag','NOCRY'),role=row.get('cargo','')))
            db.session.commit()

def error(msg,code=400): return jsonify(error=msg),code

def current_user():
    uid=session.get('uid')
    return db.session.get(User,uid) if isinstance(uid,int) else None

def auth(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u: return error('Faça login.',401)
        if not u.verified: return error('Confirme seu e-mail.',403)
        return fn(u,*a,**kw)
    return wrapper

def admin(fn):
    @auth
    @wraps(fn)
    def wrapper(u,*a,**kw):
        if not u.is_admin: return error('Acesso restrito à liderança.',403)
        return fn(u,*a,**kw)
    return wrapper

@portal.before_app_request
def csrf_check():
    if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE') and request.endpoint != 'inscricao':
        if request.headers.get('X-CSRF-Token') != session.get('csrf') or not session.get('csrf'):
            return error('Sessão expirada. Recarregue a página.',403)

def body(): return request.get_json(silent=True) if isinstance(request.get_json(silent=True),dict) else {}

def serializer(): return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='nocry-email-verify-v1')

def mail_verification(user):
    host=os.environ.get('SMTP_HOST','')
    if not host: return False
    token=serializer().dumps({'id':user.id,'email':user.email})
    base=os.environ.get('SITE_URL','https://www.nocry.com.br').rstrip('/')
    msg=EmailMessage()
    msg['Subject']='NOCRY — confirme seu e-mail'
    msg['From']=os.environ['SMTP_FROM']
    msg['To']=user.email
    msg.set_content(f'Confirme seu cadastro no NOCRY (link válido por 24 horas):\n{base}/api/auth/verify?token={token}\n\nSe não foi você, ignore.')
    with smtplib.SMTP(host,int(os.environ.get('SMTP_PORT','587')),timeout=12) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)
    return True

@portal.get('/api/auth/session')
def whoami():
    if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
    u=current_user()
    return jsonify(csrf=session['csrf'],user={'id':u.id,'username':u.username,'admin':u.is_admin,'verified':u.verified,'discord_connected':bool(u.discord_id),'discord_name':u.discord_name,'reminders':u.reminders} if u else None)

@portal.post('/api/auth/register')
def register():
    d=body(); name=str(d.get('username','')).strip(); email=str(d.get('email','')).strip().lower(); pw=str(d.get('password',''))
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',name): return error('Login: 3 a 40 caracteres, letras, números, _, . ou -.')
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(email)>254: return error('E-mail inválido.')
    if len(pw)<10 or len(pw)>128: return error('Senha deve ter entre 10 e 128 caracteres.')
    if not os.environ.get('SMTP_HOST'): return error('Cadastro indisponível: envio de e-mail não configurado.',503)
    if User.query.filter((func.lower(User.username)==name.lower()) | (User.email==email)).first(): return error('Login ou e-mail já utilizado.',409)
    u=User(username=name,email=email,password_hash=generate_password_hash(pw))
    db.session.add(u)
    try: db.session.commit()
    except IntegrityError: db.session.rollback();return error('Login ou e-mail já utilizado.',409)
    try: mail_verification(u)
    except Exception:
        current_app.logger.exception('Falha no e-mail de confirmação')
        return error('Conta criada, mas não foi possível enviar o e-mail. Contate a liderança.',503)
    return jsonify(ok=True,message='Conta criada! Verifique seu e-mail.'),201

@portal.post('/api/auth/resend-verification')
def resend_verification():
    """Reenvia confirmação sem revelar se um endereço está cadastrado."""
    d=body()
    email=str(d.get('email','')).strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or len(email)>254:
        return error('Informe um e-mail válido.')
    ip=request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[0].strip()[:45]
    # Limite por IP e endereço, sem exigir login de uma conta ainda não confirmada.
    bucket='verify:'+hashlib.sha256((ip+'|'+email).encode()).hexdigest()[:40]
    cutoff=utcnow()-timedelta(minutes=15)
    db.session.query(LoginAttempt).filter(LoginAttempt.created_at<cutoff).delete()
    if LoginAttempt.query.filter_by(key=bucket).count()>=3:
        db.session.commit()
        return error('Muitas solicitações. Aguarde 15 minutos antes de tentar novamente.',429)
    db.session.add(LoginAttempt(key=bucket))
    db.session.commit()
    u=User.query.filter_by(email=email).first()
    if u and not u.verified:
        try:
            if not mail_verification(u):
                return error('Envio de e-mail não configurado. Contate a liderança.',503)
        except Exception:
            current_app.logger.exception('Falha ao reenviar confirmação')
            return error('Não foi possível enviar o e-mail agora. Tente novamente mais tarde.',503)
    return jsonify(ok=True,message='Se existir uma conta pendente com esse e-mail, enviaremos um novo link de confirmação.')

@portal.get('/api/auth/verify')
def verify():
    try: d=serializer().loads(request.args.get('token',''),max_age=86400)
    except (BadSignature,SignatureExpired): return 'Link inválido ou expirado.',400
    u=db.session.get(User,d.get('id'))
    if not u or u.email!=d.get('email'): return 'Conta não encontrada.',404
    u.verified=True
    admin_email=os.environ.get('NOCRY_ADMIN_EMAIL','').lower().strip()
    if admin_email and u.email==admin_email: u.is_admin=True
    db.session.commit()
    return redirect('/?conta=confirmada#portal')

@portal.post('/api/auth/login')
def login():
    d=body(); key=str(d.get('login','')).strip().lower(); pw=str(d.get('password',''))
    ip=request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[0].strip()[:45]
    bucket=hashlib.sha256((ip+'|'+key).encode()).hexdigest()[:40]
    cutoff=utcnow()-timedelta(minutes=15)
    db.session.query(LoginAttempt).filter(LoginAttempt.created_at<cutoff).delete()
    if LoginAttempt.query.filter_by(key=bucket).count()>=8: db.session.commit();return error('Muitas tentativas. Aguarde 15 minutos.',429)
    u=User.query.filter((func.lower(User.username)==key)|(User.email==key)).first()
    if not u or not check_password_hash(u.password_hash,pw):
        db.session.add(LoginAttempt(key=bucket));db.session.commit();return error('Credenciais inválidas.',401)
    if not u.verified: return error('Confirme seu e-mail antes de entrar.',403)
    db.session.query(LoginAttempt).filter_by(key=bucket).delete()
    db.session.commit()
    session.clear();session['uid']=u.id;session['csrf']=secrets.token_urlsafe(32);session.permanent=True
    return jsonify(ok=True,admin=u.is_admin)

@portal.post('/api/auth/logout')
def logout(): session.clear();return jsonify(ok=True)

def public_char(c): return dict(id=c.id,nome=c.name,classe=c.klass,nivel=c.level,percent=round(c.percent,2),tag=c.tag,cargo=c.role)

@portal.get('/api/characters')
def characters():
    tag=request.args.get('tag','').upper()
    if tag and tag not in TAGS: return error('TAG inválida.')
    q=Character.query
    if tag:q=q.filter_by(tag=tag)
    return jsonify(characters=[public_char(c) for c in q.order_by(Character.level.desc(),Character.name).all()])

@portal.get('/api/me/characters')
@auth
def my_characters(u):
    chars=Character.query.filter_by(owner_id=u.id).order_by(Character.name).all()
    claims=Claim.query.filter_by(user_id=u.id).all()
    return jsonify(characters=[public_char(c) for c in chars],claims=[{'id':x.id,'character_id':x.character_id,'status':x.status} for x in claims])

def validate_char(d):
    name=str(d.get('nome','')).strip(); klass=d.get('classe'); tag=d.get('tag')
    try: level=int(d.get('nivel'));percent=float(d.get('percent'))
    except (TypeError,ValueError): raise ValueError('Nível e porcentagem inválidos.')
    if not re.fullmatch(r'[\w.\[\]\-]{2,50}',name,re.UNICODE): raise ValueError('Nome inválido (2 a 50 caracteres).')
    if klass not in CLASSES or tag not in TAGS: raise ValueError('Classe ou TAG inválida.')
    if not (1<=level<=999 and 0<=percent<=100): raise ValueError('Nível ou porcentagem fora do intervalo.')
    return name,klass,tag,level,percent

@portal.post('/api/me/characters')
@auth
def add_char(u):
    try: name,klass,tag,level,percent=validate_char(body())
    except ValueError as e:return error(str(e))
    key=name.casefold();existing=Character.query.filter_by(name_key=key).first()
    if existing:
        if existing.owner_id==u.id: return error('Você já possui este personagem. Use Atualizar.',409)
        if existing.owner_id is not None:return error('Personagem já vinculado. Peça à liderança uma transferência.',409)
        claim=Claim.query.filter_by(character_id=existing.id,user_id=u.id).first()
        if claim:
            if claim.status=='pending':return error('Solicitação já enviada; aguarde aprovação.',409)
            claim.status='pending'
        else: db.session.add(Claim(character_id=existing.id,user_id=u.id))
        db.session.commit()
        return jsonify(ok=True,pending=True,message='Personagem existente. Vinculação enviada à liderança.'),202
    c=Character(name=name,name_key=key,klass=klass,tag=tag,level=level,percent=percent,owner_id=u.id)
    db.session.add(c)
    try: db.session.commit()
    except IntegrityError:db.session.rollback();return error('Este nome já foi cadastrado.',409)
    return jsonify(ok=True,character=public_char(c)),201

@portal.put('/api/me/characters/<int:cid>')
@auth
def edit_char(u,cid):
    c=db.session.get(Character,cid)
    if not c or c.owner_id!=u.id:return error('Personagem não encontrado ou sem permissão.',404)
    try: name,klass,tag,level,percent=validate_char(body())
    except ValueError as e:return error(str(e))
    if name.casefold()!=c.name_key:return error('O nome não pode ser alterado. Cadastre um novo personagem.',400)
    c.klass=klass;c.tag=tag;c.level=level;c.percent=percent
    db.session.commit();return jsonify(ok=True,character=public_char(c))

@portal.delete('/api/me/characters/<int:cid>')
@auth
def delete_char(u,cid):
    c=db.session.get(Character,cid)
    if not c or c.owner_id!=u.id:return error('Personagem não encontrado.',404)
    if c.role:return error('Personagem da liderança: solicite alteração ao administrador.',403)
    db.session.delete(c);db.session.commit();return jsonify(ok=True)

@portal.get('/api/admin/claims')
@admin
def pending_claims(u):
    rows=db.session.query(Claim,Character,User).join(Character,Claim.character_id==Character.id).join(User,Claim.user_id==User.id).filter(Claim.status=='pending').all()
    return jsonify(claims=[dict(id=cl.id,character=ch.name,tag=ch.tag,username=usr.username,created_at=cl.created_at.isoformat()) for cl,ch,usr in rows])

@portal.post('/api/admin/claims/<int:claim_id>/decision')
@admin
def decision(u,claim_id):
    cl=db.session.get(Claim,claim_id);choice=body().get('decision')
    if not cl or cl.status!='pending' or choice not in ('approve','reject'):return error('Solicitação ou decisão inválida.')
    c=db.session.get(Character,cl.character_id)
    if choice=='approve':
        if c.owner_id is not None:return error('Personagem já vinculado.',409)
        c.owner_id=cl.user_id
        for other in Claim.query.filter_by(character_id=c.id,status='pending').all():other.status='approved' if other.id==cl.id else 'rejected'
    else:cl.status='rejected'
    db.session.commit();return jsonify(ok=True)

@portal.post('/api/me/reminders')
@auth
def reminder_setting(u):
    d=body();enabled=d.get('enabled')
    if not isinstance(enabled,bool):return error('Configuração inválida.')
    if enabled and not u.discord_id:return error('Conecte seu Discord antes de ativar lembretes.',400)
    u.reminders=enabled;db.session.commit();return jsonify(ok=True,reminders=enabled)

@portal.get('/api/discord/connect')
@auth
def discord_connect(u):
    cid=os.environ.get('DISCORD_CLIENT_ID');redirect_uri=os.environ.get('DISCORD_REDIRECT_URI')
    if not cid or not redirect_uri:return error('Discord OAuth ainda não configurado.',503)
    state=secrets.token_urlsafe(32);session['discord_state']=state
    return redirect('https://discord.com/oauth2/authorize?'+urlencode(dict(client_id=cid,redirect_uri=redirect_uri,response_type='code',scope='identify',state=state,prompt='consent')))

@portal.get('/api/discord/callback')
def discord_callback():
    u=current_user();state=session.pop('discord_state',None)
    if not u or not state or not secrets.compare_digest(state,request.args.get('state','')):return error('Autorização inválida.',403)
    uri=os.environ.get('DISCORD_REDIRECT_URI')
    data=urlencode(dict(client_id=os.environ['DISCORD_CLIENT_ID'],client_secret=os.environ['DISCORD_CLIENT_SECRET'],grant_type='authorization_code',code=request.args.get('code',''),redirect_uri=uri)).encode()
    try:
        req=Request('https://discord.com/api/oauth2/token',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'},method='POST')
        with urlopen(req,timeout=10) as resp: token=json.load(resp)['access_token']
        req=Request('https://discord.com/api/users/@me',headers={'Authorization':'Bearer '+token})
        with urlopen(req,timeout=10) as resp: info=json.load(resp)
        uid=str(info['id'])
        if User.query.filter(User.discord_id==uid,User.id!=u.id).first():return error('Discord já conectado a outra conta.',409)
        u.discord_id=uid;u.discord_name=info.get('global_name') or info.get('username')
        db.session.commit()
        return redirect('/#portal')
    except Exception:
        current_app.logger.exception('Falha na vinculação Discord')
        return error('Falha na conexão com Discord.',502)

# Run separately as a Render Cron Job (every 6 hours), never inside a web worker.
def run_reminders(app):
    bot=os.environ.get('DISCORD_BOT_TOKEN','')
    if not bot:raise RuntimeError('Configure DISCORD_BOT_TOKEN')
    with app.app_context():
        for u in User.query.filter_by(reminders=True,verified=True).filter(User.discord_id.isnot(None)).all():
            if u.last_reminder and utcnow()-u.last_reminder.replace(tzinfo=timezone.utc) < timedelta(hours=5,minutes=50):continue
            if not Character.query.filter_by(owner_id=u.id).first():continue
            try:
                def discord(method,url,payload):
                    req=Request('https://discord.com/api/v10'+url,data=json.dumps(payload).encode(),headers={'Authorization':'Bot '+bot,'Content-Type':'application/json','User-Agent':'NOCRY-Reminder/1.0'},method=method)
                    with urlopen(req,timeout=15) as response:return json.load(response)
                channel=discord('POST','/users/@me/channels',{'recipient_id':u.discord_id})['id']
                discord('POST',f'/channels/{channel}/messages',{'content':'⚔️ NOCRY: já atualizou seu nível e sua experiência (%)? Entre em https://www.nocry.com.br e atualize seu personagem no Portal do Jogador. Para parar estes avisos, desative os lembretes no painel.','allowed_mentions':{'parse':[]}})
                u.last_reminder=utcnow();db.session.commit()
            except Exception:
                db.session.rollback();app.logger.exception('Não foi possível enviar DM Discord para user_id=%s',u.id)
