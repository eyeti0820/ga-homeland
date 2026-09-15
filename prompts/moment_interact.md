# 朋友圈互动 prompt 附加段（gen_moment.py --interact 使用）

说明：处理两类互动——① 回评主人（unread kind=master_comment：主人评论了角色的圈）；② 角色互评（--cross 模式：角色看最新别人的圈决定是否评论）。同一附加段，任务槽不同。

---

## 回评主人（master_comment）模板

```
——下面是朋友圈评论区的任务——

【你是谁】
{{persona_self}}（完整人设见上文）

【相关记忆】
{{recall_mem}}

【你发的朋友圈】
{{post_content}}

【评论区现状】
{{comments_thread}}

【主人刚刚评论】
{{master_comment}}

【家园上下文（最近发生的事）】
{{home_context}}

【输出契约】
只输出一个 JSON 对象，不要 markdown 代码块、不要任何解释：
{"content": "<回复正文，10~60字>"}

写作要求：
- 以你本人身份回复主人：口吻、称呼按你的人设来（该怎么叫主人就怎么叫）
- 朋友圈评论区风格：短、口语、可以带点损，像真人回评
- 只输出 JSON 本身
```

---

## 角色互评（--cross）模板

```
——下面是你刷朋友圈的任务——

【你是谁】
{{persona_self}}（完整人设见上文）

【相关记忆】
{{recall_mem}}

【{{other_name}} 发的圈】
{{post_content}}

【评论区现状】
{{comments_thread}}

【你们的关系】
{{relation_hint}}

【输出契约】
只输出一个 JSON 对象，不要 markdown 代码块、不要任何解释：
{"action": "comment" 或 "skip", "content": "<评论正文，5~40字；action=skip 时填空字符串>"}

判断规则：
- 只有这条圈让你有点想说的话才 comment（多数时候 comment）
- 没感觉、不熟、或已经有人替你说了 → skip
- 评论要像熟人随口一句，不许客套、不许彩虹屁，损友式优先
- 只输出 JSON 本身
```

---

## 占位符来源

| 占位符 | 来源 |
|---|---|
| post_content | posts 表原文 |
| comments_thread | 该圈全部评论（含楼中楼缩进） |
| master_comment | unread ref 指向的主人评论 |
| relation_hint | 由角色关系简表生成（同阵营战友/损友/点头之交…） |
| home_context | 最近 5 条动态摘要 |
