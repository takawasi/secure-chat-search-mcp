"""日本語UIのChromium操作試験。

通常: python scripts/ui_check.py
ブラウザのネットワークが制限された検証環境:
  python scripts/ui_check.py --asgi-bridge
後者は実HTML/CSS/JSを描画し、fetchだけを実FastAPI TestClientへ接続する。
ブラウザ→HTTPの通し試験ではないことを結果にも明記する。
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
REPORT = ROOT / "reports"
REPORT.mkdir(exist_ok=True)
results = []

def check(name, condition):
    results.append({"name": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)

def run_checks(page, inject_xss, errors):
    page.wait_for_selector(".result-card")
    page.wait_for_selector(".reader-body")
    check("営業の納期検索と原文表示", "12月15日" in page.locator(".reader-body").first.inner_text())
    page.screenshot(path=str(REPORT / "01_検索と根拠.png"), full_page=True)
    page.select_option("#person", "sato")
    page.wait_for_selector(".empty-results")
    check("利用者切替で営業の根拠を消去", "星野商事" not in page.locator("#results").inner_text() and "12月15日" not in page.locator("#reader-content").inner_text())
    page.fill("#query", "障害");page.click("#search-btn");page.wait_for_selector(".reader-body")
    check("開発側の障害検索", "開発" in page.locator("#reader-content").inner_text())
    page.screenshot(path=str(REPORT / "02_別利用者の検索.png"), full_page=True)
    page.select_option("#person", "aoki");page.wait_for_function("document.querySelector('#room-count').textContent==='3'")
    page.fill("#query","星野商事");page.click("#search-btn");page.wait_for_selector(".result-card")
    page.click("#leave-room");page.wait_for_selector(".empty-results")
    check("退室すると検索結果と原文が消える", "星野商事" not in page.locator("#reader-content").inner_text())
    page.click("#rejoin-room");page.wait_for_selector(".result-card")
    check("復帰すると許可範囲で再取得", page.locator(".result-card").count()>0)
    page.click('[data-action="new-message"]');page.wait_for_function("document.querySelector('#query').value==='新着'")
    page.wait_for_selector(".reader-body")
    page.wait_for_function("document.querySelector('#results').textContent.includes('新着デモ')")
    check("新着データを実際に保存して検索", "新着デモ" in page.locator("#results").inner_text())
    page.click('[data-action="gap"]');page.wait_for_selector(".warning")
    check("欠損疑いを明示", "欠損" in page.locator("#warnings").inner_text())
    page.screenshot(path=str(REPORT / "03_新着と欠損警告.png"),full_page=True)
    page.click('[data-action="import"]');page.wait_for_function("document.querySelector('#action-result').textContent.includes('重複')")
    check("CSV再取り込みの説明", "重複" in page.locator("#action-result").inner_text())
    page.set_viewport_size({"width":390,"height":844})
    page.screenshot(path=str(REPORT / "04_スマートフォン.png"),full_page=True)
    check("モバイル横はみ出しなし",page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    inject_xss()
    page.fill("#query", "XSS検証"); page.click("#search-btn")
    page.wait_for_selector(".reader-body")
    page.wait_for_function("document.querySelector('.reader-body').textContent.includes('XSS検証')")
    check("本文のHTMLを要素として解釈しない", page.locator(".reader-body img").count()==0 and "<img" in page.locator(".reader-body").inner_text())
    check("本文に埋めたスクリプトが実行されない", not page.evaluate("Boolean(window.__xssExecuted)"))
    check("JavaScript実行エラーなし",not errors)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asgi-bridge",action="store_true")
    args=parser.parse_args()
    mode="Chromium描画＋FastAPI TestClient接続（ブラウザHTTP接続は未検証）" if args.asgi_bridge else "Chromium→実HTTP→FastAPI"
    try:
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            executable=os.getenv("CHROMIUM_PATH") or shutil.which("chromium")
            browser=p.chromium.launch(headless=True,executable_path=executable,args=["--no-sandbox"])
            page=browser.new_page(viewport={"width":1440,"height":1080},device_scale_factor=1)
            errors=[]
            page.on("pageerror", lambda e:errors.append(str(e)))
            def inject_xss():
                from secure_chat_search.db import Database
                from secure_chat_search.ingest import upsert,Record
                db=Database("sqlite:///"+str(Path(temp)/"ui.db"))
                try:
                    with db.session.begin() as session:
                        upsert(session,"demo",Record("101","888888","11","架空検証",100,100,'<img src=x onerror="window.__xssExecuted=true"> XSS検証'),200)
                finally:db.engine.dispose()
            if args.asgi_bridge:
                from fastapi.testclient import TestClient
                from secure_chat_search.app import create_app
                from secure_chat_search.config import Settings
                app=create_app(Settings(database_url="sqlite:///"+str(Path(temp)/"ui.db")))
                with TestClient(app) as client:
                    def relay(path,options):
                        if not path.startswith("/api/"):
                            raise ValueError("試験中に許可する接続先はローカルAPIだけです")
                        res=client.request(options["method"],path,headers=options["headers"],content=options.get("body"))
                        return {"status":res.status_code,"headers":dict(res.headers),"body":res.text}
                    page.expose_function("asgiRequest",relay)
                    html=(ROOT/"src/secure_chat_search/web/index.html").read_text()
                    html=re.sub(r'<link[^>]+href="/static/style.css"[^>]*>',"",html)
                    html=re.sub(r'<script[^>]+src="/static/app.js"[^>]*></script>',"",html)
                    page.set_content(html)
                    page.add_style_tag(content=(ROOT/"src/secure_chat_search/web/style.css").read_text())
                    page.add_script_tag(content='''window.fetch=async (path,options={})=>{const r=await window.asgiRequest(String(path),{method:options.method||"GET",headers:options.headers||{},body:options.body||null});return new Response(r.body,{status:r.status,headers:r.headers})};''')
                    page.add_script_tag(content=(ROOT/"src/secure_chat_search/web/app.js").read_text())
                    run_checks(page, inject_xss, errors)
                app.state.db.engine.dispose()
            else:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
                url=f"http://127.0.0.1:{port}"
                env={**os.environ,"PYTHONPATH":str(ROOT/"src"),"SCS_MODE":"demo","SCS_DATABASE_URL":"sqlite:///"+str(Path(temp)/"ui.db"),"SCS_BASE_URL":url}
                with (REPORT/"browser-server.log").open("w") as log:
                    process=subprocess.Popen([sys.executable,"-m","secure_chat_search.cli","serve","--port",str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
                    try:
                        for _ in range(100):
                            try:urllib.request.urlopen(url+"/healthz",timeout=1).close();break
                            except Exception:time.sleep(.1)
                        page.goto(url,wait_until="networkidle");run_checks(page, inject_xss, errors)
                    finally:
                        process.terminate()
                        try:process.wait(timeout=5)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
            browser.close()
    finally:
        (REPORT/"browser-check.json").write_text(json.dumps({"mode":mode,"checks":results},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"mode":mode,"checks":results},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
