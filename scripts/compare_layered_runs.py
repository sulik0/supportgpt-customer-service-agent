"""对比两次分层运行：python scripts/compare_layered_runs.py 旧结果.json 新结果.json"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.layered_compare import compare_runs, load_result, render_comparison


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    diff = compare_runs(load_result(Path(sys.argv[1])), load_result(Path(sys.argv[2])))
    print(render_comparison(diff))
    return 1 if diff["regressed"] else 0


if __name__ == "__main__":
    sys.exit(main())
