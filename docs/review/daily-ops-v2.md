# 每日数据运营新版流程（试运行）

本流程默认关闭。启用前，现有 01:00 与 02:00 定时任务继续按原方式运行。

## 流程

1. 01:00 抓取微博，整理媒体和分类，按关键词生成候选。
2. 抓取前记录全部分类和 N宗罪关联数量，作为当天新增统计的起点。
3. 评论采集显式使用候选优先模式：最多 20 帖，其中 12 帖来自待审候选，8 帖用于发现新内容。
4. 抓取完成后写入 `run-manifest.json`，记录数据快照、请求账本、微博/评论数量、分类新增数量、关键词基线、评论审核包和失败阶段。
5. 03:30 审核前先检查清单状态；只有 `status=success` 才使用当天完整结果。
6. 子智能体审核待确认微博，并分析当天评论。
7. 评论分析只输出语义建议，不修改规则、分类或文档。
8. `daily_ops.py review` 把待确认微博和语义建议合并为一份人审清单。
9. 用户批准后，微博决定通过人工决定命令落库；规则和文档修改另走 Issue 和 PR。

## 评论选帖

默认模式保持原行为：

```bash
.venv/bin/python wbq.py comments-pilot --fetch --output dist/comment-pilot/STAMP
```

试运行候选优先模式：

```bash
.venv/bin/python wbq.py comments-pilot \
  --strategy candidate-first \
  --candidate-count 12 \
  --fetch \
  --output dist/comment-pilot/STAMP
```

候选池优先顺序：

1. 已进入人工待审的微博。
2. 尚无评论的待审候选。
3. 来自关键词搜索的候选。
4. 按更新时间排序，并在分类之间轮转。

候选不足 12 帖时，探索选帖补足 20 帖。

## 分类新增数量

抓取开始前和最终分类完成后，分别采集一次分类数量：

```bash
.venv/bin/python daily_ops.py counts \
  --output dist/daily-ops/STAMP/classification-before.json

# 抓取、评论和最终分类完成后
.venv/bin/python daily_ops.py counts \
  --output dist/daily-ops/STAMP/classification-after.json
```

任务状态 JSON 加入两份快照：

```json
{
  "classification_before": {
    "categories": {
      "hot": {"associations": 5005, "unique_posts": 5005}
    },
    "memes": {
      "chishi": {"associations": 39, "unique_posts": 39}
    }
  },
  "classification_after": {
    "categories": {
      "hot": {"associations": 5653, "unique_posts": 5653}
    },
    "memes": {
      "chishi": {"associations": 46, "unique_posts": 46}
    }
  }
}
```

`run-manifest.json` 会生成两个稳定排序的数组：

- `category_deltas`：每个普通分类的关联新增数、唯一微博新增数。
- `meme_deltas`：每个 N宗罪条目的关联新增数、唯一微博新增数。

数字采用有符号差值。正数表示新增，`0` 表示没有变化，负数表示重新分类后减少。
同一微博可以关联多个分类，因此“关联新增数”和“唯一微博新增数”必须分开汇报。
主线程日报要列出所有有变化的分类和 N宗罪条目；没有变化时明确写 `0`。

## 每日完成清单

任务编排者把状态写入 JSON，再生成固定清单：

```bash
.venv/bin/python daily_ops.py manifest \
  --state dist/daily-ops/STAMP/state.json \
  --output dist/daily-ops/STAMP
```

成功清单必须包含：

- 数据 Release、资产名和 SHA256。
- 关键词基线版本。
- 请求账本已用数和上限。
- 微博、评论、评论线程数量。
- 分类及 N宗罪关联新增数量。
- baseline、评论审核包等产物路径。

失败清单必须写明失败阶段。后续审核步骤不得把失败清单当成完整数据。

## 统一人审清单

```bash
.venv/bin/python daily_ops.py review \
  --candidate-dir dist/candidate-review/STAMP \
  --proposals dist/comment-review/STAMP/semantic-proposals.json \
  --manifest dist/daily-ops/DAILY_STAMP/run-manifest.json \
  --stamp REVIEW_STAMP \
  --output dist/daily-review/REVIEW_STAMP
```

输出 `daily-review.json` 和 `daily-review.md`。第一部分是待确认微博，第二部分是规则、分类和语义文档建议。

## 安全边界

- 评论只能证明观众如何称呼、解释、关联和表达情绪，不能单独证明钱钱做过某事。
- 新流程不得提高每日 500 次请求上限。
- 语义建议不得自动修改关键词、分类、few-shot 或文档。
- 候选优先模式默认关闭，自动化启用必须单独确认。
- 分类统计失败不能掩盖主任务状态；失败清单至少保留已经采集到的阶段数据。

精简指标及样本不足时的报告口径见
[每日数据运营指标口径 v1](../metrics/daily-ops-metrics-v1.md)。
