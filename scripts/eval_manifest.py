"""题集清单命令：check 校验所有层；update <层名> 更新一个补充层的题数和指纹。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.manifest import FrozenLayerError, update_layer, validate_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    update = sub.add_parser("update")
    update.add_argument("layer")
    args = parser.parse_args()

    if args.command == "check":
        errors = validate_manifest()
        for line in errors:
            print("ERROR", line)
        print("题集清单校验通过" if not errors else f"校验失败：{len(errors)} 项")
        return 1 if errors else 0
    try:
        layer = update_layer(args.layer)
    except (KeyError, FrozenLayerError) as exc:
        print("ERROR", exc)
        return 1
    print(f"已更新 {args.layer}：{layer['count']} 题，指纹 {layer['fingerprint'][:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
