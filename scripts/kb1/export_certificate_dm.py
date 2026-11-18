"""Prepare a credential-free Dameng JDBC migration package from validated SQLite."""
import argparse
import base64
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


TABLES=['file_assets','sources','sections','chunks','rewards','shared_rules','reward_rules','mappings','schedules','attachments','issues']
EXTRA={
    'sources':{'included_in_batch1':('I','included_in_batch1'),'region':('T','metadata.region'),
               'organization':('T','metadata.organization'),'channel':('T','metadata.channel')},
    'sections':{'source_locator_json':('T','locator'),'record_status':('T','record_status'),
                'chapter':('T','chapter'),'region':('T','metadata.region'),'organization':('T','metadata.organization')},
    'chunks':{'source_locator_json':('T','locator'),'embedding':('T','embedding'),'embedding_model':('T','embedding_model')},
    'rewards':{'leave_days':('I','leave_days'),'registration_period':('T','registration_period'),
               'usage_period':('T','usage_period'),'registration_raw':('T','registration_raw'),
               'usage_raw':('T','usage_raw'),'conditions_raw':('T','conditions_raw'),
               'acquisition_raw':('T','acquisition_raw'),'grade_raw':('T','grade_raw'),
               'annual_cap_yuan':('I','annual_cap_yuan'),'per_project_yuan':('I','per_project_yuan'),
               'effective_start':('T','effective_start'),'effective_end':('T','effective_end'),
               'validity':('T','validity'),'source_row':('I','source_row'),
               'shared_rule_ids_json':('T','shared_rule_ids'),'combination_alternatives_json':('T','combination_alternatives'),
               'title_hint_note':('T','title_hint_note'),'source_locator_json':('T','locator')},
    'mappings':{'serial_no':('I','serial'),'policy_basis_raw':('T','policy_basis_raw'),
                'record_status':('T','record_status'),'issuer_raw':('T','issuer_raw'),
                'country_raw':('T','country_raw'),'field_raw':('T','field_raw'),
                'group_raw':('T','group_raw'),'region':('T','metadata.region'),
                'organization':('T','metadata.organization'),'channel':('T','metadata.channel'),
                'source_locator_json':('T','locator'),'alternate_source_id':('T','alternate_source_id')},
    'schedules':{'precision_raw':('T','precision'),'raw_date':('T','raw_date'),
                 'region':('T','metadata.region'),'channel':('T','metadata.channel')},
    'attachments':{'attachment_no':('T','attachment_no'),'sort_order':('I','sort_order'),
                   'content_indexed':('I','content_indexed'),'attachment_source_id':('T','source_id')},
    'shared_rules':{'source_locator_json':('T','locator')},
}


def field(obj,path):
    for key in path.split('.'):
        if not isinstance(obj,dict): return None
        obj=obj.get(key)
    if isinstance(obj,(dict,list)):
        return json.dumps(obj,ensure_ascii=False,separators=(',',':'))
    return obj


def encoded(value,kind):
    if value is None: return 'N'
    if kind=='I': return 'I:'+str(int(value))
    return 'T:'+base64.b64encode(str(value).encode('utf-8')).decode('ascii')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--latest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    manifest=json.loads(a.latest.read_text(encoding='utf-8'))
    source=Path(manifest['database'])
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash!=manifest['database_sha256'] or manifest['validation']['checks_failed']:
        raise ValueError('Source is not the validated snapshot')
    out=a.output.resolve()
    allowed=Path(__file__).resolve().parents[2] / 'data' / 'material_review'
    if not out.is_relative_to(allowed) or out.exists():
        raise ValueError('Choose a new package directory under ignored material_review')
    out.mkdir(parents=True)
    (out/'rows').mkdir()
    db=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    metadata=[]
    ddls=[]
    fks=[]
    counts={}
    layouts={}
    shorttext={'id','version_id','file_asset_id','sha256','source_id','section_id','reward_id','rule_id',
               'main_version_id','attachment_version_id','attachment_source_id','evidence_section_id',
               'code','severity','status','embedding_status','kind','grade','event_type','certainty',
               'start_date','end_date','record_status','effective_start','effective_end','validity',
               'region','organization','channel','registration_period','usage_period','precision_raw',
               'attachment_no','embedding_model','alternate_source_id','country_raw'}
    def table(t): return '"{{SCHEMA}}"."KB1_'+t.upper()+'"'
    for t in TABLES:
        columns=list(db.execute(f'PRAGMA table_info("{t}")'))
        names=[r['name'] for r in columns]
        kinds=['I' if r['type'].upper()=='INTEGER' else 'T' for r in columns]
        pk=[r['name'] for r in sorted(columns,key=lambda r:r['pk']) if r['pk']]
        data=[dict(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY '+','.join('"'+k+'"' for k in pk))]
        extra=EXTRA.get(t,{})
        extra={k:v for k,v in extra.items() if k not in names}
        for row in data:
            obj=json.loads(row['data']) if 'data' in row else {}
            for name,(kind,path) in extra.items():
                value=field(obj,path)
                if name=='record_status' and value is None: value='review_ready'
                row[name]=value
        for name,(kind,_) in extra.items():
            names.append(name)
            kinds.append(kind)
        modes=['S' if kind=='I' or name in shorttext else 'L' for name,kind in zip(names,kinds)]
        for row in data:
            for name,kind,mode in zip(names,kinds,modes):
                if mode=='S' and kind=='T' and row[name] is not None and len(str(row[name]).encode('utf-8'))>512:
                    raise ValueError(f'Short-column bound requires review: {t}.{name}')
        counts[t]=len(data)
        payload=''.join('\t'.join(encoded(row[name],kind) for name,kind in zip(names,kinds))+'\n' for row in data)
        path=out/'rows'/f'{t}.tsv'
        path.write_text(payload,encoding='utf-8',newline='\n')
        checksum=hashlib.sha256(path.read_bytes()).hexdigest()
        metadata.append('\t'.join(['T',t,str(len(data)),checksum]))
        for name,kind,mode in zip(names,kinds,modes):
            metadata.append('\t'.join(['C',t,name,kind,mode]))
        for key in pk: metadata.append('\t'.join(['P',t,key]))
        layouts[t]=dict(columns=names,kinds=kinds,modes=modes,primary_key=pk)
        parts=[]
        required={r['name'] for r in columns if r['notnull'] or r['pk']}
        for name,kind,mode in zip(names,kinds,modes):
            dtype='BIGINT' if kind=='I' else ('VARCHAR(512)' if mode=='S' else 'CLOB')
            parts.append('"'+name.upper()+'" '+dtype+(' NOT NULL' if name in required else ''))
        parts.append('CONSTRAINT "PK_KB1_'+t.upper()+'" PRIMARY KEY ('+','.join('"'+k.upper()+'"' for k in pk)+')')
        unique_num=0
        for idx in db.execute(f'PRAGMA index_list("{t}")'):
            if not idx['unique'] or idx['origin']=='pk': continue
            members=[r['name'] for r in db.execute('PRAGMA index_info("'+idx['name']+'")')]
            unique_num+=1
            parts.append('CONSTRAINT "UQ_KB1_'+t.upper()+str(unique_num)+'" UNIQUE ('+','.join('"'+k.upper()+'"' for k in members)+')')
        ddls.append('CREATE TABLE '+table(t)+' (\n  '+',\n  '.join(parts)+'\n)')
        for i,fk in enumerate(db.execute(f'PRAGMA foreign_key_list("{t}")')):
            fks.append('ALTER TABLE '+table(t)+' ADD CONSTRAINT "FK_KB1_'+t.upper()+str(i)+'" FOREIGN KEY ("'+fk['from'].upper()+'") REFERENCES '+table(fk['table'])+' ("'+fk['to'].upper()+'")')
    db.close()
    ddls+=fks
    ddls.append('CREATE TABLE "{{SCHEMA}}"."KB1_MIGRATION_INFO" ("RUN_ID" VARCHAR(64) PRIMARY KEY,"SOURCE_SHA256" VARCHAR(64) NOT NULL,"BATCH_ID" VARCHAR(32) NOT NULL,"MIGRATED_AT" TIMESTAMP NOT NULL,"ROW_COUNTS" CLOB,"VERIFIED" CHAR(1) NOT NULL)')
    labels={'file_assets':'原始文件资产，同哈希复用','sources':'文档版本与处理状态','sections':'完整政策条款及出处',
            'chunks':'待向量化检索文本，向量尚未接入','rewards':'证书奖励及领取条件','shared_rules':'全表共同奖励规则',
            'reward_rules':'奖励记录与共同规则关联','mappings':'资格职称对应，含隔离待核记录',
            'schedules':'年度时间事项及原文依据','attachments':'主通知具体版本与附件外键关联','issues':'数据处理及政策待核问题'}
    for t,label in labels.items():
        ddls.append("COMMENT ON TABLE "+table(t)+" IS '"+label+"'")
    (out/'schema.sql').write_text(';\n'.join(ddls)+';\n',encoding='utf-8')
    (out/'tables.tsv').write_text('\n'.join(metadata)+'\n',encoding='utf-8')
    views=[]
    def view(name,select,source,where=''):
        views.append('CREATE VIEW "{{SCHEMA}}"."'+name+'" AS SELECT '+select+' FROM '+source+(' WHERE '+where if where else ''))
    def cols(mapping):
        return ','.join('"'+key.upper()+'" AS "'+label+'"' for key,label in mapping)
    view('V_KB1_REWARDS',cols([
        ('id','记录ID'),('certificate_name','证书名称'),('variant','专业或级别分支'),('category','奖励类别'),
        ('grade','奖励级别'),('one_time_reward_yuan','一次性奖励元'),('leave_days','奖励假天数'),
        ('registration_yuan','注册补贴元'),('registration_period','注册补贴周期'),
        ('usage_cap_yuan','使用补贴上限元'),('usage_period','使用补贴周期'),
        ('reimbursement_cap_yuan','考试费报销上限元'),('annual_cap_yuan','年度上限元'),('per_project_yuan','每项目元'),
        ('conditions_raw','领取限制原文'),('title_hint_note','字体含义'),('shared_rule_ids_json','共同规则ID'),
        ('validity','目录有效性'),('source_id','原文件ID'),('source_locator_json','原表位置')]),table('rewards'))
    mappingcols=cols([('id','记录ID'),('certificate_name','资格证书名称'),('kind','对应表类型'),
                      ('target_title_raw','对应职称原文'),('conditions_raw','附加条件原文'),('policy_basis_raw','政策依据'),
                      ('region','地区'),('organization','来源组织'),('channel','评审渠道'),('record_status','核对状态'),
                      ('source_id','原文件ID'),('source_locator_json','原文位置')])
    view('V_KB1_TITLE_MAPPINGS',mappingcols,table('mappings'),'"RECORD_STATUS"=\'review_ready\'')
    view('V_KB1_MAPPING_CONFLICTS',mappingcols,table('mappings'),'"RECORD_STATUS"<>\'review_ready\'')
    view('V_KB1_POLICY_TEXT',cols([('id','条款ID'),('source_id','原文件ID'),('heading','条款标题'),
                                 ('chapter','所属章节'),('text','条款全文'),('region','地区'),
                                 ('organization','来源组织'),('source_locator_json','原文位置')]),table('sections'))
    view('V_KB1_SCHEDULES',cols([('id','事项ID'),('source_id','原文件ID'),('event_type','事项名称'),
                               ('start_date','开始日期'),('end_date','截止日期'),('precision_raw','日期精度'),
                               ('certainty','确定性'),('raw_date','日期原文'),('region','地区'),('channel','渠道'),
                               ('evidence_section_id','依据条款ID')]),table('schedules'))
    view('V_KB1_ATTACHMENTS',
         'm."ID" AS "主文件ID",m."TITLE" AS "主文件名称",a."MAIN_VERSION_ID" AS "主文件版本",'
         'a."DISPLAY_NAME" AS "附件名称",a."ATTACHMENT_NO" AS "附件序号",a."PURPOSE" AS "用途",'
         'a."CONTENT_INDEXED" AS "是否解析正文",s."ORIGINAL_PATH" AS "本机原件路径",a."DOWNLOAD_POLICY" AS "下载权限状态"',
         table('attachments')+' a JOIN '+table('sources')+' m ON m."VERSION_ID"=a."MAIN_VERSION_ID" JOIN '+table('sources')+' s ON s."VERSION_ID"=a."ATTACHMENT_VERSION_ID"')
    view('V_KB1_SOURCES',cols([('id','文件ID'),('title','文件名称'),('status','处理状态'),
                             ('included_in_batch1','是否第一批'),('original_path','本机原件路径'),('sha256','文件哈希')]),table('sources'))
    view('V_KB1_ISSUES',cols([('id','问题ID'),('source_id','原文件ID'),('code','问题类型'),('severity','级别'),('message','问题说明')]),table('issues'))
    views.append('CREATE VIEW "{{SCHEMA}}"."V_KB1_COUNTS" AS '+' UNION ALL '.join(
        "SELECT '"+t+"' AS \"数据表\",COUNT(*) AS \"记录数\" FROM "+table(t) for t in TABLES))
    (out/'views.sql').write_text(';\n'.join(views)+';\n',encoding='utf-8')
    properties={'source.sha256':source_hash,'batch.id':manifest['summary']['batch'],'run.id':'B1_'+source_hash[:16],
                'schema':'CAREER','expected.database':'DAMENG','total.rows':str(sum(counts.values()))}
    (out/'migration.properties').write_text('\n'.join(k+'='+v for k,v in properties.items())+'\n',encoding='utf-8')
    (out/'export_summary.json').write_text(json.dumps(dict(source_database=str(source),source_sha256=source_hash,
        target_schema='CAREER',tables=counts,layouts=layouts,total_rows=sum(counts.values()),
        view_names=re.findall(r'CREATE VIEW "\{\{SCHEMA\}\}"\."([^"]+)"','\n'.join(views))),ensure_ascii=False,indent=2),encoding='utf-8')
    from comment_certificate_dm import generate
    generate(out)
    print(json.dumps(dict(package=str(out),table_counts=counts,total_rows=sum(counts.values()),views=len(views)),ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
