"""Mercado interno NOCRY: pedidos por quantidade, reservas e confirmação de entrega."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from flask import Blueprint, jsonify, request
from sqlalchemy import func
from portal import db, User, auth, current_user

market = Blueprint('market', __name__)
def now(): return datetime.now(timezone.utc)
SPECS=('Pike','Atalanta','Lutador','Cavaleiro','Sacerdotisa','Mago','Arqueira','Mecânico','NS')

class MarketRequest(db.Model):
    __tablename__='market_requests'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    item=db.Column(db.String(100),nullable=False,index=True)
    level=db.Column(db.Integer)
    spec=db.Column(db.String(20))
    mode=db.Column(db.String(12),nullable=False)
    bid=db.Column(db.Numeric(12,2))
    note=db.Column(db.String(400),default='')
    contact=db.Column(db.String(12),nullable=False)
    contact_value=db.Column(db.String(100),nullable=False)
    quantity=db.Column(db.Integer,nullable=False,default=1)
    fulfilled=db.Column(db.Integer,nullable=False,default=0)
    status=db.Column(db.String(12),nullable=False,default='open')
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)

class MarketOffer(db.Model):
    __tablename__='market_offers'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    item=db.Column(db.String(100),nullable=False,index=True)
    level=db.Column(db.Integer)
    spec=db.Column(db.String(20))
    mode=db.Column(db.String(12),nullable=False)
    price=db.Column(db.Numeric(12,2))
    note=db.Column(db.String(400),default='')
    contact=db.Column(db.String(12),nullable=False)
    contact_value=db.Column(db.String(100),nullable=False)
    status=db.Column(db.String(12),nullable=False,default='open')
    matched_request_id=db.Column(db.Integer,db.ForeignKey('market_requests.id'))
    quantity=db.Column(db.Integer,nullable=False,default=1)
    fulfilled=db.Column(db.Integer,nullable=False,default=0)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)

class MarketMatch(db.Model):
    __tablename__='market_matches'
    id=db.Column(db.Integer,primary_key=True)
    offer_id=db.Column(db.Integer,db.ForeignKey('market_offers.id'),nullable=False,index=True)
    request_id=db.Column(db.Integer,db.ForeignKey('market_requests.id'),nullable=False,index=True)
    status=db.Column(db.String(15),nullable=False,default='pending')
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)

class MarketNotice(db.Model):
    __tablename__='market_notices'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    message=db.Column(db.String(500),nullable=False)
    read=db.Column(db.Boolean,nullable=False,default=False)
    created_at=db.Column(db.DateTime(timezone=True),nullable=False,default=now)

def clean(x,n): return str(x or '').strip()[:n]
def fail(message,code=400): return jsonify(error=message),code
def pending(model,id):
    column=MarketMatch.offer_id if model=='offer' else MarketMatch.request_id
    return MarketMatch.query.filter(column==id,MarketMatch.status=='pending').count()
def remaining(x,kind):return max(0,(x.quantity or 1)-(x.fulfilled or 0)-pending(kind,x.id))
def serialize(x,kind):
    user=db.session.get(User,x.user_id)
    d=dict(id=x.id,item=x.item,level=x.level,spec=x.spec,mode=x.mode,note=x.note,status=x.status,username=user.username if user else 'Membro',quantity=x.quantity or 1,fulfilled=x.fulfilled or 0,available=remaining(x,kind),created=x.created_at.isoformat())
    if kind=='offer': d['price']=float(x.price) if x.price is not None else None
    else:d['bid']=float(x.bid) if x.bid is not None else None
    return d
def fields():
    d=request.get_json(silent=True)
    if not isinstance(d,dict):return None
    item=clean(d.get('item'),100)
    if len(item)<3:return None
    try:
        level=int(d['level']) if str(d.get('level','')).strip() else None
        quantity=int(d.get('quantity',1))
    except (ValueError,TypeError):return None
    if level is not None and not 1<=level<=999:return None
    if not 1<=quantity<=99:return None
    spec=clean(d.get('spec'),20)
    contact=clean(d.get('contact'),12)
    contact_value=clean(d.get('contact_value'),100)
    if spec not in SPECS or contact not in ('discord','whatsapp') or len(contact_value)<3:return None
    return d,item,level,spec,quantity,contact,contact_value
def money(d,key):
    if not str(d.get(key,'')).strip():return None
    try:
        amount=Decimal(str(d[key]))
        if not amount.is_finite() or not Decimal('0')<amount<=Decimal('100000000'):raise InvalidOperation()
        return amount.quantize(Decimal('.01'))
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Valor inválido')

def compatible(q,x):
    return (q.status=='open' and q.user_id!=x.user_id and q.item.casefold()==x.item.casefold()
            and q.spec==x.spec and (q.level is None or x.level is None or q.level==x.level)
            and (q.mode in ('donation','either') if x.mode=='donation' else q.mode in ('buy','either'))
            and remaining(q,'request')>0)

def notify_availability(q,x):
    place='Doações' if x.mode=='donation' else 'Vendas'
    db.session.add(MarketNotice(user_id=q.user_id,message=f'🔔 O item {x.item} (nível {x.level}, {x.spec}) que você pediu foi anunciado em {place}! Abra Mercado NOCRY > {place} e procure o anúncio de {db.session.get(User,x.user_id).username}.'))

@market.get('/api/market')
@auth
def listing(u):
    uid=current_user().id
    requests=MarketRequest.query.filter_by(status='open').order_by(MarketRequest.created_at,MarketRequest.id).limit(250).all()
    offers=MarketOffer.query.filter_by(status='open').order_by(MarketOffer.created_at,MarketOffer.id).limit(250).all()
    notices=MarketNotice.query.filter_by(user_id=uid).order_by(MarketNotice.id.desc()).limit(30).all()
    matches=MarketMatch.query.join(MarketRequest,MarketMatch.request_id==MarketRequest.id).join(MarketOffer,MarketMatch.offer_id==MarketOffer.id).filter(db.or_(MarketRequest.user_id==uid,MarketOffer.user_id==uid)).order_by(MarketMatch.id.desc()).limit(100).all()
    return jsonify(requests=[serialize(x,'request') for x in requests],offers=[serialize(x,'offer') for x in offers],notices=[dict(id=x.id,message=x.message,read=x.read,created=x.created_at.isoformat()) for x in notices],unread=MarketNotice.query.filter_by(user_id=uid,read=False).count(),mine=dict(requests=[x.id for x in requests if x.user_id==uid],offers=[x.id for x in offers if x.user_id==uid]),matches=[dict(id=m.id,offer_id=m.offer_id,request_id=m.request_id,item=db.session.get(MarketOffer,m.offer_id).item,status=m.status,recipient=db.session.get(MarketRequest,m.request_id).user_id==uid,owner=db.session.get(MarketOffer,m.offer_id).user_id==uid) for m in matches])

@market.post('/api/market/request')
@auth
def request_item(u):
    f=fields()
    if not f:return fail('Informe item, Spec, quantidade (1 a 99) e contato.')
    d,item,level,spec,quantity,contact,value=f
    mode=clean(d.get('mode'),12)
    if mode not in ('donation','buy','either'):return fail('Modalidade inválida.')
    try:bid=money(d,'bid')
    except ValueError as exc:return fail(str(exc))
    x=MarketRequest(user_id=current_user().id,item=item,level=level,spec=spec,quantity=quantity,mode=mode,bid=bid,note=clean(d.get('note'),400),contact=contact,contact_value=value)
    db.session.add(x);db.session.flush()
    for offer in MarketOffer.query.filter_by(status='open').all():
        if compatible(x,offer) and remaining(offer,'offer')>0:notify_availability(x,offer)
    db.session.commit()
    return jsonify(ok=True,id=x.id)

@market.post('/api/market/offer')
@auth
def offer_item(u):
    f=fields()
    if not f:return fail('Informe item, Spec, quantidade (1 a 99) e contato.')
    d,item,level,spec,quantity,contact,value=f
    mode=clean(d.get('mode'),12)
    if mode not in ('donation','sale'):return fail('Modalidade inválida.')
    try:price=money(d,'price')
    except ValueError as exc:return fail(str(exc))
    if mode=='sale' and price is None:return fail('Informe o preço da venda.')
    x=MarketOffer(user_id=current_user().id,item=item,level=level,spec=spec,quantity=quantity,mode=mode,price=price if mode=='sale' else None,note=clean(d.get('note'),400),contact=contact,contact_value=value)
    db.session.add(x);db.session.flush()
    for wanted in MarketRequest.query.filter_by(status='open').all():
        if compatible(wanted,x):notify_availability(wanted,x)
    db.session.commit()
    return jsonify(ok=True,id=x.id)

@market.post('/api/market/offer/<int:offer_id>/match')
@auth
def match(u, offer_id):
    x=MarketOffer.query.filter_by(id=offer_id).with_for_update().first()
    if not x or x.user_id!=current_user().id or x.status!='open' or remaining(x,'offer')<1:return fail('Oferta indisponível.')
    d=request.get_json(silent=True) or {}
    if not isinstance(d,dict):return fail('Dados inválidos.')
    qs=MarketRequest.query.filter_by(status='open').order_by(MarketRequest.created_at,MarketRequest.id).all()
    eligible=[q for q in qs if q.item.casefold()==x.item.casefold() and q.spec==x.spec and (q.level is None or x.level is None or q.level==x.level) and q.user_id!=x.user_id and (q.mode in ('donation','either') if x.mode=='donation' else q.mode in ('buy','either')) and remaining(q,'request')>0]
    q=next((q for q in eligible if q.id==d.get('request_id')),None) if d.get('request_id') is not None else (eligible[0] if eligible else None)
    if not q:return fail('Nenhum pedido compatível disponível.')
    db.session.add(MarketMatch(offer_id=x.id,request_id=q.id))
    owner=db.session.get(User,x.user_id);recipient=db.session.get(User,q.user_id)
    owner_contact='WhatsApp' if x.contact=='whatsapp' else 'Discord'
    recipient_contact='WhatsApp' if q.contact=='whatsapp' else 'Discord'
    if x.mode=='donation':
        recipient_message=f'O usuário {owner.username} quer doar {x.item} para você. Entre em contato pelo {owner_contact}: {x.contact_value}. Confirme o recebimento no Mercado NOCRY.'
        owner_message=f'O usuário {recipient.username} está na fila para receber sua doação de {x.item}. Entre em contato pelo {recipient_contact}: {q.contact_value}.'
    else:
        recipient_message=f'O usuário {owner.username} quer negociar {x.item} com você. Entre em contato pelo {owner_contact}: {x.contact_value}.'
        owner_message=f'O usuário {recipient.username} quer negociar {x.item} com você. Entre em contato pelo {recipient_contact}: {q.contact_value}.'
    db.session.add(MarketNotice(user_id=q.user_id,message=recipient_message))
    db.session.add(MarketNotice(user_id=x.user_id,message=owner_message))
    db.session.commit()
    return jsonify(ok=True,recipient=recipient.username)

@market.post('/api/market/match/<int:match_id>/received')
@auth
def received(u, match_id):
    m=MarketMatch.query.filter_by(id=match_id).with_for_update().first()
    if not m or m.status!='pending':return fail('Reserva indisponível.')
    q=MarketRequest.query.filter_by(id=m.request_id).with_for_update().first()
    x=MarketOffer.query.filter_by(id=m.offer_id).with_for_update().first()
    if q.user_id!=current_user().id:return fail('Somente quem recebeu pode confirmar.',403)
    m.status='received';q.fulfilled=(q.fulfilled or 0)+1;x.fulfilled=(x.fulfilled or 0)+1
    if q.fulfilled>=q.quantity:q.status='completed'
    if x.fulfilled>=x.quantity:x.status='completed'
    db.session.add(MarketNotice(user_id=x.user_id,message=f'{db.session.get(User,q.user_id).username} confirmou o recebimento de {x.item}.'))
    db.session.commit()
    return jsonify(ok=True,request_complete=q.status=='completed',offer_complete=x.status=='completed')

@market.post('/api/market/match/<int:match_id>/cancel')
@auth
def cancel_match(u, match_id):
    m=MarketMatch.query.filter_by(id=match_id).with_for_update().first()
    if not m or m.status!='pending':return fail('Reserva indisponível.')
    x=db.session.get(MarketOffer,m.offer_id);q=db.session.get(MarketRequest,m.request_id)
    if current_user().id not in (x.user_id,q.user_id):return fail('Sem permissão.',403)
    m.status='cancelled'
    other=q.user_id if current_user().id==x.user_id else x.user_id
    db.session.add(MarketNotice(user_id=other,message=f'Reserva de {x.item} cancelada. A unidade voltou à fila.'))
    db.session.commit()
    return jsonify(ok=True)

@market.post('/api/market/notifications/read')
@auth
def mark_read(u):
    MarketNotice.query.filter_by(user_id=current_user().id,read=False).update({'read':True})
    db.session.commit()
    return jsonify(ok=True)

@market.post('/api/market/<kind>/<int:entry_id>/close')
@auth
def close(u, kind,entry_id):
    model=MarketRequest if kind=='request' else MarketOffer if kind=='offer' else None
    if model is None:return fail('Tipo inválido.')
    x=model.query.filter_by(id=entry_id).with_for_update().first()
    if not x or x.user_id!=current_user().id or x.status!='open':return fail('Registro indisponível.')
    if pending(kind,x.id):return fail('Existem entregas pendentes. Confirme ou cancele as reservas antes de remover.')
    data=request.get_json(silent=True) or {}
    reason=data.get('reason','removed') if isinstance(data,dict) else 'removed'
    if reason not in ('removed','sold','donated'):return fail('Motivo inválido.')
    if kind=='request' and reason!='removed':return fail('Pedidos só podem ser removidos.')
    if kind=='offer' and reason=='sold' and x.mode!='sale':return fail('Esta oferta não é uma venda.')
    if kind=='offer' and reason=='donated' and x.mode!='donation':return fail('Esta oferta não é uma doação.')
    x.status=reason;db.session.commit()
    return jsonify(ok=True)
