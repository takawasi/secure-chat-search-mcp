# 検証記録

検証元コミット：`b0796ec3fb3aa2cc092b686e4716a84612158176`
実行記録：[GitHub Actions](https://github.com/takawasi/secure-chat-search-mcp/actions/runs/34575143943)

結果は合成データによる参照実装の検証です。顧客環境での統合検収・本番安全性の保証ではありません。

| 検証 | 実行結果 |
|---|---|
| SQLite／PostgreSQL 回帰テスト | success |
| 実HTTP MCP＋Chromium画面操作 | success |
| 実Embeddingモデルの意味検索 | success |
| Dockerの非root起動 | success |

**公開PoCの自動検証はすべて成功しました。**

## 回帰テストの実測

公開前点検は各回帰テストジョブの静的確認で実行し、秘密らしい文字列・禁止ファイル・壊れた相対リンクの指摘がないことを確認します。これは目視確認・依存ライセンス・脆弱性確認の代わりではありません。

検索・`fetch`・保護された原文表示・期間取得・MCPの期間取得は、同じ現在ACLを通す回帰を含みます。退室後の既知IDを別入口から再取得できないことを確認します。
- sqlite: 87件、失敗0、エラー0、スキップ0。
- postgres: 87件、失敗0、エラー0、スキップ0。

## 検証したソース

コード指紋（SHA-256）：`c729f983ee599e6de1ba9ae25bcc8948789c1c8e3bde2512942c109a87f54259`
各ジョブとの一致：確認済み。
初版の分割追加を整合させた後の通常ソースを検証しています。公開版にも同じソースを保存し、起動時の動的な書き換えは行いません。

## browser-verification.json

```json
{
  "status": "passed",
  "transport": "real HTTP + Chromium",
  "checks": [
    "営業の過去・最新検索",
    "開発予算の権限差",
    "利用者切替時の旧本文消去",
    "退室後の直接取得拒否",
    "再参加",
    "最新ログ追加",
    "初期化",
    "390px表示"
  ],
  "data": "synthetic only"
}
```

## mcp.json

```json
{
  "status": "passed",
  "transport": "real HTTP + official MCP ClientSession",
  "tools": [
    "fetch",
    "read_room_period",
    "search"
  ],
  "data": "synthetic only"
}
```

## semantic.json

```json
{
  "status": "passed",
  "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
  "dimensions": 384,
  "indexed": 10,
  "query": "納品する日を後ろにずらす相談",
  "top_results": [
    "社内文書を参照する際は根拠と更新日を確認してください。",
    "星野商事との初回相談です。希望納期は6月30日です。",
    "支払条件は月末締め、翌月末払いです。"
  ],
  "scope": "合成10発言の接続・権限・索引確認。顧客データでの精度評価や大規模ベンチマークではない。"
}
```

## container.json

```json
{
  "status": "passed",
  "uid": "10001",
  "search_results": 4,
  "data": "synthetic only"
}
```

## 実際の画面

[営業：過去と最新の経緯](assets/demo-sales.png)
[開発：利用者による権限差](assets/demo-development.png)
[モバイル表示](assets/demo-mobile.png)

## 未実施：顧客環境への統合

実Chatwork OAuth、管理者エクスポート実形式、Google Workspace本人確認、Firestore／Secret Manager実接続、ChatGPT Businessへの登録、Cloud Run実デプロイは未実施です。実データの検索精度・負荷・保持削除・運用監視・第三者監査も別途検収が必要です。

## 再現と配布

READMEの手順でローカル起動できます。テスト・実モデル・画面操作はscriptsとtestsに同梱しています。実行時のソース、検証記録、画面を含むZIPはこのActions実行のArtifactsに保存します。モデル重み、実DB、秘密情報は含みません。
