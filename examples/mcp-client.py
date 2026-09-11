"""ローカル架空デモ専用のMCPクライアント。先にchat-search serveを起動する。"""
import argparse
import json
from urllib.parse import urlparse
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    args=parser.parse_args()
    url=urlparse(args.base_url)
    if url.hostname not in ('127.0.0.1','localhost','::1') or url.scheme!='http' or url.username or url.password:
        parser.error('この例はローカルHTTPデモ専用です')
    with httpx.Client(base_url=args.base_url,timeout=15,follow_redirects=False) as client:
        config=client.get('/api/config');config.raise_for_status()
        if config.json()['mode']!='demo':
            raise RuntimeError('統合環境にはこのデモ認証クライアントを使わないでください')
        response=client.post('/api/demo/session',json={'subject':'aoki'});response.raise_for_status()
        token=response.json()['access_token']
        client.headers.update({'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'})
        counter=0
        def rpc(method,params):
            nonlocal counter
            counter+=1
            r=client.post('/mcp',json={'jsonrpc':'2.0','id':counter,'method':method,'params':params});r.raise_for_status()
            data=r.json()
            if 'error' in data:raise RuntimeError(json.dumps(data['error'],ensure_ascii=False))
            if data['result'].get('isError'):raise RuntimeError(str(data['result']['content']))
            return data['result']
        init=rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'example-client','version':'1'}})
        client.post('/mcp',json={'jsonrpc':'2.0','method':'notifications/initialized'}).raise_for_status()
        found=rpc('tools/call',{'name':'search','arguments':{'query':'納期'}})['structuredContent']
        if not found['results']:raise RuntimeError('架空データが見つかりません。デモの状態を確認してください')
        original=rpc('tools/call',{'name':'fetch','arguments':{'id':found['results'][0]['id']}})['structuredContent']
        timeline=rpc('tools/call',{'name':'read_timeline','arguments':{'room_id':original['room_id'],'start':0,'end':2000000000,'limit':2}})['structuredContent']
        print(json.dumps({'接続':init['serverInfo'],'検索':found,'原文':original,'時系列':timeline},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
