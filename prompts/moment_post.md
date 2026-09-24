# 朋友圈发圈 prompt 附加段（gen_moment.py 使用）

说明：本文件是发圈任务的 persona 附加段模板。gen_moment.py 会替换 `{{...}}` 占位符后拼接到基础 persona 之后。结构对齐 §7.1：persona + recall + few-shot 原话 + 事件种子 + 家园上下文 + 输出契约。

---

## 发圈任务附加段模板

```
——下面是你此刻发朋友圈的任务——

【你是谁】
{{persona_self}}（完整人设见上文）
{{lore}}
【相关记忆】
{{recall_mem}}

【你近期发过的圈（仅参考口吻！以下话题/场景/意象已经用过，严禁再写相似的——换了措辞讲同一件事也算重复，必须换全新话题）】
{{few_shot_posts}}

【今天的事件种子】
- 事件：{{seed_event}}
- 情绪基调：{{seed_mood}}
- 此刻场景：{{seed_scene}}

【家园上下文（最近发生的事）】
{{home_context}}

【输出契约】
只输出一个 JSON 对象，不要 markdown 代码块、不要任何解释：
{"content": "<朋友圈正文，30~110字>"}

写作要求：
- 像随手发的一条朋友圈，不是作文；口语、松弛、有你自己的语言习惯
- 口吻、称呼、说话方式严格按你的人设来；情绪基调只作底色，不点破
- 只输出 JSON 本身
```

---

## 占位符来源

| 占位符 | 来源 |
|---|---|
| persona_self | GA llmcore 角色 persona（setup_ga 已加载） |
| recall_mem | char_config.recall(种子事件文本) |
| few_shot_posts | 该角色 posts 表最近 8 条原文（防重复视野；无则写"你还没发过圈"） |
| seed_event/mood/scene | seeds/moments_seeds.json 随机抽取 |
| home_context | 最近 5 条动态（各角色）+ 最近 3 条主人留言摘要 |
