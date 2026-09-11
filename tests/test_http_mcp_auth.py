import json
import time
import pytest
import jwt
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from secure_chat_search.auth import Auth, Principal, FileTokenProvider
from secure_chat_search.config import Settings
from secure_chat_search.app import create_app
from secure_chat_search.errors import AppError


def test_no_token_rejected(client):
    assert client.post("/api/search",json={"query":"納期"}).status_code==401

def test_forged_identity_field_rejected(client,auth_headers):
    assert client.post("/api/search",headers=auth_headers,json={"query":"納期","user_id":"mori"}).status_code==422

def test_altered_bearer_rejected(client,auth_headers):
    assert client.get("/api/me",headers={"Authorization":auth_headers["Authorization"]+"x"}).status_code==401

@pytest.mark.parametrize("claim,value",[("exp",1),("aud","other"),("iss","other"),("nbf",9999999999)])
def test_claim_validation(app,claim,value):
    now=int(time.time());s=app.state.settings
    data={"sub":"aoki","tid":"demo","iss":s.jwt_issuer,"aud":s.jwt_audience,"exp":now+300,"iat":now,"nbf":now}
    data[claim]=value;token=jwt.encode(data,s.jwt_secret,algorithm="HS256")
    with pytest.raises(AppError):app.state.auth.verify("Bearer "+token)

def test_rs256_integration_auth_and_demo_disabled(db):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pub=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    s=Settings(mode="integration",database_url="sqlite:///:memory:",base_url="https://search.example",
               jwt_public_key=pub,jwt_issuer="https://issuer.example",token_file="/not-read-in-this-test")
    class Live:
        def current_group_rooms(self,p,account):return {"100"}
    app=create_app(s,db,Live());now=int(time.time())
    token=jwt.encode({"sub":"aoki","tid":"demo","iss":s.jwt_issuer,"aud":s.jwt_audience,"exp":now+60,"nbf":now,"iat":now},key,algorithm="RS256")
    assert app.state.auth.verify("Bearer "+token)==Principal("demo","aoki")
    with TestClient(app) as c:
        assert c.post("/api/demo/session",json={"subject":"aoki"}).status_code==404
        assert c.post("/api/demo/reset",headers={"Authorization":"Bearer "+token}).status_code==404
        assert c.get("/api/me",headers={"Authorization":"Bearer "+token}).json()["rooms"][0]["id"]=="100"

def test_bad_integration_settings_rejected():
    with pytest.raises(ValueError):Settings(mode="integration").validate()

def test_embedding_external_cleartext_rejected():
    with pytest.raises(ValueError):Settings(allow_embeddings=True,embedding_url="http://evil.example",embedding_model="x").validate()

def test_origin_dns_rebinding_and_cache(client,auth_headers):
    assert client.get("/api/me",headers={**auth_headers,"Origin":"https://attacker.example"}).status_code==403
    assert client.get("/",headers={"Host":"attacker.example"}).status_code==400
    r=client.get("/api/me",headers=auth_headers)
    assert r.headers["cache-control"]=="no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]

def test_request_size_limit(client,auth_headers):
    r=client.post("/api/search",headers=auth_headers,content=b"x"*(2*1024*1024+1))
    assert r.status_code==413

def test_mcp_init_list_call_fetch(client,mcp_headers):
    def call(method,params={}):
        r=client.post("/mcp",headers=mcp_headers,json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
        assert r.status_code==200;return r.json()
    init=call("initialize",{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"tests","version":"1"}})
    assert init["result"]["protocolVersion"]=="2025-06-18"
    tools=call("tools/list")["result"]["tools"]
    assert {x["name"] for x in tools}=={"search","fetch","read_timeline"}
    assert all(x["annotations"]["readOnlyHint"] for x in tools)
    result=call("tools/call",{"name":"search","arguments":{"query":"納期"}})["result"]
    assert result["structuredContent"]==json.loads(result["content"][0]["text"])
    mid=result["structuredContent"]["results"][0]["id"]
    assert call("tools/call",{"name":"fetch","arguments":{"id":mid}})["result"]["structuredContent"]["text"]
    assert call("ping")["result"]=={}

def test_mcp_scope_spoof_rejected(client,mcp_headers):
    body={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search","arguments":{"query":"予算","user_id":"mori"}}}
    assert client.post("/mcp",headers=mcp_headers,json=body).json()["error"]["code"]==-32602

def test_mcp_tool_error_not_raw_trace(client,mcp_headers):
    body={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"fetch","arguments":{"id":"unknown"}}}
    r=client.post("/mcp",headers=mcp_headers,json=body).json()["result"]
    assert r["isError"] and "Traceback" not in str(r)

def test_mcp_notification_202_and_get405(client,mcp_headers):
    r=client.post("/mcp",headers=mcp_headers,json={"jsonrpc":"2.0","method":"notifications/initialized"})
    assert r.status_code==202 and r.content==b""
    assert client.get("/mcp",headers=mcp_headers).status_code==405

def test_mcp_parse_error_and_batch_rejection(client,mcp_headers):
    headers={**mcp_headers,"Content-Type":"application/json"}
    assert client.post("/mcp",headers=headers,content="{").json()["error"]["code"]==-32700
    assert client.post("/mcp",headers=headers,json=[]).status_code==400

def test_mcp_accept_and_version_validation(client,mcp_headers):
    body={"jsonrpc":"2.0","id":1,"method":"ping"}
    assert client.post("/mcp",headers={**mcp_headers,"Accept":"application/json"},json=body).status_code==406
    assert client.post("/mcp",headers={**mcp_headers,"MCP-Protocol-Version":"unsupported"},json=body).status_code==400

def test_real_token_file_expiry_and_permissions(tmp_path):
    file=tmp_path/"tokens.json";file.write_text(json.dumps({"t":{"u":{"access_token":"not-real","expires_at":time.time()+200}}}));file.chmod(0o600)
    provider=FileTokenProvider(str(file));assert provider.get_access_token(Principal("t","u"))=="not-real"
    file.chmod(0o644)
    with pytest.raises(AppError):provider.get_access_token(Principal("t","u"))

def test_demo_actions(client,auth_headers):
    assert client.post("/api/demo/new-message",headers=auth_headers,json={}).status_code==200
    assert client.post("/api/search",headers=auth_headers,json={"query":"新着"}).json()["results"]
    assert client.post("/api/demo/import",headers=auth_headers,json={}).json()["replayed"]
    client.post("/api/demo/gap",headers=auth_headers,json={})
    assert client.post("/api/search",headers=auth_headers,json={"query":"納期"}).json()["meta"]["warnings"]
    client.post("/api/demo/membership",headers=auth_headers,json={"room_id":"101","enabled":False})
    assert not client.post("/api/search",headers=auth_headers,json={"query":"星野商事","mode":"keyword"}).json()["results"]
    assert client.post("/api/demo/reset",headers=auth_headers,json={}).status_code==200
