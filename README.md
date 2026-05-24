# 💳 Credit Portfolio Optimizer
### クレジットカード与信枠アロケーション数理最適化シミュレータ

[![Streamlit App](https://static.streamlit.io/badge_svg.svg)](https://share.streamlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)

本アプリケーションは、数理最適化（Pyomo + NLPソルバー Ipopt）を用いて、クレジットカード会員個々のデフォルトリスク（デフォルト確率: PD、貸倒損失率: LGD）と決済意欲の弾力性モデルを考慮し、**ポートフォリオ全体の期待純利益を最大化（または期待損失を最小化）する最適な与信枠（クレジットカード限度額）アロケーションを動的にシミュレーションするダッシュボード**です。

---

## 🌟 主な機能と特徴

### 1. 🏦 現状ポートフォリオ分析 (マクロ統計の可視化)
最適化を実行する前に、現行のルールベースで設定されている与信枠における全体マクロ統計（総与信、平均PD、期待損失、期待純利益など）および顧客属性（セグメント、職業、信用格付け、与信枠分布、セグメント×格付けヒートマップ）を瞬時に可視化し、現状のポートフォリオの構造的な課題を把握できます。

### 2. 🎯 最適化シミュレーション (意思決定と実務的ルール適用の連動)
- **2つの最適化アプローチの選択:**
  - **期待純利益の最大化 (Profit Max):** ポートフォリオ全体の期待損失額（貸倒引当金）を一定の「許容上限」以下に抑えた状態で、期待純利益を最大化します。
  - **期待損失の最小化 (Risk Min):** 経営上の必達目標である「目標期待純利益」をクリアした状態で、全体のデフォルト期待損失を最小化します。
- **実務ルール（後処理）の自動適用:**
  連続ソルバーが算出した理論上の連続値解に対し、自動的に以下の実務的ビジネスルールを適用します。
  - 与信限度額メニュー（{10, 30, 50, 70, 100}万円）への丸め（離散化）
  - 主婦・学生属性に対する一律上限制限（10万円以下）
  - ショッピング専用セグメントに対する減枠禁止ルール（顧客離反防止）
- **「制約突き抜け」リスクの自動警告:**
  ソルバーの理論解に対し、上記の後処理を適用することで発生する「期待損失の上限オーバー」や「目標純利益の未達」といった実務上の制約違反リスクを動的に検知し、経営上の意思決定を警告やインサイトとしてダッシュボード上でフィードバックします。

### 3. 🌪️ モデル堅牢性・感度分析
顧客の決済ポテンシャル $C$、与信枠反応度 $k$、デフォルト確率 $PD$ などの外部パラメータが $\pm 10\%$ 変動した際に、最適化ポートフォリオの期待純利益が受ける感度を**トルネードチャート**で可視化します。どのモデルパラメータが経営成績（ROI）に強い影響を与えるかを定量的に把握できます。

---

## 📐 数理モデルと定式化概要

### 1. 意思決定変数
- $L_i \in [0, 100]$: 顧客 $i$ の与信枠限度額（万円）

### 2. 想定年間利用額 (EAD) の反応性モデル
与信限度額の大きさに応じて、顧客の決済意欲は非線形（指数飽和型）に追従します。
$$EAD_i(L_i) = C_i \left(1 - e^{-k_i L_i}\right)$$
- $C_i$: 顧客 $i$ の潜在的な最大年間決済額（万円）
- $k_i$: 与信枠の増減に対する利用の反応感度

### 3. 目的関数と制約式

#### 利益最大化モード (Profit Maximization)
$$\text{Maximize} \quad \sum_{i} \left[ r_i \cdot EAD_i(L_i) \cdot (1 - PD_i) - PD_i \cdot LGD_i \cdot EAD_i(L_i) \right]$$
$$\text{Subject to} \quad \sum_{i} \left[ PD_i \cdot LGD_i \cdot EAD_i(L_i) \right] \le T_{\text{risk}}$$
- $r_i$: 顧客 $i$ のリターン利回り比率
- $PD_i$: 顧客 $i$ のデフォルト確率
- $LGD_i$: 顧客 $i$ のデフォルト時損失率
- $T_{\text{risk}}$: ポートフォリオ全体の許容期待損失上限

---

## 🛠️ インストールと起動手順

### 1. Conda 環境の構築 (推奨)
本アプリは数理ソルバーである `ipopt` を必要とします。リポジトリに同梱されている `environment.yml` を利用することで、ソルバーとすべてのPython依存関係を確実に一括インストールできます。

```bash
# 1. リポジトリをクローンまたはローカルディレクトリに移動
cd credit_card_demo

# 2. environment.yml から Conda 環境を作成
conda env create -f environment.yml

# 3. 作成した環境を有効化
conda activate credit_card_demo
```

### 2. アプリケーションの起動
```bash
streamlit run app.py
```
起動完了後、ブラウザで **http://localhost:8501**（または指定のポート）を開いてください。

---

## ☁️ Streamlit Community Cloud へのデプロイ

本リポジトリは Streamlit Community Cloud へ直接デプロイ可能です。デプロイ時の環境の差異（OSライブラリのパスの違い等）を吸収するためのロバストな仕組みを実装しています。

1. **環境設定ファイルの自動認識:**
   リポジトリルートにある `environment.yml` を Streamlit Cloud が自動認識し、Condaコンテナを起動するため、クラウド上でもシステムレベルで `ipopt` ソルバーが自動インストールされます。
2. **動的実行ファイルパス検出:**
   `app.py` 内の自動検出ロジックが、Streamlit Cloud 環境のパス（`/home/adminuser/.conda/bin/ipopt`）を自動検知して Pyomo に引き渡すため、追加設定なしでデプロイ直後から稼働します。

---

## 🔬 技術スタック

- **Front-end / Dashboard:** Streamlit
- **Optimization Model:** Pyomo
- **Solver Engine:** Ipopt (COIN-OR)
- **Data Manipulation:** Pandas, NumPy
- **Data Visualization:** Plotly (Premium Interactive Chart), Matplotlib

---

## 📄 ライセンス
本プロジェクトは [MIT License](LICENSE) の下で公開されています。
