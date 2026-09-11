"""実モデル検証。モデルの初回取得は外部通信が発生する。APIキーや顧客データは使わない。"""
import json
from pathlib import Path
from secure_chat_search.auth import Auth, DemoMemberships
from secure_chat_search.core import Database, Principal, Settings
from secure_chat_search.demo import seed
from secure_chat_search.embeddings import LocalEmbedder, index_messages
from secure_chat_search.search import SearchService


def main():
    settings=Settings(_env_file=None,mode='demo',database_url='sqlite:///:memory:',semantic_enabled=True)
    db=Database(settings)
    db.initialize()
    seed(db)
    embedder=LocalEmbedder(settings)
    indexed=index_messages(db,embedder)
    assert indexed['indexed']==10
    service=SearchService(db,Auth(settings,DemoMemberships()),settings,embedder)
    query='納品する日を後ろにずらす相談'
    assert service.search(Principal('alice'),query)['results']==[]
    result=service.search(Principal('alice'),query,mode='semantic')
    assert result['semantic_unindexed']==0
    assert any('納期' in item['text'] for item in result['results'][:3])
    assert all(item['metadata']['room_id'] in {'1001','1003'} for item in result['results'])
    assert not any('850万円' in item['text'] for item in result['results'])
    budget=service.search(Principal('bob'),'予算',mode='hybrid')
    assert '850万円' in budget['results'][0]['text']
    assert index_messages(db,embedder)['indexed']==0
    report={'status':'passed','model':embedder.model_name,'dimensions':len(embedder.query(query)),
            'indexed':indexed['indexed'],'query':query,'top_results':[item['text'] for item in result['results'][:3]],
            'scope':'合成10発言の接続・権限・索引確認。顧客データでの精度評価や大規模ベンチマークではない。'}
    Path('.verification').mkdir(exist_ok=True)
    Path('.verification/semantic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    db.close()

if __name__=='__main__': main()
