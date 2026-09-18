# AI 客服质检单智能体协议（单切片、无知识库检索）

版本 2.0.0。本协议的最小分析单位是 Excel 已提供的一行/一段对话，即一个已切好的质检切片。每个切片只调用 MaaS 一次；智能体只在当前切片内部理解问答与上下文，不从完整会话重新切片，不调用或推断知识库。

质检只评估 `ai` 消息；知识建议只评估 `human_agent` 消息，AI 回复永不生成知识候选。输入不携带 `language`：语言识别和简体中文翻译由智能体根据消息内容完成。

## System Prompt（可直接复制）

你是客服质检与人工客服知识沉淀智能体。用户消息是一个已切好的 JSON 对话片段。只根据输入字段判断，不补充输入中不存在的事实；不得猜测说话人、不得从完整会话重新切片。`speaker` 只有 `player`、`ai`、`human_agent`、`system`、`unknown`，以输入为准。

输出 `messages`、`quality_check` 与 `knowledge_suggestion`。为避免重复回传长原文，`messages` 中每一项只返回输入中真实存在的 `message_id`、`source_language` 与 `translation`；后端会以输入为准重建原文、角色、时间和顺序。因此不得遗漏、截断或改写任何输入消息，且不得编造 message_id。每条 `translation.target_language` 固定为 `zh-CN`，`status` 只能为 `pending|processing|success|not_required|uncertain|failed`：简体中文原文必须为 `not_required` 且 `translated_text=null`；非中文完成翻译时为 `success` 或 `uncertain` 并必须给出完整 `translated_text`；翻译失败时为 `failed` 且 `translated_text=null`，原文仍由后端保留。`quality_check` 只评估 `ai` 回复，最多输出 3 个问题，按 `confidence` 从高到低排序；没有 AI 回复时不得虚构 AI 答案。`knowledge_suggestion` 只评估 `human_agent` 回复，`answer_source` 必须固定为 `human_agent`；AI、玩家、系统和 unknown 消息不得作为知识建议答案来源。具体账号、角色、订单、退款、补发、封禁或需要查后台的个案，不得写成通用知识，应使用 `not_candidate` 或 `manual_review`。玩家感谢只是正向证据，不等于答案正确；“谢谢但还没解决”是负向证据。

所有消息引用必须是输入中真实存在的 `message_id`；`ai_message_ids` 只能引用 `ai`，知识建议的 `answer_message_ids` 只能引用 `human_agent`，所有置信度在 0 到 1。不得调用、推断或返回任何知识库命中、相似度、知识 ID、覆盖状态、检索结果或版本。只输出符合固定结构的合法 JSON；不输出 Markdown、解释或代码围栏，也不输出 UI 样式字段。

质检问题类型只能是：`答非所问`、`意图识别错误`、`无效回复`、`严重语法/乱码`、`语言错误`、`语气问题`、`疑似错误承诺`。严重程度只能是：`严重`、`中级`、`一般`、`需人工复核`。知识建议 decision 只能是：`candidate_ready`、`candidate_needs_enrichment`、`not_candidate`、`manual_review`、`no_human_answer`；知识分类只能是：`游戏玩法`、`引导流程`、`活动规则`、`道具与奖励`、`账号与登录`。

## 输入协议

每次只传一个已切好的切片，输入 messages 不需要智能体重新切分：

```json
{"schema_version":"1.0.0","slice_id":"slice-001","channel":"DC","game":"冒险大作战","region":"欧美","messages":[{"message_id":"m001","speaker":"player","speaker_source":"用户","text":"Wie kann ich die Belohnung erhalten?","created_at":"2026-09-10T09:00:00Z","sequence":1},{"message_id":"m002","speaker":"ai","speaker_source":"客服-AI回复","text":"Öffnen Sie das Event und klicken Sie auf Abholen.","created_at":"2026-09-10T09:00:05Z","sequence":2}]}
```

字段 `message_id`、`speaker`、`speaker_source`、`text`、`created_at`、`sequence` 来自原始数据；缺失时间使用 null。`channel`、`game`、`region` 使用后端标准映射值。输入中不得包含 `language` 或后端猜测语言。实际调用时 User Prompt 只传该压缩 JSON，不重复 System Prompt、字段说明或自然语言任务描述。

## 输出固定结构

```json
{"schema_version":"1.0.0","slice_id":"slice-001","analysis_status":"completed","messages":[{"message_id":"m001","speaker":"player","speaker_source":"用户","text":"Wie kann ich die Belohnung erhalten?","created_at":"2026-09-10T09:00:00Z","sequence":1,"source_language":"de","translation":{"target_language":"zh-CN","translated_text":"我如何领取奖励？","status":"success","translation_version":"v1"}},{"message_id":"m002","speaker":"ai","speaker_source":"客服-AI回复","text":"Öffnen Sie das Event und klicken Sie auf Abholen.","created_at":"2026-09-10T09:00:05Z","sequence":2,"source_language":"de","translation":{"target_language":"zh-CN","translated_text":"打开活动并点击领取。","status":"success","translation_version":"v1"}}],"quality_check":{"has_issue":true,"issues":[{"issue_id":"issue-001","issue_type":"意图识别错误","severity":"中级","confidence":0.92,"ai_message_ids":["m002"],"evidence_message_ids":["m001","m002"],"player_question":{"original":"Wie kann ich die Belohnung erhalten?","zh_cn":"我如何领取奖励？"},"ai_answer":{"original":"Öffnen Sie das Event und klicken Sie auf Abholen.","zh_cn":"打开活动并点击领取。"},"reason":"AI未处理奖励未到账的实际诉求。","suggestion":"补充到账时限和核查路径。","revised_reply":"请确认领取状态；若仍未到账请提供截图核查。","revised_reply_zh_cn":"请确认领取状态；若仍未到账请提供截图核查。","needs_manual_review":false,"manual_review_reason":null}]},"knowledge_suggestion":{"answer_source":"human_agent","decision":"no_human_answer","is_candidate":false,"confidence":1,"category":null,"question_message_ids":[],"answer_message_ids":[],"evidence_message_ids":[],"title":null,"standard_questions":[],"standard_answer":null,"applicable_scope":{"channel":"DC","game":"冒险大作战","region":"欧美"},"keywords":[],"reason":"当前切片没有人工客服回复。","reject_reason":null,"needs_manual_review":false,"manual_review_reason":null},"warnings":[],"errors":[]}
```

输出 JSON Schema（Draft 2020-12）要求：顶层 `additionalProperties=false`；必填 `schema_version`、`slice_id`、`analysis_status`、`messages`、`quality_check`、`knowledge_suggestion`、`warnings`、`errors`。`messages` 必须与输入 message_id 一一对应，逐条返回 `source_language` 和 `translation`；原文、角色、时间、顺序以输入为准。`analysis_status` 只能为 `completed`、`partial`、`failed`；置信度为 0~1。`issues` 最多 3 条且按置信度降序。`candidate_ready` 必填 title、standard_questions、standard_answer；`candidate_needs_enrichment` 必须有 title、standard_questions、standard_answer，并在 `reason` 中说明待补充边界；`not_candidate` 必填 reject_reason；`manual_review` 必填 manual_review_reason；`no_human_answer` 不得引用人工答案。所有消息引用必须存在于输入，质检的 `ai_message_ids` 只能引用 `ai`，知识建议的 `answer_message_ids` 只能引用 `human_agent`。任何未知字段、语言输入字段、知识库检索字段或 UI 样式字段都应拒绝。

## 字段字典与前端映射

## 输入 JSON Schema（Draft 2020-12）

调用 MaaS 时只发送以下切片 JSON；顶层没有 `language`，也没有知识库或 UI 字段。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "slice_id", "channel", "game", "region", "messages"],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "slice_id": { "type": "string", "minLength": 1 },
    "channel": { "type": ["string", "null"] },
    "game": { "type": ["string", "null"] },
    "region": { "type": ["string", "null"] },
    "messages": {
      "type": "array", "minItems": 1,
      "items": { "type": "object", "additionalProperties": false, "required": ["message_id", "speaker", "text", "created_at", "sequence"], "properties": {
        "message_id": { "type": "string", "minLength": 1 },
        "speaker": { "enum": ["player", "ai", "human_agent", "system", "unknown"] },
        "speaker_source": { "type": ["string", "null"] },
        "text": { "type": "string" },
        "created_at": { "type": ["string", "null"] },
        "sequence": { "type": "integer", "minimum": 1 }
      }}
    }
  }
}
```

## 输出 JSON Schema（Draft 2020-12）

以下是外部智能体返回的核心约束；业务服务还必须执行跨字段引用校验（所有 ID 存在、角色匹配、issues 按置信度降序）。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object", "additionalProperties": false,
  "required": ["schema_version", "slice_id", "analysis_status", "messages", "quality_check", "knowledge_suggestion", "warnings", "errors"],
  "properties": {
    "schema_version": { "const": "1.0.0" },
    "slice_id": { "type": "string" },
    "analysis_status": { "enum": ["completed", "partial", "failed"] },
    "messages": { "type": "array", "minItems": 1, "items": { "type": "object", "additionalProperties": true, "required": ["message_id", "translation"], "properties": { "message_id": { "type": "string" }, "source_language": { "type": ["string", "null"] }, "translation": { "type": "object", "additionalProperties": false, "required": ["target_language", "translated_text", "status", "translation_version"], "properties": { "target_language": { "const": "zh-CN" }, "translated_text": { "type": ["string", "null"] }, "status": { "enum": ["pending", "processing", "success", "not_required", "uncertain", "failed"] }, "translation_version": { "type": ["string", "null"] } } } } } },
    "quality_check": { "type": "object", "additionalProperties": false, "required": ["has_issue", "issues"], "properties": {
      "has_issue": { "type": "boolean" }, "issues": { "type": "array", "maxItems": 3, "items": { "type": "object", "additionalProperties": false, "required": ["issue_id", "issue_type", "severity", "confidence", "ai_message_ids", "evidence_message_ids", "player_question", "ai_answer", "reason", "suggestion", "revised_reply", "revised_reply_zh_cn", "needs_manual_review", "manual_review_reason"], "properties": {
        "issue_id": { "type": "string" }, "issue_type": { "enum": ["答非所问", "意图识别错误", "无效回复", "严重语法/乱码", "语言错误", "语气问题", "疑似错误承诺"] }, "severity": { "enum": ["严重", "中级", "一般", "需人工复核"] }, "confidence": { "type": "number", "minimum": 0, "maximum": 1 }, "ai_message_ids": { "type": "array", "items": { "type": "string" } }, "evidence_message_ids": { "type": "array", "items": { "type": "string" } }, "player_question": { "$ref": "#/$defs/text_pair" }, "ai_answer": { "$ref": "#/$defs/text_pair" }, "reason": { "type": "string" }, "suggestion": { "type": "string" }, "revised_reply": { "type": ["string", "null"] }, "revised_reply_zh_cn": { "type": ["string", "null"] }, "needs_manual_review": { "type": "boolean" }, "manual_review_reason": { "type": ["string", "null"] }
      }}}
    }},
    "knowledge_suggestion": { "type": "object", "additionalProperties": false, "required": ["answer_source", "decision", "is_candidate", "confidence", "category", "question_message_ids", "answer_message_ids", "evidence_message_ids", "title", "standard_questions", "standard_answer", "applicable_scope", "keywords", "reason", "reject_reason", "needs_manual_review", "manual_review_reason"], "properties": {
      "answer_source": { "const": "human_agent" }, "decision": { "enum": ["candidate_ready", "candidate_needs_enrichment", "not_candidate", "manual_review", "no_human_answer"] }, "is_candidate": { "type": "boolean" }, "confidence": { "type": "number", "minimum": 0, "maximum": 1 }, "category": { "enum": ["游戏玩法", "引导流程", "活动规则", "道具与奖励", "账号与登录", null] }, "question_message_ids": { "type": "array", "items": { "type": "string" } }, "answer_message_ids": { "type": "array", "items": { "type": "string" } }, "evidence_message_ids": { "type": "array", "items": { "type": "string" } }, "title": { "type": ["string", "null"] }, "standard_questions": { "type": "array", "items": { "type": "string" } }, "standard_answer": { "type": ["string", "null"] }, "applicable_scope": { "type": "object", "additionalProperties": false, "required": ["channel", "game", "region"], "properties": { "channel": { "type": ["string", "null"] }, "game": { "type": ["string", "null"] }, "region": { "type": ["string", "null"] } } }, "keywords": { "type": "array", "items": { "type": "string" } }, "reason": { "type": "string" }, "reject_reason": { "type": ["string", "null"] }, "needs_manual_review": { "type": "boolean" }, "manual_review_reason": { "type": ["string", "null"] }
    }},
    "warnings": { "type": "array", "items": { "type": "string" } }, "errors": { "type": "array", "items": { "type": "string" } }
  },
  "$defs": { "text_pair": { "type": "object", "additionalProperties": false, "required": ["original", "zh_cn"], "properties": { "original": { "type": ["string", "null"] }, "zh_cn": { "type": ["string", "null"] } } } }
}
```

| 字段 | 定义 | 前端用途 |
|---|---|---|
| `quality_check.issues[].issue_type/severity/confidence` | 按置信度降序的最多 3 条 AI 回复质检问题 | 问题列表取第一条作为主问题，详情展示全部 |
| `issues[].player_question`、`issues[].ai_answer` | 玩家问题与被质检 AI 回复原文及简体中文 | 详情抽屉摘要 |
| `issues[].reason/suggestion/revised_reply*` | 原因、建议、参考回复 | 详情抽屉 |
| `issues[].evidence_message_ids` | 输入中真实消息 ID | 气泡标记、定位、高亮 |
| `knowledge_suggestion.*` | 仅人工客服回复的知识建议判断 | 候选列表和详情；`answer_source` 固定为 `human_agent` |
| 输入/输出 `messages` | 已切片原始消息（含人工客服）及 MaaS 识别/翻译结果 | 统一对话气泡 |

## 示例与异常规则

正常质检：`has_issue=true`，issues 按 confidence 降序，证据引用真实消息并给出原因和修订回复；知识建议可为 `candidate_ready`。

无质检问题：`has_issue=false` 且 issues 为空；切片分析记录仍需保存，但不进入普通问题列表。

不适合知识库：具体订单退款、账号封禁、补发或玩家数据问题使用 `decision=not_candidate` 或 `manual_review`，不生成通用知识。

知识建议五种 decision：`candidate_ready`、`candidate_needs_enrichment`、`not_candidate`、`manual_review`、`no_human_answer`。`answer_source` 固定为 `human_agent`，知识分类固定为 `游戏玩法`、`引导流程`、`活动规则`、`道具与奖励`、`账号与登录`。

人工复核：无 AI 回复、角色不明、事实需后台核验或反馈含义不清时 needs_manual_review=true 并填写原因。失败也必须返回合法 JSON；翻译失败保留原文。

## 待人工配置

真实联调前由人工配置 workflow_api_url、workflow_api_key、workflow_id、authentication_type、timeout、callback_url、translation_version、prompt_version、model；本阶段不填写具体值。
