"""一時DBと実HTTPサーバーで確認。外部サービス・既存DBには触れない。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports'


def main():
    checks=[]
    def check(name,condition):
        checks.append({'name':name,'passed':bool(condition)})
        if not condition:raise AssertionError(name)
    REPORT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'SCS_MODE':'demo','SCS_DATABASE_URL':'sqlite:///'+str(Path(tmp)/'http.db'),'SCS_BASE_URL':base}
        def request(path,data=None,token=None,headers=None):
            h=dict(headers or {})
            if token:h['Authorization']='Bearer '+token
            if data is not None:h['Content-Type']='application/json'
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
            try:r=urllib.request.urlopen(req,timeout=15)
            except urllib.error.HTTPError as e:r=e
            with r:
                content=r.read();status=r.status
                if 'application/json' in r.headers.get('Content-Type',''):content=json.loads(content)
                return status,content,dict(r.headers)
        with (REPORT/'http-server.log').open('w') as log:
            process=subprocess.Popen([sys.executable,'-m','secure_chat_search.cli','serve','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:raise RuntimeError('サーバー起動失敗。http-server.logを確認してください')
                    try:
                        status,data,_=request('/healthz')
                        if status==200:break
                    except OSError:time.sleep(.1)
                else:raise RuntimeError('サーバー起動タイムアウト')
                check('別プロセスのHTTPサーバーが起動する',status==200 and data['status']=='ok')
                status,html,headers=request('/')
                check('日本語HTMLをHTTPで取得',status==200 and 'ことのは検索' in html.decode())
                check('JavaScriptとCSSが配信される',request('/static/app.js')[0]==200 and request('/static/style.css')[0]==200)
                check('無認証の検索を拒否',request('/api/search',{'query':'納期'})[0]==401)
                status,session,_=request('/api/demo/session',{'subject':'aoki'})
                token=session['access_token'];check('架空利用者のデモセッション',status==200)
                status,found,_=request('/api/search',{'query':'納期'},token)
                check('過去の納期変更を検索',status==200 and '12月15日' in found['results'][0]['excerpt'])
                mid=found['results'][0]['id']
                status,original,_=request('/api/messages/'+mid,token=token)
                check('根拠本文を再認可して取得',status==200 and '12月15日' in original['text'])
                h={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'}
                def mcp(method,params):
                    return request('/mcp',{'jsonrpc':'2.0','id':1,'method':method,'params':params},token,h)
                status,init,_=mcp('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'http-check','version':'1'}})
                check('MCP初期化を実HTTPで交換',status==200 and init['result']['protocolVersion']=='2025-06-18')
                status,result,_=mcp('tools/call',{'name':'search','arguments':{'query':'納期'}})
                check('MCP検索とRESTが同じ根拠を返す',status==200 and result['result']['structuredContent']['results'][0]['id']==mid)
                status,result,_=mcp('tools/call',{'name':'fetch','arguments':{'id':mid}})
                check('MCP本文とREST本文が一致',result['result']['structuredContent']['text']==original['text'])
                _,other,_=request('/api/demo/session',{'subject':'sato'})
                check('別利用者による原文ID直接参照を拒否',request('/api/messages/'+mid,token=other['access_token'])[0]==404)
                request('/api/demo/membership',{'room_id':'101','enabled':False},token)
                check('退室後の原文取得を拒否',request('/api/messages/'+mid,token=token)[0]==404)
                check('不正Originを拒否',request('/api/me',token=token,headers={'Origin':'https://untrusted.invalid'})[0]==403)
                check('通知は本文なし202',request('/mcp',{'jsonrpc':'2.0','method':'notifications/initialized'},token,h)[:2]==(202,b''))
                check('SSEなしのGETは405',request('/mcp',token=token,headers=h)[0]==405)
                check('キャッシュ抑止ヘッダー',headers.get('cache-control')=='no-store')
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                (REPORT/'http-check.json').write_text(json.dumps({'mode':'Python HTTPクライアント→別プロセスUvicorn→FastAPI','checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
