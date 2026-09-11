"""実HTTP上の画面をPlaywrightで操作し、合成データのみ撮影する。"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',default='docs/assets')
    args=parser.parse_args()
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='networkidle')
        expect(page.locator('#result-count')).to_have_text('4件')
        page.locator('.result-card').filter(has_text='部材調達').click()
        expect(page.locator('#detail')).to_contain_text('部材調達')
        page.screenshot(path=str(output/'demo-sales.png'),full_page=True)
        page.locator('[data-query="予算"]').click()
        expect(page.locator('#result-count')).to_have_text('0件')
        page.locator('[data-user="bob"]').click()
        expect(page.locator('#results')).to_contain_text('850万円')
        page.locator('.result-card').first.click()
        expect(page.locator('#detail')).to_contain_text('850万円')
        page.screenshot(path=str(output/'demo-development.png'),full_page=True)
        page.locator('[data-user="alice"]').click()
        expect(page.locator('#result-count')).to_have_text('0件')
        expect(page.locator('#detail')).not_to_contain_text('850万円')
        page.locator('[data-query="納期"]').click()
        expect(page.locator('#result-count')).to_have_text('4件')
        page.locator('.result-card').first.click()
        expect(page.locator('#detail')).to_contain_text('10月20日')
        page.locator('#leave-button').click()
        expect(page.locator('#result-count')).to_have_text('0件')
        page.locator('#refetch-button').click()
        expect(page.locator('#notice')).to_contain_text('閲覧権限')
        expect(page.locator('#detail')).not_to_contain_text('10月20日')
        page.locator('#leave-button').click()
        expect(page.locator('#result-count')).to_have_text('4件')
        page.locator('#sync-button').click()
        expect(page.locator('#result-count')).to_have_text('5件')
        page.locator('#reset-button').click()
        expect(page.locator('#result-count')).to_have_text('4件')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'demo-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'モバイルで横にはみ出しています'
        assert not errors, errors
        browser.close()
    report={'status':'passed','transport':'real HTTP + Chromium','checks':['営業の過去・最新検索','開発予算の権限差','利用者切替時の旧本文消去','退室後の直接取得拒否','再参加','最新ログ追加','初期化','390px表示'],'data':'synthetic only'}
    (output/'browser-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
