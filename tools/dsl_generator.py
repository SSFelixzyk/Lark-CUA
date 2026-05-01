"""Shim — implementation moved to dsl/generator.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dsl.generator import generate, build_messages, load_ui_context, load_few_shot, next_case_id, main  # noqa

if __name__ == "__main__":
    main()
