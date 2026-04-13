# 私聊对象档案记忆 V2 升级记录

本文档记录 2026-04-13 这一轮本地二开内容，方便后续继续升级、合并上游版本或补做语音/群聊扩展。

## 目标

本轮升级的目标是把私聊链路从“单一回复 Prompt 驱动”拆成四层：

1. 回复层：继续使用后台当前绑定的人格 Prompt
2. 意图层：负责输出结构化意图 JSON
3. Patch 层：负责输出最小 JSON Patch
4. 摘要层：负责压缩滚动摘要

核心思路是保留你现有的人设与语气，同时把记忆更新逻辑和回复逻辑解耦，降低后续继续扩展时的耦合度。

## 本次实际修改

本轮新增或修改的关键文件如下：

1. `private_memory_engine.py`
2. `wxbot_core.py`
3. `web_server.py`
4. `templates/dashboard.html`
5. `config/prompt_internal/private_intent_extract.md`
6. `config/prompt_internal/private_state_patch.md`
7. `config/prompt_internal/private_rolling_summary.md`

说明：

- `private_memory_engine.py` 是新增的私聊对象记忆编排层
- `wxbot_core.py` 只在私聊文本链路中接入该引擎
- `web_server.py` 与 `templates/dashboard.html` 补充了配置读写和面板入口
- 现有 `config/prompt/` 机制保留不变
- 内部 Prompt 改为固定放在 `config/prompt_internal/`，不进入普通 Prompt 选择器

## 新增配置项

本轮在配置中新增了以下字段：

```json
{
  "private_profile_memory_switch": false,
  "memory_agent_api_index": -1,
  "memory_recent_turns": 8,
  "memory_summary_budget_chars": 6000
}
```

字段含义：

- `private_profile_memory_switch`：私聊对象档案记忆 V2 总开关
- `memory_agent_api_index`：内部意图/Patch/摘要任务使用的接口索引，`-1` 表示跟随当前私聊回复接口
- `memory_recent_turns`：拼装档案上下文时带上的最近原始对话轮数
- `memory_summary_budget_chars`：历史超出该字符预算时触发滚动摘要

## 新增数据结构

新增私聊对象记忆目录：

- `memory_v2/{wxid}/private/{entity_hash}/`

其中：

- `entity_hash = sha1("private::" + chat.who)`
- 目录名不直接使用聊天对象名，避免 Windows 特殊字符和重名问题
- 原始显示名保存在 `meta.json`

每个对象目录包含：

1. `meta.json`
2. `state.json`
3. `rolling_summary.json`
4. `chat_history.jsonl`
5. `patch_log.jsonl`

`state.json` 当前固定 schema：

- `profile.facts`
- `profile.preferences.likes`
- `profile.preferences.dislikes`
- `profile.traits`
- `profile.communication_style`
- `profile.emotional_triggers`
- `profile.memory_hooks`
- `profile.open_loops`
- `relationship.turn_count`
- `relationship.stage`
- `relationship.intimacy_score`
- `relationship.last_intent`
- `relationship.last_emotional_intensity`
- `relationship.last_interaction_at`

## 运行时链路

当前私聊普通文本链路已调整为：

1. 读取 `state.json`、`rolling_summary.json`、最近 JSONL 对话
2. 调用 `private_intent_extract.md` 获取固定 JSON 意图结果
3. 若 `memory_update_needed = true`，调用 `private_state_patch.md` 输出最小 Patch
4. 对 Patch 进行白名单校验后更新 `state.json`
5. 用“现有人格 Prompt + 阶段指令 + 档案摘要 + 滚动摘要 + 最近对话 + reply_mode”组装最终回复 Prompt
6. 回复完成后把本轮用户消息和助手回复写入 `chat_history.jsonl`
7. 当历史超预算时，调用 `private_rolling_summary.md` 更新 `rolling_summary.json`

说明：

- 只升级了私聊链路
- 群聊链路本轮不变
- 现有 `memory/` 与 `MemoryManager` 没有删除，而是保留兼容
- 首次命中新链路时会尝试把旧私聊记忆懒迁移到 `memory_v2`

## Prompt 策略

你当前的“优化后提示词”没有被替换，仍然作为私聊回复的人格 Prompt 使用。

这次只是重新定义了职责边界：

- 人格 Prompt 只负责“怎么回复”
- 意图识别、档案更新、滚动摘要交给内部 Prompt

运行时补充了两条硬约束：

1. 只能引用系统提供的记忆，不得编造“你之前说过”
2. 不得输出时间戳、系统提示痕迹、调试文本

同时，显式 `Chain of Thought / 内部思考层` 不再参与实际回复链路。

## 当前阶段规则

`relationship.stage` 由程序按轮次计算，不允许模型自由改写：

- `1-15` -> `orientation`
- `16-40` -> `exploratory`
- `41-80` -> `affective`
- `81+` -> `stable`

## Patch 白名单

当前只允许写入以下路径：

- `/profile/facts/-`
- `/profile/preferences/likes/-`
- `/profile/preferences/dislikes/-`
- `/profile/traits/-`
- `/profile/communication_style/-`
- `/profile/emotional_triggers/-`
- `/profile/memory_hooks/-`
- `/profile/open_loops/-`
- `/relationship/intimacy_score`
- `/relationship/last_intent`
- `/relationship/last_emotional_intensity`

限制规则：

- 只允许 `add`、`replace`、`remove`
- 非白名单路径直接拒绝
- Patch 非法时只跳过档案更新，不阻断本轮正常回复

## Web 面板改动

本轮已补充以下面板能力：

- 私聊对象档案记忆开关
- 内部记忆任务接口索引
- 最近对话轮数
- 摘要预算字符数

同时，删除记忆与自动备份逻辑已经兼容：

- `memory/`
- `memory_v2/`

## 已完成验证

本轮已做的验证包括：

1. `python -m py_compile wxbot_core.py web_server.py private_memory_engine.py`
2. `PrivateMemoryEngine.prepare()` / `finalize()` 最小烟测
3. 确认 `memory_v2` 可真实生成目录与基础文件
4. 确认 Patch 可应用，`turn_count` 可累计

## 本轮明确暂缓

本轮没有继续做以下内容：

1. 群聊对象建档
2. 记忆查看页的“档案 / 摘要”可视化页签
3. 更复杂的 tokenizer 级预算估算
4. 语音单独模型路由

关于语音，当前状态是：

- 程序里已经存在 `msg.to_text()` 的语音转文字处理
- 也就是说，对方发语音时，现有链路会先尽量转成文字，再继续走主模型处理
- 本轮没有新增“语音识别模型设置”

如果后续要补语音模型路由，建议沿用图片识别的配置模式，新增：

- 语音转写开关
- 语音转写接口索引
- 明确“内置优先”还是“模型优先”的回退策略

## 后续升级时优先检查的位置

如果下次继续从上游同步或追加本地二开，优先检查这些位置：

1. `wxbot_core.py` 私聊 `process_message()` 文本处理链路
2. `wxbot_core.py` 语音 `msg.to_text()` 处理位置
3. `web_server.py` 配置保存、记忆删除、自动备份逻辑
4. `templates/dashboard.html` 记忆设置区域
5. `config/prompt_internal/` 的内部 Prompt 是否仍被正确加载
6. `private_memory_engine.py` 的接口与主流程是否仍匹配

## 后续建议的扩展顺序

如果下一轮继续做“语音转写模型设置”或“群聊对象记忆”，建议按这个顺序继续：

1. 先补配置字段和面板入口
2. 再补独立路由层，不要把逻辑直接写死在 `process_message()`
3. 保持失败回退，不要因为转写或记忆任务失败而阻断正常回复
4. 最后再补可视化调试页或更细的管理能力
