"""
与信枠の後処理ビジネスルール。

Pyomo/Ipoptが算出する連続値の理論解を、実務適用可能な離散的な与信枠に
変換するための純粋関数群。ソルバー（Ipopt）に依存しないため、
軽量なユニットテストが可能。
"""
import numpy as np
import pandas as pd

DEFAULT_LIMIT_OPTIONS = (10, 30, 50, 70, 100)


def round_to_menu(value: float, limit_options=DEFAULT_LIMIT_OPTIONS) -> float:
    """
    理論値を最も近い与信枠メニューに丸める。

    Args:
        value: ソルバーが算出した連続値の与信枠（万円）
        limit_options: 実務上提供可能な与信枠メニュー

    Returns:
        limit_options のいずれかに丸められた値
    """
    options = np.array(limit_options)
    closest_idx = np.abs(options - value).argmin()
    return float(options[closest_idx])


def apply_business_rules(
    raw_limits: np.ndarray,
    job: pd.Series,
    segment: pd.Series,
    current_limit: pd.Series,
    limit_options=DEFAULT_LIMIT_OPTIONS,
    housewife_student_cap: float = 10.0,
) -> np.ndarray:
    """
    ソルバーの理論解（連続値）に対し、実務ルールを順に適用する。

    Rules (適用順序に意味がある):
      1. 与信メニューへの丸め（離散化）
      2. Shoppingセグメントは現行枠を下回らない（減枠禁止、顧客離反防止）
      3. 主婦・学生は一律 housewife_student_cap 万円以下に制限（最終・最優先）

    Note:
        主婦・学生への上限制限は社会的配慮・規制上の要請であり、他のどの
        ビジネスルール（Shoppingの減枠禁止など）よりも優先されるべきため、
        最後に適用して確実に上限が守られるようにしている。
        （仮に②を③より後に適用すると、現行枠が上限を超える主婦・学生
        顧客のケースで規制上限が上書きされてしまう可能性がある）

    Args:
        raw_limits: ソルバーが算出した連続値の与信枠配列（万円）
        job: 各顧客の職業属性（Employee / Housewife / Student）
        segment: 各顧客のセグメント（Shopping / Revo / Cashing / Combo）
        current_limit: 各顧客の現行与信枠
        limit_options: 実務上提供可能な与信枠メニュー
        housewife_student_cap: 主婦・学生に適用する上限（万円）

    Returns:
        後処理ルール適用後の与信枠配列
    """
    n = len(raw_limits)
    result = np.zeros(n)

    for i in range(n):
        rounded = round_to_menu(raw_limits[i], limit_options)

        if segment.iloc[i] == "Shopping":
            rounded = max(rounded, current_limit.iloc[i])

        # 規制・社会的配慮ルールは最後に適用し、必ず優先させる
        if job.iloc[i] in ("Housewife", "Student"):
            rounded = min(rounded, housewife_student_cap)

        result[i] = rounded

    return result


def classify_action(current_limit: float, optimized_limit: float) -> str:
    """
    現行枠と最適化後の枠を比較し、アクション区分を返す。

    Returns:
        "up" | "down" | "maintain"
    """
    if optimized_limit > current_limit:
        return "up"
    elif optimized_limit < current_limit:
        return "down"
    return "maintain"
