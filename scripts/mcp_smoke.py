import argparse
import asyncio
import json
from pathlib import Path
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(url):
    async with httpx.AsyncClient(headers={'Authorization':'Bearer demo:alice'},timeout=30) as http:
        async with streamable_http_client(url.rstrip('/')+'/mcp/',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools=await session.list_tools()
                names={t.name for t in tools.tools}
                assert names=={'search','fetch','read_room_period'}
                result=await session.call_tool('search',{'query':'納期'})
                assert not result.isError
                content=json.loads(result.content[0].text)
                assert content==result.structuredContent
                assert len(content['results'])==4
                detail=await session.call_tool('fetch',{'id':content['results'][0]['id']})
                assert not detail.isError and detail.structuredContent['text']
                report={'status':'passed','transport':'real HTTP + official MCP ClientSession','tools':sorted(names),'data':'synthetic only'}
                Path('.verification').mkdir(exist_ok=True)
                Path('.verification/mcp.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    asyncio.run(check(parser.parse_args().url))
