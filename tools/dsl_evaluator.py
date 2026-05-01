"""Shim — implementation moved to dsl/evaluator.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dsl.evaluator import evaluate, save_result, VERDICT_LABEL, DIM_ORDER, main  # noqa

if __name__ == "__main__":
    main()
