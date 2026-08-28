# vision_solver — 通用视觉验证码求解器

用多模型视觉大模型「投票」求解图形验证码，通用 driver 据答案驱动浏览器。
通过 `CaptchaSpec`（prompt + 选择器 + 交互模式）描述一类验证码，不写死任何平台。

> 由 `common/agent_captcha.py`（GitHub Arkose 专用）的内核**复制+泛化**而来，
> 独立成库、独立演进，**不影响**现有 GitHub 注册流程。

## 能力

- **四种交互范式**
  - `grid_select` 多选网格：reCAPTCHA / 旧 hCaptcha（DOM 里有可点 tile）「选出所有含 X 的格子」→ 点多个 tile + Verify
  - `single_pick` 单选导航：Arkose 排序拼图「匹配参考图」→ 导航到第 best 张 + Submit
  - `canvas_grid` 整块画布选择：新版 hCaptcha（挑战画进一个 `<canvas>`，无 DOM tile）→ 截画布 + 按行列编号 + 投票 + 按**像素坐标点 canvas** + Submit。题干含 "the item/thing"(单数) 或 "card/different" 自动判**单选**(只点 1 格)，"select all" 判多选
  - `canvas_drag` 整块画布拖拽：把 piece 拖到 target → 截画布 + 投票 **FROM/TO 归一化坐标**(各模型取中位数) + **mouse down/move/up 分步拖放**(带抖动模拟人手) + Submit。预置 `presets/hcaptcha_drag.json`
- **多模型投票**：gemini / gpt / claude 并发，多数表决互相纠错（`vote_picklist` 多选 / `vote_answer` 单选 / `vote_points` 拖拽两点）
- **多网关/多协议**：OpenAI 兼容 + Anthropic 原生，key 轮换、模型降级、拒答检测
- **图像增强**：截元素放大、拼网格带编号、本地锐化、复盘标注（REVIEW_*.png）
- **画布稳定截图**：`canvas_grid` 等渲染稳定（连续帧一致）再截，避免截到渐入/加载中的半成品

## 用法

```python
from vision_solver import solve, CaptchaSpec

# 1) 用预置
spec = CaptchaSpec.from_json("vision_solver/presets/recaptcha_v2.json")
ok = await solve(page, spec)          # page = Playwright Page，验证码已出现

# 2) 现写一个 spec（dict）
ok = await solve(page, {
    "name": "my_captcha",
    "frame_match": ["challenges.example.com"],   # 哪个 iframe 承载挑战
    "mode": "grid_select",
    "challenge_text_sel": ".prompt",              # 读题干 "select all X"
    "tile_sel": ".tile",                          # 每个可点格子
    "submit_sel": ".verify-btn",
    "cols": 3,
})
```

## CaptchaSpec 字段

| 字段 | 说明 |
|---|---|
| `frame_match` | iframe url 关键字列表，命中即为挑战框；空=主页面 |
| `mode` | `grid_select`(DOM多选) / `single_pick`(单选导航) / `canvas_grid`(画布选择) / `canvas_drag`(画布拖拽) |
| `prompt` | 给模型的题目模板；`{instruction}` 替换页面题干、`{last}`=候选数-1。空则用内置模板 |
| `challenge_text_sel` | 题干元素选择器（读 "select all X"） |
| `tile_sel` | grid_select：每个可点格子（按 DOM 顺序 = #0,#1...） |
| `grid_image_sel` | grid_select：整块挑战图（与逐 tile 截图二选一） |
| `submit_sel` | grid_select / canvas_grid：Verify/确认按钮 |
| `ref_sel`/`cand_sel` | single_pick：参考图/候选图 |
| `next_btn_role`/`prev_btn_role`/`submit_role` | single_pick：导航/提交按钮的 aria role name |
| `canvas_sel` | canvas_grid：挑战画布元素（默认 `canvas`） |
| `rows` | canvas_grid：网格行数（题干判单选卡片时按 1×3） |
| `grid_top/bottom/left/right_frac` | canvas_grid：网格区在画布内的四边内缩比例（扣掉题干条/边距，保证点中心准；实测 hCaptcha 500×470≈ 0.30/0.036/0.164/0.164） |
| `answer_format` | `PICK_LIST`(多选) / `ANSWER_INDEX`(单选) |
| `cols`/`max_rounds`/`deadline`/`settle_ms` | 网格列数/最多轮数/单轮投票秒/操作间隔ms |
| `success_gone_frame` | 挑战 frame 消失=通过 |
| `success_markers` | 主页面模式：出现这些文本=通过 |

## 配置（.env，复用现有视觉网关）

`VISION_API_BASE` / `VISION_API_KEY` / `VISION_MODEL`、`VOTE_ZZ_BASE/KEY`、
`VOTE_GPT_KEY`、`VOTE_OPUS_BASE/KEY`、`GEMMA_API_BASE/KEY`。投票池缺网关/key 的模型自动剔除；
全缺时退化为单模型 `ask_vision`。

## 调试

每轮存 `screenshots_vision/REVIEW_r*.png`：标注最终选择(粗红框)+各模型投票。求解不准时看它定位是模型选错还是选择器/prompt 不对。
