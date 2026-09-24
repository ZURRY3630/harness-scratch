# 16 - 技能系统（安装 / 创建 / 调用）

## 何时需要

你想让 Agent 用上"打包好的外部能力"——比如别人发布的服务技能包，或你自己写的多步工作流脚本——而不希望把它们逐个改写成 `@tool` 函数时，用技能系统。

技能包与工具的区别：**工具是一个函数**（内核直接调），**技能是一份文档 + 若干 CLI 脚本**（模型读文档、按文档调脚本）。

## 接口约定

### 1. 技能包格式

```
<slug>/                       目录名即技能标识（支持中文）
├── SKILL.md                  必需
├── _meta.json                可选：{"ownerId","slug","version","publishedAt"}
└── scripts/*.py              可选：argparse CLI，stdout 输出 JSON
```

`SKILL.md` 的 front-matter：

```yaml
---
name: 城市天气宣传画报生成          # 必需
description: 根据城市生成天气海报     # 必需（模型据此判断相关性）
slug: city-weather-poster         # 可选；未写则用 _meta.json.slug / 目录名 / name 兜底
dependency: {python: [requests]}  # 可选，仅作提示，不做硬校验
credentials:                      # 可选，声明需要注入脚本的凭证
  - name: showapi_appkey
    env: SHOWAPI_APP_KEY          # 变量名；值从仓库根 .env 读取
    description: ShowAPI 密钥
    required: true                # false = 可选配置，缺失不阻塞执行
---
# 正文：任务目标 / 操作步骤 / 资源索引 / 响应结构 ...
```

未在 front-matter 声明的环境变量，系统会**从脚本源码里探测**：`os.environ.get("X")` 视为必需、
`os.environ.get("X", 默认值)` 视为可选。注入只发生在下列范围内：

| 规则 | 说明 |
|---|---|
| 注入什么 | 声明的 `credentials[].env` ∪ 脚本里探测到的变量 |
| 宿主环境 | **不继承**。只保留 PATH / SYSTEMROOT 等启动 Python 必需项 |
| 永不注入 | `LLM_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等宿主模型密钥；技能要求这些凭证会被直接拒绝 |
| 缺失必需凭证 | 拒绝执行并给出可行动提示（技能脚本常用 `getpass` 兜底，服务端会阻塞，所以提前拒绝） |

### 2. 技能工具（三个，需在 `tools.builtin` 里声明才注册）

`list_skills()`（`full_trust`，列出已启用技能）· `read_skill(skill)`（`full_trust`，读 SKILL.md 全文）·
`run_skill_script(skill, script, params)`（**`ask_first`** 逐次审批，执行 `scripts/<script>`；
`params` 是键值对，`{"name": "小明"}` → `--name=小明`）。

这是**渐进披露**：系统提示词里只放几行索引，正文与执行按需触发，因此装几十个技能也不会撑爆上下文。

### 3. 启用与生效

技能的"生效"由两层决定，**运行时覆盖优先于配置**（配置白名单 = 项目 `config.yaml` 的 `skills.enabled`，
改后需重启进程；运行时覆盖 = 界面「技能」页签勾选或 `PUT /api/skills/{slug}/enabled`，**立即生效**，
因为会清空引擎缓存，下个请求即用新索引）。

`GET /api/skills` 返回的 `enabled` 是最终结果，`enabled_source` 说明它来自 `config` 还是 `override`（空 = 未启用）。
`DELETE /api/skills/{slug}/enabled` 清除覆盖、回到配置默认。**卸载会一并清掉覆盖**，不留孤儿状态。

### 4. 配置与 CLI

```yaml
skills:
  dir: null                 # 安装根目录；null -> <仓库根>/data/skills
  enabled: [hello-world]    # 启用白名单；默认空 = 已安装但对模型不可见
  timeout: null             # 单脚本超时秒数；null -> EXECUTION_TIMEOUT_SECONDS
  max_output_bytes: 20000   # 输出截断上限
```

启用技能还要在 `tools.builtin` 里加上三个技能工具名（未加时启动日志会给出提示）：

```yaml
tools:
  builtin: [get_time, list_skills, read_skill, run_skill_script]
```

```bash
python -m mini_harness.skills install examples/skills/hello-world   # 目录（自己创建）
python -m mini_harness.skills install dist/pkg-1.0.0.zip            # 本地 zip
python -m mini_harness.skills install --url https://host/pkg.zip    # 网上直链
python -m mini_harness.skills list                                  # 已安装 + 是否启用
python -m mini_harness.skills remove <slug>                         # 卸载
```

HTTP 侧：`GET /api/skills`、`GET /api/skills/{slug}`、`POST /api/skills/install`（请求体直接是 zip 字节）、
`POST /api/skills/install-url`（`{"url": "..."}`）、`POST /api/skills/create`（表单创建）、
`PUT /api/skills/{slug}/enabled`（`{"enabled": true}`）、`DELETE /api/skills/{slug}/enabled`、`DELETE /api/skills/{slug}`。

**前端**：侧边栏「技能」页签 = 列表（名称/版本/描述/脚本/缺凭证/状态来源）+ 上传 zip + URL 安装 + ＋新建 + 启停开关 + 卸载。

**安装即引入可执行代码**，因此安装时会强制校验：拒绝绝对路径 / `..` / 符号链接、只允许白名单后缀、
限制单文件 5MB 与整包 20MB、zip 魔数校验；URL 安装只接受 http/https。

## 完整示例

从零做一个"周报助手"技能（自己创建的完整流程）：

```bash
mkdir -p examples/skills/weekly-report/scripts
```

`examples/skills/weekly-report/SKILL.md`：

```markdown
---
name: 周报助手
slug: weekly-report
description: 把一周的流水账整理成结构化周报（本周完成 / 进行中 / 风险 / 下周计划）
credentials:
  - name: report_team
    env: REPORT_TEAM
    description: 可选：团队名，会写进周报标题
    required: false
---
# 周报助手

## 操作步骤
1. 收集用户提供的本周事项（每行一条）
2. 执行 `python scripts/build_report.py --raw "<事项，用;分隔>"`
3. 把脚本输出的 JSON 里 `sections` 字段整理给用户
```

`examples/skills/weekly-report/scripts/build_report.py`：

```python
#!/usr/bin/env python3
"""把流水账归类成周报结构。调用: python scripts/build_report.py --raw "a;b" """

import argparse
import json
import os

_KEYWORDS = {
    "本周完成": ("完成", "上线", "修复", "交付"),
    "进行中": ("进行", "开发", "推进"),
    "风险": ("阻塞", "风险", "延期"),
}


def build_report(raw: str) -> dict:
    items = [x.strip() for x in raw.split(";") if x.strip()]
    sections = {name: [] for name in _KEYWORDS}
    sections["下周计划"] = []
    for item in items:
        for name, words in _KEYWORDS.items():
            if any(w in item for w in words):
                sections[name].append(item)
                break
        else:
            sections["下周计划"].append(item)
    return {"ok": True, "team": os.environ.get("REPORT_TEAM", "本团队"), "sections": sections}


def main() -> None:
    parser = argparse.ArgumentParser(description="周报助手")
    parser.add_argument("--raw", required=True, help="本周事项，用 ; 分隔")
    args = parser.parse_args()
    print(json.dumps(build_report(args.raw), ensure_ascii=False))


if __name__ == "__main__":
    main()
```

安装并启用（两种方式，二选一）：

```bash
python -m mini_harness.skills install examples/skills/weekly-report   # 或界面「技能」页签 → 上传 zip
```

```yaml
# projects/<你的项目>/config.yaml
tools:
  builtin: [list_skills, read_skill, run_skill_script]
skills:
  enabled: [weekly-report]      # 也可以不写，改用界面勾选启用（立即生效）
```

验证：重启后端后，让模型"用周报助手整理：修复了登录 bug;开发报表导出;接口联调被阻塞"
→ 依次出现 `list_skills` / `read_skill` / `approval_required`（run_skill_script）→ 批准后返回分类结果。

## 接入步骤

1. **安装**：界面「技能」页签上传 zip / 填 URL 安装，或 `python -m mini_harness.skills install <目录|zip|--url https://...>`。
2. **配置凭证**：列表里的「缺凭证」提示要配的环境变量；写进仓库根 `.env`（例：`SHOWAPI_APP_KEY=xxx`）。
3. **启用**：界面直接勾选（立即生效）；或写进项目 `config.yaml` 的 `skills.enabled`（重启生效）。
4. **注册技能工具**：把 `list_skills / read_skill / run_skill_script` 加进 `tools.builtin`（未加时启动日志会 WARNING 提示）。
5. **调权限**（可选）：`run_skill_script` 默认 `ask_first`；已充分信任的技能可用 `PUT /api/tools/run_skill_script/permission` 放开。

## 常见错误

- **模型说"没有这个技能"** → 三种原因：slug 拼错；未启用（界面未勾选且不在 `skills.enabled`）；未把技能工具加进 `tools.builtin`（启动日志会有 WARNING 提示）。
- **`[MISSING_CREDENTIAL]`** → 技能需要的环境变量没配。按提示把变量名写进仓库根 `.env` 后**重启进程**（环境变量在启动时载入）。
- **`[FORBIDDEN_CREDENTIAL]`** → 技能脚本试图读取 `LLM_API_KEY` 之类的宿主密钥。这是策略性拒绝：模型密钥不会下发给技能进程，请让技能使用自有凭证。
- **`[TIMEOUT]`** → 脚本执行超过 `skills.timeout`（默认取 `EXECUTION_TIMEOUT_SECONDS`）。长任务请让技能自己分两步（提交任务 + 轮询查询）。
