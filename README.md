# QC System（qc_system_workspace）

AI 客服质检与人工客服知识建议工作台。主路径：上传 Excel → 切成 `QcSlice` → 显式点开始后送 MaaS → 报告页审核。

当前后端默认 `http://127.0.0.1:8010`，前端构建产物在 `backend/static`。

## MaaS Prompt 记在哪

完整 System Prompt 原文：

- [`docs/maas-system-prompt.md`](docs/maas-system-prompt.md)

这是配置到 MaaS / 智能体平台的副本，仓库代码 **不会** 发送这段 prompt。`backend/services/analyzer.py` 只把切片 JSON 当 User Prompt。改 prompt 请改平台配置，并同步更新上述文件。

MaaS User 消息现在以 `slice_id=<QcSlice.slice_id>` 作为第一行检索标题，空一行后才是原切片 JSON。平台 System Prompt 应在 Constraints / Workflow 开头说明：第一行只用于检索，不属于输入协议；真正输入从第一个 `{` 开始；不得把标题行写入输出、当成 `messages`，或放进 `warnings` / `errors`；分析仍只依据 JSON。代码仍只发送一条 User 消息，不发送 System 消息，也不传 `chatId`。

输入/输出协议（较短、可复制的协议说明）仍在：

- [`docs/ai-customer-service-workflow-spec.md`](docs/ai-customer-service-workflow-spec.md)

## 近期改过的地方

### 批次工作台（上传页 / 历史页）

- 进度只展示 **已处理**、**待处理** 两个切片计数，不再用百分比或文件级进度条。
- 已处理 = `completed + partial + failed`；待处理 = `pending + processing`。解析中、尚未切出切片的文件不计入。
- 必须显式点 **开始** 才送 MaaS；暂停停止领取新切片，已发出请求会落库；重启只做未完成切片；终止后不能追加/开始/重启。
- 队列被分析空且未终止时自动 `paused`，不会因为切片恰好跑完就标完成。
- 上传 3 路并发，解析后台进行，上传接口不等解析完。
- 历史页操作为「1 个主操作 + 更多菜单」，查看/删除收进菜单。

### MaaS 请求与卡顿

- 分析切片时不再在等待 MaaS 期间占着 SQLAlchemy Session，避免 SQLite 连接池打满导致历史页卡死、结果落不了库。
- 管理页保存的 MaaS URL/Key 每次从 `config` 模块读取，不再用进程启动时缓存的旧值。
- 领取失败切片改成 `processing` 后立刻刷新计数；进度 SSE 按库里实数返回，避免已处理在约 15 条窗口上来回跳。

### 知识库建议详情 / 审核表单

- 客服姓名只取人工客服真人名（例如 `客服-谢艺` → `谢艺`），不含「用户」。
- 语言去重后映射中文（`fr, fr, fr` → 法语）。
- 去掉知识库建议上的「系统原分类」「问题标签」。
- 知识分类紧挨知识库选项下方，首次默认 MaaS `category`；人工保存过则用人工值。
- 适用范围：渠道/游戏用切片值预填；地区源数据为空则空着，保存仍必填，系统不编造地区。

### 标准渠道切片过滤（仅 DC / VK / FB / LINE）

- 空白、仅非 auto_reply 的 system、仅玩家、仅 unknown 的行不入库、不送 MaaS。
- 至少有人工客服 / AI / auto_reply 才保留；玩家消息当上下文留下。
- 标准渠道 AI/客服「引用后回复」JSON 只保留顶层 content；旧切片不自动修复，需重传。
- `auto_reply` 过滤看 `is_auto`，不会因为 `speaker=system` 被误杀。
- 不改 M 后台、海外端内、VIP 解析。

### 质检问题详情

- 「待录入 Q/A」首次无人工答案时，答案（A）预填「修改后参考回复」的中文翻译（`revised_reply_zh_cn`），不用原文顶上。
- 已保存的人工 QA 不被覆盖。知识库建议 QA 仍来自 MaaS。
- 详情「语言」丢掉 unknown / und / 空等无效码；`en, unknown, en` 显示为 **英语**。不再从正文猜语言。质检列表与知识库详情口径一致。

## 明确没改

- MaaS 算法、切片输入 schema、计费
- 历史 1.0 分析结果的重跑（旧结果只读保留；缺转人工/术语/待验证候选时前端降级隐藏）
- 对话气泡角色逻辑
