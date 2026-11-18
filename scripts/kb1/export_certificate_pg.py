"""Generate a clean, credential-free PostgreSQL load from the validated SQLite snapshot."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3


SCHEMA = {
    "file_assets": ("去重后的原始文件资产", """
        id text PRIMARY KEY,
        sha256 text NOT NULL UNIQUE,
        original_path text NOT NULL,
        size_bytes bigint NOT NULL CHECK (size_bytes >= 0)
    """),
    "sources": ("知识库文件来源，每份具体文件单独分配 ID", """
        id text PRIMARY KEY CHECK (octet_length(id) BETWEEN 1 AND 128),
        title text NOT NULL,
        original_path text NOT NULL,
        file_asset_id text NOT NULL REFERENCES public.kb1_file_assets(id),
        status text NOT NULL,
        channel text,
        organization text,
        region text,
        route text,
        source_kind text,
        applicable_year integer,
        effective_from date,
        effective_to date,
        validity_status text NOT NULL DEFAULT '待生效' CHECK (validity_status IN ('待生效','现行有效','失效')),
        replaces_source_id text REFERENCES public.kb1_sources(id),
        CHECK (replaces_source_id <> id),
        CHECK (effective_to >= effective_from)
    """),
    "sections": ("完整条款原文，一行对应一条向量索引", """
        id text PRIMARY KEY CHECK (octet_length(id) BETWEEN 1 AND 128),
        source_id text NOT NULL REFERENCES public.kb1_sources(id),
        chapter text,
        heading text,
        text text NOT NULL,
        source_locator_json jsonb,
        embedding_status text NOT NULL DEFAULT '待入库' CHECK (embedding_status IN ('待入库','已入库','失败','排除')),
        embedding_model text,
        indexed_at timestamptz,
        CHECK (embedding_status <> '已入库' OR (indexed_at IS NOT NULL AND embedding_model IS NOT NULL))
    """),
    "attachments": ("主文件和附件的关系，模板只关联、不索引正文", """
        id text PRIMARY KEY,
        main_source_id text NOT NULL REFERENCES public.kb1_sources(id),
        attachment_source_id text NOT NULL REFERENCES public.kb1_sources(id),
        display_name text,
        purpose text,
        download_policy text NOT NULL,
        attachment_no text,
        sort_order integer,
        content_indexed boolean NOT NULL
    """),
    "shared_rules": ("奖励目录的共同条件", """
        id text PRIMARY KEY,
        source_id text NOT NULL REFERENCES public.kb1_sources(id),
        text text NOT NULL,
        source_locator_json jsonb
    """),
    "rewards": ("奖励和费用报销记录，保留金额、周期、规则组合与出处", """
        id text PRIMARY KEY,
        source_id text NOT NULL REFERENCES public.kb1_sources(id),
        evidence_section_id text REFERENCES public.kb1_sections(id),
        alternate_source_id text REFERENCES public.kb1_sources(id),
        certificate_name text NOT NULL,
        category text NOT NULL,
        variant text,
        grade text,
        one_time_reward_yuan bigint,
        registration_yuan bigint,
        registration_period text,
        registration_raw text,
        usage_cap_yuan bigint,
        usage_period text,
        usage_raw text,
        reimbursement_cap_yuan bigint,
        leave_days integer,
        annual_cap_yuan bigint,
        per_project_yuan bigint,
        conditions_raw text,
        acquisition_raw text,
        grade_raw text,
        effective_start date,
        effective_end date,
        validity text,
        source_row integer,
        combination_alternatives jsonb,
        combination_logic text,
        title_hint_from_markdown boolean,
        title_hint_applies_to_whole_combination boolean,
        title_hint_note text,
        source_locator_json jsonb,
        CHECK (effective_end >= effective_start)
    """),
    "reward_rules": ("奖励记录和共同规则的多对多关联", """
        reward_id text REFERENCES public.kb1_rewards(id),
        rule_id text REFERENCES public.kb1_shared_rules(id),
        PRIMARY KEY (reward_id, rule_id)
    """),
    "mappings": ("职业资格与职称的对应关系，待确认冲突不作为默认依据", """
        id text PRIMARY KEY,
        source_id text NOT NULL REFERENCES public.kb1_sources(id),
        evidence_section_id text REFERENCES public.kb1_sections(id),
        alternate_source_id text REFERENCES public.kb1_sources(id),
        kind text NOT NULL,
        serial_no integer,
        certificate_name text NOT NULL,
        target_title_raw text,
        conditions_raw text,
        common_note_raw text,
        policy_basis_raw text,
        record_status text NOT NULL CHECK (record_status IN ('review_ready','held_source_conflict')),
        issuer_raw text,
        country_raw text,
        field_raw text,
        group_raw text,
        source_locator_json jsonb,
        alternate_locator_json jsonb
    """),
    "schedules": ("申报、审核和资历计算等事项及其日期精度", """
        id text PRIMARY KEY,
        source_id text NOT NULL REFERENCES public.kb1_sources(id),
        evidence_section_id text NOT NULL REFERENCES public.kb1_sections(id),
        event_type text NOT NULL,
        start_date date,
        end_date date,
        certainty text,
        precision_raw text,
        raw_date text,
        CHECK (end_date >= start_date)
    """),
    "issues": ("数据冲突、适用性缺口和其他待处理问题台账", """
        id text PRIMARY KEY,
        source_id text REFERENCES public.kb1_sources(id),
        code text NOT NULL,
        severity text NOT NULL,
        message text NOT NULL
    """),
}

FIELD_LABELS = dict(
    id="本表记录唯一标识", sha256="文件内容 SHA-256", original_path="原件位置；不是浏览器下载地址",
    size_bytes="原件大小，单位字节", title="文件标题，用于查询、展示和引用",
    file_asset_id="外键，指向去重后的文件资产", status="清洗处理状态；不能用于判断政策有效性",
    channel="原始资料分类", organization="原资料所属组织或来源分组", region="适用地区；空值不等于全国适用",
    route="职称申报渠道；证书名称、奖励主题不属于渠道", source_kind="制度办法、年度通知或其他资料类型",
    applicable_year="文件明确对应的申报年度；非文件发布日期", effective_from="明确的生效日",
    effective_to="明确的有效期截止日；不是报名截止日", validity_status="发布状态：待生效、现行有效、失效",
    replaces_source_id="明确被全量替换的旧来源 ID；补充材料不填写",
    source_id="具体文件 ID，外键关联来源", chapter="原文章名；不是父条款 ID", heading="条款标题",
    text="清洗后的完整原文", source_locator_json="原件定位：页码、段落、表格或工作表单元格",
    embedding_status="入索引状态：待入库、已入库、失败、排除；写入向量库成功后才标已入库",
    embedding_model="实际用于生成稠密向量的模型", indexed_at="与向量记录一致的入索引时间",
    main_source_id="主文件的来源 ID", attachment_source_id="附件的来源 ID",
    display_name="附件展示名称", purpose="附件用途", download_policy="附件下载限制", attachment_no="原文件的附件编号",
    sort_order="主文件下的展示次序", content_indexed="附件正文是否应进入索引",
    evidence_section_id="对应原文条款 ID", alternate_source_id="已对照的其他来源 ID",
    certificate_name="证书或资格名称", category="奖励类型", variant="专业或级别分支", grade="奖励级别",
    one_time_reward_yuan="一次性奖励，元", registration_yuan="注册补贴，元；见周期",
    registration_period="注册补贴周期", registration_raw="注册补贴原文", usage_cap_yuan="证书使用补贴上限，元",
    usage_period="使用补贴周期", usage_raw="使用补贴原文", reimbursement_cap_yuan="考试费报销上限，元",
    leave_days="奖励假天数", annual_cap_yuan="年度额度上限，元", per_project_yuan="每项目奖励金额，元",
    conditions_raw="限制与附加条件原文", acquisition_raw="取得证书要求原文", grade_raw="奖励等级或金额说明原文",
    effective_start="奖励适用开始日期", effective_end="奖励适用结束日期", validity="奖励资料的时效说明",
    source_row="原表数据行号", combination_alternatives="替代证书组合，组合内须同时满足、组合之间为或",
    combination_logic="证书组合的逻辑说明", title_hint_from_markdown="原资料是否标注职称对应提示",
    title_hint_applies_to_whole_combination="职称提示是否适用于组合全部证书", title_hint_note="职称提示限制和原文说明",
    reward_id="奖励记录 ID", rule_id="共同规则 ID", kind="职业资格对应表类别", serial_no="源对应表序号",
    target_title_raw="对应的职称原文；不表示自动满足申报条件", common_note_raw="对应表的共同说明",
    policy_basis_raw="对应表所列政策依据原文", record_status="可用于候选查询或来源冲突待确认",
    issuer_raw="发证或发布机构原文", country_raw="国家或地区原文", field_raw="领域原文", group_raw="所属清单原文",
    alternate_locator_json="对照载体的原件位置", event_type="时间事项类型", start_date="明确或可据原文归一的开始日期",
    end_date="明确或可据原文归一的结束日期", certainty="明确、推算或预计等确定性", precision_raw="日、月、月底等精度",
    raw_date="原文的日期表述", code="问题代码", severity="问题严重程度", message="问题和处理说明",
)

ANNUAL_NOTICES = {"N006": 2026, "N021": 2026, "N023": 2026, "N044": 2026}
REGION_NAMES = {"北京": "北京市", "北京海淀": "北京市海淀区", "河北": "河北省"}
SOURCE_KINDS = {
    **{key: "年度通知" for key in ANNUAL_NOTICES},
    "N001": "制度办法", "N002": "历史对照", "N003": "印发通知", "N007": "资格条件",
    "N008": "材料要求", "N013": "材料要求", "N017": "职称对应表", "N018": "职称对应表",
    "N019": "制度办法", "N020": "印发通知", "N022": "制度办法", "N024": "职称对应表",
    "N025": "职称对应表", "N026": "培训参考", "N027": "培训参考", "N028": "政策汇编",
    "N029": "政策汇编", "N030": "专业目录", "N031": "培训参考", "N032": "培训参考",
    "N033": "奖励目录", "N034": "奖励目录", "N035": "资格条件", "N036": "操作指引",
    "N039": "材料要求", "N041": "材料要求",
}


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


def literal(value, sql_type):
    if value is None:
        return f"NULL::{sql_type}"
    if sql_type == "boolean":
        if value not in (0, 1, False, True):
            raise ValueError(f"Invalid boolean: {value}")
        return "TRUE" if value else "FALSE"
    if sql_type in {"integer", "bigint"}:
        return f"{int(value)}::{sql_type}"
    if sql_type == "jsonb":
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    payload = base64.b64encode(str(value).encode("utf-8")).decode("ascii")
    return f"convert_from(decode('{payload}','base64'),'UTF8')::{sql_type}"


def columns_for(ddl):
    return [match.groups() for match in re.finditer(
        r"^\s*([a-z][a-z0-9_]*) (text|integer|bigint|jsonb|date|timestamptz|boolean)\b",
        ddl, re.MULTILINE)]


def values_for(table, row, versions, sections):
    row = dict(row)
    raw = json.loads(row.pop("data")) if "data" in row else {}
    row = {**raw, **row}
    source_metadata = row.get("metadata", {})
    if table == "sources":
        source = row["id"]
        number = int(source[1:])
        region = source_metadata.get("region")
        route = None
        if 1 <= number <= 16:
            route = "公司内部"
        elif number in {19, 20, 21, 22, 23, 28, 29, 30}:
            route = "地方人社"
        elif number in set(range(35, 42)) | {44}:
            route = "集团内部"
        row.update(channel=source_metadata.get("channel"), organization=source_metadata.get("organization"),
                   region="河北省" if 28 <= number <= 30 else REGION_NAMES.get(region, region), route=route,
                   source_kind=SOURCE_KINDS.get(source, "报名模板" if row["status"] == "opaque_attachment_no_body_parsing" else None),
                   applicable_year=ANNUAL_NOTICES.get(source), validity_status="待生效",
                   effective_from=source_metadata.get("effective_at"), effective_to=source_metadata.get("expires_at"),
                   replaces_source_id=None)
    elif table == "sections":
        status = row.get("record_status")
        row.update(embedding_status="排除" if status not in (None, "review_ready") else "待入库",
                   embedding_model=None, indexed_at=None, chapter=row.get("chapter") or None)
    elif table == "attachments":
        attachment_source = versions[row["attachment_version_id"]]
        if attachment_source != row["source_id"]:
            raise ValueError(f"Conflicting attachment origin: {row['id']}")
        row.update(main_source_id=versions[row["main_version_id"]], attachment_source_id=attachment_source)
    elif table == "mappings":
        row.update(serial_no=row.get("serial"), record_status=row.get("record_status") or "review_ready",
                   alternate_locator_json=row.get("alternate_locator"))
    elif table == "schedules":
        row["precision_raw"] = row.get("precision")
    if table in {"sections", "shared_rules", "mappings", "rewards"}:
        row["source_locator_json"] = row.get("locator")
    if row.get("evidence_section_id") and sections.get(row["evidence_section_id"]) != row["source_id"]:
        raise ValueError(f"Evidence section does not match source: {table}.{row['id']}")
    return row


def relation(table):
    return f"public.kb1_{table}"


def guard(condition, error):
    return f"DO $$ BEGIN IF {condition} THEN RAISE EXCEPTION {quoted(error)}; END IF; END $$"


def write_sql(path, statements):
    path.write_text(";\n".join(statements) + ";\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.latest.read_text(encoding="utf-8"))
    source = Path(manifest["database"]).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != manifest["database_sha256"] or manifest["validation"]["checks_failed"]:
        raise ValueError("Source is not the validated snapshot")
    root = Path(__file__).resolve().parents[2] / "data" / "material_review"
    out = args.output.resolve()
    if not out.is_relative_to(root) or out.exists():
        raise ValueError("Choose a new path under ignored data/material_review")
    db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    versions = dict(db.execute("SELECT version_id,id FROM sources"))
    sections = dict(db.execute("SELECT id,source_id FROM sections"))
    objects = ("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
               "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S') "
               "AND c.relname ~* '^(v_)?kb1_'")
    target = guard("current_database() <> 'career'", "Expected independent career database")
    table_ddl = [target, "SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '40s'",
                 "SELECT pg_advisory_xact_lock(hashtext('career-kb1-postgresql-import'))",
                 guard(f"EXISTS ({objects})", "Target has existing KB1 objects; nothing overwritten")]
    insertions, verify, counts = [], [target], {}
    for table, (description, ddl) in SCHEMA.items():
        name = relation(table)
        columns = columns_for(ddl)
        names = ",".join(key for key, _ in columns)
        table_ddl.extend([
            f"CREATE TABLE {name} ({ddl.strip()},\ncreated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            f"COMMENT ON TABLE {name} IS {quoted(description)}",
            f"COMMENT ON COLUMN {name}.created_at IS '记录入库时间'",
        ])
        table_ddl.extend(f"COMMENT ON COLUMN {name}.{key} IS {quoted(FIELD_LABELS[key])}" for key, _ in columns)
        rows = [values_for(table, row, versions, sections) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        counts[table] = len(rows)
        expected_count = manifest["summary"]["counts"].get(table)
        if expected_count is not None and expected_count != counts[table]:
            raise ValueError(f"Unexpected count for {table}")
        rows_sql = ",\n".join("(" + ",".join(literal(row.get(key), sql_type) for key, sql_type in columns) + ")" for row in rows)
        insertions.append(f"INSERT INTO {name} ({names}) VALUES\n{rows_sql}")
        diff = (f"SELECT * FROM (VALUES\n{rows_sql}\n) AS expected ({names}) "
                f"EXCEPT ALL SELECT {names} FROM {name}")
        check_count = f"(SELECT count(created_at) FROM {name}) <> {counts[table]}"
        # Base64-encoded text keeps internal semicolons out of the package's SQL statement delimiters.
        verify.append(guard(f"{check_count} OR EXISTS ({diff})", f"Data or count verification failed: {name}"))
    db.close()
    table_ddl.extend([
        "CREATE INDEX kb1_sources_search_scope_idx ON public.kb1_sources(validity_status,route,region,applicable_year)",
        "CREATE INDEX kb1_sections_source_idx ON public.kb1_sections(source_id)",
        "CREATE INDEX kb1_attachments_main_idx ON public.kb1_attachments(main_source_id,sort_order)",
        "CREATE INDEX kb1_mappings_lookup_idx ON public.kb1_mappings(certificate_name,record_status)",
        "CREATE INDEX kb1_rewards_lookup_idx ON public.kb1_rewards(certificate_name)",
        "CREATE INDEX kb1_schedules_source_idx ON public.kb1_schedules(source_id,event_type)",
    ])
    expected_objects = ",".join(quoted("kb1_" + table) for table in SCHEMA)
    verify.extend([
        guard(f"EXISTS (SELECT 1 FROM ({objects}) found WHERE relname NOT IN ({expected_objects}))",
              "Unexpected KB1 table or view"),
        guard("EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name LIKE 'kb1_%' "
              "AND column_name IN ('data','version_id','main_version_id','attachment_version_id'))", "Legacy columns should not exist"),
    ])
    if sum(counts.values()) != 1207 or len(counts) != 10:
        raise ValueError(f"Unexpected migration scope: {counts}")
    out.mkdir()
    write_sql(out / "schema.sql", table_ddl)
    write_sql(out / "load.sql", table_ddl + insertions)
    write_sql(out / "verify.sql", verify)
    report = {"database": "career", "schema": "public", "source_sha256": digest,
              "tables": counts, "total_rows": sum(counts.values()), "views": 0,
              "initial_validity_status": "待生效", "vectors_generated": 0}
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
