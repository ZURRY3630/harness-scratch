---
name: 文本统计分析
slug: text-stats
version: 0.1.0
description: 统计一段文本的字数、汉字数、英文词数、句子数、段落数与预计阅读时长，输出 JSON；用于内容质检与篇幅评估。
credentials:
  - name: 阅读语速
    env: READING_WPM
    description: 可选，每分钟阅读字数（汉字当量），缺省 300
    required: false
---

# 文本统计分析

## 任务目标

- 输入一段文本，输出其篇幅与构成统计（JSON），用于内容质检、篇幅评估与阅读时长预估。
- 触发场景：写完一段文案/文档后快速核对长度，或在交付前做内容密度检查。

## 能力组成

单脚本 `scripts/stats.py`，仅依赖 Python 标准库，不读外部文件、不访问网络。

## 前置准备

- 无需凭证。
- 可选：配置环境变量 `READING_WPM`（每分钟阅读字数，缺省 300）自定义阅读语速。

## 操作步骤

1. 确认参数：`--text`（待统计文本）。
2. 执行脚本：

   ```bash
   python scripts/stats.py --text "今天天气不错。我们出去走走吧！"
   ```

3. 解析脚本 stdout 的 JSON，把各计数字段回报给用户。

## 使用示例

### 示例 1：统计一段中文

- 输入：帮我统计一下这段话的篇幅。
- 调用：`run_skill_script(skill="text-stats", script="stats.py", params={"text": "今天天气不错。我们出去走走吧！"})`
- 产出：

```json
{
  "ok": true,
  "chars_no_space": 16,
  "han_characters": 16,
  "latin_words": 0,
  "numbers": 0,
  "sentences": 2,
  "paragraphs": 1,
  "reading_units": 16.0,
  "reading_minutes": 0.05,
  "wpm": 300
}
```

## 资源索引

- [scripts/stats.py](scripts/stats.py)：篇幅与构成统计逻辑。

## 响应数据结构

```json
{
  "ok": true,
  "chars_no_space": "去除空白后的总字符数",
  "han_characters": "汉字个数",
  "latin_words": "英文单词个数",
  "numbers": "数字串个数",
  "sentences": "句子数（按句末标点切分）",
  "paragraphs": "段落数（按空行切分）",
  "reading_units": "阅读当量（汉字 + 英文词×1.5）",
  "reading_minutes": "预计阅读分钟数",
  "wpm": "本次使用的阅读语速"
}
```

## 注意事项

1. `--text` 不传时统计空串，全部计数为 0。
2. 脚本只依赖 Python 标准库，stdout 为单行 JSON。
3. 阅读时长为粗估，英文词按 1.5 汉字当量折算。
