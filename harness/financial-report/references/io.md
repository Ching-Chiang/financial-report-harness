# 接口与数据约定

## 命令

`doctor` 检查辅助依赖与 `MINERU_API_TOKEN` 是否存在，不访问接口、不显示 Token。

`parse --pdf <文件> --output <新目录> [--pages 58-61,63-65] [--model vlm|pipeline] [--timeout 900] [--poll-interval 5] [--resume]`

页码从 1 开始，指原 PDF 物理页码。脚本生成所选页的片段上传 MinerU，再把返回的零基页索引映射回原文。不会把片段页码或页脚印刷页码当成原 PDF 页码。

`validate --input <candidates.json> --output <quality.json>`

`export --input <candidates.json> --output <新目录>`

退出码：0 成功/可放行，2 输入、配置或接口错误，3 数据需要复核。parse 成功只表示解析完成，不表示财务数据已经合格。

## 解析产物

- `source.pdf`：原 PDF 副本。
- `input/selected.pdf`：上传片段。
- `parse-manifest.json`：源文件哈希、总页数、页码映射、API 模型名称、batch_id、状态和证据哈希。不保存 Token 或签名上传/下载 URL。
- `result.zip`、`mineru/`：API 返回的原始解析结果。
- `evidence.json`：证据块的 block_id、原 PDF 页码、原始结构和可检索文本。

超时后使用 --resume 查询已有 batch_id。首次提交失败且未取得 ID 时，不能证明服务端是否创建任务，不自动重复提交。上传未完成的任务不能通过反复轮询补传；检查失败原因后再决定是否新建任务。

## 候选格式

完整结构见 `candidate.schema.json`。源路径相对 candidates.json，通常为 `source.pdf` 和 `parse-manifest.json`，便于整体迁移。

每条 `records` 代表一个目标表、一个期间及一种口径。本期、上期分别建记录，保留原始列标题。`metadata` 使用 SQL 原字段名，值为字符串或 null，不放金额。

每个金额字段包含 `field`、`raw_label`、`raw_value`、`source_unit`、`pdf_page`、`block_id`、`confidence`、`mapping_status`、`mapping_basis`、`review_status`。清洗值由脚本通过 Decimal 从原始金额计算，不使用浮点数作为原始输入。

- mapping_status：candidate / confirmed / ambiguous。
- review_status：unreviewed / verified / needs_review。
- business_rules：status 为 pending / confirmed，reference 指向真实业务确认依据，amount_tolerance 默认字符串 `0.01`。
- unmatched_items：保存未处理项目及原因，至少有 reason。

证据文本包含一个数字不等于它属于正确列，智能体必须核实行列、单位与期间，不能仅凭文本匹配标记 verified。

## 导出

始终保存候选、质量报告及异常清单。整批通过才生成正式三表 CSV、事务包裹的 INSERT 和导出清单。输出目录必须不存在，避免混入以前的正式结果。

CSV：UTF-8、标准逗号及双引号转义，null 用 `\N`，零保留为数字，金额四位小数。SQL 不包含 DELETE、REPLACE 或忽略冲突操作，不直接执行。

全部三表是否覆盖，以及本期/上期是否齐全，须结合 quality-report.coverage 和任务要求判断；少量合法记录通过不能自动代表整份年报完成。
