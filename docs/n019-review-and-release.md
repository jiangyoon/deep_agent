# N019 北京职称评审暂行办法校对与放行

**交付目标**　将京人社事业发〔2020〕12 号的数据库正文与政府公报对齐，更新有改动的 Milvus 记录，通过验收后让网页按北京人社渠道检索。处理范围限定为 `N019`。

## 已核对的情况

- `public.kb1_sources` 中 `N019` 的文号为 `京人社事业发〔2020〕12号`，地区为北京市，有效性为「现行有效」。处理状态仍是 `visual_transcription_needs_business_review`，网页工具只接收 `review_dataset`，这个状态使该来源无法入选。
- PostgreSQL 已有 54 个正文单元，包括 45 条正式条款、8 个章标题和 1 个印发通知单元。54 个单元的 `embedding_status` 都是「已入库」。
- 先前把 45 条与《首都之窗》公报逐条对齐。在统一全半角、去掉空白和标点后，33 条相同，12 条仍有文字差异。第七条的委员会类型存在明确差异，第十八条的审核用语也不同。请复现对比后确定最终修改清单。 [北京市政府公报](https://www.beijing.gov.cn/zhengce/zhengcefagui/202008/t20200803_1969932.html) · [北京市人社局发布页](https://rsj.beijing.gov.cn/xxgk/2024zcwj/202406/t20240617_3716905.html)

先前发现有差异的条款包括第四、六、七、八、九、十、十一、十八、二十八、三十二、四十二、四十三条。

## 1. 生成校对清单并备份

以《首都之窗》公报为校对底本，用北京市人社局发布页交叉核实有歧义的文字。直连网页受限时，可用网页检索工具读取正文，再按条号提取。按第一至第四十五条逐条对应 PostgreSQL 中的 `N019-S…`，保留同一条跨段、跨页的完整内容。用空白和标点归一化定位差异，实际替换时保留官方的段落、数字、标点和条号。核对章标题和印发通知单元的归属。

先生成变更清单，记录每个受影响的 `section_id`、条号、旧文、新文及官方出处。先前发现的 12 条与本次清单不同时，先查清是网页抽取差异、标点差异还是库内版本已经变化。原始 PDF 及视觉转录可用来核对页码和记录历史。复用现有来源 ID、条款 ID 和原件定位。

在 Git 忽略的 `data/material_review/` 下留存备份。备份 `N019` 来源行、54 条 PostgreSQL 正文及其状态、模型和时间戳。Milvus 备份需记录对应 `section_id`、`source_id`、`search_text`、`dense_vector` 和 `indexed_at`，以便恢复。BM25 向量由服务器根据检索文本生成。

## 2. 更新 PostgreSQL 正文

在一个数据库事务里只更新变更清单中的 `public.kb1_sections` 记录。用 `source_id = 'N019'` 和明确的条款 ID 限定范围，提交前确认受影响行数等于清单数量。

- 更新 `text`。`heading` 中包含被校正词语时，同步更新该标题。
- 将修改记录的 `embedding_status` 改为「待入库」。记录旧模型与时间戳用于回滚。
- 在校对和重建期间保持该来源的待复核状态，使线上检索继续屏蔽整份文件。

提交后列出 `N019` 中待入库和失败的条款 ID，并与变更清单比对。当前入库脚本支持按来源筛选和限制数量，它的默认预览只显示配置，待处理 ID 要以 PostgreSQL 查询为准。

```sql
SELECT id, embedding_status
FROM public.kb1_sections
WHERE source_id = 'N019'
  AND embedding_status IN ('待入库', '失败')
ORDER BY id;
```

## 3. 重建改动条款的索引

从仓库根目录使用现有入库脚本。该脚本将「章名＋正文」生成 1024 维向量，按原 `section_id` 写入 `career.career_policy_sections`，并在 Milvus 确认后将 PostgreSQL 状态记为「已入库」。待复核的 `N019` 可以执行这个入库流程。

```powershell
python -B scripts/kb1/index_sections.py --env-file .env --source-id N019 --apply
```

只有确认待重试 ID 都属于本次清单时，再加 `--retry-failed`。使用现有 Embedding Key、模型和集合配置。日志记录批次和结果，密钥留在本机配置。

逐条验收改动记录。PostgreSQL 正文与官方底本一致，状态为「已入库」，`embedding_model` 为 `Qwen/Qwen3-VL-Embedding-8B`。Milvus 对应 ID 的 `search_text` 与脚本生成的文本一致，`indexed_at` 与 PostgreSQL 同一时间戳对齐。对照两个库的 N019 ID 集合，预期仍为同一组 54 个单元。

## 4. 放行并验证网页工具

校对和索引都通过后，只将 `N019` 的来源处理状态改为 `review_dataset`。用现有文号和预期旧状态限定更新，并确认只更新了一行。

```sql
UPDATE public.kb1_sources
SET status = 'review_dataset'
WHERE id = 'N019'
  AND doc_no = '京人社事业发〔2020〕12号'
  AND status = 'visual_transcription_needs_business_review'
  AND validity_status = '现行有效'
RETURNING id, doc_no, status, validity_status;
```

走网页注册的 `search_policy_sections` 做两项验收。

1. 用 `scope="北京人社"`、`source_id="N019"` 和检索词「职称评审委员会 聘任委员会」。确认返回官方口径的第七条、文件名和原件页码。
2. 用 `scope="集团内部"` 和 `source_id="N019"` 查询同一问题。确认返回 `no_verified_source`，确保地区政策按渠道生效。

通过后在项目状态及第一批处理结果中记下放行时间、修改条款 ID、官方出处、PostgreSQL 和 Milvus 对账结果。

## 故障和回滚

校对、重建或验收失败时，将来源置于 `visual_transcription_needs_business_review`，先恢复线上屏蔽。可从备份恢复此次改动的 Milvus 记录与 PostgreSQL 正文、状态和索引时间。恢复后核对两个库的 ID、文本和时间戳，再记录故障与剩余工作。若入库只完成一部分，保持来源待复核，核对成功批次后按清单重试。

实现参考 [入库脚本](../scripts/kb1/index_sections.py) · [网页检索工具](../career_agent/knowledge/policy_search.py)
