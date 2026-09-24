#!/usr/bin/env python3
"""问候卡片生成 - 生成问候语与当前时间。

调用方式: python scripts/greet.py --name "小明"

仅使用 Python 标准库。
"""

import argparse
import json
import os
from datetime import datetime


def greet(name: str = "世界") -> dict:
    """生成问候语。可选前缀来自 GREETING_PREFIX（未配置时用默认值）。"""
    prefix = os.environ.get("GREETING_PREFIX", "你好")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "ok": True,
        "name": name,
        "prefix": prefix,
        "message": f"{prefix}，{name}！现在是 {now}。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="问候卡片生成")
    parser.add_argument("--name", "-n", default="世界", help="要问候的名字")
    args = parser.parse_args()
    print(json.dumps(greet(args.name), ensure_ascii=False))


if __name__ == "__main__":
    main()
