#!/usr/bin/env python3
"""文本统计分析 - 统计一段文本的篇幅与构成，输出 JSON。

调用方式: python scripts/stats.py --text "要统计的文本"

仅使用 Python 标准库；不读外部文件、不访问网络。
"""

import argparse
import json
import os
import re

_HAN_RE = re.compile(r"[一-鿿]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")
_NUMBER_RE = re.compile(r"\d+")
_SENTENCE_END_RE = re.compile(r"[。！？!?]+")
_BLANK_RE = re.compile(r"\s")


def analyze(text: str, wpm: int) -> dict:
    """统计文本篇幅与构成。

    Args:
        text: 待统计文本。
        wpm: 每分钟阅读字数（仅计汉字当量），用于估算阅读时长。
    """
    text = text or ""
    han_characters = len(_HAN_RE.findall(text))
    latin_words = _LATIN_WORD_RE.findall(text)
    numbers = _NUMBER_RE.findall(text)
    sentences = [s for s in _SENTENCE_END_RE.split(text) if s.strip()]
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chars_no_space = len(_BLANK_RE.sub("", text))

    # 阅读当量：1 个英文词约折合 1.5 个汉字，粗算阅读分钟数
    units = han_characters + len(latin_words) * 1.5
    reading_minutes = round(units / wpm, 2) if wpm > 0 else 0.0

    return {
        "ok": True,
        "chars_no_space": chars_no_space,
        "han_characters": han_characters,
        "latin_words": len(latin_words),
        "numbers": len(numbers),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "reading_units": round(units, 1),
        "reading_minutes": reading_minutes,
        "wpm": wpm,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="文本统计分析")
    parser.add_argument("--text", "-t", default="", help="待统计的文本")
    parser.add_argument("--wpm", type=int, default=None,
                        help="每分钟阅读字数；默认读环境变量 READING_WPM，缺省 300")
    args = parser.parse_args()

    wpm = args.wpm
    if wpm is None:
        env_wpm = os.environ.get("READING_WPM", "").strip()
        wpm = int(env_wpm) if env_wpm.isdigit() else 300

    print(json.dumps(analyze(args.text, wpm), ensure_ascii=False))


if __name__ == "__main__":
    main()
