import json
import time
from pathlib import Path
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from secure_chat_search.app import create_app
from secure_chat_search.auth import Auth, DemoMemberships
from secure_chat_search.chatwork import ChatworkClient, CredentialStore
from secure_chat_search.core import Database, DomainError, Settings, User
from secure_chat_search.demo import change_membership


def gateway_settings(tmp_path):
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pub=tmp_path/'public.pem'
    pub.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    credentials=tmp_path/'credentials.json'
    credentials.write_text('{}')
    settings=Settings(_env_file=None,mode='gateway',database_url='sqlite:///'+str(tmp_path/'private.sqlite3'),
        public_url='http://testserver',jwt_issuer='test-issuer',jwt_audience='test-audience',
        jwt_public_key_file=str(pub),credentials_file=str(credentials))
    return settings,private


def claims():
    now=int(time.time())
    return dict(sub='alice',iss='test-issuer',aud='test-audience',iat=now,nbf=now,exp=now+120)


def test_valid_gateway_token(tmp_path):
    settings,key=gateway_settings(tmp_path)
    token=jwt.encode(claims(),key,algorithm='RS256')
    assert Auth(settings,DemoMemberships()).authenticate('Bearer '+token).sub=='alice'


@pytest.mark.parametrize('case',['expired','audience','issuer','future','too_long','missing_sub','wrong_algorithm','cookie_only'])
def test_gateway_rejects_invalid_identity(tmp_path,case):
    settings,key=gateway_settings(tmp_path)
    data=claims()
    if case=='expired': data.update(iat=int(time.time())-300,nbf=int(time.time())-300,exp=int(time.time())-10)
    if case=='audience': data['aud']='elsewhere'
    if case=='issuer': data['iss']='elsewhere'
    if case=='future': data['iat']=data['nbf']=int(time.time())+100
    if case=='too_long': data['exp']=data['iat']+301
    if case=='missing_sub': del data['sub']
    token=jwt.encode(data,'not-a-real-key-of-any-service',algorithm='HS256') if case=='wrong_algorithm' else jwt.encode(data,key,algorithm='RS256')
    with pytest.raises(DomainError) as exc:
        Auth(settings,DemoMemberships()).authenticate(None if case=='cookie_only' else 'Bearer '+token,'alice')
    assert exc.value.status==401


def test_gateway_demo_endpoints_are_disabled(tmp_path):
    settings,key=gateway_settings(tmp_path)
    app=create_app(settings)
    with app.state.db.session() as session:
        session.add(User(sub='alice',name='社員',account_id='101',enabled=True))
    token=jwt.encode(claims(),key,algorithm='RS256')
    with TestClient(app) as client:
        assert client.post('/api/demo/session',json={'user':'alice'}).status_code==404
        assert client.post('/api/demo/reset',json={},headers={'Authorization':'Bearer '+token}).status_code==404
        assert client.get('/docs').status_code==404
    app.state.db.close()


def test_dataset_mode_cannot_change(env):
    other=env.settings.model_copy(update={'mode':'gateway'})
    database=Database(other)
    with pytest.raises(DomainError) as exc:
        database.initialize()
    assert exc.value.code=='dataset_mode'
    database.close()


def test_cloud_run_rejects_demo(monkeypatch):
    monkeypatch.setenv('K_SERVICE','ci-test-service')
    with pytest.raises(ValueError):
        Settings(_env_file=None,mode='demo')


def test_gateway_requires_auth_configuration(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None,mode='gateway')


@pytest.mark.parametrize('status',[301,401,403,429,500,503])
def test_upstream_errors_are_sanitized(status):
    def handler(request):
        return httpx.Response(status,text='private-token-and-message',headers={'location':'https://unexpected.example'})
    with ChatworkClient('synthetic-token',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DomainError) as exc:
            client.messages('1001')
        assert exc.value.status==503 and 'private-token' not in str(exc.value)


def test_api_uses_fixed_host_and_force_one():
    def handler(request):
        assert request.url.host=='api.chatwork.com'
        assert request.url.path=='/v2/rooms/1001/messages'
        assert request.url.params['force']=='1'
        assert request.headers['Authorization']=='Bearer synthetic-token'
        return httpx.Response(200,json=[])
    with ChatworkClient('synthetic-token',transport=httpx.MockTransport(handler)) as client:
        assert client.messages('1001')==[]
        with pytest.raises(DomainError):
            client.messages('https://evil.example/')


@pytest.mark.parametrize('mismatch',[False,True])
def test_credential_store_rejects_expired_or_wrong_account(tmp_path,mismatch):
    path=tmp_path/'credentials.json'
    path.write_text(json.dumps({'alice':{'account_id':'999' if mismatch else '101','access_token':'synthetic-token','expires_at':int(time.time())+100 if mismatch else 0}}))
    with pytest.raises(DomainError):
        CredentialStore(str(path)).client_for('alice','101')


def test_http_auth_headers_and_no_spoofing(env):
    app=create_app(env.settings,db=env.db)
    with TestClient(app) as client:
        assert client.get('/healthz').json()['status']=='ok'
        assert client.post('/api/search',json={'query':'納期'}).status_code==401
        assert client.post('/mcp/',json={}).status_code==401
        assert client.post('/api/demo/session',json={'user':'alice'}).status_code==200
        response=client.post('/api/search',json={'query':'予算','user':'bob','sub':'bob'})
        assert response.status_code==200 and response.json()['results']==[]
        assert response.headers['cache-control']=='no-store'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert client.get('/api/session',headers={'Host':'evil.example'}).status_code==400
        assert client.post('/api/demo/reset',json={},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/webhooks/chatwork',content=b'{}').status_code==404
        assert client.post('/api/demo/session',json={'user':'disabled'}).status_code==200
        assert client.get('/api/session').status_code==403


def test_body_limit_and_duplicate_auth(env):
    settings=env.settings.model_copy(update={'max_request_bytes':1024})
    with TestClient(create_app(settings,db=env.db)) as client:
        headers={'Authorization':'Bearer demo:alice','Content-Type':'application/json'}
        assert client.post('/api/search',content=b'x'*1025,headers=headers).status_code==413
        assert client.get('/api/session',headers=[('Authorization','Bearer demo:alice'),('Authorization','Bearer demo:bob')]).status_code==400


def test_japanese_static_files(env):
    with TestClient(create_app(env.settings,db=env.db)) as client:
        assert '過去の経緯まで' in client.get('/').text
        assert 'textContent' in client.get('/static/app.js').text
        assert client.get('/static/style.css').status_code==200


@pytest.mark.asyncio
async def test_official_mcp_client_round_trip_and_revocation(env):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    app=create_app(env.settings,db=env.db)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',
                                    headers={'Authorization':'Bearer demo:alice'}) as client:
            async with streamable_http_client('http://testserver/mcp/',http_client=client) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools=await session.list_tools()
                    assert {tool.name for tool in tools.tools}=={'search','fetch','read_room_period'}
                    result=await session.call_tool('search',{'query':'納期'})
                    assert not result.isError
                    content=json.loads(result.content[0].text)
                    assert content==result.structuredContent
                    assert len(content['results'])==4
                    key=content['results'][0]['id']
                    fetched=await session.call_tool('fetch',{'id':key})
                    assert not fetched.isError and fetched.structuredContent['id']==key
                    period=await session.call_tool('read_room_period',{'room_id':'1001','since':'2023-01-01T00:00:00+09:00','until':'2026-12-31T23:59:59+09:00','limit':2})
                    assert period.structuredContent['next_cursor']
                    change_membership(env.db,'alice',False)
                    denied=await session.call_tool('fetch',{'id':key})
                    assert denied.isError and denied.structuredContent['error']=='not_found'
                    after=await session.call_tool('search',{'query':'納期'})
                    assert after.structuredContent['results']==[]
