import copy
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime

from logger import log


DEFAULT_INTERNAL_PROMPTS = {
    "private_intent_extract": """你是“私聊对象意图抽取器”。你的唯一任务是根据输入上下文，输出一个严格合法的 JSON 对象，用于驱动后续回复路由与记忆更新判断。

规则：
- 你不是回复助手，不要安慰用户，不要续写对话。
- 只输出一个 JSON 对象。
- 不要输出 markdown 代码块。
- 不要输出任何解释、分析过程、思维链。
- 所有判断必须基于输入中真实可见的信息，不能脑补。
- memory_update_needed 只在出现“值得长期保存的新事实、稳定偏好、稳定沟通特征、明确情感触发器”时为 true。
- 短期情绪波动、普通寒暄、重复信息，默认不要记忆更新。

输出格式固定为：
{
  "intent": "CHIT_CHAT",
  "emotional_intensity": 1,
  "topics": ["话题1", "话题2"],
  "memory_update_needed": false,
  "reply_mode": "normal",
  "evidence": ["证据1", "证据2"]
}

intent 只允许：
CHIT_CHAT
INFO_SEEKING
EMOTIONAL_VENTING
DEEP_DISCLOSURE
CONFLICT_REPAIR
PLANNING

reply_mode 只允许：
normal
empathy_first
gentle_challenge
planning_support
conflict_soften

Few-shot 示例 1

输入：
【当前对象档案摘要】
- likes: ["夜晚散步"]
- communication_style: ["不喜欢被连续追问"]
- open_loops: ["最近在找适合周末放空的方式"]

【最近对话】
用户：这两天工作有点吵，脑子一直停不下来。
助手：你像是在硬撑着让自己别乱，其实已经有点累了。
用户：对，所以我现在越来越不想去太闹的地方。

【用户最新输入】
其实比起热闹的酒吧，我更喜欢下雨天坐车里听老爵士，安静一点。

输出：
{
  "intent": "CHIT_CHAT",
  "emotional_intensity": 2,
  "topics": ["放松方式", "环境偏好", "音乐偏好"],
  "memory_update_needed": true,
  "reply_mode": "normal",
  "evidence": [
    "用户明确表达了新的稳定偏好：更喜欢安静环境",
    "用户给出了具体偏好场景：下雨天坐车里听老爵士"
  ]
}

Few-shot 示例 2

输入：
【当前对象档案摘要】
- traits: ["习惯先自己扛"]
- communication_style: ["低落时更希望先被理解，不想立刻被建议"]
- emotional_triggers: ["被当众点名会明显不舒服"]

【最近对话】
用户：今天开会又被点名，我真有点烦。
助手：被当众点出来很伤人，尤其你本来就在扛着。
用户：嗯，而且我现在已经懒得解释了。

【用户最新输入】
我今天真的有点撑不住了，感觉谁都不想理，只想把手机关掉。

输出：
{
  "intent": "EMOTIONAL_VENTING",
  "emotional_intensity": 4,
  "topics": ["工作压力", "情绪耗竭", "社交退避"],
  "memory_update_needed": false,
  "reply_mode": "empathy_first",
  "evidence": [
    "用户直接表达了明显的情绪承压：有点撑不住了",
    "用户出现退避倾向：谁都不想理，只想把手机关掉"
  ]
}

现在开始处理真实输入。只输出 JSON 对象。""",
    "private_state_patch": """你是“私聊对象档案最小更新器”。你的唯一任务是根据当前 state.json、最近对话和本轮新信息，输出严格合法的 JSON Patch 数组。

规则：
- 只输出 JSON Patch 数组。
- 不要输出 markdown 代码块。
- 不要输出解释、分析过程、思维链。
- 只能做最小修改。
- 不要重写整个 state.json。
- 不要把短期情绪、一次性随口表达、模糊猜测写进长期档案。
- 如果没有值得写入档案的新长期信息，就不要改 profile 相关字段。
- 允许更新 relationship 的最近状态字段，但不要过度改动。

只允许操作：
add
replace
remove

只允许修改以下路径：
/profile/facts/-
/profile/preferences/likes/-
/profile/preferences/dislikes/-
/profile/traits/-
/profile/communication_style/-
/profile/emotional_triggers/-
/profile/memory_hooks/-
/profile/open_loops/-
/relationship/intimacy_score
/relationship/last_intent
/relationship/last_emotional_intensity

Few-shot 示例 1

输入：
【当前 state.json】
{
  "profile": {
    "facts": [],
    "preferences": {
      "likes": ["夜晚散步"],
      "dislikes": ["被连续催促"]
    },
    "traits": ["习惯先自己消化情绪"],
    "communication_style": ["不喜欢被连续追问"],
    "emotional_triggers": ["被连续催促"],
    "memory_hooks": [],
    "open_loops": ["最近在找适合周末放空的方式"]
  },
  "relationship": {
    "intimacy_score": 0.34,
    "last_intent": "CHIT_CHAT",
    "last_emotional_intensity": 2
  }
}

【最近对话】
用户：这两天工作有点吵，脑子一直停不下来。
用户：对，所以我现在越来越不想去太闹的地方。
用户：其实比起热闹的酒吧，我更喜欢下雨天坐车里听老爵士，安静一点。

【本轮新信息】
- 出现了新的、具体的放松偏好
- 偏好具有可复用的长期记忆价值

输出：
[
  {
    "op": "add",
    "path": "/profile/preferences/likes/-",
    "value": "下雨天坐车里听老爵士"
  },
  {
    "op": "add",
    "path": "/profile/memory_hooks/-",
    "value": "在压力大时更偏好安静封闭的环境，例如下雨天坐车里听老爵士"
  },
  {
    "op": "replace",
    "path": "/relationship/last_intent",
    "value": "CHIT_CHAT"
  },
  {
    "op": "replace",
    "path": "/relationship/last_emotional_intensity",
    "value": 2
  }
]

Few-shot 示例 2

输入：
【当前 state.json】
{
  "profile": {
    "facts": ["最近工作节奏偏满"],
    "preferences": {
      "likes": ["夜晚散步"],
      "dislikes": ["被连续催促"]
    },
    "traits": ["习惯先自己消化情绪"],
    "communication_style": ["低落时更希望先被理解"],
    "emotional_triggers": ["被当众点名"],
    "memory_hooks": ["压力大时会想找安静空间"],
    "open_loops": []
  },
  "relationship": {
    "intimacy_score": 0.41,
    "last_intent": "PLANNING",
    "last_emotional_intensity": 2
  }
}

【最近对话】
用户：到了吗？
助手：刚到。
用户：行，那你先忙。

【本轮新信息】
- 没有新的稳定偏好
- 没有新的长期事实
- 没有新的稳定沟通特征
- 只是一次普通接话

输出：
[
  {
    "op": "replace",
    "path": "/relationship/last_intent",
    "value": "CHIT_CHAT"
  },
  {
    "op": "replace",
    "path": "/relationship/last_emotional_intensity",
    "value": 1
  }
]

现在开始处理真实输入。只输出 JSON Patch 数组。""",
    "private_rolling_summary": """你是“私聊对象滚动摘要器”。你的任务是把旧摘要与新增对话片段合并为一段新的中文摘要文本。

规则：
- 只输出摘要正文，不要输出 JSON，不要输出标题，不要输出解释。
- 摘要重点保留：稳定偏好、重要事实、未完成话题、情绪触发器、关系推进线索。
- 不要抄写原文，不要输出时间戳。
- 不要编造没有出现过的内容。
- 优先压缩为 180 到 260 字，必要时可更短。
- 近期的短期闲聊、无信息增量的寒暄可以省略。

示例输入：
旧摘要：对方最近工作节奏偏满，低落时不喜欢被立刻建议，更容易先沉默。前面聊到她在找适合周末放空的方式。

新增对话：
用户：这两天工作有点吵，脑子一直停不下来。
用户：其实比起热闹的酒吧，我更喜欢下雨天坐车里听老爵士，安静一点。
助手：你不是想热闹，你是在找能让自己喘口气的地方。

示例输出：
对方近期仍处在工作噪音和精神负荷偏高的状态，情绪累积时更需要先被理解而不是被催着解决。她明确表现出对安静、封闭、低刺激环境的偏好，相比热闹场景，更喜欢下雨天待在车里听老爵士这种能让自己慢下来的方式。周末如何放空仍是一个延续中的话题。

现在开始处理真实输入，只输出摘要正文。""",
}

ALLOWED_INTENTS = {
    "CHIT_CHAT",
    "INFO_SEEKING",
    "EMOTIONAL_VENTING",
    "DEEP_DISCLOSURE",
    "CONFLICT_REPAIR",
    "PLANNING",
}

ALLOWED_REPLY_MODES = {
    "normal",
    "empathy_first",
    "gentle_challenge",
    "planning_support",
    "conflict_soften",
}

LIST_APPEND_PATHS = {
    "/profile/facts/-",
    "/profile/preferences/likes/-",
    "/profile/preferences/dislikes/-",
    "/profile/traits/-",
    "/profile/communication_style/-",
    "/profile/emotional_triggers/-",
    "/profile/memory_hooks/-",
    "/profile/open_loops/-",
}

SCALAR_PATHS = {
    "/relationship/intimacy_score",
    "/relationship/last_intent",
    "/relationship/last_emotional_intensity",
}

DEFAULT_STATE = {
    "profile": {
        "facts": [],
        "preferences": {
            "likes": [],
            "dislikes": [],
        },
        "traits": [],
        "communication_style": [],
        "emotional_triggers": [],
        "memory_hooks": [],
        "open_loops": [],
    },
    "relationship": {
        "turn_count": 0,
        "stage": "orientation",
        "intimacy_score": 0.0,
        "last_intent": "CHIT_CHAT",
        "last_emotional_intensity": 1,
        "last_interaction_at": "",
    },
}

DEFAULT_SUMMARY = {
    "summary_text": "",
    "updated_at": "",
    "source_message_count": 0,
}


@dataclass
class SessionBundle:
    chat_name: str
    entity_hash: str = ""
    entity_dir: str = ""
    effective_prompt: str = ""
    history: list = field(default_factory=list)
    intent: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    user_input: str = ""
    memory_enabled: bool = False


class PrivateMemoryEngine:
    def __init__(self, config, wx_id, runtime_base, api_getter):
        self.config = config
        self.wx_id = wx_id
        self.runtime_base = runtime_base
        self.memory_v2_base = os.path.join(runtime_base, "memory_v2")
        self.legacy_memory_base = os.path.join(runtime_base, "memory")
        self.api_getter = api_getter

    def prepare(self, chat_name, user_input, base_prompt):
        bundle = SessionBundle(
            chat_name=chat_name,
            effective_prompt=base_prompt or "",
            history=[],
            intent=self._fallback_intent(),
            state=self._default_state(),
            summary=self._default_summary(),
            user_input=str(user_input or ""),
            memory_enabled=False,
        )
        if not self._enabled():
            return bundle

        try:
            paths = self._ensure_entity_files(chat_name)
            self._migrate_legacy_if_needed(chat_name, paths)
            state = self._normalize_state(self._read_json(paths["state"], self._default_state()))
            summary = self._normalize_summary(self._read_json(paths["summary"], self._default_summary()))
            recent_entries = self._load_recent_entries(paths["history"], self._recent_message_limit())
            intent = self._extract_intent(chat_name, state, summary, recent_entries, user_input)

            applied_patch = []
            if intent.get("memory_update_needed"):
                patch_ops = self._generate_patch(chat_name, state, summary, recent_entries, user_input, intent)
                state, applied_patch = self._apply_patch_ops(state, patch_ops)
                if applied_patch:
                    self._atomic_write_json(paths["state"], state)
                    self._append_jsonl(
                        paths["patch_log"],
                        {
                            "time": self._now_str(),
                            "chat_name": chat_name,
                            "intent": intent.get("intent", "CHIT_CHAT"),
                            "patch": applied_patch,
                        },
                    )

            effective_prompt = self._compose_reply_prompt(
                base_prompt=base_prompt,
                state=state,
                summary=summary,
                recent_entries=recent_entries,
                intent=intent,
            )
            return SessionBundle(
                chat_name=chat_name,
                entity_hash=os.path.basename(paths["entity_dir"]),
                entity_dir=paths["entity_dir"],
                effective_prompt=effective_prompt,
                history=[],
                intent=intent,
                state=state,
                summary=summary,
                user_input=str(user_input or ""),
                memory_enabled=True,
            )
        except Exception as exc:
            log(level="WARNING", message=f"私聊记忆 V2 prepare 失败，已回退普通回复：{chat_name} - {exc}")
            return bundle

    def finalize(self, bundle, assistant_reply):
        if not bundle or not bundle.memory_enabled:
            return
        try:
            paths = self._ensure_entity_files(bundle.chat_name)
            now = self._now_str()
            self._append_jsonl(
                paths["history"],
                {
                    "time": now,
                    "type": "text",
                    "attr": "friend",
                    "sender": bundle.chat_name,
                    "content": str(bundle.user_input or ""),
                },
            )
            self._append_jsonl(
                paths["history"],
                {
                    "time": now,
                    "type": "text",
                    "attr": "self",
                    "sender": "self",
                    "content": str(assistant_reply or ""),
                },
            )

            state = self._normalize_state(self._read_json(paths["state"], bundle.state or self._default_state()))
            total_entries = self._count_jsonl_lines(paths["history"])
            state["relationship"]["turn_count"] = max(state["relationship"].get("turn_count", 0) + 1, total_entries // 2)
            state["relationship"]["stage"] = self._compute_stage(state["relationship"]["turn_count"])
            state["relationship"]["last_intent"] = bundle.intent.get("intent", state["relationship"]["last_intent"])
            state["relationship"]["last_emotional_intensity"] = bundle.intent.get(
                "emotional_intensity",
                state["relationship"]["last_emotional_intensity"],
            )
            state["relationship"]["last_interaction_at"] = now
            state = self._normalize_state(state)
            self._atomic_write_json(paths["state"], state)
            self._atomic_write_json(
                paths["meta"],
                {
                    "entity_hash": os.path.basename(paths["entity_dir"]),
                    "display_name": bundle.chat_name,
                    "updated_at": now,
                },
                merge_existing=True,
            )
            self._maybe_refresh_summary(bundle.chat_name, paths)
        except Exception as exc:
            log(level="WARNING", message=f"私聊记忆 V2 finalize 失败，已跳过摘要/档案更新：{bundle.chat_name} - {exc}")

    def clear_entity(self, chat_name):
        if not chat_name:
            return False
        entity_dir = self._entity_dir(chat_name)
        if os.path.exists(entity_dir):
            shutil.rmtree(entity_dir)
            return True
        return False

    def clear_all(self):
        root = os.path.join(self.memory_v2_base, self.wx_id)
        if not os.path.exists(root):
            return 0
        count = 0
        private_dir = os.path.join(root, "private")
        if os.path.exists(private_dir):
            for name in os.listdir(private_dir):
                target = os.path.join(private_dir, name)
                if os.path.isdir(target):
                    shutil.rmtree(target)
                    count += 1
        return count

    def _enabled(self):
        return bool(getattr(self.config, "private_profile_memory_switch", False))

    def _default_state(self):
        return copy.deepcopy(DEFAULT_STATE)

    def _default_summary(self):
        return copy.deepcopy(DEFAULT_SUMMARY)

    def _entity_hash(self, chat_name):
        return hashlib.sha1(f"private::{chat_name}".encode("utf-8")).hexdigest()

    def _entity_dir(self, chat_name):
        return os.path.join(self.memory_v2_base, self.wx_id, "private", self._entity_hash(chat_name))

    def _build_paths(self, chat_name):
        entity_dir = self._entity_dir(chat_name)
        return {
            "entity_dir": entity_dir,
            "meta": os.path.join(entity_dir, "meta.json"),
            "state": os.path.join(entity_dir, "state.json"),
            "summary": os.path.join(entity_dir, "rolling_summary.json"),
            "history": os.path.join(entity_dir, "chat_history.jsonl"),
            "patch_log": os.path.join(entity_dir, "patch_log.jsonl"),
        }

    def _ensure_entity_files(self, chat_name):
        paths = self._build_paths(chat_name)
        os.makedirs(paths["entity_dir"], exist_ok=True)
        if not os.path.exists(paths["meta"]):
            self._atomic_write_json(
                paths["meta"],
                {
                    "entity_hash": os.path.basename(paths["entity_dir"]),
                    "display_name": chat_name,
                    "created_at": self._now_str(),
                    "updated_at": self._now_str(),
                },
            )
        if not os.path.exists(paths["state"]):
            self._atomic_write_json(paths["state"], self._default_state())
        if not os.path.exists(paths["summary"]):
            self._atomic_write_json(paths["summary"], self._default_summary())
        for key in ("history", "patch_log"):
            if not os.path.exists(paths[key]):
                with open(paths[key], "w", encoding="utf-8"):
                    pass
        return paths

    def _migrate_legacy_if_needed(self, chat_name, paths):
        if os.path.getsize(paths["history"]) > 0:
            return
        legacy_path = os.path.join(self.legacy_memory_base, self.wx_id, chat_name, f"{chat_name}_memory.json")
        if not os.path.exists(legacy_path):
            return
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy_messages = json.load(f)
            if not isinstance(legacy_messages, list):
                legacy_messages = []
        except Exception:
            legacy_messages = []

        migrated_lines = []
        friend_count = 0
        self_count = 0
        for item in legacy_messages:
            if not isinstance(item, dict):
                continue
            entry = {
                "time": str(item.get("time", "")),
                "type": str(item.get("type", "text")),
                "attr": str(item.get("attr", "friend")),
                "sender": str(item.get("sender", chat_name)),
                "content": str(item.get("content", "")),
            }
            migrated_lines.append(json.dumps(entry, ensure_ascii=False))
            if entry["attr"] == "self":
                self_count += 1
            elif entry["attr"] != "system":
                friend_count += 1

        if migrated_lines:
            with open(paths["history"], "w", encoding="utf-8") as f:
                f.write("\n".join(migrated_lines) + "\n")
            state = self._read_json(paths["state"], self._default_state())
            state = self._normalize_state(state)
            state["relationship"]["turn_count"] = max(friend_count, self_count)
            state["relationship"]["stage"] = self._compute_stage(state["relationship"]["turn_count"])
            if legacy_messages:
                state["relationship"]["last_interaction_at"] = str(legacy_messages[-1].get("time", ""))
            self._atomic_write_json(paths["state"], state)

    def _extract_intent(self, chat_name, state, summary, recent_entries, user_input):
        prompt = self.config.get_internal_prompt_content("private_intent_extract")
        payload = {
            "state": state,
            "rolling_summary": summary.get("summary_text", ""),
            "recent_dialogue": self._render_recent_dialogue(recent_entries),
            "user_input": str(user_input or ""),
        }
        raw = self._call_internal_prompt(chat_name, prompt, json.dumps(payload, ensure_ascii=False, indent=2))
        parsed = self._parse_json_payload(raw, expect_array=False)
        if not isinstance(parsed, dict):
            return self._fallback_intent()
        return self._normalize_intent(parsed)

    def _generate_patch(self, chat_name, state, summary, recent_entries, user_input, intent):
        prompt = self.config.get_internal_prompt_content("private_state_patch")
        payload = {
            "state": state,
            "rolling_summary": summary.get("summary_text", ""),
            "recent_dialogue": self._render_recent_dialogue(recent_entries),
            "user_input": str(user_input or ""),
            "intent_result": intent,
        }
        raw = self._call_internal_prompt(chat_name, prompt, json.dumps(payload, ensure_ascii=False, indent=2))
        parsed = self._parse_json_payload(raw, expect_array=True)
        return parsed if isinstance(parsed, list) else []

    def _call_internal_prompt(self, chat_name, prompt, message):
        api = self.api_getter(chat_name)
        return api.chat(message, stream=False, prompt=prompt, history=None)

    def _parse_json_payload(self, raw, expect_array=False):
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        candidates = [text]
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
        if fence_match:
            candidates.append(fence_match.group(1).strip())
        bracket_segment = self._extract_balanced_segment(text, "[" if expect_array else "{", "]" if expect_array else "}")
        if bracket_segment:
            candidates.append(bracket_segment)
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        return None

    def _extract_balanced_segment(self, text, open_char, close_char):
        start = text.find(open_char)
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return ""

    def _normalize_intent(self, data):
        intent = str(data.get("intent", "CHIT_CHAT")).strip()
        if intent not in ALLOWED_INTENTS:
            intent = "CHIT_CHAT"
        emotional_intensity = self._coerce_int(data.get("emotional_intensity", 1), 1, 5, 1)
        reply_mode = str(data.get("reply_mode", "normal")).strip()
        if reply_mode not in ALLOWED_REPLY_MODES:
            reply_mode = "normal"
        topics = self._normalize_string_list(data.get("topics", []), max_items=6)
        evidence = self._normalize_string_list(data.get("evidence", []), max_items=6)
        return {
            "intent": intent,
            "emotional_intensity": emotional_intensity,
            "topics": topics,
            "memory_update_needed": bool(data.get("memory_update_needed", False)),
            "reply_mode": reply_mode,
            "evidence": evidence,
        }

    def _fallback_intent(self):
        return {
            "intent": "CHIT_CHAT",
            "emotional_intensity": 1,
            "topics": [],
            "memory_update_needed": False,
            "reply_mode": "normal",
            "evidence": [],
        }

    def _apply_patch_ops(self, state, patch_ops):
        if not isinstance(patch_ops, list):
            return self._normalize_state(state), []
        current = self._normalize_state(copy.deepcopy(state))
        applied = []
        for item in patch_ops:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip()
            path = str(item.get("path", "")).strip()
            if op not in {"add", "replace", "remove"}:
                continue
            if path in LIST_APPEND_PATHS:
                if op != "add":
                    continue
                value = str(item.get("value", "")).strip()
                if not value:
                    continue
                target = self._resolve_state_path(current, path[:-2])
                if isinstance(target, list):
                    target.append(value)
                    applied.append({"op": "add", "path": path, "value": value})
            elif path in SCALAR_PATHS:
                old_value = self._resolve_state_path(current, path)
                if op == "remove":
                    new_value = self._default_scalar_value(path)
                else:
                    new_value = item.get("value")
                new_value = self._normalize_scalar_value(path, new_value, old_value)
                self._set_state_path(current, path, new_value)
                applied.append({"op": op, "path": path, "value": new_value})
        return self._normalize_state(current), applied

    def _resolve_state_path(self, state, path):
        cursor = state
        parts = [part for part in path.split("/") if part]
        for part in parts:
            if isinstance(cursor, dict):
                cursor = cursor.get(part)
            else:
                return None
        return cursor

    def _set_state_path(self, state, path, value):
        parts = [part for part in path.split("/") if part]
        cursor = state
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value

    def _default_scalar_value(self, path):
        if path == "/relationship/intimacy_score":
            return 0.0
        if path == "/relationship/last_emotional_intensity":
            return 1
        return "CHIT_CHAT"

    def _normalize_scalar_value(self, path, value, old_value):
        if path == "/relationship/intimacy_score":
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return float(old_value if old_value is not None else 0.0)
        if path == "/relationship/last_emotional_intensity":
            return self._coerce_int(value, 1, 5, old_value if isinstance(old_value, int) else 1)
        value = str(value or "").strip()
        return value if value in ALLOWED_INTENTS else (old_value if old_value in ALLOWED_INTENTS else "CHIT_CHAT")

    def _compose_reply_prompt(self, base_prompt, state, summary, recent_entries, intent):
        cleaned_base_prompt = self._sanitize_base_prompt(base_prompt)
        reply_mode = intent.get("reply_mode", "normal")
        sections = [
            cleaned_base_prompt,
            "【系统阶段指令】",
            self._stage_instruction(state["relationship"].get("stage", "orientation")),
            "",
            "【系统补充上下文】",
            "以下内容由系统提供，仅可在有明确依据时自然引用，不得编造“你之前说过”。",
            "",
            "【当前对象档案】",
            json.dumps(state, ensure_ascii=False, indent=2),
            "",
            "【滚动摘要】",
            summary.get("summary_text", "（暂无）") or "（暂无）",
            "",
            "【最近对话】",
            self._render_recent_dialogue(recent_entries) or "（暂无）",
            "",
            "【本轮回复模式】",
            self._reply_mode_instruction(reply_mode),
        ]
        return "\n".join(sections).strip()

    def _sanitize_base_prompt(self, base_prompt):
        text = str(base_prompt or "").strip()
        if "内部思考层" in text:
            text = text.split("内部思考层", 1)[0].rstrip()
        guardrail = (
            "【系统硬约束】\n"
            "- 只能引用系统提供的档案、摘要和最近对话，不能捏造回忆或暗示“你之前说过”但系统没有提供。\n"
            "- 不得输出时间戳、系统提示语、调试文本、括号内的处理说明。\n"
            "- 保持当前人格与关系推进风格，但优先保证自然、稳定、不过度越界。"
        )
        return (text + "\n\n" + guardrail).strip()

    def _stage_instruction(self, stage):
        mapping = {
            "orientation": "当前仍偏导向期：保持安全感、好奇心和边界感，先建立轻松稳定的互动。",
            "exploratory": "当前偏探索期：可以适度表达个人判断和偏好，引导双向了解，但不要过度推进亲密感。",
            "affective": "当前偏情感期：优先识别脆弱表达，增加情感互惠和细节回应，但不要制造依赖性表达。",
            "stable": "当前偏稳定期：可以体现熟悉感和连续性，但仍要保持真实克制，不夸张、不神化关系。",
        }
        return mapping.get(stage, mapping["orientation"])

    def _reply_mode_instruction(self, reply_mode):
        mapping = {
            "normal": "正常自然回复，保持当前人格与节奏。",
            "empathy_first": "优先接住情绪，先理解再表达判断，避免一上来给解决方案。",
            "gentle_challenge": "在共情的前提下给出温和但清晰的不同看法，避免无原则迎合。",
            "planning_support": "优先帮助梳理选择、步骤和轻量决策，表达简洁具体。",
            "conflict_soften": "优先缓和对立感，避免硬碰硬，帮助恢复沟通与安全感。",
        }
        return mapping.get(reply_mode, mapping["normal"])

    def _maybe_refresh_summary(self, chat_name, paths):
        summary = self._normalize_summary(self._read_json(paths["summary"], self._default_summary()))
        entries = self._load_all_entries(paths["history"])
        keep_tail = self._recent_message_limit()
        if len(entries) <= keep_tail:
            return

        cutoff = len(entries) - keep_tail
        already_summarized = self._coerce_int(summary.get("source_message_count", 0), 0, len(entries), 0)
        if cutoff <= already_summarized:
            return

        pending_entries = entries[already_summarized:cutoff]
        pending_text = self._render_recent_dialogue(pending_entries)
        if len(pending_text) < int(getattr(self.config, "memory_summary_budget_chars", 6000)):
            return

        prompt = self.config.get_internal_prompt_content("private_rolling_summary")
        payload = {
            "old_summary": summary.get("summary_text", ""),
            "new_dialogue": pending_text,
        }
        raw = self._call_internal_prompt(chat_name, prompt, json.dumps(payload, ensure_ascii=False, indent=2))
        summary_text = str(raw or "").strip()
        if not summary_text:
            return
        summary["summary_text"] = summary_text
        summary["updated_at"] = self._now_str()
        summary["source_message_count"] = cutoff
        self._atomic_write_json(paths["summary"], summary)

    def _render_recent_dialogue(self, entries):
        lines = []
        for entry in entries or []:
            attr = str(entry.get("attr", "friend"))
            speaker = "助手" if attr == "self" else str(entry.get("sender", "用户"))
            content = str(entry.get("content", "")).strip()
            if not content or attr == "system":
                continue
            lines.append(f"{speaker}：{content}")
        return "\n".join(lines)

    def _normalize_state(self, state):
        base = self._default_state()
        source = state if isinstance(state, dict) else {}
        profile = source.get("profile", {}) if isinstance(source.get("profile", {}), dict) else {}
        relationship = source.get("relationship", {}) if isinstance(source.get("relationship", {}), dict) else {}

        base["profile"]["facts"] = self._normalize_string_list(profile.get("facts", []))
        preferences = profile.get("preferences", {}) if isinstance(profile.get("preferences", {}), dict) else {}
        base["profile"]["preferences"]["likes"] = self._normalize_string_list(preferences.get("likes", []))
        base["profile"]["preferences"]["dislikes"] = self._normalize_string_list(preferences.get("dislikes", []))
        base["profile"]["traits"] = self._normalize_string_list(profile.get("traits", []))
        base["profile"]["communication_style"] = self._normalize_string_list(profile.get("communication_style", []))
        base["profile"]["emotional_triggers"] = self._normalize_string_list(profile.get("emotional_triggers", []))
        base["profile"]["memory_hooks"] = self._normalize_string_list(profile.get("memory_hooks", []))
        base["profile"]["open_loops"] = self._normalize_string_list(profile.get("open_loops", []))

        turn_count = self._coerce_int(relationship.get("turn_count", 0), 0, 10**9, 0)
        base["relationship"]["turn_count"] = turn_count
        base["relationship"]["stage"] = self._compute_stage(turn_count)
        base["relationship"]["intimacy_score"] = self._normalize_scalar_value(
            "/relationship/intimacy_score",
            relationship.get("intimacy_score", 0.0),
            0.0,
        )
        base["relationship"]["last_intent"] = self._normalize_scalar_value(
            "/relationship/last_intent",
            relationship.get("last_intent", "CHIT_CHAT"),
            "CHIT_CHAT",
        )
        base["relationship"]["last_emotional_intensity"] = self._normalize_scalar_value(
            "/relationship/last_emotional_intensity",
            relationship.get("last_emotional_intensity", 1),
            1,
        )
        base["relationship"]["last_interaction_at"] = str(relationship.get("last_interaction_at", "") or "")
        return base

    def _normalize_summary(self, summary):
        base = self._default_summary()
        if isinstance(summary, dict):
            base["summary_text"] = str(summary.get("summary_text", "") or "")
            base["updated_at"] = str(summary.get("updated_at", "") or "")
            base["source_message_count"] = self._coerce_int(summary.get("source_message_count", 0), 0, 10**9, 0)
        elif isinstance(summary, str):
            base["summary_text"] = summary
        return base

    def _normalize_string_list(self, values, max_items=20):
        if not isinstance(values, list):
            return []
        seen = set()
        result = []
        for item in values:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
            if len(result) >= max_items:
                break
        return result

    def _coerce_int(self, value, min_value, max_value, default):
        try:
            return max(min_value, min(max_value, int(value)))
        except (TypeError, ValueError):
            return default

    def _compute_stage(self, turn_count):
        if turn_count >= 81:
            return "stable"
        if turn_count >= 41:
            return "affective"
        if turn_count >= 16:
            return "exploratory"
        return "orientation"

    def _recent_message_limit(self):
        turns = self._coerce_int(getattr(self.config, "memory_recent_turns", 8), 2, 30, 8)
        return max(4, turns * 2)

    def _load_recent_entries(self, path, limit):
        entries = self._load_all_entries(path)
        return entries[-limit:]

    def _load_all_entries(self, path):
        entries = []
        if not os.path.exists(path):
            return entries
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
        return entries

    def _count_jsonl_lines(self, path):
        if not os.path.exists(path):
            return 0
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def _append_jsonl(self, path, data):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _read_json(self, path, default):
        if not os.path.exists(path):
            return copy.deepcopy(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return copy.deepcopy(default)

    def _atomic_write_json(self, path, data, merge_existing=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = data
        if merge_existing and os.path.exists(path):
            current = self._read_json(path, {})
            if isinstance(current, dict) and isinstance(data, dict):
                payload = {**current, **data}
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def _now_str(self):
        return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
