# 评论语义建议格式 v1

评论分析子智能体只输出建议，不直接修改系统。文件必须是 JSON：

```json
{
  "schema_version": "semantic-proposals-v1",
  "proposals": [
    {
      "proposal_key": "唯一编号",
      "kind": "entity_alias",
      "title": "一句话标题",
      "target": "目标分类或文档",
      "proposed_change": {"add": ["建议内容"]},
      "reason": "为什么需要修改",
      "confidence": 0.9,
      "evidence": [
        {
          "post_id": "微博 id",
          "comment_id": "评论 id，可为空",
          "quote": "短引文"
        }
      ]
    }
  ]
}
```

`kind` 只允许：

- `entity_alias`：实体别称。
- `search_term`：检索词。
- `category_boundary`：分类边界。
- `subcategory`：子分类。
- `new_category`：新分类。
- `semantic_document`：语义文档。

每条建议必须有证据和 0 到 1 的置信度。证据不足时不得创建建议。
