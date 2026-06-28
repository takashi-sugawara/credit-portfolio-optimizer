# Azure App Service 移行 振り返り

## ✅ 今日やったこと

Streamlit Community Cloud で動いていたアプリを Azure App Service へ移行した。

---

## 📋 作業の流れと時間がかかったポイント

### Phase 1：方針検討（比較的スムーズ）
- リポジトリのクローンと構成確認
- `ipopt` ソルバーの依存関係を発見 → 対応方針の検討
- **当初は Docker コンテナ案** を提示したが、ユーザーの希望でシンプルな App Service 案に変更

### Phase 2：デプロイ設定（最も時間がかかった）

| 問題 | 原因 | 解決策 |
|------|------|--------|
| `SCM_DO_BUILD_DURING_DEPLOYMENT=true` が反映されない | Azure CLI のバグ（`value: null` 表示） | `az webapp up` コマンドで自動設定 |
| `/antenv/bin/activate: No such file` のループ | Oryx が仮想環境を作らずに起動しようとしていた | ビルド設定を有効化 |
| `startup.sh: No such file or directory` | Oryx がファイルを `/tmp/ランダム名/` に解凍するため絶対パスが使えない | ファイル参照をやめ、起動コマンドに直接記述 |
| アプリが起動しても `:( Application Error` | Streamlit はポート `8080` で起動、Azure は `8000` を監視 → ポート不一致 | 起動コマンドのポートを `8000` に変更 |

### Phase 3：パフォーマンス確認（スムーズ）
- CPU 使用量を Azure Monitor で確認
- 最適化計算時に CPU がほぼ 100% に達することを確認
- B1 → B2 にスケールアップ

---

## 🔑 「仏に魂を込める」ポイント

**技術的に正しい設定をしても、最後に動かない理由は「環境の差異」にある。**

今日の最大のハマりどころはすべて「Azure の内部動作（Oryx のビルド・解凍の仕組み）」を知らなかったことに起因していた。

### 具体的に何が求められたか

1. **ログを読む力** — Azure のログは情報量が多いが、本質的なエラー行を見つける
2. **エラーの原因を仮説立て** — 「ファイルが無い」→「パスが違う」→「Oryx が `/tmp` に解凍している」と逆引きする
3. **1つ変数を変えて再試行** — 複数を同時に変えると何が効いたかわからなくなる
4. **ポートの概念** — アプリが起動していても外部からアクセスできない理由がポート不一致にあると気づく

> Streamlit Community Cloud は「全部おまかせ」なので意識しなくて済むが、App Service は「自分でポートを合わせる」「自分で仮想環境を作るか確認する」といった一段低いレイヤーの知識が必要になる。

---

## 💰 節約方法

### 今すぐできること

#### 1. 使わないときはアプリを「停止」する
```bash
az webapp stop --name credit-portfolio-optimizer --resource-group rg-credit-optimizer-west
```
再開するとき：
```bash
az webapp start --name credit-portfolio-optimizer --resource-group rg-credit-optimizer-west
```
> ⚠️ **注意**: App Service Plan（プラン）は止まらないので、B2の固定費（~$26/月）は発生し続ける。ただし、停止中は amplpy のインストールなどが走らない分、起動コストはゼロ。

#### 2. 使わない期間はプランごと「削除」する（最強の節約）
```bash
# プランとアプリを削除（データは GitHub にあるので消えても OK）
az group delete --name rg-credit-optimizer-west --yes
```
次回使うときは `az webapp up` で1コマンドで再構築できる。
> 💡 今日の手順を理解できていれば、次回は30分で再デプロイ可能。

#### 3. リージョンを Japan East に変更する（次回検討）
Japan West より Japan East の方が若干安い場合がある。
また Japan East はサービスの展開が早く、新機能が使いやすい。

---

### コスト感の目安

| 利用パターン | 月額概算 |
|-------------|---------|
| 毎日8時間だけ使う（停止活用） | B2では節約にならない（固定費のため） |
| 週1〜2回だけ使う | 使わないときプラン削除 → 再構築が最安 |
| 毎日使う | B2のまま ~$26/月 |

---

## 🗒️ 次のステップ候補（参考）

- [ ] 毎回起動のたびに `amplpy install coin` が走る問題を改善（起動を速くする）
- [ ] 最適化計算を Azure Functions などに疎結合化（CPU爆発問題の根本解決）
- [ ] カスタムドメインの設定（現状は `.azurewebsites.net`）
