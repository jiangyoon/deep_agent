"""Read-only queries for the prepared local review dataset (no model calls)."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys


def query(db_path, kind, *, name='', source=None, limit=10, include_held=False):
    if not 1<=limit<=100:
        raise ValueError('limit must be between 1 and 100')
    aliases={'一建':'一级建造师','二建':'二级建造师'}
    name=aliases.get(name,name)
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        if kind in ('rewards','mappings','schedules'):
            field='event_type' if kind=='schedules' else 'certificate_name'
            # PDF table cells may wrap a certificate name across lines. Preserve
            # original text in storage while matching a whitespace-free query.
            sql=f"SELECT data FROM {kind} WHERE replace(replace(replace({field},char(10),''),char(13),''),' ','') LIKE ?"
            params=['%'+''.join(name.split())+'%']
            if kind=='mappings':
                sql=f"SELECT data FROM {kind} WHERE (replace(replace(replace({field},char(10),''),char(13),''),' ','') LIKE ? OR replace(replace(replace(target_title_raw,char(10),''),char(13),''),' ','') LIKE ?)"
                params=params*2
            if source:
                sql+=' AND source_id=?'
                params.append(source)
            if kind=='mappings' and not include_held:
                sql+=" AND coalesce(json_extract(data,'$.record_status'),'review_ready')='review_ready'"
            sql+=' ORDER BY id LIMIT ?'
            rows=[json.loads(row['data']) for row in db.execute(sql,params+[limit])]
            if kind=='rewards':
                for row in rows:
                    row['shared_conditions']=[json.loads(x[0]) for x in db.execute(
                        'SELECT g.data FROM shared_rules g JOIN reward_rules rr ON rr.rule_id=g.id WHERE rr.reward_id=? ORDER BY g.id',
                        (row['id'],))]
            return rows
        if kind=='attachments':
            if not source:
                raise ValueError('attachments requires source, e.g. N006 or N044')
            return [dict(json.loads(row['data']),local_file=row['original_path']) for row in db.execute(
                '''SELECT a.data,s.original_path FROM attachments a
                   JOIN sources m ON m.version_id=a.main_version_id
                   JOIN sources s ON s.version_id=a.attachment_version_id
                   WHERE m.id=? AND a.purpose!='修订对比资料' ORDER BY a.id LIMIT ?''',(source,limit))]
        if kind=='policy':
            terms=name.split()
            if not terms:
                raise ValueError('policy requires at least one explicit keyword')
            sql="SELECT data FROM sections WHERE coalesce(json_extract(data,'$.record_status'),'review_ready')='review_ready'"
            params=[]
            for term in terms:
                sql+=' AND text LIKE ?'
                params.append('%'+term+'%')
            if source:
                sql+=' AND source_id=?'
                params.append(source)
            sql+=' ORDER BY length(text) DESC,id LIMIT ?'
            result=[]
            for row in db.execute(sql,params+[limit]):
                section=json.loads(row['data'])
                section['related_notice_attachments']=[json.loads(x[0]) for x in db.execute(
                    '''SELECT a.data FROM attachments a JOIN sources s ON s.version_id=a.main_version_id
                       WHERE s.id=? AND a.purpose!='修订对比资料'
                       UNION SELECT a.data FROM attachments a JOIN attachments child ON child.main_version_id=a.main_version_id
                         JOIN sources s ON s.version_id=child.attachment_version_id
                       WHERE s.id=? AND a.purpose!='修订对比资料' ''',(section['source_id'],section['source_id']))]
                result.append(section)
            return result
        raise ValueError('Unknown query kind')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('database',type=Path)
    p.add_argument('kind',choices=['rewards','mappings','schedules','attachments','policy'])
    p.add_argument('--name',default='')
    p.add_argument('--source')
    p.add_argument('--limit',type=int,default=10)
    p.add_argument('--include-held',action='store_true',help='Maintenance only: show source-conflict mappings')
    a=p.parse_args()
    rows=query(a.database,a.kind,name=a.name,source=a.source,limit=a.limit,include_held=a.include_held)
    print(json.dumps(dict(query_mode='local_structured_or_keyword_review',embedding_available=False,
                          count=len(rows),results=rows),ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
