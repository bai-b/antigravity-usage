---
name: usage-dashboard
description: 打开 Antigravity 本机用量看板，查看 Token、缓存、生成速度、上下文、额度和历史 API 参考费用。
disable-model-invocation: true
---

调用插件的 `open_dashboard` 工具并返回它提供的 Markdown 链接。如果当前会话的真实 UUID 已在会话上下文中提供，传给 `conversation_id`。否则省略该参数，让用户在看板选择会话。工具账户额度是桌面客户端返回的快照，费用按公开 API 参考价折算。
