"""
strategy_manager.py
전략 파라미터의 중앙 관리, 버전 관리, 변경 이력 추적
"""

import json
import os
import shutil
from datetime import datetime
from typing import Optional

CONFIG_PATH   = os.path.join(os.path.dirname(__file__), "strategy_config.json")
HISTORY_DIR   = os.path.join(os.path.dirname(__file__), "strategy_history")

_cache: Optional[dict] = None


def get_config() -> dict:
    """현재 전략 설정을 반환합니다. 파일이 없으면 기본값을 생성합니다."""
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(CONFIG_PATH):
        _cache = _default_config()
        save_config(_cache, reason="자동 생성 기본값")
        return _cache
    with open(CONFIG_PATH, encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


def save_config(config: dict, reason: str = ""):
    """
    새 설정을 저장하고 이전 버전을 history에 백업합니다.
    """
    global _cache

    # 이전 버전 백업
    if os.path.exists(CONFIG_PATH):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        old = _load_raw()
        ts  = old.get("updated_at", datetime.now().isoformat())[:19].replace(":", "-")
        ver = old.get("version", 0)
        backup_path = os.path.join(HISTORY_DIR, f"v{ver:04d}_{ts}.json")
        shutil.copy2(CONFIG_PATH, backup_path)

    config["version"]    = (config.get("version") or 0) + 1
    config["updated_at"] = datetime.now().isoformat()
    config["update_reason"] = reason

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    _cache = config
    print(f"  전략 업데이트 v{config['version']}: {reason}")


def reload():
    """캐시를 무효화하고 파일에서 다시 로드합니다."""
    global _cache
    _cache = None
    return get_config()


def get_history() -> list:
    """버전 이력 목록을 반환합니다."""
    if not os.path.exists(HISTORY_DIR):
        return []
    files = sorted(os.listdir(HISTORY_DIR))
    history = []
    for f in files:
        if f.endswith(".json"):
            with open(os.path.join(HISTORY_DIR, f), encoding="utf-8") as fp:
                data = json.load(fp)
            history.append({
                "file":    f,
                "version": data.get("version"),
                "updated_at": data.get("updated_at"),
                "reason":  data.get("update_reason", ""),
            })
    return history


def print_history():
    """버전 이력을 출력합니다."""
    history = get_history()
    if not history:
        print("  버전 이력 없음")
        return
    print(f"\n  {'버전':>4} | {'일시':>19} | 변경 이유")
    print(f"  {'-'*60}")
    for h in history:
        print(f"  v{h['version']:>3} | {h['updated_at'][:19]} | {h['reason']}")
    cur = get_config()
    print(f"  v{cur['version']:>3} | {cur['updated_at'][:19]} | {cur['update_reason']} (현재)")


def _load_raw() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _default_config() -> dict:
    return {
        "version": 0,
        "updated_at": datetime.now().isoformat(),
        "update_reason": "기본값",
        "factor_weights": {"quality": 0.35, "value": 0.35, "momentum": 0.30},
        "filters": {
            "min_market_cap_억": 500,
            "max_debt_ratio": 2.0,
            "min_roe": 0.05,
            "exclude_finance": True,
        },
        "risk_overlay": {
            "ma_weeks": 40,
            "strong_bull_gap_pct": 5.0,
            "strong_bear_gap_pct": -10.0,
            "caution_equity_ratio": 0.50,
            "strong_bear_equity_ratio": 0.30,
        },
        "portfolio": {
            "top_n": 25,
            "stop_loss_pct": -0.08,
            "equity_budget": 0.95,
        },
        "validation_gates": {
            "min_sharpe": 0.2,
            "max_mdd_pct": -30.0,
            "min_alpha_pct": 0.0,
        },
    }


if __name__ == "__main__":
    cfg = get_config()
    print(f"\n현재 전략 설정 (v{cfg['version']})")
    print(f"  팩터 가중치: {cfg['factor_weights']}")
    print(f"  필터: {cfg['filters']}")
    print(f"  포트폴리오: {cfg['portfolio']}")
    print_history()
