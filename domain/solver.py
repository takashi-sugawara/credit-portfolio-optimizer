"""
数理最適化モデル構築・求解ロジック（Pyomo + Ipopt）。

UI（Streamlit）に依存しない。呼び出し側が翻訳ラベルやキャッシュを担当する。
"""
import numpy as np
import pandas as pd
import pyomo.environ as pyo
from pyomo.opt import SolverFactory

from domain.config import SCENARIO_PD_MULTIPLIERS
from domain.rules import apply_business_rules


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

    # 4. amplpy modules経由でインストールされたIpoptを探索（Azure App Service向け）
    #    amplpy.modules.load() が、amplpyが管理するソルバーバイナリのディレクトリを
    #    現在プロセスのPATH環境変数に追加してくれる。
    try:
        import amplpy.modules as amplpy_modules
        amplpy_modules.load()
        path = shutil.which("ipopt")
        if path:
            return path
    except Exception:
        pass

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
    rounded_limits = apply_business_rules(
        raw_limits=np.array(opt_limits),
        job=df['Job'],
        segment=df['Segment'],
        current_limit=df['CurrentLimit'],
    )

    return rounded_limits, shadow_price


def get_efficient_frontiers(df: pd.DataFrame) -> dict:
    """
    楽観、中立、悲観の3つのシナリオにおいて、リスク上限を変化させて効率的フロンティア曲線のデータを事前生成する。
    """
    frontiers = {}

    for sc_name, multiplier in SCENARIO_PD_MULTIPLIERS.items():
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
            except Exception:
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


def run_sensitivity_analysis(df, target_value, pd_mult, opt_mode, parameter_labels):
    """
    parameter_labels: {'potential_c': <label>, 'alpha_k': <label>, 'pd': <label>}
    表示用ラベルは呼び出し側（UI言語設定を持つ側）が翻訳して渡す。
    """
    try:
        base_limits, _ = solve_optimization(df, target_value, pd_multiplier=pd_mult, mode=opt_mode)
        base_profit = calculate_portfolio_profit(df, base_limits, pd_mult)
    except Exception:
        return None

    parameters = {
        parameter_labels['potential_c']: ('PotentialC', 0.1),
        parameter_labels['alpha_k']: ('AlphaK', 0.1),
        parameter_labels['pd']: ('PD', 0.1),
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
