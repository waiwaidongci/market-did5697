# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、复核和报表分发动作。

# 002 版本化批次与报告日重试

## `datasets`：批次版本链

同一批次名称下内容只增不改，每一版都是不可变快照。

- `revision`：批次内从 1 递增的版本号，与 `name` 组成唯一约束。
- `supersedes_id`：本版接续的上一版；首版为空。旧版行数据保留，可回查。
- `checksum`：**合并后**快照的指纹，运行锁定它；不设唯一约束，允许把内容改回旧值时生成新版本。
- `upload_checksum`：**本次上传行**的指纹，仅用于审计，不设唯一约束；这样把某条记录改回旧值仍会生成可追溯的新版本。重复上传同一批内容（即使只是补报子集）由合并比对判定为无变化，不新增版本。
- `added_rows` / `updated_rows`：本版相对上一版新增、更正的记录数。
- 晚到修订合并到上一版：缺失市场日补齐，同一市场日的新值覆盖旧值，任何已有记录都不会被删除。

## `runs`：锁定输入快照

- `dataset_revision`、`snapshot_checksum`、`snapshot`：排队时冻结的数据版本、指纹和行数据。之后导入的新版本不会改变既有运行及其导出。
- `report_day`：运行对应的报告日（北京时间）。`(schedule_id, report_day)` 唯一，保证每个计划每天至多一条运行。

## `schedules`：报告日与重试

- `report_day`：尚未结清的报告日。仅在该日运行成功后才推进一天；失败时保留原报告日重试。
- `retries`：同一报告日的重试次数。
- `slot_at`：每日计划的名义执行时刻，重试不会移动它，避免补跑导致时间漂移。

## 升级说明

全新环境由 `Base.metadata.create_all` 直接建出新结构。已有库需先执行：

```sql
ALTER TABLE datasets
  ADD COLUMN upload_checksum CHAR(64) NOT NULL DEFAULT '',
  ADD COLUMN revision INT NOT NULL DEFAULT 1,
  ADD COLUMN supersedes_id INT NULL,
  ADD COLUMN added_rows INT NOT NULL DEFAULT 0,
  ADD COLUMN updated_rows INT NOT NULL DEFAULT 0;
UPDATE datasets SET upload_checksum = checksum WHERE upload_checksum = '';
ALTER TABLE datasets
  ADD CONSTRAINT uq_datasets_name_revision UNIQUE (name, revision);
-- 若旧库已有 uq_datasets_name_checksum，可保留；新版本不再依赖它去重。

ALTER TABLE schedules
  ADD COLUMN slot_at DATETIME NULL,
  ADD COLUMN report_day DATE NULL,
  ADD COLUMN retries INT NOT NULL DEFAULT 0,
  ADD COLUMN created_at DATETIME NULL;

ALTER TABLE runs
  ADD COLUMN dataset_revision INT NOT NULL DEFAULT 1,
  ADD COLUMN snapshot_checksum CHAR(64) NOT NULL DEFAULT '',
  ADD COLUMN snapshot JSON NULL,
  ADD COLUMN report_day DATE NULL;
ALTER TABLE runs
  ADD CONSTRAINT uq_runs_schedule_day UNIQUE (schedule_id, report_day);
```

历史运行没有 `snapshot` 时，worker 回退读取当前批次内容；它们是升级前的产物，无法追溯到原版本。新运行一律带快照。
