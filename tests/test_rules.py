"""
domain/rules.py のユニットテスト。

Ipopt/Pyomoに依存しないため、pandas/numpy/pytestのみで高速に実行できる。
"""
import numpy as np
import pandas as pd
import pytest

from domain.rules import apply_business_rules, classify_action, round_to_menu


class TestRoundToMenu:
    def test_picks_nearest_option(self):
        # |42-30|=12, |42-50|=8 -> 50に丸まる
        assert round_to_menu(42) == 50
        assert round_to_menu(15) == 10
        assert round_to_menu(95) == 100

    def test_exact_match_returns_same_value(self):
        assert round_to_menu(50) == 50

    def test_boundary_value_zero_rounds_to_minimum_option(self):
        assert round_to_menu(0) == 10

    def test_custom_limit_options(self):
        assert round_to_menu(25, limit_options=(0, 20, 40)) == 20


class TestApplyBusinessRules:
    def test_housewife_and_student_are_capped_at_10(self):
        raw = np.array([80.0, 60.0])
        job = pd.Series(["Housewife", "Student"])
        segment = pd.Series(["Revo", "Revo"])
        current = pd.Series([10.0, 10.0])

        result = apply_business_rules(raw, job, segment, current)

        assert (result <= 10.0).all()

    def test_shopping_segment_never_decreases_below_current_limit(self):
        raw = np.array([5.0])  # 理論解が現行枠より低い
        job = pd.Series(["Employee"])
        segment = pd.Series(["Shopping"])
        current = pd.Series([50.0])

        result = apply_business_rules(raw, job, segment, current)

        assert result[0] >= 50.0

    def test_non_shopping_segment_can_decrease(self):
        raw = np.array([5.0])
        job = pd.Series(["Employee"])
        segment = pd.Series(["Cashing"])
        current = pd.Series([50.0])

        result = apply_business_rules(raw, job, segment, current)

        assert result[0] < 50.0

    def test_employee_shopping_can_increase_above_current(self):
        raw = np.array([95.0])
        job = pd.Series(["Employee"])
        segment = pd.Series(["Shopping"])
        current = pd.Series([30.0])

        result = apply_business_rules(raw, job, segment, current)

        assert result[0] == 100.0

    def test_housewife_shopping_combined_rule_cap_wins(self):
        # 主婦 x Shopping x 現行枠30万円 の場合でも、上限10万円ルールが優先される
        raw = np.array([90.0])
        job = pd.Series(["Housewife"])
        segment = pd.Series(["Shopping"])
        current = pd.Series([30.0])

        result = apply_business_rules(raw, job, segment, current)

        assert result[0] == 10.0

    def test_result_length_matches_input(self):
        raw = np.array([10.0, 20.0, 30.0])
        job = pd.Series(["Employee"] * 3)
        segment = pd.Series(["Revo"] * 3)
        current = pd.Series([10.0] * 3)

        result = apply_business_rules(raw, job, segment, current)

        assert len(result) == 3


class TestClassifyAction:
    @pytest.mark.parametrize(
        "current,optimized,expected",
        [
            (30.0, 50.0, "up"),
            (50.0, 30.0, "down"),
            (30.0, 30.0, "maintain"),
        ],
    )
    def test_classification(self, current, optimized, expected):
        assert classify_action(current, optimized) == expected
