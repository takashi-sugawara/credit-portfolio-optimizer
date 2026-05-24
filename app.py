import streamlit as st
import pandas as pd
import numpy as np
import pyomo.environ as pyo
from pyomo.opt import SolverFactory
import time

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
# 3. 数理最適化ソルバーエンジン（Pyomo ＋ Ipopt）
# ==========================================
def get_ipopt_path():
    """
    動的にIPOPT実行ファイルパスを検出する。
    Streamlit Community Cloud などのデプロイ環境とローカル開発環境の差異を吸収する。
    """
    import shutil
    import sys
    import os
    
    # 優先度順に検索
    # 1. PATH環境変数内を検索
    path = shutil.which("ipopt")
    if path:
        return path
    
    # 2. 現在のPython実行ファイルと同じbin ディレクトリ（Conda環境で信頼性が高い）
    bin_dir = os.path.dirname(sys.executable)
    path_in_bin = os.path.join(bin_dir, "ipopt")
    if os.path.exists(path_in_bin):
        return path_in_bin

    # 3. 一般的なConda/Linux パス
    for p in ["/home/adminuser/.conda/bin/ipopt",  # Streamlit Cloud
              "/usr/bin/ipopt",                    # Linux標準
              "/opt/conda/bin/ipopt"]:             # Conda
        if os.path.exists(p):
            return p
    
    return None

def solve_optimization(df, target_value, pd_multiplier=1.0, mode='profit_max'):
    """
    アプローチに応じて、与えられた制約のもとで最適化を実行する。
    - mode='profit_max': target_value = 許容期待損失上限。期待利益を最大化する。
    - mode='risk_min': target_value = 目標期待純利益。期待損失を最小化する。
    """
    model = pyo.ConcreteModel()
    
    # 双対（シャドープライス）サフィックスの宣言
    model.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    
    # 顧客インデックスの定義
    N = len(df)
    model.I = pyo.RangeSet(0, N-1)
    
    # 定数データの辞書化 (高速化のため)
    p_dict = {i: df['PD'].iloc[i] * pd_multiplier for i in range(N)}
    lgd_dict = {i: df['LGD'].iloc[i] for i in range(N)}
    r_dict = {i: df['RevenueRate'].iloc[i] for i in range(N)}
    C_dict = {i: df['PotentialC'].iloc[i] for i in range(N)}
    k_dict = {i: df['AlphaK'].iloc[i] for i in range(N)}
    
    # 意思決定変数: 各個人の与信枠 L_i (0〜100万円の間で連続値として解く)
    model.L = pyo.Var(model.I, bounds=(0, 100))
    
    # 想定利用額の関数 EAD_i(L_i) = C_i * (1 - exp(-k_i * L_i))
    def ead_rule(model, i):
        return C_dict[i] * (1 - pyo.exp(-k_dict[i] * model.L[i]))
    
    # 期待収益 (金利・手数料など)
    def rev_rule(model):
        return sum(r_dict[i] * ead_rule(model, i) * (1 - p_dict[i]) for i in model.I)
    
    # デフォルト期待損失 (焦げ付き・引当金)
    def loss_rule(model):
        return sum(p_dict[i] * lgd_dict[i] * ead_rule(model, i) for i in model.I)
    
    if mode == 'profit_max':
        # 1. 利益最大化モード (期待純利益を最大化、期待損失を target_value 以下に制限)
        model.obj = pyo.Objective(expr=rev_rule(model) - loss_rule(model), sense=pyo.maximize)
        model.risk_limit = pyo.Constraint(expr=loss_rule(model) <= target_value)
    else:
        # 2. リスク最小化モード (期待損失を最小化、期待純利益を target_value 以上に制限)
        model.obj = pyo.Objective(expr=loss_rule(model), sense=pyo.minimize)
        model.profit_limit = pyo.Constraint(expr=rev_rule(model) - loss_rule(model) >= target_value)
    
    # ソルバーの呼び出し (動的なIPOPT実行ファイルパス検出とフォールバック)
    ipopt_exec = get_ipopt_path()
    if ipopt_exec:
        opt = SolverFactory('ipopt', executable=ipopt_exec)
    else:
        opt = SolverFactory('ipopt')
    
    # ソルバーログを非表示にして高速化
    results = opt.solve(model, tee=False)
    
    # 実行不可能（Infeasible）判定
    if str(results.solver.termination_condition) == 'infeasible':
        raise ValueError("Ipopt Solver returned Infeasible status.")
    
    # 結果の抽出
    opt_limits = [pyo.value(model.L[i]) for i in range(N)]
    
    # シャドープライス（双対価格）の抽出
    shadow_price = 0.0
    try:
        if mode == 'profit_max':
            shadow_price = model.dual[model.risk_limit]
        else:
            shadow_price = model.dual[model.profit_limit]
    except Exception:
        shadow_price = 0.0
    
    # 実務の離散化処理とビジネスルールの適用（後処理）
    limit_options = np.array([10, 30, 50, 70, 100])
    rounded_limits = []
    for i in range(N):
        val = opt_limits[i]
        job = df['Job'].iloc[i]
        segment = df['Segment'].iloc[i]
        curr_limit = df['CurrentLimit'].iloc[i]
        
        # 1. キリの良いメニューへの丸め（最小値は10万円）
        closest_idx = np.abs(limit_options - val).argmin()
        rounded_val = float(limit_options[closest_idx])
            
        # 2. ビジネスルール①：主婦・学生は一律10万円上限
        if job in ['Housewife', 'Student']:
            rounded_val = min(rounded_val, 10.0)
            
        # 3. ビジネスルール②：ショッピングの減枠は原則しない (顧客離反の防止)
        if segment == 'Shopping':
            if rounded_val < curr_limit:
                rounded_val = curr_limit
                
        rounded_limits.append(rounded_val)
        
    return np.array(rounded_limits), shadow_price

# ==========================================
# 4. 効率的フロンティアのキャッシュ生成機能
# ==========================================
@st.cache_data
def get_efficient_frontiers():
    """
    楽観、中立、悲観の3つのシナリオにおいて、リスク上限を変化させて効率的フロンティア曲線のデータを事前生成する。
    """
    df = generate_customer_data()
    scenarios = {
        'Optimistic': 0.7,
        'Neutral': 1.0,
        'Pessimistic': 1.5
    }
    
    frontiers = {}
    
    for sc_name, multiplier in scenarios.items():
        risks = []
        profits = []
        
        max_possible_loss = sum(
            df['PD'].iloc[i] * multiplier * df['LGD'].iloc[i] * df['PotentialC'].iloc[i] * (1 - np.exp(-df['AlphaK'].iloc[i] * 100))
            for i in range(len(df))
        )
        
        risk_range = np.linspace(max_possible_loss * 0.05, max_possible_loss * 0.65, 20)
        
        for r_limit in risk_range:
            try:
                opt_limits, _ = solve_optimization(df, r_limit, pd_multiplier=multiplier)
                
                total_rev = 0
                total_loss = 0
                for i in range(len(df)):
                    ead = df['PotentialC'].iloc[i] * (1 - np.exp(-df['AlphaK'].iloc[i] * opt_limits[i]))
                    pd_sc = df['PD'].iloc[i] * multiplier
                    
                    revenue = df['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
                    loss = pd_sc * df['LGD'].iloc[i] * ead
                    
                    total_rev += revenue
                    total_loss += loss
                
                profits.append(total_rev - total_loss)
                risks.append(total_loss)
            except Exception as e:
                continue
                
        frontiers[sc_name] = {
            'risk': risks,
            'profit': profits
        }
        
    return frontiers

def calculate_portfolio_profit(df, limits, pd_mult):
    total_rev = 0
    total_loss = 0
    for i in range(len(df)):
        lim = limits[i]
        ead = df['PotentialC'].iloc[i] * (1 - np.exp(-df['AlphaK'].iloc[i] * lim))
        pd_sc = df['PD'].iloc[i] * pd_mult
        
        revenue = df['RevenueRate'].iloc[i] * ead * (1 - pd_sc)
        loss = pd_sc * df['LGD'].iloc[i] * ead
        
        total_rev += revenue
        total_loss += loss
    return total_rev - total_loss

def run_sensitivity_analysis(df, target_value, pd_mult, opt_mode):
    try:
        base_limits, _ = solve_optimization(df, target_value, pd_multiplier=pd_mult, mode=opt_mode)
        base_profit = calculate_portfolio_profit(df, base_limits, pd_mult)
    except Exception:
        return None
        
    parameters = {
        '顧客決済ポテンシャル C': ('PotentialC', 0.1),
        '与信利用反応度 k': ('AlphaK', 0.1),
        'デフォルト確率 PD': ('PD', 0.1)
    }
    
    results = []
    
    for label, (col, pct) in parameters.items():
        df_high = df.copy()
        pd_mult_high = pd_mult
        if col == 'PD':
            pd_mult_high = pd_mult * (1.0 + pct)
        else:
            df_high[col] = df[col] * (1.0 + pct)
            
        try:
            limits_high, _ = solve_optimization(df_high, target_value, pd_multiplier=pd_mult_high, mode=opt_mode)
            profit_high = calculate_portfolio_profit(df_high, limits_high, pd_mult_high)
            delta_high = profit_high - base_profit
        except Exception:
            delta_high = 0.0
            
        df_low = df.copy()
        pd_mult_low = pd_mult
        if col == 'PD':
            pd_mult_low = pd_mult * (1.0 - pct)
        else:
            df_low[col] = df[col] * (1.0 - pct)
            
        try:
            limits_low, _ = solve_optimization(df_low, target_value, pd_multiplier=pd_mult_low, mode=opt_mode)
            profit_low = calculate_portfolio_profit(df_low, limits_low, pd_mult_low)
            delta_low = profit_low - base_profit
        except Exception:
            delta_low = 0.0
            
        results.append({
            'Parameter': label,
            'High': delta_high,
            'Low': delta_low
        })
        
    return results

# ==========================================
# 5. メインアプリケーション処理
# ==========================================

# データ生成
df_customers = generate_customer_data()

# タイトルエリア
st.title("💳 クレジットカード与信ポートフォリオ全体最適化デモ")
st.markdown("##### 1,000名の顧客ポートフォリオにおける「マクロな利益・リスクのトレードオフ（効率的フロンティア）」と「ミクロな個人の与信枠調整」を繋ぐシミュレーター")

# フロンティアの事前計算（初回のみ実行されキャッシュされます）
with st.spinner("効率的フロンティア曲線を計算中... (初回のみ数秒かかります)"):
    frontier_data = get_efficient_frontiers()

# タブの作成
tab1, tab2, tab3, tab4 = st.tabs([
    "🏦 現状ポートフォリオ分析",
    "🎯 最適化シミュレーション",
    "📑 前提条件と定式化の整理",
    "🌪️ モデル感度分析（堅牢性の検証）"
])

# ==========================================
# 6. サイドバーコントローラー（操作パネル）
# ==========================================
st.sidebar.header("🛠️ 最適化シミュレーション設定")

# 1. シナリオ選択
scenario_choice = st.sidebar.selectbox(
    "1. 景気シナリオの選択 (デフォルト確率の変動)",
    options=['中立シナリオ (デフォルト確率 1.0倍)', '楽観シナリオ (デフォルト確率 0.7倍)', '悲観シナリオ (デフォルト確率 1.5倍)'],
    index=0
)
scenario_map = {
    '中立シナリオ (デフォルト確率 1.0倍)': ('Neutral', 1.0, "🔵"),
    '楽観シナリオ (デフォルト確率 0.7倍)': ('Optimistic', 0.7, "🟢"),
    '悲観シナリオ (デフォルト確率 1.5倍)': ('Pessimistic', 1.5, "🔴")
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
st.sidebar.subheader("2. 最適化アプローチ (経営ゴールの設定)")
opt_mode_label = st.sidebar.radio(
    "目標とする経営KPI",
    options=[
        "🎯 利益の最大化 (期待損失を一定以下に制限)",
        "🛡️ リスクの最小化 (期待純利益の目標値を達成)"
    ],
    index=0,
    help="会社の経営方針に合わせて、攻め（利益最大化）か守り（リスク最小化）かのアプローチを選択します。"
)
opt_mode = 'profit_max' if "利益の最大化" in opt_mode_label else 'risk_min'

# 最適化アプローチの解説ボックス
if opt_mode == 'profit_max':
    st.sidebar.info("""
    **🎯 利益最大化アプローチ（攻め）**
    設定した「期待損失上限」の許容範囲の中で、ポートフォリオ全体の純利益を最も大きくする与信枠を計算します。優良顧客への**「増枠アクション」**が促されます。
    """)
else:
    st.sidebar.success("""
    **🛡️ リスク最小化アプローチ（守り）**
    必達したい「目標純利益」をクリアした上で、全体のデフォルト期待損失額（焦げ付き）を極限まで低くする与信枠を計算します。危険顧客への**「減枠アクション」**が促されます。
    """)

# スライダーの動的切り替え
if opt_mode == 'profit_max':
    st.sidebar.markdown("**📊 リスク上限の調整**")
    st.sidebar.write(f"現行ルールの期待損失額: **{current_total_loss:.1f} 万円**")
    min_slider = float(np.round(current_total_loss * 0.4, 0))
    max_slider = float(np.round(current_total_loss * 1.6, 0))
    default_slider = float(np.round(current_total_loss, 0))
    
    target_value = st.sidebar.slider(
        "全体の許容期待損失上限 (万円)",
        min_value=min_slider,
        max_value=max_slider,
        value=default_slider,
        step=5.0,
        help="この期待損失（引当金）の総額の中に、1000人全員の損失を絶対に閉じ込めます。"
    )
else:
    st.sidebar.markdown("**📈 売上（純利益）目標の調整**")
    st.sidebar.write(f"現行ルールの期待純利益: **{current_net_profit:.1f} 万円**")
    
    max_possible_profit = max(frontier_data[sc_name]['profit'])
    min_slider = float(np.round(current_net_profit * 0.8, 0))
    max_slider = float(np.round(max_possible_profit * 0.98, 0))
    default_slider = float(np.round(current_net_profit, 0))
    
    target_value = st.sidebar.slider(
        "会社全体の期待純利益目標 (万円)",
        min_value=min_slider,
        max_value=max_slider,
        value=default_slider,
        step=5.0,
        help="期待純利益を現行以上に維持したまま、全体のデフォルトリスク（期待損失額）をどれだけ最小化（削減）できるかを検証します。"
    )

# 3. 最適化の実行ボタン
st.sidebar.markdown("---")
st.sidebar.subheader("3. 最適化の実行")
st.sidebar.write("パラメータを設定後、ボタンを押すとIpoptソルバーが走り、全体最適化アロケーションが実行されます。")
run_opt = st.sidebar.button("⚡ 最適化アロケーションを実行", type="primary", width='stretch')

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
    st.markdown("### 🏦 現状ポートフォリオ分析")
    st.write("数理最適化を行う前の、現在のルールベース与信における顧客属性と与信枠の分布を可視化します。現状を把握することで、最適化による「変化」がより鮮明に見えてきます。")

    # --- KPIカード（4枚）---
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">👥 総顧客数</div>
            <div class="metric-value">{len(df_customers):,} 名</div>
            <div class="metric-delta" style="color: #6c757d;">分析対象ポートフォリオ</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">💴 総与信限度額（現行）</div>
            <div class="metric-value">{df_customers['CurrentLimit'].sum():,.0f} 万円</div>
            <div class="metric-delta" style="color: #6c757d;">ポートフォリオ全体の与信枠総額</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        avg_rating = df_customers['Rating'].mean()
        st.markdown(f"""
        <div class="metric-card metric-card-positive">
            <div class="metric-title">⭐ 平均信用格付け</div>
            <div class="metric-value">{avg_rating:.2f}</div>
            <div class="metric-delta" style="color: #2ca02c;">1:最優良 〜 10:最高リスク</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        avg_pd = df_customers['PD'].mean() * 100
        st.markdown(f"""
        <div class="metric-card metric-card-negative">
            <div class="metric-title">⚠️ 平均デフォルト確率 (PD)</div>
            <div class="metric-value">{avg_pd:.2f}%</div>
            <div class="metric-delta" style="color: #d62728;">ポートフォリオ全体の平均PD</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # --- 1列目: セグメント構成パイチャート ＋ 職業属性棒グラフ ---
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**📊 セグメント構成（利用目的別）**")
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
                hovertemplate='%{label}<br>人数: %{value}名<br>割合: %{percent}<extra></extra>'
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
        st.markdown("**👔 職業属性分布**")
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
                hovertemplate='%{x}<br>人数: %{y}名<extra></extra>'
            ))
            fig_job.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="職業属性",
                yaxis_title="顧客数 (名)",
                showlegend=False
            )
            st.plotly_chart(fig_job, width='stretch')
        else:
            fig_job, ax_job = plt.subplots(figsize=(5, 4))
            ax_job.bar(job_counts['Job'], job_counts['Count'])
            ax_job.set_ylabel("顧客数 (名)")
            st.pyplot(fig_job)

    st.markdown("---")

    # --- 2列目: 信用格付け分布ヒストグラム ＋ 与信枠分布 ---
    col_left2, col_right2 = st.columns(2)

    with col_left2:
        st.markdown("**📉 信用格付け（Rating）分布**")
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
                hovertemplate='格付け: %{x}<br>顧客数: %{y}名<extra></extra>'
            ))
            fig_rating_hist.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(title="信用格付け (1:最優良 〜 10:最高リスク)", tickmode='linear'),
                yaxis_title="顧客数 (名)",
                showlegend=False
            )
            st.plotly_chart(fig_rating_hist, width='stretch')
        else:
            fig_rh, ax_rh = plt.subplots(figsize=(5, 4))
            ax_rh.bar(rating_counts['Rating'], rating_counts['Count'])
            ax_rh.set_xlabel("信用格付け")
            ax_rh.set_ylabel("顧客数 (名)")
            st.pyplot(fig_rh)

    with col_right2:
        st.markdown("**💳 現行与信枠（CurrentLimit）分布**")
        limit_counts = df_customers['CurrentLimit'].value_counts().sort_index().reset_index()
        limit_counts.columns = ['Limit', 'Count']
        limit_counts['Limit'] = limit_counts['Limit'].astype(int)
        limit_colors = ['#17a2b8', '#1f77b4', '#2ca02c', '#ff7f0e', '#9467bd']
        if USE_PLOTLY:
            fig_limit = go.Figure(go.Bar(
                x=[f"{int(l)}万円" for l in limit_counts['Limit']],
                y=limit_counts['Count'],
                marker_color=limit_colors[:len(limit_counts)],
                text=limit_counts['Count'],
                textposition='outside',
                hovertemplate='与信枠: %{x}<br>顧客数: %{y}名<extra></extra>'
            ))
            fig_limit.update_layout(
                template="plotly_dark",
                height=320,
                margin=dict(l=20, r=20, t=20, b=40),
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="現行与信枠",
                yaxis_title="顧客数 (名)",
                showlegend=False
            )
            st.plotly_chart(fig_limit, width='stretch')
        else:
            fig_lm, ax_lm = plt.subplots(figsize=(5, 4))
            ax_lm.bar([f"{int(l)}万円" for l in limit_counts['Limit']], limit_counts['Count'])
            ax_lm.set_ylabel("顧客数 (名)")
            st.pyplot(fig_lm)

    st.markdown("---")

    # --- セグメント × 格付け ヒートマップ ---
    st.markdown("**🔥 セグメント × 信用格付け ヒートマップ（顧客数）**")
    st.write("各セグメントにどの格付けの顧客が何名いるかを可視化。セグメント別のリスク構造の違いが一目でわかります。")
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
            texttemplate="%{text}名",
            hovertemplate='セグメント: %{y}<br>格付け: %{x}<br>顧客数: %{z}名<extra></extra>',
            colorbar=dict(title="顧客数")
        ))
        fig_heat.update_layout(
            template="plotly_dark",
            height=300,
            margin=dict(l=20, r=20, t=20, b=40),
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="信用格付け (1:最優良 → 10:最高リスク)",
            yaxis_title="セグメント"
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
    st.markdown(f"**📈 現状収益性スナップショット（選択シナリオ: {sc_emoji} {scenario_choice}）**")
    st.info("💡 左サイドバーの「景気シナリオ」を変えると、デフォルト確率の変動に伴い、この収益性指標がリアルタイムに更新されます。")
    snap1, snap2, snap3 = st.columns(3)
    with snap1:
        st.metric(
            label="📈 現行の期待純利益",
            value=f"{current_net_profit:,.1f} 万円",
            help="現行のルールベース与信における年間期待純利益（収益 - 損失）"
        )
    with snap2:
        st.metric(
            label="⚠️ 現行の期待損失額（引当金）",
            value=f"{current_total_loss:,.1f} 万円",
            help="現行のルールベース与信におけるデフォルト期待損失額"
        )
    with snap3:
        current_efficiency = current_net_profit / current_total_loss if current_total_loss > 0 else 0
        st.metric(
            label="⚡ リスク対比運用効率",
            value=f"{current_efficiency:.2f}",
            help="純利益 ÷ 期待損失額。この値が高いほどリスク対比の収益性が高い"
        )

# ==========================================
# 8. Tab2: 最適化シミュレーション（メインダッシュボード）
# ==========================================
with tab2:
    st.markdown("### 🎯 最適化シミュレーション")
    st.write(f"景気シナリオ: **{sc_emoji} {scenario_choice}** | 最適化アプローチ: **{opt_mode_label}**")

    if not st.session_state.optimized:
        st.info("⬅️ **左のサイドバーでパラメータを設定し、「⚡ 最適化アロケーションを実行」ボタンを押してください。**\n\n最適化が完了すると、効率的フロンティアグラフと1,000名分の与信アロケーション明細が表示されます。")
        # 最適化前でもフロンティア曲線は表示する
        st.markdown("---")
        st.markdown("**📈 効率的フロンティア曲線（参考）**")
        st.write("以下は3つのシナリオにおける理論的な効率的フロンティアです。最適化を実行すると、現行ルールベースと最適化後のポートフォリオ位置も重ねて表示されます。")
        if USE_PLOTLY:
            fig_pre = go.Figure()
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Optimistic']['risk'],
                y=frontier_data['Optimistic']['profit'],
                mode='lines+markers',
                name='🟢 楽観フロンティア (デフォルト 0.7倍)',
                line=dict(color='#2ca02c', width=2, dash='dash'),
                marker=dict(size=6), opacity=0.6
            ))
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Neutral']['risk'],
                y=frontier_data['Neutral']['profit'],
                mode='lines+markers',
                name='🔵 中立フロンティア (デフォルト 1.0倍)',
                line=dict(color='#1f77b4', width=3),
                marker=dict(size=6), opacity=0.8
            ))
            fig_pre.add_trace(go.Scatter(
                x=frontier_data['Pessimistic']['risk'],
                y=frontier_data['Pessimistic']['profit'],
                mode='lines+markers',
                name='🔴 悲観フロンティア (デフォルト 1.5倍)',
                line=dict(color='#d62728', width=2, dash='dash'),
                marker=dict(size=6), opacity=0.6
            ))
            # 現行ルールベースの位置
            fig_pre.add_trace(go.Scatter(
                x=[current_total_loss],
                y=[current_net_profit],
                mode='markers',
                name='⭐️ 現行ルールベース (現在地)',
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
                    annotation_text=f" 許容期待損失上限 ({target_value:.0f}万円) ",
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
                    text="制約内で純利益を最大化 📈",
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
                    annotation_text=f" 目標期待純利益 ({target_value:.0f}万円) ",
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
                    text="目標利益を維持しリスクを最小化 🛡️",
                    font=dict(color="#d62728", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#d62728", borderwidth=1, borderpad=4
                )

            fig_pre.update_layout(
                template="plotly_dark",
                xaxis_title="期待総損失額 (貸倒引当金) [万円]",
                yaxis_title="期待総純利益 (収益 - 損失) [万円]",
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
    with st.spinner(f"Ipoptソルバーで最適化計算中... ({sc_emoji} {sc_name} シナリオ)"):
        try:
            opt_limits, shadow_price = solve_optimization(
                df_customers, target_value, pd_multiplier=pd_mult, mode=opt_mode
            )
        except ValueError as e:
            st.error(f"⚠️ ソルバーが実行不可能（Infeasible）を返しました。\n\n**原因**: 現在の制約条件（スライダーの値）では実現可能な解が存在しません。\n\nサイドバーのスライダーを調整してください（利益最大化モードではリスク上限を上げる、リスク最小化モードでは利益目標を下げる）。\n\n**詳細**: {e}")
            infeasible = True
        except Exception as e:
            st.error(f"⚠️ 最適化の実行中にエラーが発生しました: {e}")
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
                violation_msg = (
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
                violation_msg = (
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
                actions.append("🟢 増枠 (Limit Up)")
            elif opt < curr:
                actions.append("🔴 減枠 (Limit Down)")
            else:
                actions.append("⚪️ 維持 (Maintain)")

        # ==========================================
        # 改善効果のKPIカード
        # ==========================================
        if has_constraint_violation:
            st.warning(violation_msg)

        st.markdown("#### 📊 最適化による改善効果（現行ルール vs 最適化後）")
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        profit_delta = opt_net_profit - current_net_profit
        loss_delta = opt_total_loss - current_total_loss
        eff_delta = opt_efficiency - current_efficiency

        with m_col1:
            st.markdown(f"""
            <div class="metric-card metric-card-positive">
                <div class="metric-title">📈 最適化後の期待純利益</div>
                <div class="metric-value">{opt_net_profit:,.1f} 万円</div>
                <div class="metric-delta" style="color: {'#2ca02c' if profit_delta >= 0 else '#d62728'};">
                    {'▲' if profit_delta >= 0 else '▼'} {abs(profit_delta):.1f} 万円 (現行比)
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col2:
            st.markdown(f"""
            <div class="metric-card metric-card-negative">
                <div class="metric-title">⚠️ 最適化後の期待損失額</div>
                <div class="metric-value">{opt_total_loss:,.1f} 万円</div>
                <div class="metric-delta" style="color: {'#2ca02c' if loss_delta <= 0 else '#d62728'};">
                    {'▼' if loss_delta <= 0 else '▲'} {abs(loss_delta):.1f} 万円 (現行比)
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-title">⚡ リスク対比運用効率</div>
                <div class="metric-value">{opt_efficiency:.2f}</div>
                <div class="metric-delta" style="color: {'#2ca02c' if eff_delta >= 0 else '#d62728'};">
                    {'▲' if eff_delta >= 0 else '▼'} {abs(eff_delta):.2f} (現行比)
                </div>
            </div>
            """, unsafe_allow_html=True)
        with m_col4:
            if opt_mode == 'risk_min' and abs(shadow_price) > 0.01:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">🔬 シャドープライス（双対価格）</div>
                    <div class="metric-value">{shadow_price:.2f} 万円</div>
                    <div class="metric-delta" style="color: #17a2b8;">
                        利益目標1万円緩和→リスク削減量
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                action_counts_temp = pd.Series(actions).value_counts()
                up_c = action_counts_temp.get("🟢 増枠 (Limit Up)", 0)
                st.markdown(f"""
                <div class="metric-card metric-card-positive">
                    <div class="metric-title">🟢 増枠アクション件数</div>
                    <div class="metric-value">{up_c} 名</div>
                    <div class="metric-delta" style="color: #2ca02c;">
                        全体収益性を向上させる増枠
                    </div>
                </div>
                """, unsafe_allow_html=True)

        # ==========================================
        # 効率的フロンティアグラフ
        # ==========================================
        st.markdown("---")
        st.markdown("### 📈 マクロ視点：リスクとリターンの「効率的フロンティア」")
        st.markdown(f"現在選択中のシナリオ: **{sc_emoji} {scenario_choice}**")

        if USE_PLOTLY:
            fig = go.Figure()

            # 楽観シナリオ (緑)
            fig.add_trace(go.Scatter(
                x=frontier_data['Optimistic']['risk'],
                y=frontier_data['Optimistic']['profit'],
                mode='lines+markers',
                name='🟢 楽観フロンティア (デフォルト 0.7倍)',
                line=dict(color='#2ca02c', width=2, dash='dash'),
                marker=dict(size=6),
                opacity=0.6
            ))

            # 中立シナリオ (青)
            fig.add_trace(go.Scatter(
                x=frontier_data['Neutral']['risk'],
                y=frontier_data['Neutral']['profit'],
                mode='lines+markers',
                name='🔵 中立フロンティア (デフォルト 1.0倍)',
                line=dict(color='#1f77b4', width=3),
                marker=dict(size=6),
                opacity=0.8
            ))

            # 悲観シナリオ (赤)
            fig.add_trace(go.Scatter(
                x=frontier_data['Pessimistic']['risk'],
                y=frontier_data['Pessimistic']['profit'],
                mode='lines+markers',
                name='🔴 悲観フロンティア (デフォルト 1.5倍)',
                line=dict(color='#d62728', width=2, dash='dash'),
                marker=dict(size=6),
                opacity=0.6
            ))

            # 現行ルールベースの位置（オレンジの星）
            fig.add_trace(go.Scatter(
                x=[current_total_loss],
                y=[current_net_profit],
                mode='markers',
                name='⭐️ 現行ルールベース (現在地)',
                marker=dict(color='#ff7f0e', size=16, symbol='star', line=dict(color='black', width=1.5)),
                hovertemplate="<b>現行ルールベース</b><br>期待損失: %{x:.1f}万円<br>期待利益: %{y:.1f}万円<extra></extra>"
            ))

            # 最適化後のポートフォリオ位置（紫の丸）
            fig.add_trace(go.Scatter(
                x=[opt_total_loss],
                y=[opt_net_profit],
                mode='markers',
                name='🎯 最適化後のポートフォリオ (スライダー選択位置)',
                marker=dict(color='#9467bd', size=14, symbol='circle', line=dict(color='black', width=1.5)),
                hovertemplate="<b>最適化後</b><br>期待損失: %{x:.1f}万円<br>期待利益: %{y:.1f}万円<extra></extra>"
            ))

            # 動的ガイドラインと探索ベクトルの追加
            if opt_mode == 'profit_max':
                # 縦の制約線（期待損失上限）
                fig.add_vline(
                    x=target_value,
                    line_width=2,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text=f" 許容期待損失上限 ({target_value:.0f}万円) ",
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
                    text="制約内で純利益を最大化 📈",
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
                    annotation_text=f" 目標期待純利益 ({target_value:.0f}万円) ",
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
                    text="目標をクリアし期待損失を最小化 🛡️",
                    font=dict(color="#d62728", size=11),
                    bgcolor="rgba(30,30,30,0.75)", bordercolor="#d62728", borderwidth=1, borderpad=4
                )

            fig.update_layout(
                template="plotly_dark",
                xaxis_title="期待総損失額 (貸倒引当金) [万円]",
                yaxis_title="期待総純利益 (収益 - 損失) [万円]",
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
            ax_mpl.plot(frontier_data['Optimistic']['risk'], frontier_data['Optimistic']['profit'], 'o--', color='#2ca02c', label='楽観フロンティア')
            ax_mpl.plot(frontier_data['Neutral']['risk'], frontier_data['Neutral']['profit'], 'o-', color='#1f77b4', linewidth=2.5, label='中立フロンティア')
            ax_mpl.plot(frontier_data['Pessimistic']['risk'], frontier_data['Pessimistic']['profit'], 'o--', color='#d62728', label='悲観フロンティア')
            ax_mpl.plot(current_total_loss, current_net_profit, '*', color='#ff7f0e', markersize=15, label='現行ルールベース')
            ax_mpl.plot(opt_total_loss, opt_net_profit, 'o', color='#9467bd', markersize=12, label='最適化後')
            ax_mpl.set_xlabel("期待総損失額 (貸倒引当金) [万円]")
            ax_mpl.set_ylabel("期待総純利益 (収益 - 損失) [万円]")
            ax_mpl.legend(loc='upper left')
            ax_mpl.grid(True, linestyle='--', alpha=0.5)
            st.pyplot(fig_mpl)

        # ==========================================
        # ミクロドリルダウン：個人別アクション詳細
        # ==========================================
        st.markdown("---")
        st.markdown("### 🔍 ミクロドリルダウン：個人別与信アクション詳細")
        st.write("会社全体の目標を達成するために、ソルバーが導き出した個人の限度額アロケーション明細です。")

        # 詳細データの作成
        detail_df = pd.DataFrame({
            '顧客ID': df_customers['CustomerID'],
            'セグメント': df_customers['Segment'],
            '信用格付け': df_customers['Rating'],
            'デフォルト確率 (PD)': df_customers['PD'] * pd_mult,
            '現行の与信枠 (万円)': df_customers['CurrentLimit'].astype(int),
            '最適化後の与信枠 (万円)': opt_limits.astype(int),
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

        detail_df['期待利益 (万円)'] = np.round(ind_rev, 2)
        detail_df['期待損失 (万円)'] = np.round(ind_loss, 2)
        detail_df['限界リスク効率 (リターン/リスク比)'] = marginal_eff
        detail_df['与信アクション'] = actions

        # 統計サマリー
        action_counts = detail_df['与信アクション'].value_counts()
        up_count = action_counts.get("🟢 増枠 (Limit Up)", 0)
        down_count = action_counts.get("🔴 減枠 (Limit Down)", 0)
        maintain_count = action_counts.get("⚪️ 維持 (Maintain)", 0)

        s_col1, s_col2, s_col3 = st.columns(3)
        s_col1.metric("🟢 増枠 (Limit Up) アクション件数", f"{up_count} 名", "全体の収益性を向上させる攻めの増枠")
        s_col2.metric("🔴 減枠 (Limit Down) アクション件数", f"{down_count} 名", "全体の期待損失を抑える守りの減枠")
        s_col3.metric("⚪️ 維持 (Maintain) アクション件数", f"{maintain_count} 名", "現状維持")

        # 2. 優先増枠/減枠TOP10の表示
        st.markdown("---")
        st.markdown("**🔍 リスク効率に基づく優先顧客アクション（TOP10）**")
        st.write("「与信を1万円追加したときのリターン/リスク比（限界リスク効率）」に基づき、ソルバーが最も優先して増枠した顧客、および優先して減枠した顧客のTOP10リストです。")

        top_df_base = pd.DataFrame({
            'CustomerID': df_customers['CustomerID'],
            'Segment': df_customers['Segment'],
            'Rating': df_customers['Rating'],
            'MarginalEfficiency': marginal_eff,
            'CurrentLimit': df_customers['CurrentLimit'].astype(int),
            'OptimizedLimit': opt_limits.astype(int),
            'Action': actions
        })

        st.markdown("🟢 **優先増枠推奨 (リターン/リスク比が高い顧客TOP10)**")
        up_df = top_df_base[top_df_base['OptimizedLimit'] > top_df_base['CurrentLimit']].sort_values(by='MarginalEfficiency', ascending=False).head(10)
        if len(up_df) > 0:
            up_df_display = up_df.rename(columns={
                'CustomerID': '顧客ID', 'Segment': 'セグメント', 'Rating': '格付け',
                'MarginalEfficiency': '限界リスク効率', 'CurrentLimit': '現行枠 (万円)', 'OptimizedLimit': '最適化枠 (万円)',
            })
            st.dataframe(up_df_display.drop(columns=['Action']), hide_index=True, width='stretch')
        else:
            st.write("対象顧客なし（現在の制約下では増枠アクションは発生していません）")

        st.markdown("🔴 **優先減枠推奨 (リターン/リスク比がマイナス・極低の顧客TOP10)**")
        down_df = top_df_base[top_df_base['OptimizedLimit'] < top_df_base['CurrentLimit']].sort_values(by='MarginalEfficiency', ascending=True).head(10)
        if len(down_df) > 0:
            down_df_display = down_df.rename(columns={
                'CustomerID': '顧客ID', 'Segment': 'セグメント', 'Rating': '格付け',
                'MarginalEfficiency': '限界リスク効率', 'CurrentLimit': '現行枠 (万円)', 'OptimizedLimit': '最適化枠 (万円)',
            })
            st.dataframe(down_df_display.drop(columns=['Action']), hide_index=True, width='stretch')
        else:
            st.write("対象顧客なし（現在の制約下では減枠アクションは発生していません）")

        # 3. 格付け別の与信アクション比率
        st.markdown("---")
        st.markdown("**📈 信用格付け（Rating）別の与信アロケーションシフト分析**")
        st.write("格付けごとに、ソルバーが与信枠をどう調整したか（ポートフォリオ・シフト）のインサイトです。")

        rating_crosstab = pd.crosstab(
            df_customers['Rating'],
            top_df_base['Action']
        ).reindex(
            columns=["🟢 増枠 (Limit Up)", "🔴 減枠 (Limit Down)", "⚪️ 維持 (Maintain)"],
            fill_value=0
        )

        if USE_PLOTLY:
            fig_rating = go.Figure()
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab['🟢 増枠 (Limit Up)'], name='🟢 増枠 (Limit Up)', marker_color='#2ca02c'))
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab['🔴 減枠 (Limit Down)'], name='🔴 減枠 (Limit Down)', marker_color='#d62728'))
            fig_rating.add_trace(go.Bar(x=rating_crosstab.index, y=rating_crosstab['⚪️ 維持 (Maintain)'], name='⚪️ 維持 (Maintain)', marker_color='#7f7f7f'))
            fig_rating.update_layout(
                template="plotly_dark", barmode='stack',
                xaxis=dict(title="信用格付け (1:最優良 〜 10:最リスク)", tickmode='linear'),
                yaxis_title="顧客数 (名)",
                margin=dict(l=40, r=40, t=10, b=40), height=380,
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_rating, width='stretch')
        else:
            fig_rating_mpl, ax_r = plt.subplots(figsize=(10, 4))
            rating_crosstab.plot(kind='bar', stacked=True, color=['#2ca02c', '#d62728', '#7f7f7f'], ax=ax_r)
            ax_r.set_xlabel("信用格付け")
            ax_r.set_ylabel("顧客数 (名)")
            st.pyplot(fig_rating_mpl)

        # 4. セグメント別アクション集計
        st.markdown("---")
        st.markdown("### 📊 セグメント別与信アクション集計 (マクロ要約)")
        st.markdown("**📁 セグメント別アクション集計表 (名)**")
        crosstab_df = pd.crosstab(
            detail_df['セグメント'],
            detail_df['与信アクション']
        ).reindex(
            columns=["🟢 増枠 (Limit Up)", "🔴 減枠 (Limit Down)", "⚪️ 維持 (Maintain)"],
            fill_value=0
        )
        crosstab_df['合計'] = crosstab_df.sum(axis=1)
        st.dataframe(crosstab_df, width='stretch')

        st.markdown("**📊 セグメント別アクション配分比率**")
        if USE_PLOTLY:
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df['🟢 増枠 (Limit Up)'], name='🟢 増枠 (Limit Up)', marker_color='#2ca02c'))
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df['🔴 減枠 (Limit Down)'], name='🔴 減枠 (Limit Down)', marker_color='#d62728'))
            fig_bar.add_trace(go.Bar(x=crosstab_df.index, y=crosstab_df['⚪️ 維持 (Maintain)'], name='⚪️ 維持 (Maintain)', marker_color='#7f7f7f'))
            fig_bar.update_layout(
                template="plotly_dark", barmode='stack',
                xaxis_title="セグメント", yaxis_title="人数 (名)",
                margin=dict(l=40, r=40, t=10, b=40), height=380,
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_bar, width='stretch')
        else:
            fig_bar_mpl, ax_b = plt.subplots(figsize=(10, 4))
            crosstab_df[["🟢 増枠 (Limit Up)", "🔴 減枠 (Limit Down)", "⚪️ 維持 (Maintain)"]].plot(
                kind='bar', stacked=True, color=['#2ca02c', '#d62728', '#7f7f7f'], ax=ax_b)
            ax_b.set_ylabel("人数 (名)")
            ax_b.legend(loc='upper right', fontsize='small')
            st.pyplot(fig_bar_mpl)

        # PDの書式設定
        detail_df['デフォルト確率 (PD)'] = detail_df['デフォルト確率 (PD)'].map(lambda x: f"{x*100:.2f}%")

        st.markdown("---")
        st.markdown("### 🔍 ミクロ視点：個人別与信枠調整アクションリスト (1,000名分)")
        st.write("各顧客に対して適用される具体的な与信枠の調整アクションと、その詳細なパラメータを含む一覧テーブルです。")

        csv_bytes = detail_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 最適化与信枠リスト (CSV) をダウンロード",
            data=csv_bytes,
            file_name=f"optimized_limits_{sc_name}_{opt_mode}.csv",
            mime="text/csv",
            help="このCSVファイルをダウンロードして、Excelでの分析や、基幹の与信システムへそのまま流し込むことができます。"
        )

        st.dataframe(
            detail_df.sort_values(by='限界リスク効率 (リターン/リスク比)', ascending=False),
            width='stretch',
            height=400
        )

        st.markdown("##### 💡 デモのポイント:")
        st.markdown("""
        1. **マクロのワープ効果**: グラフ上の『現行ルールベース（星印）』がいかに非効率であるかを確認してください。全体最適化した『フロンティア（曲線）』へ移行するだけで、同一リスクでリターンを最大化、または同一リターンでリスクを最小化できます。
        2. **ミクロの連動**: 左側のサイドバーのスライダー（リスク上限）を動かしてみてください。会社全体の許容リスクを変えた瞬間、ソルバーが瞬時に再計算を行い、画面下の『増枠/減枠のアクション対象者』が動的に入れ替わることが確認できます。これが全体最適と個別与信枠のリアルな連動性です。
        """)

# ==========================================
# 9. Tab3: 前提条件と数理定式化の解説タブ
# ==========================================
with tab3:
    st.markdown("### 📑 与信ポートフォリオ全体最適化の数理モデルと前提条件")
    st.markdown(r"""
    本システムは、金融工学と数理最適化技術（非線形計画法）を組み合わせることで、顧客ポートフォリオ全体の「リスク（期待損失）」と「リターン（期待純利益）」を最適化するシステムです。
    以下に、本シミュレーターで用いられている前提条件、数理モデルの定式化、および実務的なビジネスルールの適用プロセスについて解説します。
    """)

    st.markdown("---")
    st.markdown("#### 1. 想定利用額（EAD）の非線形飽和カーブ")
    st.markdown(r"""
    各顧客 $i$ の与信枠（限度額）を $L_i$ [万円] としたとき、その顧客の年間想定利用額（EAD: Exposure at Default）は、与信枠に対して線形に伸びるのではなく、ある限界値（決済ポテンシャル）に達すると頭打ちになる（飽和する）非線形な**飽和カーブ（指数関数）**に従うとモデリングしています。
    """)
    st.latex(r"\text{EAD}_i(L_i) = C_i \times \left(1 - e^{-k_i \times L_i}\right)")
    st.markdown(r"""
    - $C_i$ : 顧客 $i$ の潜在的な年間最大決済ポテンシャル（年収や決済能力の推定値）
    - $k_i$ : 与信枠に対する利用額の反応度（飽和スピードを示す係数）

    *このモデリングにより、「利用が少ない顧客に過大な枠を与えても無駄になり、利用意欲の高い顧客に枠を寄せるべき」という実務的なダイナミクスを再現できます。*

    > **⚠️ 実運用に向けたパラメータ推定の注意点とモデルの限界:**
    > - **EAD飽和カーブのパラメータ ($C_i, k_i$) の推定**: 本シミュレーターではデモ用にこれらを乱数で生成していますが、実運用に適用する際は、過去のカード利用履歴や枠増減データに基づき、顧客ごとに統計的フィッティング（非線形回帰など）を行ってパラメータを推定する必要があります。
    > - **回収不能率 ($LGD$) の固定化**: 本モデルでは簡略化のため $LGD$ を一律 80% と設定していますが、実務上は担保・保証の有無、顧客セグメント、および法的な回収ルートによって大きく変動する変数である点にご留意ください。
    """)

    st.markdown("#### 2. ポートフォリオ全体の数理定式化")
    st.markdown(r"""
    本シミュレーターでは、経営戦略に応じて以下の**双方向の最適化問題**を定式化し、非線形ソルバーである **IPOPT** を用いて瞬時に最適解を算出しています。

    ##### 各個人の基本指標:
    - **期待収益 (Revenue)** : $Rev_i(L_i) = r_i \times \text{EAD}_i(L_i) \times (1 - P_i)$
    - **期待損失 (Expected Loss)** : $Loss_i(L_i) = P_i \times \text{LGD} \times \text{EAD}_i(L_i)$
    - **期待純利益 (Net Profit)** : $Profit_i(L_i) = Rev_i(L_i) - Loss_i(L_i)$

    ここで、$r_i$ はセグメントごとの適用収益率（ショッピング: 2.0%, リボ: 15.0%, キャッシング: 18.0%, Combo: 16.5%）、$P_i$ は景気シナリオ倍率を考慮した顧客のデフォルト確率、$LGD$ はデフォルト時の回収不能率（一律 80%）です。
    """)

    st.markdown("##### 🎯 アプローチ A: 利益最大化モデル (Profit Maximization)")
    st.markdown(r"""
    会社全体の期待損失（引当金）を一定の許容値 $T_{risk}$ 以下に抑えつつ、ポートフォリオ全体の期待純利益を最大化します。
    """)
    st.latex(r"""
    \begin{aligned}
    \text{Maximize} \quad & \sum_{i=1}^{N} Profit_i(L_i) \\
    \text{subject to} \quad & \sum_{i=1}^{N} Loss_i(L_i) \le T_{risk} \\
    & 0 \le L_i \le 100 \quad (\forall i)
    \end{aligned}
    """)

    st.markdown("##### 🛡️ アプローチ B: リスク最小化モデル (Risk Minimization)")
    st.markdown(r"""
    会社全体として必達すべき期待純利益目標 $T_{profit}$ をクリアした上で、デフォルトリスク（期待損失額）を最小化します。
    """)
    st.latex(r"""
    \begin{aligned}
    \text{Minimize} \quad & \sum_{i=1}^{N} Loss_i(L_i) \\
    \text{subject to} \quad & \sum_{i=1}^{N} Profit_i(L_i) \ge T_{profit} \\
    & 0 \le L_i \le 100 \quad (\forall i)
    \end{aligned}
    """)

    st.markdown("---")
    st.markdown("#### 3. 実務的制約（ビジネスルール）の後処理適用")
    st.markdown(r"""
    数理最適化の「理論解」は連続値であり、そのままでは実務（顧客管理やシステム制約、法規制）に適用できません。そのため、本システムでは理論解に対して以下の**後処理ビジネスルール**を厳格に適用しています。

    1. **与信メニューへの丸め（離散化）**:
       クレジットカードの与信枠は、一般的にキリの良い数字で提供されます。本システムでは、理論最適値 $L_i^*$ に最も近い与信枠メニュー $\{10, 30, 50, 70, 100\}$ 万円に丸め処理（Rounding）を行っています（利用額が極小の場合は 0 万円）。

    2. **主婦・学生の一律上限制限（社会的・規制ルール）**:
       社会的配慮および返済能力の観点から、主婦や学生セグメントの顧客に対しては、理論解がどれほど大きくても一律で**最大 10万円** に制限します。
    """)
    st.latex(r"L_i \leftarrow \min(L_i^*, 10) \quad \text{if } Job_i \in \{\text{Housewife}, \text{Student}\}")
    st.markdown(r"""
    3. **ショッピング枠の減枠原則禁止（CRM・顧客保護ルール）**:
       ショッピング枠の突然の減枠は、顧客に不快感を与え、競合他社への乗り換え（離反）を招く深刻なリスクです。そのため、ショッピングを主軸とするセグメントの顧客に対しては、**「現在の与信枠を下回る減枠は原則として行わない（維持または増枠のみ）」**というルールを強制適用しています。
    """)
    st.latex(r"L_i \leftarrow \max(L_i^*, L_i^{current}) \quad \text{if } Segment_i = \text{Shopping}")
    st.markdown(r"""
    *このような「数理最適化 ✕ 後処理ビジネスルール」のハイブリッド設計こそが、理論の美しさと実務のリアリティを両立させる PoC の核心です。*
    """)

    st.markdown("---")
    st.markdown("#### 4. 📚 モデル運用上の注意事項と説明責任（ガバナンス）")
    st.markdown("""
    本数理モデルを実業務・本番システムへと展開するにあたっては、以下のガバナンスおよび運用要件を考慮することが推奨されます。

    1. **データの鮮度と定期キャリブレーション**:
       - 顧客の属性（職業、年収等）や信用格付けデータは、定期的に（例：月次・四半期ごと）更新される必要があります。
       - デフォルト確率（PD）推定モデルのパラメータは、少なくとも年次でバックテストを行い、実績値との乖離（キャリブレーション）を確認してモデルを再学習させることが重要です。

    2. **ビジネスルールの優先と人間による最終判断（Human-in-the-Loop）**:
       - 本数理モデルが出力する与信アロケーション計画は「意思決定を支援する高度な参考情報」であり、最終的な限度額適用は、与信審査部門の最終判断や例外承認プロセスを最優先する設計にしてください。
       - 個人の信用情報保護法や各種規制（総量規制など）に抵触しないよう、数理最適化ロジックの前に「法規制フィルタ」をかけるレイヤーを実務上配置することが不可欠です。

    3. **モデル検証とバックテスト**:
       - 最適化による効果（期待利益の向上、デフォルト損失の抑制）を検証するため、実際の適用対象から一部を対照群（Control Group）として除外し、現行ルール適用のままとする **A/Bテスト** を実務上で一定期間実施し、モデルの真の有効性を監査することを強く推奨します。
    """)


# ==========================================
# 10. Tab4: モデル感度分析（堅牢性の検証）タブ
# ==========================================
with tab4:
    st.markdown("### 🌪️ ポートフォリオ期待純利益の感度分析（トルネードチャート）")
    st.markdown("""
    与信意思決定モデルの主要パラメータを **±10%** 変動させた際に、ポートフォリオ全体の「最適化後の期待純利益」が受ける影響度（インパクト）を検証したものです。
    グラフの横幅が広い（上の項目）ほど、そのパラメータの推定精度が全体の収益計画や経営判断に対して与える影響が大きいことを意味し、優先的にデータ品質の改善やモニタリングを行うべき対象となります。
    """)

    if not st.session_state.optimized:
        st.info("⬅️ **先に「🎯 最適化シミュレーション」タブで最適化を実行してください。**\n\n最適化実行後、感度分析が利用可能になります。")
    elif infeasible:
        st.warning("⚠️ 現在の最適化条件が実行不可能（Infeasible）なため、感度分析を実行できません。左側サイドバーのスライダーを調整して実行可能な範囲にしてください。")
    else:
        with st.spinner("パラメータ感度を分析中..."):
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
                    name='パラメータ +10% 変動時',
                    orientation='h',
                    marker=dict(color='#2ca02c', line=dict(color='white', width=0.5)),
                    hovertemplate="期待純利益の変動: %{x:+.1f} 万円<extra></extra>"
                ))

                fig_tornado.add_trace(go.Bar(
                    y=labels,
                    x=low_values,
                    name='パラメータ -10% 変動時',
                    orientation='h',
                    marker=dict(color='#d62728', line=dict(color='white', width=0.5)),
                    hovertemplate="期待純利益の変動: %{x:+.1f} 万円<extra></extra>"
                ))

                fig_tornado.update_layout(
                    template="plotly_dark",
                    barmode='relative',
                    xaxis=dict(title="最適化期待純利益の変動量 (万円)", zeroline=True, zerolinecolor='white', zerolinewidth=1.5),
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
                ax_tor.set_xlabel("最適化期待純利益の変動量 (万円)")
                ax_tor.legend()
                st.pyplot(fig_tor)

            st.markdown("""
            **💡 感度分析結果から得られる経営的インサイト**:
            - **顧客決済ポテンシャル $C$**: この感度が高い場合、各顧客の潜在的な最大年間決済能力の推定精度を上げることが最重要です。信用格付けや年収情報等との掛け合わせによる予測精度向上が投資対効果（ROI）を生みます。
            - **与信利用反応度 $k$**: この感度が高い場合、与信枠（限度額）の増減に対する顧客のカード利用意欲の反応性モデル（与信弾力性）の精緻化が必要です。
            - **デフォルト確率 $PD$**: この感度が高い場合、マクロ経済の動向による収益変動リスク（ボラティリティ）が大きいことを意味します。好不況サイクルに応じた早期警戒インジケーター（EWS）の設定や、機動的なリスクアロケーション枠調整が有効です。
            """)
