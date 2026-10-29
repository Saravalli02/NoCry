"""Mercado NOCRY: filas de pedidos, ofertas, mediação e notificações privadas."""
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from sqlalchemy import or_
from portal import db, User, auth, current_user

market = Blueprint('market', __name__)
def now(): return datetime.now(timezone.utc)

class MarketRequest(db.Model):
    __tablename__='market_requests'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    item=db.Column(db.String(100),nullable=False,index=True)
    level=db.Column(db.Integer,nullable=True)
    mode=db.Column(db.String(12),nullable=False)
    bid=db.Column(db.Numeric(12,2),nullable=True)
    note=db.Column(db.String(400),default='')
    contact=db.Column(db.String(12),nullable=False)
    contact_value=db.Column(db.String(100),nullable=False)
    status=db.Column(db.String(12),default='open',nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)

class MarketOffer(db.Model):
    __tablename__='market_offers'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    item=db.Column(db.String(100),nullable=False,index=True)
    level=db.Column(db.Integer,nullable=True)
    mode=db.Column(db.String(12),nullable=False)
    price=db.Column(db.Numeric(12,2),nullable=True)
    note=db.Column(db.String(400),default='')
    contact=db.Column(db.String(12),nullable=False)
    contact_value=db.Column(db.String(100),nullable=False)
    status=db.Column(db.String(12),default='open',nullable=False)
    matched_request_id=db.Column(db.Integer,db.ForeignKey('market_requests.id'),nullable=True)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)

class MarketNotice(db.Model):
    __tablename__='market_notices'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True)
    message=db.Column(db.String(500),nullable=False)
    read=db.Column(db.Boolean,default=False,nullable=False)
    created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)

def clean(x,limit): return str(x or '').strip()[:limit]
def serialize_request(x):
    return dict(id=x.id,item=x.item,level=x.level,mode=x.mode,bid=float(x.bid) if x.bid is not None else None,note=x.note,status=x.status,username=db.session.get(User,x.user_id).username,created=x.created_at.isoformat())
def serialize_offer(x):
    return dict(id=x.id,item=x.item,level=x.level,mode=x.mode,price=float(x.price) if x.price is not None else None,note=x.note,status=x.status,username=db.session.get(User,x.user_id).username,created=x.created_at.isoformat())
def fail(msg): return jsonify(error=msg),400
def fields():
    d=request.get_json(silent=True) or {}
    if not isinstance(d,dict): return None
    item=clean(d.get('item'),100)
    if len(item)<3: return None
    try: level=int(d['level']) if str(d.get('level','')).strip() else None
    except (TypeError,ValueError): return None
    if level is not None and not 1<=level<=999: return None
    contact=clean(d.get('contact'),12)
    contact_value=clean(d.get('contact_value'),100)
    if contact not in ('discord','whatsapp') or len(contact_value)<3: return None
    return d,item,level,contact,contact_value

@market.get('/api/market')
@auth
def listing():
    uid=current_user().id
    requests=MarketRequest.query.filter_by(status='open').order_by(MarketRequest.created_at,MarketRequest.id).limit(250).all()
    offers=MarketOffer.query.filter_by(status='open').order_by(MarketOffer.created_at,MarketOffer.id).limit(250).all()
    notices=MarketNotice.query.filter_by(user_id=uid).order_by(MarketNotice.id.desc()).limit(30).all()
    return jsonify(requests=[serialize_request(x) for x in requests],offers=[serialize_offer(x) for x in offers],notices=[dict(id=x.id,message=x.message,read=x.read,created=x.created_at.isoformat()) for x in notices],unread=MarketNotice.query.filter_by(user_id=uid,read=False).count(),mine=dict(requests=[x.id for x in requests if x.user_id==uid],offers=[x.id for x in offers if x.user_id==uid]))

@market.post('/api/market/request')
@auth
def request_item():
    f=fields()
    if not f: return fail('Informe item, nível válido e contato Discord ou WhatsApp.')
    d,item,level,contact,value=f
    mode=clean(d.get('mode'),12)
    if mode not in ('donation','buy','either'): return fail('Escolha doação, compra ou ambas.')
    try: bid=float(d['bid']) if str(d.get('bid','')).strip() else None
    except (ValueError,TypeError): return fail('Valor inválido.')
    if bid is not None and (bid<=0 or bid>100000000): return fail('Valor inválido.')
    x=MarketRequest(user_id=current_user().id,item=item,level=level,mode=mode,bid=bid,note=clean(d.get('note'),400),contact=contact,contact_value=value)
    db.session.add(x);db.session.commit()
    return jsonify(ok=True,id=x.id)

@market.post('/api/market/offer')
@auth
def offer_item():
    f=fields()
    if not f: return fail('Informe item, nível válido e contato Discord ou WhatsApp.')
    d,item,level,contact,value=f
    mode=clean(d.get('mode'),12)
    if mode not in ('donation','sale'): return fail('Escolha doação ou venda.')
    try: price=float(d['price']) if str(d.get('price','')).strip() else None
    except (ValueError,TypeError): return fail('Preço inválido.')
    if mode=='sale' and (price is None or not 0<price<=100000000): return fail('Informe o preço da venda.')
    x=MarketOffer(user_id=current_user().id,item=item,level=level,mode=mode,price=price if mode=='sale' else None,note=clean(d.get('note'),400),contact=contact,contact_value=value)
    db.session.add(x);db.session.commit()
    return jsonify(ok=True,id=x.id)

@market.post('/api/market/offer/<int:offer_id>/match')
@auth
def match(offer_id):
    x=db.session.get(MarketOffer,offer_id)
    if not x or x.user_id!=current_user().id or x.status!='open': return fail('Oferta indisponível.')
    d=request.get_json(silent=True) or {}
    candidates=MarketRequest.query.filter_by(status='open').order_by(MarketRequest.created_at,MarketRequest.id).all()
    eligible=[q for q in candidates if q.item.casefold()==x.item.casefold() and (q.level is None or x.level is None or q.level==x.level) and q.user_id!=x.user_id and (q.mode in ('donation','either') if x.mode=='donation' else q.mode in ('buy','either'))]
    selected=next((q for q in eligible if q.id==d.get('request_id')),None) if d.get('request_id') else (eligible[0] if eligible else None)
    if not selected: return fail('Nenhum pedido compatível na fila.')
    x.status='matched';x.matched_request_id=selected.id;selected.status='matched'
    owner=db.session.get(User,x.user_id);buyer=db.session.get(User,selected.user_id)
    action='doar' if x.mode=='donation' else 'negociar'
    db.session.add(MarketNotice(user_id=buyer.id,message=f'{owner.username} quer {action} {x.item} com você. Contato: {x.contact}: {x.contact_value}'))
    db.session.add(MarketNotice(user_id=owner.id,message=f'Contato de {buyer.username} para {x.item}: {selected.contact}: {selected.contact_value}'))
    db.session.commit()
    return jsonify(ok=True,matched=selected.id)

@market.post('/api/market/notifications/read')
@auth
def mark_read():
    MarketNotice.query.filter_by(user_id=current_user().id,read=False).update({'read':True})
    db.session.commit()
    return jsonify(ok=True)

@market.post('/api/market/<kind>/<int:entry_id>/close')
@auth
def close(kind,entry_id):
    model=MarketRequest if kind=='request' else MarketOffer if kind=='offer' else None
    if model is None:return fail('Tipo inválido.')
    x=db.session.get(model,entry_id)
    if not x or x.user_id!=current_user().id or x.status!='open':return fail('Registro indisponível.')
    x.status='closed';db.session.commit()
    return jsonify(ok=True)
