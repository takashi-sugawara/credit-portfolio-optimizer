"""
`config/business_rules.yaml` からビジネスルール閾値を読み込む。

コードレビューを経ずに現場が差し替える意味のある値だけを対象とする
（詳細は docs/ROADMAP.md Phase 1 参照）。
"""
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "business_rules.yaml"


def _load(path: Path = _CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


_config = _load()

LIMIT_OPTIONS: tuple = tuple(_config["credit_limit_menu"]["options"])
HOUSEWIFE_STUDENT_CAP: float = float(_config["credit_limit_menu"]["housewife_student_cap"])
SCENARIO_PD_MULTIPLIERS: dict = {
    name: float(v["pd_multiplier"])
    for name, v in _config["economic_scenarios"].items()
}
