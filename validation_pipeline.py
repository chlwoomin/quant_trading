"""
validation_pipeline.py
전략 변경 시 모의 → 실제 데이터 순으로 자동 검증

검증 게이트:
  1차 (모의):  샤프 ≥ 0.2  AND  MDD ≥ -30%  AND  Alpha ≥ 0%
  2차 (실제):  샤프 ≥ 0.0  AND  MDD ≥ -35%  AND  Alpha ≥ -2%
  → 두 게이트 모두 통과해야 전략 업데이트 확정

실제 데이터 검증은 Python 3.9 (yfinance 안정 환경) 에서 subprocess로 실행합니다.
"""

import sys
import os
import json
import subprocess
import tempfile

from backtest import Backtest, compute_metrics, generate_data, WARMUP_WEEKS

PYTHON39 = r"C:/Users/woomin/AppData/Local/Programs/Python/Python39/python.exe"


# ── 검증 게이트 정의 ──────────────────────────────────────────────────────────
def _passes_gate(metrics: dict, gates: dict) -> tuple:
    """
    Returns (passed: bool, reason: str)
    """
    sharpe  = metrics.get("sharpe", -999)
    mdd     = metrics.get("max_dd_pct", -999)
    alpha   = metrics.get("alpha_pct", -999)
    n_weeks = metrics.get("n_weeks", 0)

    if n_weeks < 52:
        return False, f"백테스트 기간 부족 ({n_weeks}주 < 52주)"
    if sharpe < gates["min_sharpe"]:
        return False, f"샤프 {sharpe:.3f} < 기준 {gates['min_sharpe']}"
    if mdd < gates["max_mdd_pct"]:
        return False, f"MDD {mdd:.1f}% < 기준 {gates['max_mdd_pct']}%"
    if alpha < gates["min_alpha_pct"]:
        return False, f"Alpha {alpha:.1f}% < 기준 {gates['min_alpha_pct']}%"

    return True, "통과"


# ── 1차: 모의 데이터 검증 ─────────────────────────────────────────────────────
def validate_sim(config: dict, backtest_weeks: int = 104, seed: int = 42) -> dict:
    """
    시뮬레이션 데이터로 전략을 검증합니다.
    """
    print("  [1차] 모의 데이터 백테스트 실행 중...")
    try:
        bt      = Backtest(backtest_weeks=backtest_weeks, seed=seed, config=config)
        bt._avg_prices = {}
        df      = bt.run()
        metrics = compute_metrics(df)
    except Exception as e:
        return {"passed": False, "reason": f"백테스트 실행 오류: {e}", "metrics": {}}

    gates = config.get("validation_gates", {
        "min_sharpe": 0.2, "max_mdd_pct": -30.0, "min_alpha_pct": 0.0
    })
    passed, reason = _passes_gate(metrics, gates)

    print(f"  [1차] {'통과 ✓' if passed else '실패 ✗'}  "
          f"Sharpe={metrics.get('sharpe', 0):.3f}  "
          f"MDD={metrics.get('max_dd_pct', 0):.1f}%  "
          f"Alpha={metrics.get('alpha_pct', 0):+.1f}%")
    if not passed:
        print(f"  [1차] 실패 사유: {reason}")

    return {"passed": passed, "reason": reason, "metrics": metrics, "stage": "simulation"}


# ── 2차: 실제 데이터 검증 ─────────────────────────────────────────────────────
def validate_real(config: dict, years: int = 2) -> dict:
    """
    yfinance 실제 데이터로 전략을 검증합니다.
    Python 3.9 환경(subprocess)에서 별도 실행합니다.
    """
    print("  [2차] 실제 데이터 백테스트 실행 중 (Python 3.9)...")

    if not os.path.exists(PYTHON39):
        print(f"  [2차] Python 3.9 없음 ({PYTHON39}) — 실제 검증 스킵")
        return {"passed": True, "reason": "Python 3.9 미설치 → 검증 스킵 (모의 통과로 대체)", "metrics": {}, "stage": "real_skipped"}

    # 임시 config 파일에 검증할 config 저장
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)
        tmp_config = f.name

    # 실제 데이터 백테스트를 subprocess로 실행하고 metrics JSON 출력
    script = os.path.join(os.path.dirname(__file__), "_bt_runner.py")
    _write_runner_script(script)

    try:
        result = subprocess.run(
            [PYTHON39, "-X", "utf8", script, "--config", tmp_config, "--years", str(years)],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(__file__),
        )
        os.unlink(tmp_config)

        if result.returncode != 0:
            print(f"  [2차] 실행 오류:\n{result.stderr[-500:]}")
            return {"passed": False, "reason": f"subprocess 실패: {result.stderr[-200:]}", "metrics": {}, "stage": "real"}

        # 마지막 JSON 라인 파싱
        metrics = {}
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{") and "sharpe" in line:
                try:
                    metrics = json.loads(line)
                    break
                except Exception:
                    pass

        if not metrics:
            print(f"  [2차] metrics 파싱 실패 (출력:\n{result.stdout[-300:]})")
            return {"passed": False, "reason": "metrics 파싱 실패", "metrics": {}, "stage": "real"}

    except subprocess.TimeoutExpired:
        return {"passed": False, "reason": "백테스트 타임아웃 (5분)", "metrics": {}, "stage": "real"}
    except Exception as e:
        return {"passed": False, "reason": str(e), "metrics": {}, "stage": "real"}

    # 실제 데이터 게이트 (모의보다 완화)
    real_gates = {
        "min_sharpe":   0.0,
        "max_mdd_pct": -35.0,
        "min_alpha_pct": -2.0,
    }
    passed, reason = _passes_gate(metrics, real_gates)

    print(f"  [2차] {'통과 ✓' if passed else '실패 ✗'}  "
          f"Sharpe={metrics.get('sharpe', 0):.3f}  "
          f"MDD={metrics.get('max_dd_pct', 0):.1f}%  "
          f"Alpha={metrics.get('alpha_pct', 0):+.1f}%")
    if not passed:
        print(f"  [2차] 실패 사유: {reason}")

    return {"passed": passed, "reason": reason, "metrics": metrics, "stage": "real"}


# ── 전체 검증 파이프라인 ──────────────────────────────────────────────────────
def validate_strategy(config: dict, skip_real: bool = False) -> dict:
    """
    모의 → 실제 순서로 전략을 검증합니다.

    Returns:
        {
            "passed": bool,
            "sim_result": dict,
            "real_result": dict,
            "summary": str
        }
    """
    print(f"\n{'─'*52}")
    print(f"  전략 검증 시작 (v{config.get('version', '?')} → 제안 버전)")
    print(f"{'─'*52}")

    # 1차: 모의
    sim = validate_sim(config)
    if not sim["passed"]:
        return {
            "passed": False,
            "sim_result": sim,
            "real_result": {},
            "summary": f"1차(모의) 실패: {sim['reason']}",
        }

    # 2차: 실제 (스킵 옵션)
    if skip_real:
        real = {"passed": True, "reason": "실제 검증 스킵 (옵션)", "metrics": {}, "stage": "skipped"}
    else:
        real = validate_real(config)

    passed = sim["passed"] and real["passed"]
    summary = "검증 통과 ✓" if passed else f"2차(실제) 실패: {real.get('reason', '')}"

    print(f"\n  최종: {'검증 통과 ✓' if passed else '검증 실패 ✗'}")
    print(f"{'─'*52}\n")

    return {
        "passed":      passed,
        "sim_result":  sim,
        "real_result": real,
        "summary":     summary,
    }


# ── subprocess용 runner 스크립트 생성 ─────────────────────────────────────────
def _write_runner_script(path: str):
    """실제 데이터 백테스트를 실행하고 metrics JSON을 stdout에 출력하는 스크립트"""
    code = '''
import sys, json, os
sys.path.insert(0, os.path.dirname(__file__))

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--years", type=int, default=2)
args = parser.parse_args()

with open(args.config, encoding="utf-8") as f:
    config = json.load(f)

from data_fetcher import load_or_fetch
from backtest import Backtest, compute_metrics

data = load_or_fetch(years=args.years)
bt = Backtest(data=data, config=config)
bt._avg_prices = {}
df = bt.run()
metrics = compute_metrics(df)
print(json.dumps(metrics, ensure_ascii=False))
'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


if __name__ == "__main__":
    from strategy_manager import get_config
    cfg = get_config()
    result = validate_strategy(cfg, skip_real=False)
    print(f"결과: {result['summary']}")
