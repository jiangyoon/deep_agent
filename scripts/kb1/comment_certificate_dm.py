"""Generate Chinese database field comments for the first-batch Dameng schema."""
import argparse
import base64
import json
from pathlib import Path
import re


COMMON={
    'id':'记录唯一标识；本批生成的业务数据主键，不是员工编号。',
    'source_id':'来源文件ID；外键关联CAREER.KB1_SOURCES.ID，用于回溯原文件。',
    'version_id':'文件版本ID，格式为来源ID@文件SHA256前12位；KB1_SOURCES内唯一，附件关联引用此版本。',
    'title':'源文件标题，保留原文件名称语义；不据标题推断政策生效时间。',
    'original_path':'原文件在本机的存放路径；仅保存路径，不包含文件二进制，也不是浏览器下载URL。',
    'sha256':'原文件字节内容的SHA256摘要，64位十六进制，用于原件校验及内容去重。',
    'size_bytes':'原文件大小，单位字节。',
    'data':'SQLite审核快照中的完整JSON记录，CLOB保存；展开列便于查询，JSON保留原始记录和附加信息，不是原文件二进制。',
    'source_locator_json':'原文位置JSON，可为对象或数组；PDF物理页从1起、可含印刷页；Word段落及表格索引从0起、表内行从1起；Excel记录工作表及A1单元格范围。',
    'region':'资料的地区标签；NULL表示未设定，不能据此认定适用于所有地区，也不是员工社保所在地。',
    'organization':'资料来源组织或适用上下文，如公司、集团及公司、北京市人社政策资料；不是员工所属组织。',
    'channel':'资料所属咨询或评审渠道，如集团高级工程师初评、人社职称评价、证书奖励；用于区分政策上下文。',
    'certificate_name':'资格证书或证书组合的原始名称，可能含专业方向及原表换行；不同证书不可仅因名称近似而合并。',
    'conditions_raw':'原文附加条件或限制；必须与记录一起返回，不能只按金额或对应级别作资格判断。',
    'record_status':'记录核对状态：review_ready表示可参与本地审核查询，不代表政策已获业务确认；held_source_conflict表示来源冲突，默认查询应隔离。',
    'start_date':'事项开始日期，文本格式YYYY-MM-DD；NULL表示未给出或不能确定，不推测补齐。',
    'end_date':'事项截止日期，文本格式YYYY-MM-DD；NULL表示预计、待通知或未给出精确日期，应结合精度及原文解释。',
    'text':'完整原文或可检索文本，CLOB保存；与来源位置关联，不能脱离适用范围解释。',
}
SPECIFIC={
    'file_assets':{
        'id':'去重后的文件资产ID，格式sha256:完整文件摘要；相同内容文件共用资产记录。'},
    'sources':{
        'id':'本批来源文件ID，如N001；同一来源的具体文件版本另见VERSION_ID。',
        'status':'文件处理状态：review_dataset为审核数据；planned_later为后续批次；opaque_attachment_no_body_parsing为不解析模板；history_metadata_only为历史元信息；excluded为排除。其他值描述视觉转录待复核或跨格式载体合并，不等同政策有效性。',
        'file_asset_id':'文件资产外键，关联KB1_FILE_ASSETS.ID；重复内容共享资产，仍保留各自的来源及附件关系。',
        'included_in_batch1':'是否属于第一批处理范围：1是，0否；0的文件只登记元信息，不代表正文已处理。'},
    'sections':{
        'heading':'内容单元的标题或条款开头，保留章节语义；不是摘要生成的资格结论。',
        'text':'完整条款、材料要求或结构化记录的完整条件文本；检索子片段命中后可通过此记录取回上下文。',
        'record_status':'内容单元状态：review_ready表示可审核查询；held_transcription_gap表示转录有缺口、不得生成默认检索片段。可查询不代表政策已获业务审核。',
        'chapter':'所属章节或附表标题；NULL或空串表示源文件未明确章节，不补造。'},
    'chunks':{
        'section_id':'父内容单元外键，关联KB1_SECTIONS.ID；命中检索片段后应取回父条款，避免遗漏条件或例外。',
        'text':'待向量化的检索文本，已携带标题、条件及必要共同说明；不等同完整原文件。',
        'embedding_status':'向量生成状态；本批全部为pending_customer_embedding，表示等待接入客户Embedding。',
        'embedding':'向量预留字段，当前全部NULL；现为CLOB占位，不是达梦原生向量列，也未建向量索引。',
        'embedding_model':'生成向量所用模型标识，当前NULL；后续生成时必须记录，不能混用不同模型的向量。'},
    'rewards':{
        'category':'奖励类别原文，如单证奖励、一人多证奖励、报销考试费、年度专项奖励。',
        'variant':'从原表拆分的专业、证书级别或适用分支；NULL表示本记录未另外拆分，并非适用于所有专业。',
        'grade':'解析后的奖励级别：S、A、A-、B、C；NULL需结合GRADE_RAW判断，不能直接理解为0元。',
        'one_time_reward_yuan':'按目录奖励级别映射的一次性奖励金额，单位元；NULL表示无此解析值，须看GRADE_RAW及原文条件。',
        'registration_yuan':'注册补贴数额，单位元，周期见REGISTRATION_PERIOD；NULL不等于0，需看REGISTRATION_RAW。',
        'usage_cap_yuan':'证书使用补贴上限，单位元，周期见USAGE_PERIOD；不是保证发放金额，NULL需查看USAGE_RAW。',
        'reimbursement_cap_yuan':'考试费用报销上限，单位元/证；仅适用于原文指定证书及条件，不代表已提供完整报销办理流程。',
        'leave_days':'该奖励级别对应的奖励假天数；年度累计上限和叠加限制见关联的全表共同规则。',
        'registration_period':'注册补贴周期，原文单位月或年；必须与REGISTRATION_YUAN一起使用。',
        'usage_period':'使用补贴上限周期，原文单位月或年；必须与USAGE_CAP_YUAN一起使用。',
        'registration_raw':'注册补贴单元格原文，保留无、空白、破折号和金额单位，用于解释未解析值。',
        'usage_raw':'使用补贴上限单元格原文，保留无、空白、破折号和金额单位。',
        'acquisition_raw':'证书取得方式原文，已按真实合并单元格恢复共享内容；不是员工实际取得证书的履历。',
        'grade_raw':'一次性奖励级别或报销/专项金额说明原文，保留多分支及未解析内容。',
        'annual_cap_yuan':'年度专项奖励累计上限，单位元；适用期间见EFFECTIVE_START、EFFECTIVE_END及原文。',
        'per_project_yuan':'按项目计算的专项奖励单价，单位元/项目；仍受年度上限及适用期限制。',
        'effective_start':'本条奖励原文明示的适用开始日期，YYYY-MM-DD；NULL表示未明示，不据目录年份补造。',
        'effective_end':'本条奖励原文明示的适用结束日期，YYYY-MM-DD；2025年专项记录为2025-12-31，不自动延用。',
        'validity':'有效性标记：explicit_2025_only为明确仅限2025年；2025_catalog_current_validity_unconfirmed为2025年版目录当前是否沿用待确认。',
        'source_row':'原奖励Excel的物理行号，从1开始；同一行可因专业或奖励级别拆成多条记录。',
        'shared_rule_ids_json':'关联的共同奖励规则ID数组；实际关联表为KB1_REWARD_RULES，查询奖励必须带上这些规则。',
        'combination_alternatives_json':'多证组合候选JSON二维数组；外层为可选组合OR，内层为同时具备AND；证书名称内更细的或关系仍以原文为准，不作自动资格审批。',
        'title_hint_note':'原表蓝色字体的业务含义及局部着色提示；组合内部分文字着色不能推广为所有证书均对应职称。'},
    'shared_rules':{
        'text':'奖励目录A2单元格中的共同说明原文，如年度奖励假上限、统考成绩要求等；包含原说明行，不可遗漏。'},
    'reward_rules':{
        'reward_id':'奖励记录外键，关联KB1_REWARDS.ID；与RULE_ID组成联合主键。',
        'rule_id':'共同规则外键，关联KB1_SHARED_RULES.ID；与REWARD_ID组成联合主键。'},
    'mappings':{
        'kind':'对应表类型：domestic为国内资格对应（含集团附件上下文），international为国际资格对应。',
        'target_title_raw':'可对应或可聘职称的原文，可能同时包含资格级别、取得时间及多个选项；不是已确认的员工职称。',
        'conditions_raw':'对应条件原文，含对应关系、全表附注、学历年限限制等；有对应关系不代表满足全部高一级申报条件。',
        'serial_no':'原对应表序号；某些序号有多个专业分支，不能把最大序号当成记录总数。',
        'policy_basis_raw':'原表列出的政策依据及文号；NULL表示原表未在该字段提供，不自行编造依据。',
        'issuer_raw':'资格证书颁发机构或国内认证机构原文；不是本知识库的数据发布人。',
        'country_raw':'资格证书颁发机构的国家或地区原文；不是本条职称政策的适用地区。',
        'field_raw':'资格所属领域原文，如金融、科技、信息技术；不直接等同职称专业目录。',
        'group_raw':'原国际资格清单的分组说明，保留急需紧缺、境外、出海等类别及原文级别提示。',
        'alternate_source_id':'已核对的另一载体来源ID，逻辑关联KB1_SOURCES.ID但未建外键；存在冲突时不宣称载体等价。'},
    'schedules':{
        'event_type':'时间事项名称，如个人申报、单位审核、材料提交或资历计算；不同含义的截止日不能混用。',
        'certainty':'日期确定性及解释，如明确、年度上下文补全年份、预计、以机构通知为准；不确定日期不补造精确日。',
        'evidence_section_id':'时间原文所在条款外键，关联KB1_SECTIONS.ID，可取回完整办理背景。',
        'precision_raw':'日期精度：day为具体日，day_range为日区间，month为月，month_start为月初，month_end为月底，notification为待机构通知。',
        'raw_date':'原文中的日期表达，如11月底、12月初、通知的规定时间内；保留不确定性，用于解释空日期字段。'},
    'attachments':{
        'main_version_id':'主文件具体版本外键，关联KB1_SOURCES.VERSION_ID；不能只按通知名称挂附件，以免跨年度错配。',
        'attachment_version_id':'附件具体版本外键，关联KB1_SOURCES.VERSION_ID；附件二进制仍在文件存储中。',
        'display_name':'该主文件关系下的附件展示名称，保留附件序号及原名称；相同文件可在不同通知下有不同名称。',
        'purpose':'附件用途：报名模板、知识性附件、材料包内附件、修订对比资料等；不因名为附件就一律解析或排除。',
        'download_policy':'下载策略标记；当前requires_authenticated_internal_access_and_release_review表示须登录、内部权限及发布检查，不代表已实现网页下载服务。',
        'attachment_no':'附件在通知或材料包中的原序号/层级路径，如1/3、3/3-1；不是全局唯一ID。',
        'sort_order':'附件排序用数值，当前由本批来源顺序生成；不应代替原附件序号。',
        'content_indexed':'是否生成知识正文：1为知识性内容参与索引，0为未解析模板或历史资料；与是否允许下载是不同维度。',
        'attachment_source_id':'便于查看的附件来源ID，逻辑关联KB1_SOURCES.ID；真正受外键约束的是ATTACHMENT_VERSION_ID。'},
    'issues':{
        'code':'机器可识别的问题分类代码，如catalog_validity、international_carrier_difference、visual_recovery；不是已经解决的状态。',
        'severity':'问题级别：info为说明、review为待核、blocking为阻止对应内容发布；不代表HR审核结论。',
        'message':'问题的具体说明或待核记录ID；详细差异也可查看完整DATA JSON及冲突记录视图。'},
}
AUDIT={
    'run_id':'迁移运行标识，由本批审核数据库哈希生成；审计表主键。',
    'source_sha256':'本次迁移所用完整SQLite数据库文件的SHA256，不是单个政策原件的哈希。',
    'batch_id':'材料批次标识，本批为0848fe37e7ed，来自压缩包哈希前12位。',
    'migrated_at':'迁移提交前写入的数据库服务器时间；不代表政策发布日期或生效日期。',
    'row_counts':'各源数据表迁入行数的审计文本；不包含本审计表自身。',
    'verified':'迁移校验标志，Y表示写入事务内全字段往返比对通过；独立连接复核结果另见迁移报告。',
}
TABLE_COMMENTS={
    'file_assets':'原始文件资产元信息；相同哈希复用资产，不在此表存原件二进制。',
    'sources':'来源文件及具体版本，含处理范围、状态、元信息和完整记录。',
    'sections':'完整政策内容单元及来源位置，用于取回检索片段的父条款上下文。',
    'chunks':'待向量化检索文本；本批没有生成向量或原生向量索引。',
    'rewards':'证书奖励结构化记录；金额须结合专业分支、周期、共同条件及目录有效性。',
    'shared_rules':'奖励目录的共同说明，需与具体奖励记录一起使用。',
    'reward_rules':'具体奖励记录与全表共同规则的多对多外键关系。',
    'mappings':'资格证书与职称对应记录；保留不同适用上下文及隔离待核项。',
    'schedules':'年度时间事项及原文依据，区分报名、审核、资历计算和预计日期。',
    'attachments':'主文件具体版本与附件具体版本的关联；报名模板不解析正文，只关联供下载。',
    'issues':'数据处理与政策待复核问题，不是政策正文或员工事实。',
    'migration_info':'本批数据迁移的源快照、时间、记录数及校验审计。',
}
VIEW_COMMENTS={
    'V_KB1_REWARDS':'证书奖励中文查看视图；保留金额单位、条件及目录有效性，不等同资格审批。',
    'V_KB1_TITLE_MAPPINGS':'职称对应中文查看视图，仅包含review_ready记录，默认排除来源冲突。',
    'V_KB1_MAPPING_CONFLICTS':'待核职称对应记录，仅供维护者核对，不用于默认政策答复。',
    'V_KB1_POLICY_TEXT':'完整政策条款中文查看视图；技术可查询不代表业务核验已完成。',
    'V_KB1_SCHEDULES':'年度报名、审核及计算日期中文查看视图。',
    'V_KB1_ATTACHMENTS':'主文件版本及附件关系中文查看视图；本机路径不是已上线下载URL。',
    'V_KB1_SOURCES':'来源文件中文查看视图；是否第一批为0的文件尚未处理正文。',
    'V_KB1_ISSUES':'数据及政策待核问题中文查看视图。',
    'V_KB1_COUNTS':'11张业务数据表的记录数，不包含迁移审计表和视图自身。',
}


def generate(package):
    summary=json.loads((package/'export_summary.json').read_text(encoding='utf-8'))
    comments=[]
    layouts={}
    def add(kind,table,column,text):
        text=text.replace(';','；')
        comments.append(dict(kind=kind,table=table,column=column,comment=text))
    for table,layout in summary['layouts'].items():
        layouts['KB1_'+table.upper()]={}
        add('T','KB1_'+table.upper(),'',TABLE_COMMENTS[table])
        for col in layout['columns']:
            explanation=SPECIFIC.get(table,{}).get(col,COMMON.get(col))
            if explanation is None:
                raise ValueError(f'Missing field explanation: {table}.{col}')
            add('C','KB1_'+table.upper(),col.upper(),explanation)
            layouts['KB1_'+table.upper()][col.upper()]=explanation
    add('T','KB1_MIGRATION_INFO','',TABLE_COMMENTS['migration_info'])
    for col,explanation in AUDIT.items(): add('C','KB1_MIGRATION_INFO',col.upper(),explanation)
    sql=(package/'views.sql').read_text(encoding='utf-8')
    for statement in sql.split(';\n'):
        if not statement.strip(): continue
        found=re.search(r'CREATE VIEW "\{\{SCHEMA\}\}"\."([^"]+)" AS SELECT (.*?) FROM ',statement,re.S)
        if not found: raise ValueError('Unrecognized view SQL')
        view,select=found.groups()
        add('V',view,'',VIEW_COMMENTS[view])
        if view=='V_KB1_COUNTS':
            add('C',view,'数据表','源业务表名称，不包含模式前缀及KB1_前缀。')
            add('C',view,'记录数','该业务表当前总行数；包含隔离记录的基础表计数，不等同默认可查询数量。')
            continue
        default={
            'V_KB1_REWARDS':'rewards','V_KB1_TITLE_MAPPINGS':'mappings','V_KB1_MAPPING_CONFLICTS':'mappings',
            'V_KB1_POLICY_TEXT':'sections','V_KB1_SCHEDULES':'schedules','V_KB1_ATTACHMENTS':'attachments',
            'V_KB1_SOURCES':'sources','V_KB1_ISSUES':'issues'}[view]
        for alias,col,label in re.findall(r'(?:(\w+)\.)?"([^"]+)" AS "([^"]+)"',select):
            table='sources' if view=='V_KB1_ATTACHMENTS' and alias in ('m','s') else default
            add('C',view,label,layouts['KB1_'+table.upper()][col])
    statements=[]
    for entry in comments:
        object_name='"CAREER"."'+entry['table']+'"'
        if entry['kind']=='C': object_name+='."'+entry['column']+'"'
        kind={'T':'TABLE','V':'VIEW','C':'COLUMN'}[entry['kind']]
        text=entry['comment'].replace("'","''")
        if len(text)>3500: raise ValueError('Comment too long')
        statements.append(f"COMMENT ON {kind} {object_name} IS '{text}'")
    (package/'column_comments.sql').write_text(';\n'.join(statements)+';\n',encoding='utf-8')
    (package/'column_comments.tsv').write_text(''.join('\t'.join([r['kind'],r['table'],r['column'],
        base64.b64encode(r['comment'].encode('utf-8')).decode('ascii')])+'\n' for r in comments),encoding='utf-8')
    (package/'column_comments.json').write_text(json.dumps(comments,ensure_ascii=False,indent=2),encoding='utf-8')
    result=dict(objects=sum(r['kind'] in ('T','V') for r in comments),
                table_columns=sum(r['kind']=='C' and r['table'].startswith('KB1_') for r in comments),
                view_columns=sum(r['kind']=='C' and r['table'].startswith('V_KB1_') for r in comments))
    print(json.dumps(result,ensure_ascii=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('package',type=Path)
    generate(p.parse_args().package)
