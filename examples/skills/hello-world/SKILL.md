---
name: 问候卡片生成
slug: hello-world
version: 0.1.0
description: 演示技能包格式与调用链路：按名字生成一句问候并附上当前时间，用于验证技能装载、参数传递与凭证注入
credentials:
  - name: greeting_prefix
    env: GREETING_PREFIX
    description: 可选：问候语前缀，默认"你好"
    required: false
---

# 问候卡片生成

## 任务目标

- 输入一个名字，输出一句问候语 + 当前时间（JSON），用于验证技能包格式与执行链路
- 触发场景：新装技能后的连通性自检、技能开发调试

## 能力组成

单脚本 `scripts/greet.py`，无外部依赖、无网络调用。

## 前置准备

- 无需凭证
- 可选：配置环境变量 `GREETING_PREFIX` 自定义问候前缀（默认"你好"）

## 操作步骤

1. 确认参数：`--name`（名字，默认"世界"）
2. 执行脚本：

   ```bash
   python scripts/greet.py --name "小明"
   ```

3. 解析脚本 stdout 的 JSON，把 `message` 字段回给用户

## 使用示例

### 示例1：默认问候

- 输入：生成一句问候
- 调用：`run_skill_script(skill="hello-world", script="greet.py", params={})`
- 产出：`{"message": "你好，世界！现在是 2026-09-24 15:20:31。"}`

## 资源索引

- [scripts/greet.py](scripts/greet.py)：生成问候语与时间

## 响应数据结构

```json
{
  "ok": true,
  "name": "小明",
  "prefix": "你好",
  "message": "你好，小明！现在是 2026-09-24 15:20:31。"
}
```

## 注意事项

1. `--name` 可不填，默认"世界"。
2. 脚本只依赖 Python 标准库，输出为单行 JSON。
