import streamlit as st
import pandas as pd
import numpy as np
import time
from domain.config import SCENARIO_PD_MULTIPLIERS
from domain.solver import (
    solve_optimization,
    get_efficient_frontiers as _get_efficient_frontiers,
    calculate_portfolio_profit,
    run_sensitivity_analysis as _run_sensitivity_analysis,
)

# グラフライブラリのインポート（Plotlyを優先、Matplotlibをフォールバック）
USE_PLOTLY = True
try:
    import plotly.graph_objects as go
    import plotly.express as px
except ImportError:
    USE_PLOTLY = False
    import matplotlib.pyplot as plt

# ==========================================
# 1. ページ基本設定（モダンなワイドレイアウト）
# ==========================================
st.set_page_config(
    page_title="Credit Limit Portfolio Optimization",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# 1.5 Language Selection
# ==========================================
lang = st.sidebar.selectbox("Language / 言語", ["English", "日本語"])
st.sidebar.markdown("---")

def t(en, jp):
    return en if lang == "English" else jp


# プレミアムなダーク/ライト調和テーマ用のCSS
st.markdown("""
<style>
    .reportview-container {
        background: #f8f9fa;
    }
    .metric-card {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border-left: 5px solid #1f77b4;
        margin-bottom: 10px;
    }
    .metric-card-positive {
        border-left: 5px solid #2ca02c;
    }
    .metric-card-negative {
        border-left: 5px solid #d62728;
    }
    .metric-title {
        font-size: 14px;
        color: #6c757d;
        font-weight: bold;
    }
    .metric-value {
        font-size: 24px;
        color: #212529;
        font-weight: bold;
    }
    .metric-delta {
        font-size: 14px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. ダミーデータ生成（1,000人分のポートフォリオ）
# ==========================================
@st.cache_data
def generate_customer_data(n_customers=1000, seed=42):
    np.random.seed(seed)
    
    # 顧客ID
    customer_ids = [f"C{str(i).zfill(4)}" for i in range(1, n_customers + 1)]
    
    # セグメントの配分（Shopping: 50%, Revo: 30%, Cashing: 10%, Combo: 10%）
    segments = np.random.choice(
        ['Shopping', 'Revo', 'Cashing', 'Combo'],
        size=n_customers,
        p=[0.5, 0.3, 0.1, 0.1]
    )
    
    # 職業属性の追加 (Employee: 80%, Housewife: 15%, Student: 5%)
    jobs = np.random.choice(
        ['Employee', 'Housewife', 'Student'],
        size=n_customers,
        p=[0.8, 0.15, 0.05]
    )
    
    # 信用格付け (1が最も優良, 10が最もリスク高)
    ratings = np.random.choice(
        list(range(1, 11)),
        size=n_customers,
        p=[0.05, 0.10, 0.15, 0.20, 0.15, 0.12, 0.10, 0.08, 0.03, 0.02]
    )
    
    # 基本デフォルト確率 (PD): 格付けに応じて指数関数的に上昇 (0.1% 〜 10.0%)
    base_pds = 0.001 * (ratings ** 2)
    
    # セグメントに応じたPDの補正
    segment_pd_multipliers = {
        'Shopping': 0.5,
        'Revo': 1.2,
        'Cashing': 2.0,
        'Combo': 2.5
    }
    
    pds = np.array([base_pds[i] * segment_pd_multipliers[segments[i]] for i in range(n_customers)])
    pds = np.clip(pds, 0.0005, 0.25)
    
    # 回収不能率 (LGD) - 一律80%
    lgds = np.ones(n_customers) * 0.8
    
    # 適用収益率 (r)
    segment_rates = {
        'Shopping': 0.02, # 2.0%
        'Revo': 0.15,     # 15.0%
        'Cashing': 0.18,  # 18.0%
        'Combo': 0.165    # 16.5%
    }
    rates = np.array([segment_rates[s] for s in segments])
    
    # 想定最大決済ポテンシャル C_i (万円/年)
    c_potentials = np.zeros(n_customers)
    for i in range(n_customers):
        if segments[i] == 'Shopping':
            c_potentials[i] = np.random.uniform(20, 50)
        elif segments[i] == 'Revo':
            c_potentials[i] = np.random.uniform(40, 90)
        elif segments[i] == 'Cashing':
            c_potentials[i] = np.random.uniform(30, 60)
        else: # Combo
            c_potentials[i] = np.random.uniform(50, 120)
            
    c_potentials = c_potentials * (1.2 - 0.05 * ratings)
    c_potentials = np.clip(c_potentials, 10, 150)
    
    # 利用額の飽和係数 k_i
    segment_k = {
        'Shopping': 0.06,
        'Revo': 0.04,
        'Cashing': 0.03,
        'Combo': 0.04
    }
    k_factors = np.array([segment_k[s] for s in segments]) * np.random.uniform(0.8, 1.2, size=n_customers)
    
    # 現行の与信枠 (ルールベース limit)
    current_limits = np.zeros(n_customers)
    limit_options = [10, 30, 50, 70, 100]
    
    for i in range(n_customers):
        rating = ratings[i]
        segment = segments[i]
        job = jobs[i]
        
        # 基本の枠決定ルール
        if rating <= 2:
            base_limit = 100
        elif rating <= 5:
            base_limit = 70
        elif rating <= 7:
            base_limit = 50
        elif rating <= 8:
            base_limit = 30
        else:
            base_limit = 10
            
        # リスクのあるセグメントは一段階引き下げる
        if segment in ['Cashing', 'Combo'] and base_limit > 10:
            idx = limit_options.index(base_limit)
            base_limit = limit_options[idx - 1]
            
        # 業界ルール：主婦・学生は一律10万円上限とする (初期与信枠)
        if job in ['Housewife', 'Student']:
            base_limit = 10
            
        current_limits[i] = base_limit
        
    df = pd.DataFrame({
        'CustomerID': customer_ids,
        'Segment': segments,
        'Rating': ratings,
        'PD': pds,
        'LGD': lgds,
        'RevenueRate': rates,
        'PotentialC': c_potentials,
        'AlphaK': k_factors,
        'Job': jobs,
        'CurrentLimit': current_limits
    })
    
    return df

# ==========================================
# 3. 数理最適化エンジンの呼び出し（本体は domain/solver.py）
# ==========================================
@st.cache_data
def get_efficient_frontiers(df):
    return _get_efficient_frontiers(df)


def run_sensitivity_analysis(df, target_value, pd_mult, opt_mode):
    return _run_sensitivity_analysis(
        df, target_value, pd_mult, opt_mode,
        parameter_labels={
            'potential_c': t('Customer Payment Potential C', '顧客決済ポテンシャル C'),
            'alpha_k': t('Credit Utilization Responsiveness k', '与信利用反応度 k'),
            'pd': t('Probability of Default PD', 'デフォルト確率 PD'),
        },
    )

# ==========================================
# 5. メインアプリケーション処理
# ==========================================

# データ生成
df_customers = generate_customer_data()

# タイトルエリア
st.title(t("💳 Credit Limit Portfolio Optimization Demo", "💳 クレジットカード与信ポートフォリオ全体最適化デモ"))
st.markdown(t("##### A simulator connecting macro profit/risk trade-offs (efficient frontier) and micro individual credit limit adjustments for a portfolio of 1,000 customers", "##### 1,000名の顧客ポートフォリオにおける「マクロな利益・リスクのトレードオフ（効率的フロンティア）」と「ミクロな個人の与信枠調整」を繋ぐシミュレーター"))

# フロンティアの事前計算（初回のみ実行されキャッシュされます）
with st.spinner(t("Calculating efficient frontier curve... (Takes a few seconds on first run)", "効率的フロンティア曲線を計算中... (初回のみ数秒かかります)")):
    frontier_data = get_efficient_frontiers(df_customers)

# タブの作成
tab1, tab2, tab3, tab4 = st.tabs([
    t("🏦 Current Portfolio Analysis", "🏦 現状ポートフォリオ分析"),
    t("🎯 Optimization Simulation", "🎯 最適化シミュレーション"),
    t("📑 Assumptions & Formulation", "📑 前提条件と定式化の整理"),
    t("🌪️ Model Sensitivity Analysis", "🌪️ モデル感度分析（堅牢性の検証）")
])

# ==========================================
# 6. サイドバーコントローラー（操作パネル）
# ==========================================
st.sidebar.header(t("🛠️ Optimization Simulation Settings", "🛠️ 最適化シミュレーション設定"))

# 1. シナリオ選択
scenario_choice = st.sidebar.selectbox(
    t("1. Economic Scenario Selection (Default Probability Fluctuation)", "1. 景気シナリオの選択 (デフォルト確率の変動)"),
    options=[t('Neutral Scenario (Default Prob x1.0)', '中立シナリオ (デフォルト確率 1.0倍)'), t('Optimistic Scenario (Default Prob x0.7)', '楽観シナリオ (デフォルト確率 0.7倍)'), t('Pessimistic Scenario (Default Prob x1.5)', '悲観シナリオ (デフォルト確率 1.5倍)')],
    index=0
)
scenario_map = {
    t('Neutral Scenario (Default Prob x1.0)', '中立シナリオ (デフォルト確率 1.0倍)'): ('Neutral', SCENARIO_PD_MULTIPLIERS['Neutral'], "🔵"),
    t('Optimistic Scenario (Default Prob x0.7)', '楽観シナリオ (デフォルト確率 0.7倍)'): ('Optimistic', SCENARIO_PD_MULTIPLIERS['Optimistic'], "🟢"),
    t('Pessimistic Scenario (Default Prob x1.5)', '悲観シナリオ (デフォルト確率 1.5倍)'): ('Pessimistic', SCENARIO_PD_MULTIPLIERS['Pessimistic'], "🔴")
}
sc_name, pd_mult, sc_emoji = scenario_map[scenario_choice]

# 現行ルールベースのポートフォリオのリスク・リターンの再計算
current_total_rev = 0
current_total_loss = 0
for i in range(len(df_customers)):
    lim = df_customers['CurrentLimit'].iloc[i]
    ead = df_customers['PotentialC'].iloc[i] * (1 - np.exp(-df_customers['AlphaK'].iloc[i] * lim))
    pd_sc = df_customers['PD'].iloc[i] * pd_mult
    
    revenue = df_customers['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
    loss = pd_sc * df_customers['LGD'].iloc[i] * ead
    
    current_total_rev += revenue
    current_total_loss += loss
current_net_profit = current_total_rev - current_total_loss

# 2. 最適化アプローチの選択 (ゴール設定)
st.sidebar.markdown("---")
st.sidebar.subheader(t("2. Optimization Approach (Management Goal)", "2. 最適化アプローチ (経営ゴールの設定)"))
opt_mode_label = st.sidebar.radio(
    t("Target Management KPI", "目標とする経営KPI"),
    options=[
        t("🎯 Maximize Profit (Limit Expected Loss)", "🎯 利益の最大化 (期待損失を一定以下に制限)"),
        t("🛡️ Minimize Risk (Achieve Target Expected Net Profit)", "🛡️ リスクの最小化 (期待純利益の目標値を達成)")
    ],
    index=0,
    help=t("Select an aggressive (maximize profit) or defensive (minimize risk) approach based on company policy.", "会社の経営方針に合わせて、攻め（利益最大化）か守り（リスク最小化）かのアプローチを選択します。")
)
opt_mode = 'profit_max' if "利益の最大化" in opt_mode_label else 'risk_min'

# 最適化アプローチの解説ボックス
if opt_mode == 'profit_max':
    st.sidebar.info(t("""
    **🎯 Profit Maximization Approach (Aggressive)**
    Calculates the credit limit that maximizes the net profit of the entire portfolio within the allowed range of the set "Expected Loss Upper Limit". Encourages **"Limit Increase Actions"** for excellent customers.
    """, """
    **🎯 利益最大化アプローチ（攻め）**
    設定した「期待損失上限」の許容範囲の中で、ポートフォリオ全体の純利益を最も大きくする与信枠を計算します。優良顧客への**「増枠アクション」**が促されます。
    """))
else:
    st.sidebar.success(t("""
    **🛡️ Risk Minimization Approach (Defensive)**
    Calculates the credit limit that minimizes the overall default expected loss (bad debt) to the utmost limit while clearing the "Target Net Profit" that must be achieved. Encourages **"Limit Decrease Actions"** for risky customers.
    """, """
    **🛡️ リスク最小化アプローチ（守り）**
    必達したい「目標純利益」をクリアした上で、全体のデフォルト期待損失額（焦げ付き）を極限まで低くする与信枠を計算します。危険顧客への**「減枠アクション」**が促されます。
    """))

# スライダーの動的切り替え
if opt_mode == 'profit_max':
    st.sidebar.markdown(t("**📊 Risk Upper Limit Adjustment**", "**📊 リスク上限の調整**"))
    st.sidebar.write(t(f"Expected loss under current rules: **{current_total_loss:.1f} (10k JPY)**", f"現行ルールの期待損失額: **{current_total_loss:.1f} 万円**"))
    min_slider = float(np.round(current_total_loss * 0.4, 0))
    max_slider = float(np.round(current_total_loss * 1.6, 0))
    default_slider = float(np.round(current_total_loss, 0))
    
    target_value = st.sidebar.slider(
        t("Total Allowable Expected Loss Upper Limit (10k JPY)", "全体の許容期待損失上限 (万円)"),
        min_value=min_slider,
        max_value=max_slider,
        value=default_slider,
        step=5.0,
        help=t("The loss of all 1000 people will definitely be confined within this total expected loss (allowance).", "この期待損失（引当金）の総額の中に、1000人全員の損失を絶対に閉じ込めます。")
    )
else:
    st.sidebar.markdown(t("**📈 Sales (Net Profit) Target Adjustment**", "**📈 売上（純利益）目標の調整**"))
    st.sidebar.write(t(f"Expected net profit under current rules: **{current_net_profit:.1f} (10k JPY)**", f"現行ルールの期待純利益: **{current_net_profit:.1f} 万円**"))
    
    max_possible_profit = max(frontier_data[sc_name]['profit'])
    min_slider = float(np.round(current_net_profit * 0.8, 0))
    max_slider = float(np.round(max_possible_profit * 0.98, 0))
    default_slider = float(np.round(current_net_profit, 0))
    
    target_value = st.sidebar.slider(
        t("Company-wide Expected Net Profit Target (10k JPY)", "会社全体の期待純利益目標 (万円)"),
        min_value=min_slider,
        max_value=max_slider,
        value=default_slider,
        step=5.0,
        help=t("Verifies how much the overall default risk (expected loss) can be minimized (reduced) while maintaining the expected net profit above the current level.", "期待純利益を現行以上に維持したまま、全体のデフォルトリスク（期待損失額）をどれだけ最小化（削減）できるかを検証します。")
    )

# 3. 最適化の実行ボタン
st.sidebar.markdown("---")
st.sidebar.subheader(t("3. Execute Optimization", "3. 最適化の実行"))
st.sidebar.write(t("After setting the parameters, clicking the button runs the Ipopt solver to execute the global optimization allocation.", "パラメータを設定後、ボタンを押すとIpoptソルバーが走り、全体最適化アロケーションが実行されます。"))
run_opt = st.sidebar.button(t("⚡ Execute Optimization Allocation", "⚡ 最適化アロケーションを実行"), type="primary", width='stretch')

# セッション状態での実行制御と変更検知
if 'optimized' not in st.session_state:
    st.session_state.optimized = False
if 'prev_scenario' not in st.session_state:
    st.session_state.prev_scenario = scenario_choice
if 'prev_opt_mode' not in st.session_state:
    st.session_state.prev_opt_mode = opt_mode_label
if 'prev_target_value' not in st.session_state:
    st.session_state.prev_target_value = target_value

# パラメータ変更の検知（変更されたら未最適化状態に戻す）
if (st.session_state.prev_scenario != scenario_choice or
    st.session_state.prev_opt_mode != opt_mode_label or
    st.session_state.prev_target_value != target_value):
    st.session_state.optimized = False
    st.session_state.prev_scenario = scenario_choice
    st.session_state.prev_opt_mode = opt_mode_label
    st.session_state.prev_target_value = target_value

if run_opt:
    st.session_state.optimized = True

# ==========================================
# 7. Tab1: 現状ポートフォリオ分析（最適化不要）
# ==========================================
with tab1:
    st.markdown(t("### 🏦 Current Portfolio Analysis", "### 🏦 現状ポートフォリオ分析"))
    st.write(t("Visualizes the distribution of customer attributes and credit limits under the current rule-based credit allocation before performing mathematical optimization. By understanding the current situation, the 'changes' brought about by optimization will become clearer.", "数理最適化を行う前の、現在のルールベース与信における顧客属性と与信枠の分布を可視化します。現状を把握することで、最適化による「変化」がより鮮明に見えてきます。"))

    # --- KPIカード（4枚）---
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">{t("👥 Total Customers", "👥 総顧客数")}</div>
            <div class="metric-value">{len(df_customers):,} {t("people", "名")}</div>
            <div class="metric-delta" style="color: #6c757d;">{t("Portfolio under analysis", "分析対象ポートフォリオ")}</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">{t("💴 Total Credit Limit (Current)", "💴 総与信限度額（現行）")}</div>
            <div class="metric-value">{df_customers['CurrentLimit'].sum():,.0f} {t("10k JPY", "万円")}</div>
            <div class="metric-delta" style="color: #6c757d;">{t("Total credit limit of the entire portfolio", "ポートフォリオ全体の与信枠総額")}</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        avg_rating = df_customers['Rating'].mean()
        st.markdown(f"""
        <div class="metric-card metric-card-positive">
            <div class="metric-title">{t("⭐ Avg Credit Rating", "⭐ 平均信用格付け")}</div>
            <div class="metric-value">{avg_rating:.2f}</div>
            <div class="metric-delta" style="color: #2ca02c;">{t("1:Best to 10:Highest Risk", "1:最優良 〜 10:最高リスク")}</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        avg_pd = df_customers['PD'].mean() * 100
        st.markdown(f"""
        <div class="metric-card metric-card-negative">
            <div class="metric-title">{t("⚠️ Avg Default Probability (PD)", "⚠️ 平均デフォルト確率 (PD)")}</div>
            <div class="metric-value">{avg_pd:.2f}%</div>
            <div class="metric-delta" style="color: #d62728;">{t("Average PD of the entire portfolio", "ポートフォリオ全体の平均PD")}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # --- 1列目: セグメント構成パイチャート ＋ 職業属性棒グラフ ---
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(t("**📊 Segment Composition (By Purpose)**", "**📊 セグメント構成（利用目的別）**"))
        seg_counts = df_customers['Segment'].value_counts().reset_index()
        seg_counts.columns = ['Segment', 'Count']
        seg_color_map = {
            'Shopping': '#1f77b4',
            'Revo': '#ff7f0e',
            'Cashing': '#d62728',
            'Combo': '#9467bd'
        }
        if USE_PLOTLY:
            fig_pie = go.Figure(go.Pie(
                labels=seg_counts['Segment'],
                values=seg_counts['Count'],
                hole=0.4,
                marker=dict(colors=[seg_color_map.get(s, '#7f7f7f') for s in seg_counts['Segment']]),
                textinfo='label+percent',
                hovertemplate='%{label}<br>' + t('Count: ', '人数: ') + '%{value}' + t('people', '名') + '<br>' + t('Ratio: ', '割合: ') + '%{percent}<extra></extra>'
            ))
            fig_pie.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=20),
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5)
            )
            st.plotly_chart(fig_pie, width='stretch')
        else:
            fig_pie, ax_pie = plt.subplots(figsize=(5, 4))
            ax_pie.pie(seg_counts['Count'], labels=seg_counts['Segment'], autopct='%1.1f%%')
            st.pyplot(fig_pie)

    with col_right:
        st.markdown(t("**👔 Occupation Attribute Distribution**", "**👔 職業属性分布**"))
        job_counts = df_customers['Job'].value_counts().reset_index()
        job_counts.columns = ['Job', 'Count']
        job_color_map = {'Employee': '#1f77b4', 'Housewife': '#ff7f0e', 'Student': '#2ca02c'}
        if USE_PLOTLY:
            fig_job = go.Figure(go.Bar(
                x=job_counts['Job'],
                y=job_counts['Count'],
                marker_color=[job_color_map.get(j, '#7f7f7f') for j in job_counts['Job']],
                text=job_counts['Count'],
                textposition='outside',
                hovertemplate='%{x}<br>' + t('Count: ', '人数: ') + '%{y}' + t('people', '名') + '<extra></extra>'
            ))
            fig_job.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title=t("Occupation", "職業属性"),
                yaxis_title=t("Number of Customers", "顧客数 (名)"),
                showlegend=False
            )
            st.plotly_chart(fig_job, width='stretch')
        else:
            fig_job, ax_job = plt.subplots(figsize=(5, 4))
            ax_job.bar(job_counts['Job'], job_counts['Count'])
            ax_job.set_ylabel(t("Number of Customers", "顧客数 (名)"))
            st.pyplot(fig_job)

    st.markdown("---")

    # --- 2列目: 信用格付け分布ヒストグラム ＋ 与信枠分布 ---
    col_left2, col_right2 = st.columns(2)

    with col_left2:
        st.markdown(t("**📉 Credit Rating Distribution**", "**📉 信用格付け（Rating）分布**"))
        rating_counts = df_customers['Rating'].value_counts().sort_index().reset_index()
        rating_counts.columns = ['Rating', 'Count']
        if USE_PLOTLY:
            # 格付けに応じてグラデーション着色
            colors_rating = [
                f'hsl({int(120 - r * 12)}, 70%, 50%)' for r in rating_counts['Rating']
            ]
            fig_rating_hist = go.Figure(go.Bar(
                x=rating_counts['Rating'],
                y=rating_counts['Count'],
                marker_color=colors_rating,
                text=rating_counts['Count'],
                textposition='outside',
                hovertemplate=t('Rating: ', '格付け: ') + '%{x}<br>' + t('Count: ', '顧客数: ') + '%{y}' + t('people', '名') + '<extra></extra>'
            ))
            fig_rating_hist.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(title=t("Credit Rating (1:Best to 10:Highest Risk)", "信用格付け (1:最優良 〜 10:最高リスク)"), tickmode='linear'),
                yaxis_title=t("Number of Customers", "顧客数 (名)"),
                showlegend=False
            )
            st.plotly_chart(fig_rating_hist, width='stretch')
        else:
            fig_rh, ax_rh = plt.subplots(figsize=(5, 4))
            ax_rh.bar(rating_counts['Rating'], rating_counts['Count'])
            ax_rh.set_xlabel(t("Credit Rating", t("Credit Rating", "信用格付け")))
            ax_rh.set_ylabel(t("Number of Customers", "顧客数 (名)"))
            st.pyplot(fig_rh)

    with col_right2:
        st.markdown(t("**💳 Current Credit Limit Distribution**", "**💳 現行与信枠（CurrentLimit）分布**"))
        limit_counts = df_customers['CurrentLimit'].value_counts().sort_index().reset_index()
        limit_counts.columns = ['Limit', 'Count']
        limit_counts['Limit'] = limit_counts['Limit'].astype(int)
        limit_colors = ['#17a2b8', '#1f77b4', '#2ca02c', '#ff7f0e', '#9467bd']
        if USE_PLOTLY:
            fig_limit = go.Figure(go.Bar(
                x=[f"{int(l)}{t(' (10k JPY)', '万円')}" for l in limit_counts['Limit']],
                y=limit_counts['Count'],
                marker_color=limit_colors[:len(limit_counts)],
                text=limit_counts['Count'],
                textposition='outside',
                hovertemplate=t('Credit Limit: ', '与信枠: ') + '%{x}<br>' + t('Count: ', '顧客数: ') + '%{y}' + t('people', '名') + '<extra></extra>'
            ))
            fig_limit.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title=t("Current Credit Limit", "現行与信枠"),
                yaxis_title=t("Number of Customers", "顧客数 (名)"),
                showlegend=False
            )
            st.plotly_chart(fig_limit, width='stretch')
        else:
            fig_lm, ax_lm = plt.subplots(figsize=(5, 4))
            ax_lm.bar([f"{int(l)}{t(' (10k JPY)', '万円')}" for l in limit_counts['Limit']], limit_counts['Count'])
            ax_lm.set_ylabel(t("Number of Customers", "顧客数 (名)"))
            st.pyplot(fig_lm)

    st.markdown("---")

    # --- セグメント × 格付け ヒートマップ ---
    st.markdown(t("**🔥 Segment x Credit Rating Heatmap (Customer Count)**", "**🔥 セグメント × 信用格付け ヒートマップ（顧客数）**"))
    st.write(t("Visualizes how many customers of which rating are in each segment. You can see the difference in risk structure by segment at a glance.", "各セグメントにどの格付けの顧客が何名いるかを可視化。セグメント別のリスク構造の違いが一目でわかります。"))
    heatmap_df = df_customers.groupby(['Segment', 'Rating']).size().unstack(fill_value=0)
    # 全格付け列が存在することを保証
    for r in range(1, 11):
        if r not in heatmap_df.columns:
            heatmap_df[r] = 0
    heatmap_df = heatmap_df[sorted(heatmap_df.columns)]

    if USE_PLOTLY:
        fig_heat = go.Figure(go.Heatmap(
            z=heatmap_df.values,
            x=[f"Rating {r}" for r in heatmap_df.columns],
            y=heatmap_df.index.tolist(),
            colorscale='RdYlGn_r',
            text=heatmap_df.values,
            texttemplate='%{text}' + t('people', '名'),
            hovertemplate=t('Segment: ', 'セグメント: ') + '%{y}<br>' + t('Rating: ', '格付け: ') + '%{x}<br>' + t('Count: ', '顧客数: ') + '%{z}' + t('people', '名') + '<extra></extra>',
            colorbar=dict(title=t("Customer Count", "顧客数"))
        ))
        fig_heat.update_layout(
            template="plotly_dark",
            height=300,
            margin=dict(l=20, r=20, t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title=t("Credit Rating (1:Best to 10:Highest Risk)", "信用格付け (1:最優良 → 10:最高リスク)"),
            yaxis_title=t("Segment", "セグメント")
        )
        st.plotly_chart(fig_heat, width='stretch')
    else:
        fig_hm, ax_hm = plt.subplots(figsize=(10, 3))
        im = ax_hm.imshow(heatmap_df.values, cmap='RdYlGn_r', aspect='auto')
        ax_hm.set_xticks(range(len(heatmap_df.columns)))
        ax_hm.set_xticklabels([f"R{r}" for r in heatmap_df.columns])
        ax_hm.set_yticks(range(len(heatmap_df.index)))
        ax_hm.set_yticklabels(heatmap_df.index)
        plt.colorbar(im, ax=ax_hm)
        st.pyplot(fig_hm)

    st.markdown("---")

    # --- 収益性の現状スナップショット（シナリオ連動）---
    st.markdown(t(f"**📈 Current Profitability Snapshot (Selected Scenario: {sc_emoji} {scenario_choice})**", f"**📈 現状収益性スナップショット（選択シナリオ: {sc_emoji} {scenario_choice}）**"))
    st.info(t("💡 Changing the \"Economic Scenario\" in the left sidebar updates these profitability metrics in real-time as the probability of default fluctuates.", "💡 左サイドバーの「景気シナリオ」を変えると、デフォルト確率の変動に伴い、この収益性指標がリアルタイムに更新されます。"))
    snap1, snap2, snap3 = st.columns(3)
    with snap1:
        st.metric(
            label=t("📈 Current Exp Net Profit", "📈 現行の期待純利益"),
            value=f"{current_net_profit:,.1f} {t(' (10k JPY)', '万円')}",
            help=t("Annual expected net profit (revenue - loss) under the current rule-based credit limit", "現行のルールベース与信における年間期待純利益（収益 - 損失）")
        )
    with snap2:
        st.metric(
            label=t("⚠️ Current Exp Loss (Allowance)", "⚠️ 現行の期待損失額（引当金）"),
            value=f"{current_total_loss:,.1f} {t(' (10k JPY)', '万円')}",
            help=t("Default expected loss amount under the current rule-based credit limit", "現行のルールベース与信におけるデフォルト期待損失額")
        )
    with snap3:
        current_efficiency = current_net_profit / current_total_loss if current_total_loss > 0 else 0
        st.metric(
            label=t("⚡ Risk-Adjusted Efficiency", "⚡ リスク対比運用効率"),
            value=f"{current_efficiency:.2f}",
            help=t("Net profit ÷ Expected loss amount. The higher this value, the higher the profitability relative to the risk.", "純利益 ÷ 期待損失額。この値が高いほどリスク対比の収益性が高い")
        )

# ==========================================
# 8. Tab2: 最適化シミュレーション（メインダッシュボード）
# ==========================================
with tab3:
    st.markdown(t("### 📑 Mathematical Model and Assumptions for Credit Portfolio Global Optimization", "### 📑 与信ポートフォリオ全体最適化の数理モデルと前提条件"))
    st.markdown(t(r"""
    This system optimizes the "risk (expected loss)" and "return (expected net profit)" of the entire customer portfolio by combining financial engineering and mathematical optimization techniques (nonlinear programming).
    The following explains the assumptions, mathematical model formulation, and the process of applying practical business rules used in this simulator.
    """, r"""
    本システムは、金融工学と数理最適化技術（非線形計画法）を組み合わせることで、顧客ポートフォリオ全体の「リスク（期待損失）」と「リターン（期待純利益）」を最適化するシステムです。
    以下に、本シミュレーターで用いられている前提条件、数理モデルの定式化、および実務的なビジネスルールの適用プロセスについて解説します。
    """))

    st.markdown("---")
    st.markdown(t("#### 1. Nonlinear Saturation Curve of Estimated Usage (EAD)", "#### 1. 想定利用額（EAD）の非線形飽和カーブ"))
    st.markdown(t(r"""
    When the credit limit (credit limit) of each customer $i$ is $L_i$ [10k JPY], the annual estimated usage amount (EAD: Exposure at Default) of that customer is modeled to follow a nonlinear **saturation curve (exponential function)** that plateaus (saturates) when it reaches a certain limit (payment potential), rather than growing linearly with the credit limit.
    """, r"""
    各顧客 $i$ の与信枠（限度額）を $L_i$ [万円] としたとき、その顧客の年間想定利用額（EAD: Exposure at Default）は、与信枠に対して線形に伸びるのではなく、ある限界値（決済ポテンシャル）に達すると頭打ちになる（飽和する）非線形な**飽和カーブ（指数関数）**に従うとモデリングしています。
    """))
    st.latex(r"\text{EAD}_i(L_i) = C_i \times \left(1 - e^{-k_i \times L_i}\right)")
    st.markdown(t(r"""
    - $C_i$ : Potential annual maximum payment potential of customer $i$ (estimated value of annual income and payment ability)
    - $k_i$ : Responsiveness of usage amount to the credit limit (coefficient indicating the saturation speed)

    *This modeling can reproduce the practical dynamic that "giving an excessive limit to a customer with little usage is wasteful, and the limit should be allocated to customers with high usage motivation."*

    > **⚠️ Notes on parameter estimation and model limitations for actual operation:**
    > - **Estimation of EAD saturation curve parameters ($C_i, k_i$)**: In this simulator, these are generated randomly for demonstration purposes, but when applying to actual operations, it is necessary to estimate parameters by performing statistical fitting (nonlinear regression, etc.) for each customer based on past card usage history and limit change data.
    > - **Fixing the loss given default ($LGD$)**: In this model, $LGD$ is set uniformly at 80% for simplification, but please note that in practice it is a variable that fluctuates greatly depending on the presence of collateral/guarantees, customer segment, and legal recovery routes.
    """, r"""
    - $C_i$ : 顧客 $i$ の潜在的な年間最大決済ポテンシャル（年収や決済能力の推定値）
    - $k_i$ : 与信枠に対する利用額の反応度（飽和スピードを示す係数）

    *このモデリングにより、「利用が少ない顧客に過大な枠を与えても無駄になり、利用意欲の高い顧客に枠を寄せるべき」という実務的なダイナミクスを再現できます。*

    > **⚠️ 実運用に向けたパラメータ推定の注意点とモデルの限界:**
    > - **EAD飽和カーブのパラメータ ($C_i, k_i$) の推定**: 本シミュレーターではデモ用にこれらを乱数で生成していますが、実運用に適用する際は、過去のカード利用履歴や枠増減データに基づき、顧客ごとに統計的フィッティング（非線形回帰など）を行ってパラメータを推定する必要があります。
    > - **回収不能率 ($LGD$) の固定化**: 本モデルでは簡略化のため $LGD$ を一律 80% と設定していますが、実務上は担保・保証の有無、顧客セグメント、および法的な回収ルートによって大きく変動する変数である点にご留意ください。
    """))

    st.markdown(t("#### 2. Mathematical Formulation of the Entire Portfolio", "#### 2. ポートフォリオ全体の数理定式化"))
    st.markdown(t(r"""
    In this simulator, the following **bidirectional optimization problem** is formulated according to the management strategy, and the optimal solution is calculated instantly using the nonlinear solver **IPOPT**.

    ##### Basic indicators for each individual:
    - **Expected Revenue** : $Rev_i(L_i) = r_i \times \text{EAD}_i(L_i) \times (1 - P_i)$
    - **Expected Loss** : $Loss_i(L_i) = P_i \times \text{LGD} \times \text{EAD}_i(L_i)$
    - **Expected Net Profit** : $Profit_i(L_i) = Rev_i(L_i) - Loss_i(L_i)$

    Where $r_i$ is the revenue rate applied by segment (Shopping: 2.0%, Revolving: 15.0%, Cash advance: 18.0%, Combo: 16.5%), $P_i$ is the customer's probability of default considering the economic scenario multiplier, and $LGD$ is the loss given default (uniformly 80%).
    """, r"""
    本シミュレーターでは、経営戦略に応じて以下の**双方向の最適化問題**を定式化し、非線形ソルバーである **IPOPT** を用いて瞬時に最適解を算出しています。

    ##### 各個人の基本指標:
    - **期待収益 (Revenue)** : $Rev_i(L_i) = r_i \times \text{EAD}_i(L_i) \times (1 - P_i)$
    - **期待損失 (Expected Loss)** : $Loss_i(L_i) = P_i \times \text{LGD} \times \text{EAD}_i(L_i)$
    - **期待純利益 (Net Profit)** : $Profit_i(L_i) = Rev_i(L_i) - Loss_i(L_i)$

    ここで、$r_i$ はセグメントごとの適用収益率（ショッピング: 2.0%, リボ: 15.0%, キャッシング: 18.0%, Combo: 16.5%）、$P_i$ は景気シナリオ倍率を考慮した顧客のデフォルト確率、$LGD$ はデフォルト時の回収不能率（一律 80%）です。
    """))

    st.markdown(t("##### 🎯 Approach A: Profit Maximization Model", "##### 🎯 アプローチ A: 利益最大化モデル (Profit Maximization)"))
    st.markdown(t(r"""
    Maximizes the expected net profit of the entire portfolio while keeping the company's overall expected loss (allowance) below a certain tolerance $T_{risk}$.
    """, r"""
    会社全体の期待損失（引当金）を一定の許容値 $T_{risk}$ 以下に抑えつつ、ポートフォリオ全体の期待純利益を最大化します。
    """))
    st.latex(r"""
    \begin{aligned}
    \text{Maximize} \quad & \sum_{i=1}^{N} Profit_i(L_i) \\
    \text{subject to} \quad & \sum_{i=1}^{N} Loss_i(L_i) \le T_{risk} \\
    & 0 \le L_i \le 100 \quad (\forall i)
    \end{aligned}
    """)

    st.markdown(t("##### 🛡️ Approach B: Risk Minimization Model", "##### 🛡️ アプローチ B: リスク最小化モデル (Risk Minimization)"))
    st.markdown(t(r"""
    Minimizes the default risk (expected loss amount) after clearing the expected net profit target $T_{profit}$ that must be achieved by the company as a whole.
    """, r"""
    会社全体として必達すべき期待純利益目標 $T_{profit}$ をクリアした上で、デフォルトリスク（期待損失額）を最小化します。
    """))
    st.latex(r"""
    \begin{aligned}
    \text{Minimize} \quad & \sum_{i=1}^{N} Loss_i(L_i) \\
    \text{subject to} \quad & \sum_{i=1}^{N} Profit_i(L_i) \ge T_{profit} \\
    & 0 \le L_i \le 100 \quad (\forall i)
    \end{aligned}
    """)

    st.markdown("---")
    st.markdown(t("#### 3. Post-processing Application of Practical Constraints (Business Rules)", "#### 3. 実務的制約（ビジネスルール）の後処理適用"))
    st.markdown(t(r"""
    The 'theoretical solution' of mathematical optimization is a continuous value and cannot be applied as-is to practical operations (customer management, system constraints, legal regulations). Therefore, in this system, the following **post-processing business rules** are strictly applied to the theoretical solution.

    1. **Rounding to Credit Menu (Discretization)**:
       Credit card limits are generally provided in round numbers. In this system, the theoretical optimal value $L_i^*$ is rounded to the nearest credit limit menu of $\{10, 30, 50, 70, 100\}$ (10k JPY) (0 if usage is minimal).

    2. **Uniform Upper Limit for Housewives/Students (Social/Regulatory Rules)**:
       From the perspective of social consideration and repayment ability, for customers in the housewife or student segments, regardless of how large the theoretical solution is, it is uniformly limited to a **maximum of 100k JPY**.
    """, r"""
    数理最適化の「理論解」は連続値であり、そのままでは実務（顧客管理やシステム制約、法規制）に適用できません。そのため、本システムでは理論解に対して以下の**後処理ビジネスルール**を厳格に適用しています。

    1. **与信メニューへの丸め（離散化）**:
       クレジットカードの与信枠は、一般的にキリの良い数字で提供されます。本システムでは、理論最適値 $L_i^*$ に最も近い与信枠メニュー $\{10, 30, 50, 70, 100\}$ 万円に丸め処理（Rounding）を行っています（利用額が極小の場合は 0 万円）。

    2. **主婦・学生の一律上限制限（社会的・規制ルール）**:
       社会的配慮および返済能力の観点から、主婦や学生セグメントの顧客に対しては、理論解がどれほど大きくても一律で**最大 10万円** に制限します。
    """))
    st.latex(r"L_i \leftarrow \min(L_i^*, 10) \quad \text{if } Job_i \in \{\text{Housewife}, \text{Student}\}")
    st.markdown(t(r"""
    3. **Prohibition of Shopping Limit Decrease (CRM / Customer Protection Rules)**:
       A sudden decrease in the shopping limit causes discomfort to customers and poses a serious risk of switching to competitors (churn). Therefore, for customers in segments centered on shopping, the rule **"in principle, no limit decrease below the current credit limit (only maintain or increase)"** is forcibly applied.
    """, r"""
    3. **ショッピング枠の減枠原則禁止（CRM・顧客保護ルール）**:
       ショッピング枠の突然の減枠は、顧客に不快感を与え、競合他社への乗り換え（離反）を招く深刻なリスクです。そのため、ショッピングを主軸とするセグメントの顧客に対しては、**「現在の与信枠を下回る減枠は原則として行わない（維持または増枠のみ）」**というルールを強制適用しています。
    """))
    st.latex(r"L_i \leftarrow \max(L_i^*, L_i^{current}) \quad \text{if } Segment_i = \text{Shopping}")
    st.markdown(t(r"""
    *This hybrid design of 'mathematical optimization × post-processing business rules' is the core of a PoC that balances the beauty of theory with the reality of practice.*
    """, r"""
    *このような「数理最適化 ✕ 後処理ビジネスルール」のハイブリッド設計こそが、理論の美しさと実務のリアリティを両立させる PoC の核心です。*
    """))

    st.markdown("---")
    st.markdown(t("#### 4. 📚 Notes on Model Operation and Accountability (Governance)", "#### 4. 📚 モデル運用上の注意事項と説明責任（ガバナンス）"))
    st.markdown(t("""
    When deploying this mathematical model to actual operations and production systems, it is recommended to consider the following governance and operational requirements.

    1. **Data Freshness and Regular Calibration**:
       - Customer attribute (occupation, annual income, etc.) and credit rating data needs to be updated regularly (e.g., monthly or quarterly).
       - It is important to backtest the parameters of the probability of default (PD) estimation model at least annually, confirm deviations from actual values (calibration), and retrain the model.

    2. **Priority of Business Rules and Human Final Judgment (Human-in-the-Loop)**:
       - The credit allocation plan output by this mathematical model is "advanced reference information that supports decision-making," and the design should be such that the final judgment of the credit screening department and the exception approval process are the top priority for the final application of limits.
       - It is essential to place a "regulatory filter" layer before the mathematical optimization logic to ensure compliance with personal credit information protection laws and various regulations.

    3. **Model Validation and Backtesting**:
       - To verify the effects of optimization (improvement of expected profit, suppression of default losses), it is strongly recommended to implement an **A/B test** in actual operations for a certain period by excluding some of the actual targets as a Control Group, keeping them under the current rule application, and auditing the true effectiveness of the model.
    """, """
    本数理モデルを実業務・本番システムへと展開するにあたっては、以下のガバナンスおよび運用要件を考慮することが推奨されます。

    1. **データの鮮度と定期キャリブレーション**:
       - 顧客の属性（職業、年収等）や信用格付けデータは、定期的に（例：月次・四半期ごと）更新される必要があります。
       - デフォルト確率（PD）推定モデルのパラメータは、少なくとも年次でバックテストを行い、実績値との乖離（キャリブレーション）を確認してモデルを再学習させることが重要です。

    2. **ビジネスルールの優先と人間による最終判断（Human-in-the-Loop）**:
       - 本数理モデルが出力する与信アロケーション計画は「意思決定を支援する高度な参考情報」であり、最終的な限度額適用は、与信審査部門の最終判断や例外承認プロセスを最優先する設計にしてください。
       - 個人の信用情報保護法や各種規制（総量規制など）に抵触しないよう、数理最適化ロジックの前に「法規制フィルタ」をかけるレイヤーを実務上配置することが不可欠です。

    3. **モデル検証とバックテスト**:
       - 最適化による効果（期待利益の向上、デフォルト損失の抑制）を検証するため、実際の適用対象から一部を対照群（Control Group）として除外し、現行ルール適用のままとする **A/Bテスト** を実務上で一定期間実施し、モデルの真の有効性を監査することを強く推奨します。
    """))


# ==========================================
# 10. Tab4: モデル感度分析（堅牢性の検証）タブ
# ==========================================
with tab4:
    st.markdown(t("### 🌪️ Sensitivity Analysis of Portfolio Expected Net Profit (Tornado Chart)", "### 🌪️ ポートフォリオ期待純利益の感度分析（トルネードチャート）"))
    st.markdown(t("""
    This verifies the impact on the portfolio's "optimized expected net profit" when the key parameters of the credit decision model are varied by **±10%**.
    The wider the bar on the graph (top items), the greater the impact that the accuracy of that parameter's estimation has on the overall revenue plan and management decisions, making it the priority target for data quality improvement and monitoring.
    """, """
    与信意思決定モデルの主要パラメータを **±10%** 変動させた際に、ポートフォリオ全体の「最適化後の期待純利益」が受ける影響度（インパクト）を検証したものです。
    グラフの横幅が広い（上の項目）ほど、そのパラメータの推定精度が全体の収益計画や経営判断に対して与える影響が大きいことを意味し、優先的にデータ品質の改善やモニタリングを行うべき対象となります。
    """))

    if not st.session_state.optimized:
        st.info(t("⬅️ **Please run the optimization in the '🎯 Optimization Simulation' tab first.**\n\nSensitivity analysis will be available after the optimization is executed.", "⬅️ **先に「🎯 最適化シミュレーション」タブで最適化を実行してください。**\n\n最適化実行後、感度分析が利用可能になります。"))
    elif st.session_state.get('infeasible', False):
        st.warning(t("⚠️ Because the current optimization condition is Infeasible, sensitivity analysis cannot be executed. Adjust the slider on the left sidebar to a feasible range.", "⚠️ 現在の最適化条件が実行不可能（Infeasible）なため、感度分析を実行できません。左側サイドバーのスライダーを調整して実行可能な範囲にしてください。"))
    else:
        with st.spinner(t("Analyzing parameter sensitivity...", "パラメータ感度を分析中...")):
            sensitivity_results = run_sensitivity_analysis(df_customers, target_value, pd_mult, opt_mode)

        if sensitivity_results:
            sensitivity_results = sorted(sensitivity_results, key=lambda x: max(abs(x['High']), abs(x['Low'])), reverse=True)

            labels = [r['Parameter'] for r in sensitivity_results]
            high_values = [r['High'] for r in sensitivity_results]
            low_values = [r['Low'] for r in sensitivity_results]

            if USE_PLOTLY:
                fig_tornado = go.Figure()

                fig_tornado.add_trace(go.Bar(
                    y=labels,
                    x=high_values,
                    name=t('Parameter +10% Fluctuation', 'パラメータ +10% 変動時'),
                    orientation='h',
                    marker=dict(color='#2ca02c', line=dict(color='white', width=0.5)),
                    hovertemplate=t("Expected Net Profit Change: ", "期待純利益の変動: ") + "%{x:+.1f}" + t("(10k JPY)", "万円") + "<extra></extra>"
                ))

                fig_tornado.add_trace(go.Bar(
                    y=labels,
                    x=low_values,
                    name=t('Parameter -10% Fluctuation', 'パラメータ -10% 変動時'),
                    orientation='h',
                    marker=dict(color='#d62728', line=dict(color='white', width=0.5)),
                    hovertemplate=t("Expected Net Profit Change: ", "期待純利益の変動: ") + "%{x:+.1f}" + t("(10k JPY)", "万円") + "<extra></extra>"
                ))

                fig_tornado.update_layout(
                    template="plotly_dark",
                    barmode='relative',
                    xaxis=dict(title=t("Amount of Change in Optimized Expected Net Profit (10k JPY)", t("Amount of Change in Optimized Expected Net Profit (10k JPY)", "最適化期待純利益の変動量 (万円)")), zeroline=True, zerolinecolor='white', zerolinewidth=1.5),
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=40, r=40, t=10, b=40),
                    height=350,
                    plot_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_tornado, width='stretch')
            else:
                fig_tor, ax_tor = plt.subplots(figsize=(10, 4))
                y_pos = np.arange(len(labels))
                ax_tor.barh(y_pos, high_values, align='center', color='#2ca02c', label='+10% 変動')
                ax_tor.barh(y_pos, low_values, align='center', color='#d62728', label='-10% 変動')
                ax_tor.set_yticks(y_pos)
                ax_tor.set_yticklabels(labels)
                ax_tor.invert_yaxis()
                ax_tor.axvline(0, color='black', lw=1)
                ax_tor.set_xlabel(t("Amount of Change in Optimized Expected Net Profit (10k JPY)", "最適化期待純利益の変動量 (万円)"))
                ax_tor.legend()
                st.pyplot(fig_tor)

            st.markdown(t("""
            **💡 Management Insights from Sensitivity Analysis Results**:
            - **Customer Payment Potential $C$**: If this sensitivity is high, it is most important to improve the estimation accuracy of each customer's potential maximum annual payment capacity. Improving prediction accuracy by multiplying with credit ratings, annual income information, etc. will yield a return on investment (ROI).
            - **Credit Utilization Responsiveness $k$**: If this sensitivity is high, it is necessary to refine the customer's card usage motivation response model (credit elasticity) to increases/decreases in the credit limit.
            - **Probability of Default $PD$**: If this sensitivity is high, it means that the revenue fluctuation risk (volatility) due to macroeconomic trends is large. Setting an Early Warning Indicator (EWS) according to the business cycle and flexibly adjusting the risk allocation limit are effective.
            """, """
            **💡 感度分析結果から得られる経営的インサイト**:
            - **顧客決済ポテンシャル $C$**: この感度が高い場合、各顧客の潜在的な最大年間決済能力の推定精度を上げることが最重要です。信用格付けや年収情報等との掛け合わせによる予測精度向上が投資対効果（ROI）を生みます。
            - **与信利用反応度 $k$**: この感度が高い場合、与信枠（限度額）の増減に対する顧客のカード利用意欲の反応性モデル（与信弾力性）の精緻化が必要です。
            - **デフォルト確率 $PD$**: この感度が高い場合、マクロ経済の動向による収益変動リスク（ボラティリティ）が大きいことを意味します。好不況サイクルに応じた早期警戒インジケーター（EWS）の設定や、機動的なリスクアロケーション枠調整が有効です。
            """))
with tab2:
    st.markdown(t("### 🎯 Optimization Simulation", "### 🎯 最適化シミュレーション"))
    st.write(t(f"Economic Scenario: **{sc_emoji} {scenario_choice}** | Optimization Approach: **{opt_mode_label}**", f"景気シナリオ: **{sc_emoji} {scenario_choice}** | 最適化アプローチ: **{opt_mode_label}**"))

    if not st.session_state.optimized:
        st.info(t("⬅️ **Set the parameters in the left sidebar and click the '⚡ Execute Optimization Allocation' button.**\n\nOnce the optimization is complete, the efficient frontier graph and the credit allocation details for 1,000 people will be displayed.", "⬅️ **左のサイドバーでパラメータを設定し、「⚡ 最適化アロケーションを実行」ボタンを押してください。**\n\n最適化が完了すると、効率的フロンティアグラフと1,000名分の与信アロケーション明細が表示されます。"))
        # 最適化前でもフロンティア曲線は表示する
        st.markdown("---")
        st.markdown(t("**📈 Efficient Frontier Curve (Reference)**", "**📈 効率的フロンティア曲線（参考）**"))
        st.write(t("Below are the theoretical efficient frontiers under the three scenarios. When optimization is executed, the current rule-based and optimized portfolio positions will also be superimposed.", "以下は3つのシナリオにおける理論的な効率的フロンティアです。最適化を実行すると、現行ルールベースと最適化後のポートフォリオ位置も重ねて表示されます。"))
        if USE_PLOTLY:
            fig_pre = go.Figure()
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Optimistic']['risk'],
                y=frontier_data['Optimistic']['profit'],
                mode='lines+markers',
                name=t('🟢 Optimistic Frontier (Default x0.7)', '🟢 楽観フロンティア (デフォルト 0.7倍)'),
                line=dict(color='#2ca02c', width=2, dash='dash'),
                marker=dict(size=6), opacity=0.6
            ))
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Neutral']['risk'],
                y=frontier_data['Neutral']['profit'],
                mode='lines+markers',
                name=t('🔵 Neutral Frontier (Default x1.0)', '🔵 中立フロンティア (デフォルト 1.0倍)'),
                line=dict(color='#1f77b4', width=3),
                marker=dict(size=6), opacity=0.8
            ))
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Pessimistic']['risk'],
                y=frontier_data['Pessimistic']['profit'],
                mode='lines+markers',
                name=t('🔴 Pessimistic Frontier (Default x1.5)', '🔴 悲観フロンティア (デフォルト 1.5倍)'),
                line=dict(color='#d62728', width=2, dash='dash'),
                marker=dict(size=6), opacity=0.6
            ))
            # 現行ルールベースの位置
            fig_pre.add_trace(go.Scatter(
                x=[current_total_loss],
                y=[current_net_profit],
                mode='markers',
                name=t('⭐️ Current Rule-based (Current Pos)', '⭐️ 現行ルールベース (現在地)'),
                marker=dict(color='#ff7f0e', size=16, symbol='star', line=dict(color='black', width=1.5)),
            ))

            # 初期表示における制約線（しきい値ライン）と探索ベクトルの描画
            if opt_mode == 'profit_max':
                # 縦の制約線（期待損失上限）
                fig_pre.add_vline(
                    x=target_value,
                    line_width=2,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text=f" {t('Allowable Loss Limit', '許容期待損失上限')} ({target_value:.0f}{t('(10k JPY)', '万円')}) ",
                    annotation_position="top right",
                    annotation_font=dict(color="#d62728", size=11)
                )
                # 探索方向の矢印
                arrow_x = target_value * 0.9
                fig_pre.add_annotation(
                    x=arrow_x,
                    y=current_net_profit * 1.15,
                    ax=arrow_x,
                    ay=current_net_profit * 0.95,
                    xref="x", yref="y", axref="x", ayref="y",
                    showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor="#2ca02c",
                    text=t("Maximize profit within constraints 📈", "制約内で純利益を最大化 📈"),
                    font=dict(color="#2ca02c", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#2ca02c", borderwidth=1, borderpad=4
                )
            else:
                # 横の制約線（目標純利益）
                fig_pre.add_hline(
                    y=target_value,
                    line_width=2,
                    line_dash="dash",
                    line_color="#2ca02c",
                    annotation_text=f" {t('Target Net Profit', '目標期待純利益')} ({target_value:.0f}{t('(10k JPY)', '万円')}) ",
                    annotation_position="top right",
                    annotation_font=dict(color="#2ca02c", size=11)
                )
                # 探索方向の矢印（期待損失の最小化）
                fig_pre.add_annotation(
                    x=current_total_loss * 0.8,
                    y=target_value,
                    ax=current_total_loss * 1.1,
                    ay=target_value,
                    xref="x", yref="y", axref="x", ayref="y",
                    showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor="#d62728",
                    text=t("Minimize risk maintaining target profit 🛡️", "目標利益を維持しリスクを最小化 🛡️"),
                    font=dict(color="#d62728", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#d62728", borderwidth=1, borderpad=4
                )

            fig_pre.update_layout(
                template="plotly_dark",
                xaxis_title=t("Total Exp Loss (Allowance) [10k JPY]", "期待総損失額 (貸倒引当金) [万円]"),
                yaxis_title=t("Total Exp Net Profit (Rev - Loss) [10k JPY]", "期待総純利益 (収益 - 損失) [万円]"),
                legend=dict(x=0.98, y=0.02, xanchor="right", yanchor="bottom",
                            bgcolor="rgba(30,30,30,0.8)", bordercolor="rgba(255,255,255,0.15)", borderwidth=1),
                margin=dict(l=40, r=40, t=20, b=40),
                height=450,
                hovermode="closest",
                plot_bgcolor="rgba(0,0,0,0)"
            )
            st.plotly_chart(fig_pre, width='stretch')
        st.stop()

    # ==========================================
    # 最適化の実行（ボタン押下後）
    # ==========================================
    infeasible = False
    st.session_state['infeasible'] = False
    with st.spinner(t(f"Calculating optimization with Ipopt solver... ({sc_emoji} {sc_name} Scenario)", f"Ipoptソルバーで最適化計算中... ({sc_emoji} {sc_name} シナリオ)")):
        try:
            opt_limits, shadow_price = solve_optimization(
                df_customers, target_value, pd_multiplier=pd_mult, mode=opt_mode
            )
        except ValueError as e:
            st.error(t(f"⚠️ The solver returned Infeasible.\n\n**Cause**: There is no feasible solution under the current constraints (slider values).\n\nPlease adjust the sliders in the sidebar (raise the risk upper limit in profit maximization mode, lower the profit target in risk minimization mode).\n\n**Detail**: {e}", f"⚠️ ソルバーが実行不可能（Infeasible）を返しました。\n\n**原因**: 現在の制約条件（スライダーの値）では実現可能な解が存在しません。\n\nサイドバーのスライダーを調整してください（利益最大化モードではリスク上限を上げる、リスク最小化モードでは利益目標を下げる）。\n\n**詳細**: {e}"))
            st.session_state['infeasible'] = True
            infeasible = True
        except Exception as e:
            st.error(t(f"⚠️ An error occurred during optimization: {e}", f"⚠️ 最適化の実行中にエラーが発生しました: {e}"))
            st.session_state['infeasible'] = True
            infeasible = True

    if not infeasible:
        # 最適化後のポートフォリオ指標の計算
        opt_total_rev = 0
        opt_total_loss = 0
        for i in range(len(df_customers)):
            lim = opt_limits[i]
            ead = df_customers['PotentialC'].iloc[i] * (1 - np.exp(-df_customers['AlphaK'].iloc[i] * lim))
            pd_sc = df_customers['PD'].iloc[i] * pd_mult
            revenue = df_customers['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
            loss = pd_sc * df_customers['LGD'].iloc[i] * ead
            opt_total_rev += revenue
            opt_total_loss += loss
        opt_net_profit = opt_total_rev - opt_total_loss

        # 丸め処理による制約違反（Infeasibility）リスクのハンドリング・警告の生成
        has_constraint_violation = False
        violation_msg = ""
        if opt_mode == 'profit_max':
            # 利益最大化モード: 期待損失(opt_total_loss)が許容リスク上限(target_value)を超えていないか
            if opt_total_loss > target_value + 1e-3:
                has_constraint_violation = True
                excess_loss = opt_total_loss - target_value
                violation_msg = t(
                    f"⚠️ **Constraint violation (Risk tolerance exceeded) occurred due to application of business rules (rounding, no reduction limit, etc.).**\n\n"
                    f"In the theoretical optimal solution of the continuous value solver, the expected loss was suppressed below the upper limit (**{target_value:,.1f} 10k JPY**), but "
                    f"as a result of applying practical rules (rounding credit limits to 10/30/50/70/100 10k JPY, prohibiting reduction of shopping limits, uniform 100k JPY upper limit for housewives/students, etc.) "
                    f"in post-processing, the actual expected loss is **{opt_total_loss:,.1f} 10k JPY**, exceeding the allowable upper limit by **{excess_loss:,.1f} 10k JPY**.\n\n"
                    f"If you are going to adopt this portfolio in practice, please consider slightly lowering the 'Allowable Risk Upper Limit' in the sidebar adjustment, "
                    f"or partially relaxing the business rules.",
                    f"⚠️ **ビジネスルール適用（丸め・減枠禁止等）による制約突き抜け（許容リスク超過）が発生しています。**\n\n"
                    f"連続値ソルバーの理論上の最適解では期待損失は上限（**{target_value:,.1f}万円**）以下に抑えられていましたが、"
                    f"実務上のルール（与信枠の10/30/50/70/100万円への丸め、ショッピング枠の減枠禁止、主婦・学生の一律10万円上限など）を"
                    f"後処理で適用した結果、実際の期待損失は **{opt_total_loss:,.1f}万円** となり、許容上限を **{excess_loss:,.1f}万円** 超過しています。\n\n"
                    f"実務上このポートフォリオを採用する場合は、サイドバーの調整で『許容リスク上限』をもう少し引き下げるか、"
                    f"ビジネスルールを一部緩和することを検討してください。"
                )
        else:
            # リスク最小化モード: 期待利益(opt_net_profit)が目標純利益(target_value)を下回っていないか
            if opt_net_profit < target_value - 1e-3:
                has_constraint_violation = True
                deficit_profit = target_value - opt_net_profit
                violation_msg = t(
                    f"⚠️ **Constraint violation (Target net profit deficit) occurred due to application of business rules (rounding, no reduction limit, etc.).**\n\n"
                    f"In the theoretical optimal solution of the continuous value solver, the target expected net profit (**{target_value:,.1f} 10k JPY**) was cleared, but "
                    f"as a result of applying practical rules (rounding credit limits to 10/30/50/70/100 10k JPY, prohibiting reduction of shopping limits, uniform 100k JPY upper limit for housewives/students, etc.) "
                    f"in post-processing, the actual expected net profit is **{opt_net_profit:,.1f} 10k JPY**, falling short of the target by **{deficit_profit:,.1f} 10k JPY**.\n\n"
                    f"If you are going to adopt this portfolio in practice, please consider setting the 'Target Expected Net Profit' lower in the sidebar adjustment, "
                    f"or partially relaxing the business rules.",
                    f"⚠️ **ビジネスルール適用（丸め・減枠禁止等）による制約未達（目標純利益不足）が発生しています。**\n\n"
                    f"連続値ソルバーの理論上の最適解では目標期待純利益（**{target_value:,.1f}万円**）をクリアしていましたが、"
                    f"実務上のルール（与信枠の10/30/50/70/100万円への丸め、ショッピング枠の減枠禁止、主婦・学生の一律10万円上限など）を"
                    f"後処理で適用した結果、実際の期待純利益は **{opt_net_profit:,.1f}万円** となり、目標に **{deficit_profit:,.1f}万円** 届いていません。\n\n"
                    f"実務上このポートフォリオを採用する場合は、サイドバーの調整で『目標期待純利益』を低めに設定するか、"
                    f"ビジネスルールを一部緩和することを検討してください。"
                )

        current_efficiency = current_net_profit / current_total_loss if current_total_loss > 0 else 0
        opt_efficiency = opt_net_profit / opt_total_loss if opt_total_loss > 0 else 0

        # 限界リスク効率（リターン/リスク比）の計算
        marginal_eff = []
        for i in range(len(df_customers)):
            lim = opt_limits[i]
            ead = df_customers['PotentialC'].iloc[i] * (1 - np.exp(-df_customers['AlphaK'].iloc[i] * lim))
            pd_sc = df_customers['PD'].iloc[i] * pd_mult
            rev = df_customers['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
            ls = pd_sc * df_customers['LGD'].iloc[i] * ead
            eff = (rev - ls) / ls if ls > 0 else 0
            marginal_eff.append(round(eff, 4))

        # アクション列の定義
        actions = []
        for i in range(len(df_customers)):
            curr = df_customers['CurrentLimit'].iloc[i]
            opt = opt_limits[i]
            if opt > curr:
                actions.append(t("🟢 Limit Up", "🟢 増枠 (Limit Up)"))
            elif opt < curr:
                actions.append(t("🔴 Limit Down", "🔴 減枠 (Limit Down)"))
            else:
                actions.append(t("⚪️ Maintain", "⚪️ 維持 (Maintain)"))

        # ==========================================
        # 改善効果のKPIカード
        # ==========================================
        if has_constraint_violation:
            st.warning(violation_msg)

        st.markdown(t("#### 📊 Improvement Effect by Optimization (Current Rule vs Optimized)", "#### 📊 最適化による改善効果（現行ルール vs 最適化後）"))
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        profit_delta = opt_net_profit - current_net_profit
        loss_delta = opt_total_loss - current_total_loss
        eff_delta = opt_efficiency - current_efficiency

        with m_col1:
            st.markdown(f"""
            <div class="metric-card metric-card-positive">
                <div class="metric-title">{t("📈 Opt. Exp. Net Profit", "📈 最適化後の期待純利益")}</div>
                <div class="metric-value">{opt_net_profit:,.1f} {t("10k JPY", "万円")}</div>
                <div class="metric-delta" style="color: {'#2ca02c' if profit_delta >= 0 else '#d62728'};">
                    {'▲' if profit_delta >= 0 else '▼'} {abs(profit_delta):.1f} {t("10k JPY (vs current)", "万円 (現行比)")}
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col2:
            st.markdown(f"""
            <div class="metric-card metric-card-negative">
                <div class="metric-title">{t("⚠️ Opt. Exp. Loss", "⚠️ 最適化後の期待損失額")}</div>
                <div class="metric-value">{opt_total_loss:,.1f} {t("10k JPY", "万円")}</div>
                <div class="metric-delta" style="color: {'#2ca02c' if loss_delta <= 0 else '#d62728'};">
                    {'▼' if loss_delta <= 0 else '▲'} {abs(loss_delta):.1f} {t("10k JPY (vs current)", "万円 (現行比)")}
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">{t("⚡ Risk-Adjusted Efficiency", "⚡ リスク対比運用効率")}</div>
                <div class="metric-value">{opt_efficiency:.2f}</div>
                <div class="metric-delta" style="color: {'#2ca02c' if eff_delta >= 0 else '#d62728'};">
                    {'▲' if eff_delta >= 0 else '▼'} {abs(eff_delta):.2f} {t("(vs current)", "(現行比)")}
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col4:
            if opt_mode == 'risk_min' and abs(shadow_price) > 0.01:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">{t("🔬 Shadow Price", "🔬 シャドープライス（双対価格）")}</div>
                    <div class="metric-value">{shadow_price:.2f} {t("10k JPY", "万円")}</div>
                    <div class="metric-delta" style="color: #17a2b8;">
                        {t("Risk reduction per 10k JPY profit target relaxation", "利益目標1万円緩和→リスク削減量")}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                action_counts_temp = pd.Series(actions).value_counts()
                up_c = action_counts_temp.get(t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), 0)
                st.markdown(f"""
                <div class="metric-card metric-card-positive">
                    <div class="metric-title">{t("🟢 Limit Up Actions", "🟢 増枠アクション件数")}</div>
                    <div class="metric-value">{up_c} {t("people", "名")}</div>
                    <div class="metric-delta" style="color: #2ca02c;">
                        {t("Limit up to improve overall profitability", "全体収益性を向上させる増枠")}
                    </div>
                </div>
                """, unsafe_allow_html=True)

        # ==========================================
        # 効率的フロンティアグラフ
        # ==========================================
        st.markdown("---")
        st.markdown(t("### 📈 Macro Perspective: 'Efficient Frontier' of Risk and Return", "### 📈 マクロ視点：リスクとリターンの「効率的フロンティア」"))
        st.markdown(t(f"Currently selected scenario: **{sc_emoji} {scenario_choice}**", f"現在選択中のシナリオ: **{sc_emoji} {scenario_choice}**"))

        if USE_PLOTLY:
            fig = go.Figure()

            # 楽観シナリオ (緑)
            fig.add_trace(go.Scatter(
                x=frontier_data['Optimistic']['risk'],
                y=frontier_data['Optimistic']['profit'],
                mode='lines+markers',
                name=t('🟢 Optimistic Frontier (Default x0.7)', '🟢 楽観フロンティア (デフォルト 0.7倍)'),
                line=dict(color='#2ca02c', width=2, dash='dash'),
                marker=dict(size=6),
                opacity=0.6
            ))

            # 中立シナリオ (青)
            fig.add_trace(go.Scatter(
                x=frontier_data['Neutral']['risk'],
                y=frontier_data['Neutral']['profit'],
                mode='lines+markers',
                name=t('🔵 Neutral Frontier (Default x1.0)', '🔵 中立フロンティア (デフォルト 1.0倍)'),
                line=dict(color='#1f77b4', width=3),
                marker=dict(size=6),
                opacity=0.8
            ))

            # 悲観シナリオ (赤)
            fig.add_trace(go.Scatter(
                x=frontier_data['Pessimistic']['risk'],
                y=frontier_data['Pessimistic']['profit'],
                mode='lines+markers',
                name=t('🔴 Pessimistic Frontier (Default x1.5)', '🔴 悲観フロンティア (デフォルト 1.5倍)'),
                line=dict(color='#d62728', width=2, dash='dash'),
                marker=dict(size=6),
                opacity=0.6
            ))

            # 現行ルールベースの位置（オレンジの星）
            fig.add_trace(go.Scatter(
                x=[current_total_loss],
                y=[current_net_profit],
                mode='markers',
                name=t('⭐️ Current Rule-based (Current Pos)', '⭐️ 現行ルールベース (現在地)'),
                marker=dict(color='#ff7f0e', size=16, symbol='star', line=dict(color='black', width=1.5)),
                hovertemplate="<b>" + t("Current Rule-based", "現行ルールベース") + "</b><br>" + t("Expected Loss: ", "期待損失: ") + "%{x:.1f}" + t("(10k JPY)", "万円") + "<br>" + t("Expected Profit: ", "期待利益: ") + "%{y:.1f}" + t("(10k JPY)", "万円") + "<extra></extra>"
            ))

            # 最適化後のポートフォリオ位置（紫の丸）
            fig.add_trace(go.Scatter(
                x=[opt_total_loss],
                y=[opt_net_profit],
                mode='markers',
                name=t('🎯 Optimized Portfolio (Slider Selection)', '🎯 最適化後のポートフォリオ (スライダー選択位置)'),
                marker=dict(color='#9467bd', size=14, symbol='circle', line=dict(color='black', width=1.5)),
                hovertemplate="<b>" + t("Optimized", "最適化後") + "</b><br>" + t("Expected Loss: ", "期待損失: ") + "%{x:.1f}" + t("(10k JPY)", "万円") + "<br>" + t("Expected Profit: ", "期待利益: ") + "%{y:.1f}" + t("(10k JPY)", "万円") + "<extra></extra>"
            ))

            # 動的ガイドラインと探索ベクトルの追加
            if opt_mode == 'profit_max':
                # 縦の制約線（期待損失上限）
                fig.add_vline(
                    x=target_value,
                    line_width=2,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text=f" {t('Allowable Loss Limit', '許容期待損失上限')} ({target_value:.0f}{t('(10k JPY)', '万円')}) ",
                    annotation_position="top right",
                    annotation_font=dict(color="#d62728", size=11)
                )
                # 探索方向の矢印（制約内で利益最大化）
                arrow_x = target_value * 0.9
                fig.add_annotation(
                    x=arrow_x,
                    y=opt_net_profit * 0.95,
                    ax=arrow_x,
                    ay=opt_net_profit * 0.75,
                    xref="x", yref="y", axref="x", ayref="y",
                    showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor="#2ca02c",
                    text=t("Maximize profit within constraints 📈", "制約内で純利益を最大化 📈"),
                    font=dict(color="#2ca02c", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#2ca02c", borderwidth=1, borderpad=4
                )
            else:
                # 横の制約線（目標純利益）
                fig.add_hline(
                    y=target_value,
                    line_width=2,
                    line_dash="dash",
                    line_color="#2ca02c",
                    annotation_text=f" {t('Target Net Profit', '目標期待純利益')} ({target_value:.0f}{t('(10k JPY)', '万円')}) ",
                    annotation_position="bottom right",
                    annotation_font=dict(color="#2ca02c", size=11)
                )
                # 探索方向の矢印（目標利益クリア→リスク最小化）
                arrow_y = target_value * 1.08
                fig.add_annotation(
                    x=opt_total_loss * 1.05,
                    y=arrow_y,
                    ax=opt_total_loss * 1.45,
                    ay=arrow_y,
                    xref="x", yref="y", axref="x", ayref="y",
                    showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor="#d62728",
                    text=t("Minimize loss clearing target 🛡️", "目標をクリアし期待損失を最小化 🛡️"),
                    font=dict(color="#d62728", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#d62728", borderwidth=1, borderpad=4
                )

            fig.update_layout(
                template="plotly_dark",
                xaxis_title=t("Total Exp Loss (Allowance) [10k JPY]", "期待総損失額 (貸倒引当金) [万円]"),
                yaxis_title=t("Total Exp Net Profit (Rev - Loss) [10k JPY]", "期待総純利益 (収益 - 損失) [万円]"),
                legend=dict(
                    x=0.98, y=0.02, xanchor="right", yanchor="bottom",
                    bgcolor="rgba(30,30,30,0.8)",
                    bordercolor="rgba(255,255,255,0.15)", borderwidth=1,
                    font=dict(color="white", size=11)
                ),
                margin=dict(l=40, r=40, t=20, b=40),
                height=500,
                hovermode="closest",
                plot_bgcolor="rgba(0,0,0,0)"
            )
            st.plotly_chart(fig, width='stretch')
        else:
            fig_mpl, ax_mpl = plt.subplots(figsize=(10, 5))
            ax_mpl.plot(frontier_data['Optimistic']['risk'], frontier_data['Optimistic']['profit'], 'o--', color='#2ca02c', label=t('Optimistic Frontier', '楽観フロンティア'))
            ax_mpl.plot(frontier_data['Neutral']['risk'], frontier_data['Neutral']['profit'], 'o-', color='#1f77b4', linewidth=2.5, label=t('Neutral Frontier', '中立フロンティア'))
            ax_mpl.plot(frontier_data['Pessimistic']['risk'], frontier_data['Pessimistic']['profit'], 'o--', color='#d62728', label=t('Pessimistic Frontier', '悲観フロンティア'))
            ax_mpl.plot(current_total_loss, current_net_profit, '*', color='#ff7f0e', markersize=15, label=t('Current Rule-based', '現行ルールベース'))
            ax_mpl.plot(opt_total_loss, opt_net_profit, 'o', color='#9467bd', markersize=12, label=t('Optimized', '最適化後'))
            ax_mpl.set_xlabel(t("Total Exp Loss (Allowance) [10k JPY]", "期待総損失額 (貸倒引当金) [万円]"))
            ax_mpl.set_ylabel(t("Total Exp Net Profit (Rev - Loss) [10k JPY]", "期待総純利益 (収益 - 損失) [万円]"))
            ax_mpl.legend(loc='upper left')
            ax_mpl.grid(True, linestyle='--', alpha=0.5)
            st.pyplot(fig_mpl)

        # ==========================================
        # ミクロドリルダウン：個人別アクション詳細
        # ==========================================
        st.markdown("---")
        st.markdown(t("### 🔍 Micro Drill-down: Individual Credit Action Details", "### 🔍 ミクロドリルダウン：個人別与信アクション詳細"))
        st.write(t("This is the individual limit allocation specification derived by the solver to achieve the company's overall goals.", "会社全体の目標を達成するために、ソルバーが導き出した個人の限度額アロケーション明細です。"))

        # 詳細データの作成
        detail_df = pd.DataFrame({
            t('CustomerID', '顧客ID'): df_customers['CustomerID'],
            t('Segment', 'セグメント'): df_customers['Segment'],
            t('Rating', '信用格付け'): df_customers['Rating'],
            t('PD', 'デフォルト確率 (PD)'): df_customers['PD'] * pd_mult,
            t('Current Limit (10k JPY)', '現行の与信枠 (万円)'): df_customers['CurrentLimit'].astype(int),
            t('Optimized Limit (10k JPY)', '最適化後の与信枠 (万円)'): opt_limits.astype(int),
        })

        # 個人の期待収益・損失の計算
        ind_rev = []
        ind_loss = []
        for i in range(len(df_customers)):
            lim = opt_limits[i]
            ead = df_customers['PotentialC'].iloc[i] * (1 - np.exp(-df_customers['AlphaK'].iloc[i] * lim))
            pd_sc = df_customers['PD'].iloc[i] * pd_mult
            rev = df_customers['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
            ls = pd_sc * df_customers['LGD'].iloc[i] * ead
            ind_rev.append(rev - ls)
            ind_loss.append(ls)

        detail_df[t('Expected Profit (10k JPY)', '期待利益 (万円)')] = np.round(ind_rev, 2)
        detail_df[t('Expected Loss (10k JPY)', '期待損失 (万円)')] = np.round(ind_loss, 2)
        detail_df[t('Marginal Risk Efficiency (Return/Risk Ratio)', '限界リスク効率 (リターン/リスク比)')] = marginal_eff
        detail_df[t('Credit Action', '与信アクション')] = actions

        # 統計サマリー
        action_counts = detail_df[t('Credit Action', '与信アクション')].value_counts()
        up_count = action_counts.get(t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), 0)
        down_count = action_counts.get(t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), 0)
        maintain_count = action_counts.get(t("⚪️ Maintain", "⚪️ 維持 (Maintain)"), 0)

        s_col1, s_col2, s_col3 = st.columns(3)
        s_col1.metric(t("🟢 Limit Up Actions", "🟢 増枠 (Limit Up) アクション件数"), f"{up_count} " + t("people", "名"), t("Aggressive limit up to improve overall profitability", "全体の収益性を向上させる攻めの増枠"))
        s_col2.metric(t("🔴 Limit Down Actions", "🔴 減枠 (Limit Down) アクション件数"), f"{down_count} " + t("people", "名"), t("Defensive limit down to suppress overall expected loss", "全体の期待損失を抑える守りの減枠"))
        s_col3.metric(t("⚪️ Maintain Actions", "⚪️ 維持 (Maintain) アクション件数"), f"{maintain_count} " + t("people", "名"), t("Maintain status quo", "現状維持"))

        # 2. 優先増枠/減枠TOP10の表示
        st.markdown("---")
        st.markdown(t("**🔍 Priority Customer Actions Based on Risk Efficiency (TOP10)**", "**🔍 リスク効率に基づく優先顧客アクション（TOP10）**"))
        st.write(t("This is the TOP 10 list of customers the solver prioritized for limit increase and decrease based on the 'return/risk ratio when 10k JPY credit is added (marginal risk efficiency)'.", "「与信を1万円追加したときのリターン/リスク比（限界リスク効率）」に基づき、ソルバーが最も優先して増枠した顧客、および優先して減枠した顧客のTOP10リストです。"))

        top_df_base = pd.DataFrame({
            'CustomerID': df_customers['CustomerID'],
            'Segment': df_customers['Segment'],
            'Rating': df_customers['Rating'],
            'MarginalEfficiency': marginal_eff,
            'CurrentLimit': df_customers['CurrentLimit'].astype(int),
            'OptimizedLimit': opt_limits.astype(int),
            'Action': actions
        })

        st.markdown(t("🟢 **Priority Limit Up Recommendations (High Return/Risk Ratio TOP10)**", "🟢 **優先増枠推奨 (リターン/リスク比が高い顧客TOP10)**"))
        up_df = top_df_base[top_df_base['OptimizedLimit'] > top_df_base['CurrentLimit']].sort_values(by='MarginalEfficiency', ascending=False).head(10)
        if len(up_df) > 0:
            up_df_display = up_df.rename(columns={
                'CustomerID': t('CustomerID', '顧客ID'), 'Segment': t('Segment', 'セグメント'), 'Rating': '格付け',
                'MarginalEfficiency': t('Marginal Efficiency', '限界リスク効率'), 'CurrentLimit': t('Current Limit (10k JPY)', '現行枠 (万円)'), 'OptimizedLimit': t('Optimized Limit (10k JPY)', '最適化枠 (万円)'),
            })
            st.dataframe(up_df_display.drop(columns=['Action']), hide_index=True, width='stretch')
        else:
            st.write(t("No target customers (Limit up actions have not occurred under current constraints)", "対象顧客なし（現在の制約下では増枠アクションは発生していません）"))

        st.markdown(t("🔴 **Priority Limit Down Recommendations (Negative/Very Low Return/Risk Ratio TOP10)**", "🔴 **優先減枠推奨 (リターン/リスク比がマイナス・極低の顧客TOP10)**"))
        down_df = top_df_base[top_df_base['OptimizedLimit'] < top_df_base['CurrentLimit']].sort_values(by='MarginalEfficiency', ascending=True).head(10)
        if len(down_df) > 0:
            down_df_display = down_df.rename(columns={
                'CustomerID': t('CustomerID', '顧客ID'), 'Segment': t('Segment', 'セグメント'), 'Rating': '格付け',
                'MarginalEfficiency': t('Marginal Efficiency', '限界リスク効率'), 'CurrentLimit': t('Current Limit (10k JPY)', '現行枠 (万円)'), 'OptimizedLimit': t('Optimized Limit (10k JPY)', '最適化枠 (万円)'),
            })
            st.dataframe(down_df_display.drop(columns=['Action']), hide_index=True, width='stretch')
        else:
            st.write(t("No target customers (Limit down actions have not occurred under current constraints)", "対象顧客なし（現在の制約下では減枠アクションは発生していません）"))

        # 3. 格付け別の与信アクション比率
        st.markdown("---")
        st.markdown(t("**📈 Credit Allocation Shift Analysis by Credit Rating**", "**📈 信用格付け（Rating）別の与信アロケーションシフト分析**"))
        st.write(t("Insights on how the solver adjusted the credit limits (portfolio shift) for each rating.", "格付けごとに、ソルバーが与信枠をどう調整したか（ポートフォリオ・シフト）のインサイトです。"))

        rating_crosstab = pd.crosstab(
            df_customers['Rating'],
            top_df_base['Action']
        ).reindex(
            columns=[t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), t("⚪️ Maintain", "⚪️ 維持 (Maintain)")],
            fill_value=0
        )

        if USE_PLOTLY:
            fig_rating = go.Figure()
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab[t("🟢 Limit Up", "🟢 増枠 (Limit Up)")], name=t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), marker_color='#2ca02c'))
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab[t("🔴 Limit Down", "🔴 減枠 (Limit Down)")], name=t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), marker_color='#d62728'))
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab[t("⚪️ Maintain", "⚪️ 維持 (Maintain)")], name=t("⚪️ Maintain", "⚪️ 維持 (Maintain)"), marker_color='#7f7f7f'))
            fig_rating.update_layout(
                template="plotly_dark", barmode='stack',
                xaxis=dict(title=t("Credit Rating (1:Best to 10:Highest Risk)", "信用格付け (1:最優良 〜 10:最リスク)"), tickmode='linear'),
                yaxis_title=t("Number of Customers", "顧客数 (名)"),
                margin=dict(l=40, r=40, t=10, b=40), height=380,
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_rating, width='stretch')
        else:
            fig_rating_mpl, ax_r = plt.subplots(figsize=(10, 4))
            rating_crosstab.plot(kind='bar', stacked=True, color=['#2ca02c', '#d62728', '#7f7f7f'], ax=ax_r)
            ax_r.set_xlabel(t("Credit Rating", t("Credit Rating", "信用格付け")))
            ax_r.set_ylabel(t("Number of Customers", "顧客数 (名)"))
            st.pyplot(fig_rating_mpl)

        # 4. セグメント別アクション集計
        st.markdown("---")
        st.markdown(t("### 📊 Credit Action Summary by Segment (Macro Summary)", "### 📊 セグメント別与信アクション集計 (マクロ要約)"))
        st.markdown(t("**📁 Action Summary Table by Segment (Count)**", "**📁 セグメント別アクション集計表 (名)**"))
        crosstab_df = pd.crosstab(
            detail_df[t('Segment', 'セグメント')],
            detail_df[t('Credit Action', '与信アクション')]
        ).reindex(
            columns=[t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), t("⚪️ Maintain", "⚪️ 維持 (Maintain)")],
            fill_value=0
        )
        crosstab_df[t('Total', '合計')] = crosstab_df.sum(axis=1)
        st.dataframe(crosstab_df, width='stretch')

        st.markdown(t("**📊 Action Allocation Ratio by Segment**", "**📊 セグメント別アクション配分比率**"))
        if USE_PLOTLY:
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df[t("🟢 Limit Up", "🟢 増枠 (Limit Up)")], name=t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), marker_color='#2ca02c'))
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df[t("🔴 Limit Down", "🔴 減枠 (Limit Down)")], name=t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), marker_color='#d62728'))
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df[t("⚪️ Maintain", "⚪️ 維持 (Maintain)")], name=t("⚪️ Maintain", "⚪️ 維持 (Maintain)"), marker_color='#7f7f7f'))
            fig_bar.update_layout(
                template="plotly_dark", barmode='stack',
                xaxis_title=t("Segment", "セグメント"), yaxis_title=t("Count (people)", t("Count (people)", "人数 (名)")),
                margin=dict(l=40, r=40, t=10, b=40), height=380,
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_bar, width='stretch')
        else:
            fig_bar_mpl, ax_b = plt.subplots(figsize=(10, 4))
            crosstab_df[[t("🟢 Limit Up", "🟢 増枠 (Limit Up)"), t("🔴 Limit Down", "🔴 減枠 (Limit Down)"), t("⚪️ Maintain", "⚪️ 維持 (Maintain)")]].plot(
                kind='bar', stacked=True, color=['#2ca02c', '#d62728', '#7f7f7f'], ax=ax_b)
            ax_b.set_ylabel(t("Count (people)", "人数 (名)"))
            ax_b.legend(loc='upper right', fontsize='small')
            st.pyplot(fig_bar_mpl)

        # PDの書式設定
        detail_df[t('PD', 'デフォルト確率 (PD)')] = detail_df[t('PD', 'デフォルト確率 (PD)')].map(lambda x: f"{x*100:.2f}%")

        st.markdown("---")
        st.markdown(t("### 🔍 Micro Perspective: Individual Credit Limit Adjustment Action List (1,000 people)", "### 🔍 ミクロ視点：個人別与信枠調整アクションリスト (1,000名分)"))
        st.write(t("A comprehensive table including specific credit limit adjustment actions applied to each customer and their detailed parameters.", "各顧客に対して適用される具体的な与信枠の調整アクションと、その詳細なパラメータを含む一覧テーブルです。"))

        csv_bytes = detail_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label=t("📥 Download Optimized Credit Limit List (CSV)", "📥 最適化与信枠リスト (CSV) をダウンロード"),
            data=csv_bytes,
            file_name=f"optimized_limits_{sc_name}_{opt_mode}.csv",
            mime="text/csv",
            help=t("Download this CSV file for analysis in Excel or direct import into a core credit system.", "このCSVファイルをダウンロードして、Excelでの分析や、基幹の与信システムへそのまま流し込むことができます。")
        )

        st.dataframe(
            detail_df.sort_values(by=t('Marginal Risk Efficiency (Return/Risk Ratio)', '限界リスク効率 (リターン/リスク比)'), ascending=False),
            width='stretch',
            height=400
        )

        st.markdown(t("##### 💡 Demo Points:", "##### 💡 デモのポイント:"))
        st.markdown("""
        1. **Macro Warp Effect**: Observe how inefficient the 'Current Rule-based (Star)' on the graph is. Just by shifting to the globally optimized 'Frontier (Curve)', you can maximize return at the same risk, or minimize risk at the same return.
        2. **Micro Linkage**: Try moving the slider (Risk Limit) in the left sidebar. The moment you change the company's overall allowable risk, the solver instantly recalculates, and you can see the 'Target audience for limit up/down actions' at the bottom of the screen dynamically swapping. This is the real linkage between global optimization and individual credit limits.
        """)

# ==========================================
# 9. Tab3: 前提条件と数理定式化の解説タブ
# ==========================================
