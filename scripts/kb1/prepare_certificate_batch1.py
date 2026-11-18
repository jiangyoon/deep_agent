"""Build a traceable, local first-batch review dataset; never call a model.

Optional document parsers must already be installed in the selected interpreter.
This command never parses registration templates or mutates original files.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
import xml.etree.ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
import pdfplumber


EXPECTED_BATCH = "0848fe37e7ed"
TEMPLATES = {4, 5, 9, 10, 11, 12, 14, 15, 16, 37, 38, 40}
POLICY_WORDS = {1, 3, 6, 7, 8, 13, 35, 36, 39, 41, 44}
POLICY_PDFS = {19, 20, 21, 22, 23}
MAPPING_SOURCES = {17, 18, 24, 25}
SCOPE = TEMPLATES | POLICY_WORDS | POLICY_PDFS | MAPPING_SOURCES | {2, 33, 34}
NOTICE_IDS = {3, 6, 21, 23, 44}
PARENTS = {1: 3, 2: 3, 4: 6, 5: 6, 7: 6, 8: 6, 9: 6, 10: 6, 11: 6,
           12: 6, 13: 6, 14: 6, 15: 6, 16: 6, 35: 44, 36: 44, 37: 44,
           38: 44, 39: 44, 40: 44, 41: 44}


def jid(number):
    return f"N{number:03}"


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def norm(value):
    return unicodedata.normalize("NFKC", str(value or "")).replace("\r", "").strip()


def compact(value):
    return re.sub(r"\s+", "", norm(value))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chinese(n):
    if n < 10:
        return "零一二三四五六七八九"[n]
    if n < 100:
        return (chinese(n // 10) if n >= 20 else "") + "十" + (chinese(n % 10) if n % 10 else "")
    return str(n)


class Numbering:
    """Resolve the numbering used by these OOXML sources, keeping explicit text."""
    def __init__(self, doc):
        try:
            root = doc.part.numbering_part.element
        except (KeyError, NotImplementedError):
            # Some converted DOCX files have no automatic numbering definitions.
            root = ET.Element(qn("w:numbering"))
        self.abstract = {x.get(qn("w:abstractNumId")): x for x in root.findall(qn("w:abstractNum"))}
        self.nums = {x.get(qn("w:numId")): x for x in root.findall(qn("w:num"))}
        self.counts = {}
        self.unknown = []

    def prefix(self, p):
        candidates = p._p.xpath("./w:pPr/w:numPr")
        style = p.style
        visited = set()
        while not candidates and style is not None and style.style_id not in visited:
            visited.add(style.style_id)
            candidates = style.element.xpath("./w:pPr/w:numPr")
            style = style.base_style
        if not candidates:
            return ""
        pr = candidates[0]
        num_id = pr.find(qn("w:numId"))
        level = pr.find(qn("w:ilvl"))
        ilvl = int(level.get(qn("w:val"))) if level is not None else 0
        if num_id is None or ilvl < 0 or num_id.get(qn("w:val")) == "0":
            return ""
        nid = num_id.get(qn("w:val"))
        num = self.nums.get(nid)
        if num is None:
            self.unknown.append(nid)
            return ""
        aid = num.find(qn("w:abstractNumId")).get(qn("w:val"))
        ab = self.abstract[aid]
        levels = {int(x.get(qn("w:ilvl"))): x for x in ab.findall(qn("w:lvl"))}
        overrides = {int(x.get(qn("w:ilvl"))): x for x in num.findall(qn("w:lvlOverride"))}
        level_def = levels.get(ilvl)
        override = overrides.get(ilvl)
        if override is not None and override.find(qn("w:lvl")) is not None:
            level_def = override.find(qn("w:lvl"))
        if level_def is None:
            self.unknown.append(f"{nid}/{ilvl}")
            return ""
        start = level_def.find(qn("w:start"))
        initial = int(start.get(qn("w:val"))) if start is not None else 1
        if override is not None and override.find(qn("w:startOverride")) is not None:
            initial = int(override.find(qn("w:startOverride")).get(qn("w:val")))
        key = (nid, ilvl)
        self.counts[key] = self.counts.get(key, initial - 1) + 1
        for deeper in range(ilvl + 1, 9):
            self.counts.pop((nid, deeper), None)
        fmt = level_def.find(qn("w:numFmt")).get(qn("w:val"))
        template = level_def.find(qn("w:lvlText")).get(qn("w:val"))
        if fmt == "none":
            return ""
        def replacement(match):
            lv = int(match.group(1)) - 1
            count = self.counts.get((nid, lv), 1)
            item = levels.get(lv, level_def)
            form = item.find(qn("w:numFmt")).get(qn("w:val"))
            if form in ("chineseCounting", "japaneseCounting", "chineseCountingThousand"):
                return chinese(count)
            if form in ("decimal", "decimalZero"):
                return str(count)
            if form == "bullet":
                return "•"
            self.unknown.append(form)
            return str(count)
        return re.sub(r"%([1-9])", replacement, template)


class Builder:
    def __init__(self, batch, output):
        self.batch = batch.resolve()
        self.out = output.resolve()
        self.source = {r["id"]: r for r in json.loads((batch / "inventory.json").read_text(encoding="utf-8"))}
        self.sections = []
        self.chunks = []
        self.rewards = []
        self.rules = []
        self.mappings = []
        self.events = []
        self.relations = []
        self.files = {}
        self.issues = []
        self.blocks = {}
        self.metrics = {}
        self.statuses = {}

    def path(self, source):
        return self.batch / "raw" / self.source[source]["path"]

    def version(self, source):
        return source + "@" + self.source[source]["sha256"][:12]

    def issue(self, source, code, message, severity="review"):
        self.issues.append(dict(id=f"I{len(self.issues)+1:03}",source_id=source,
                                code=code,severity=severity,message=message))

    def meta(self, source):
        number = int(source[1:])
        if number in range(17,26):
            region = "北京海淀" if number == 23 else "北京"
            organization = "北京市人社政策资料"
            channel = "人社职称评价"
        elif number in {33,34}:
            region, organization, channel = None, "公司", "证书奖励"
        elif number in {35,36,37,38,39,40,41,44}:
            region, organization, channel = None, "集团及公司", "集团高级工程师初评"
        else:
            region, organization, channel = None, "公司", "公司专业技术职务管理"
        return dict(region=region, organization=organization, channel=channel,
                    source_id=source, version_id=self.version(source),
                    verification_status="source_transcribed_not_authority_verified",
                    effective_at=None, expires_at=None)

    def add_sections(self, source, blocks):
        """Preserve article parents; child chunks always reference their full parent."""
        self.blocks[source] = blocks
        title = Path(self.source[source]["path"]).stem
        parts = []
        current = []
        heading = title
        chapter = ""
        def flush():
            if current:
                parts.append((heading, chapter, list(current)))
                current.clear()
        for b in blocks:
            text = b["text"]
            if re.match(r"^第[一二三四五六七八九十百零〇\d]+章", text):
                flush()
                chapter = text
                heading = text
            elif re.match(r"^第[一二三四五六七八九十百零〇\d]+条", text) or b.get("heading"):
                flush()
                heading = text.split("\n")[0][:100]
            current.append(b)
        flush()
        for i, (heading, chapter, part) in enumerate(parts, 1):
            sid = f"{source}-S{i:03}"
            content = "\n\n".join(p["text"] for p in part)
            sec = dict(id=sid,source_id=source,title=title,heading=heading,
                       chapter=chapter,text=content,locator=[p["locator"] for p in part],
                       metadata=self.meta(source))
            sec['record_status']='held_transcription_gap' if '原件此处跨页文字衔接疑似缺失' in content else 'review_ready'
            self.sections.append(sec)
            if sec['record_status']!='review_ready':
                self.issue(source,'transcription_gap',f'{sid}跨页处疑似缺字，保留转录但不生成检索片段。','review')
                continue
            groups=[]
            group=[]
            size=0
            for b in part:
                if group and size+len(b["text"])>850:
                    groups.append(group)
                    group=[]
                    size=0
                group.append(b)
                size+=len(b["text"])
            if group:
                groups.append(group)
            for ci, group in enumerate(groups, 1):
                text = f"{title}\n{chapter}\n{heading}\n" + "\n".join(p["text"] for p in group)
                self.chunks.append(dict(id=f"{sid}-C{ci:02}",section_id=sid,source_id=source,
                                        text=text,locator=[p["locator"] for p in group],
                                        metadata=sec["metadata"],content_hash=digest(text),
                                        embedding=None,embedding_model=None,embedding_status="pending_customer_embedding"))

    def word(self, source):
        doc = Document(self.path(source))
        numbering = Numbering(doc)
        paragraphs = {id(p._p):i for i,p in enumerate(doc.paragraphs)}
        blocks=[]
        pi=-1
        ti=-1
        image_count=0
        for child in doc.element.body:
            if child.tag==qn("w:p"):
                pi+=1
                p=Paragraph(child,doc)
                prefix=numbering.prefix(p)
                # These boundaries were inspected against this pinned source version.
                if source=="N001" and pi>=141:
                    continue
                if source=="N039" and pi>=40:
                    continue
                if source=="N036" and not 27<=pi<186:
                    continue
                if source=="N006" and pi>=35 or source=="N044" and pi>=42:
                    continue
                text=p.text.strip()
                if text:
                    text=prefix+text if prefix else text
                    # Remove contact values, not substantive numerical conditions.
                    text=re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)","[联系人手机号未进入通用库]",text)
                    text=re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}","[联系人邮箱未进入通用库]",text)
                    heading=bool(re.match(r"^[一二三四五六七八九十]+[、．]",text))
                    if source=="N036":
                        heading=p.style.name.startswith("Heading") or text in ("概述","预报名填报流程","正式报名填报流程")
                    blocks.append(dict(text=text,locator=dict(paragraph=pi),heading=heading))
                for blip in child.xpath('.//a:blip'):
                    rid=blip.get(qn('r:embed'))
                    if rid and rid in doc.part.related_parts:
                        image_count+=1
                        part=doc.part.related_parts[rid]
                        directory=self.out/'images'/source
                        directory.mkdir(parents=True,exist_ok=True)
                        suffix=Path(str(part.partname)).suffix
                        relative=Path('images')/source/f'{image_count:02}{suffix}'
                        (self.out/relative).write_bytes(part.blob)
                        # Image bytes are not indexed; preserve the paragraph linkage.
                        self.files[str(relative)]=dict(source_id=source,paragraph=pi,role="screenshot_not_publicly_released")
            elif child.tag==qn("w:tbl"):
                ti+=1
                if source=="N035":
                    # Its 54-item annex is emitted once as individual mapping records below.
                    continue
                if source in {"N003","N006","N044"} and ti!=1:
                    continue
                if source=="N001" and pi>=141 or source=="N039" and pi>=40:
                    continue
                if source=="N036" and not 27<=pi<186:
                    continue
                table=Table(child,doc)
                rows=[]
                for ri,row in enumerate(table.rows):
                    seen=set()
                    values=[]
                    for cell in row.cells:
                        # Repeated wrappers point to the same underlying merged cell.
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        values.append(cell.text.strip().replace("\n"," / "))
                    if any(values):
                        rows.append(" | ".join(values))
                if rows:
                    blocks.append(dict(text="[原表]\n"+"\n".join(rows),locator=dict(table=ti),heading=False))
        if numbering.unknown:
            self.issue(source,"numbering_unknown",dumps(sorted(set(numbering.unknown))),"blocking")
        self.metrics[source]=dict(paragraphs=len(doc.paragraphs),tables=len(doc.tables),linked_images=image_count)
        self.add_sections(source,blocks)

    def pdf_policy(self,source):
        blocks=[]
        manual=self.batch/'manual_review'/f'{source}-transcribed.md'
        if manual.exists():
            provenance=json.loads((manual.parent/f'{source}-provenance.json').read_text(encoding='utf-8'))
            assert provenance['source_sha256']==self.source[source]['sha256']
            assert provenance['transcription_sha256']==hashlib.sha256(manual.read_bytes()).hexdigest()
            page=None
            for line in manual.read_text(encoding='utf-8').splitlines():
                marker=re.fullmatch(r'--- PAGE (\d+) ---',line)
                if marker:
                    page=int(marker[1])
                    continue
                if page and line.strip() and not line.startswith('<!--'):
                    blocks.append(dict(text=line.strip(),locator=dict(pdf_page=page,printed_page=page+56,
                                       extraction='visual_transcription'),heading=bool(re.match(r'^第[一二三四五六七八九十百]+[章条]',line))))
            assert sorted({b['locator']['pdf_page'] for b in blocks})==list(range(1,15))
            self.statuses[source]='visual_transcription_needs_business_review'
            self.metrics[source]=dict(pdf_pages=14,transcription=provenance)
            self.issue(source,'visual_recovery','字体编码乱码已按14页原图转录恢复，保留物理/印刷页码；转录正文需资料方复核后正式发布。','review')
        else:
            blocks=self.pdf_policy_blocks(source)
            if blocks is None:
                return
        self.merge_pdf_blocks(source,blocks)

    def pdf_policy_blocks(self,source):
        blocks=[]
        with pdfplumber.open(self.path(source)) as doc:
            pages=[]
            for i,page in enumerate(doc.pages,1):
                # Prefer pdfminer here; each source gets an explicit corruption gate.
                text=page.extract_text() or ""
                pages.append(text)
                for line in text.splitlines():
                    t=line.strip()
                    if not t or re.fullmatch(r"[-—－]?\s*\d+\s*[-—－]?",t):
                        continue
                    if t=="北京市人民政府公报":
                        continue
                    blocks.append(dict(text=t,locator=dict(pdf_page=i),heading=bool(re.match(r"^[一二三四五六七八九十]+、",t))))
            joined="\n".join(pages)
            if "(cid:" in joined or re.search(r"/G[0-9A-F]{2}",joined):
                self.statuses[source]="held_encoding_error"
                self.issue(source,"font_encoding","正文出现字符编码错误，未写入知识块；需图像转录/OCR并复核。","blocking")
                return None
            self.metrics[source]=dict(pdf_pages=len(pages))
        return blocks

    def merge_pdf_blocks(self,source,blocks):
        # Join wrapped lines, while retaining page locators on each paragraph.
        merged=[]
        for block in blocks:
            heading=block["heading"] or bool(re.match(r"^第[一二三四五六七八九十百零〇\d\s]+条",block["text"]))
            start=heading or bool(re.match(r"^(?:[（(][一二三四五六七八九十\d]+[）)]|\d+[.．、])",block["text"]))
            if merged and not start and not merged[-1].get("heading"):
                previous=merged[-1]
                previous["text"]+=block["text"]
                previous["locator"]["pdf_page_end"]=block["locator"]["pdf_page"]
            else:
                block["heading"]=heading
                merged.append(block)
        self.add_sections(source,merged)

    def pdf_tables(self,source):
        result=[]
        with pdfplumber.open(self.path(source)) as doc:
            for pi,page in enumerate(doc.pages,1):
                for ti,table in enumerate(page.find_tables()):
                    rows=table.extract()
                    filled=[]
                    # Fill only real geometric vertical merges, never arbitrary blanks.
                    for ri,row in enumerate(rows):
                        current=[]
                        for ci,value in enumerate(row):
                            bbox=table.rows[ri].cells[ci]
                            if value is None and bbox is None:
                                for earlier in range(ri-1,-1,-1):
                                    prior=table.rows[earlier].cells[ci]
                                    row_top=min(c[1] for c in table.rows[ri].cells if c)
                                    if prior and prior[3]>row_top+0.5:
                                        value=rows[earlier][ci]
                                        break
                            current.append(value)
                        filled.append(current)
                    for ri,row in enumerate(filled):
                        result.append(dict(values=row,locator=dict(pdf_page=pi,table=ti,row=ri+1)))
                    if source=='N018' and pi==2 and ti==0:
                        # This source's final row has no bottom border, so grid detection
                        # stops at item 11. Recover item 12 using the verified column bounds.
                        bottom=max(e['bottom'] for e in page.edges if e.get('orientation')=='v')
                        cells=[]
                        for cell in table.rows[0].cells:
                            cells.append(page.crop((cell[0],table.bbox[3],cell[2],bottom)).extract_text() or '')
                        if compact(cells[0])!='12' or 'FIA/FFA' not in cells[1]:
                            raise ValueError('Open-bottom international PDF row needs review')
                        result.append(dict(values=cells,locator=dict(pdf_page=pi,table=ti,row=13,
                                           extraction='open_bottom_row_column_crop')))
        return result

    def domestic(self,rows,source,alternate=None):
        records=[]
        if source=='N017':
            text=(self.batch/'extracted/N017.txt').read_text(encoding='utf-8')
            notes=re.findall(r'注[：:](.*?)(?=\n序号|\n--- PAGE|\Z)',text,re.S)
            if len(notes)!=2:
                raise ValueError('Domestic PDF common notes need review')
            category_note,common_note=[x.strip() for x in notes]
        else:
            notes=[p.text for p in Document(self.path(source)).paragraphs if p.text.strip().startswith(('注:','注：'))]
            common_note='\n'.join(notes)
            category_note=''
            if not common_note:
                raise ValueError('Group annex common note missing')
        for row in rows:
            cells=row['values']
            if len(cells)<4 or not compact(cells[0]).isdigit():
                continue
            serial=int(compact(cells[0]))
            names=[]
            for c in cells[1:-2]:
                if c and compact(c) not in [compact(x) for x in names]:
                    names.append(c.strip())
            name=" / ".join(names)
            if not name:
                raise ValueError(f"Missing mapping name {source}: {row}")
            item=dict(id=f"{source}-M{len(records)+1:03}",source_id=source,
                      kind="domestic",serial=serial,certificate_name=name,
                      target_title_raw=cells[-2] or "",
                      conditions_raw='\n'.join(x for x in [cells[-2],common_note,category_note if serial>=46 else ''] if x),
                      common_note_raw=common_note,
                      policy_basis_raw=cells[-1] or "",issuer_raw=None,country_raw=None,
                      field_raw=None,group_raw=None,locator=row['locator'],
                      alternate_source_id=alternate,metadata=self.meta(source))
            records.append(item)
        assert set(r['serial'] for r in records)==set(range(1,55)), f'{source}: missing 54 serial groups'
        self.mappings.extend(records)
        self.mapping_sections(source,records)
        return records

    def mapping_sections(self,source,records):
        blocks=[]
        for r in records:
            if r.get('record_status','review_ready')!='review_ready':
                continue
            text=f"{r['certificate_name']}\n职称对应原文：{r['target_title_raw']}\n附加条件原文：{r['conditions_raw']}"
            if r.get('policy_basis_raw'):
                text+='\n政策依据：'+r['policy_basis_raw']
            if r.get('group_raw'):
                text+='\n所属清单：'+r['group_raw']
            text+='\n说明：对应关系不等于满足全部申报条件。'
            blocks.append(dict(text=text,locator=r['locator'],heading=True))
        # Append for N035, whose policy sections already exist.
        indexed=[r for r in records if r.get('record_status','review_ready')=='review_ready']
        offset=sum(s['source_id']==source for s in self.sections)
        if offset:
            for i,(b,record) in enumerate(zip(blocks,indexed),offset+1):
                sid=f'{source}-S{i:03}'
                record['evidence_section_id']=sid
                self.sections.append(dict(id=sid,source_id=source,title=Path(self.source[source]['path']).stem,
                                          heading=b['text'].split('\n')[0],chapter='附表：职业资格与职称对应',
                                          text=b['text'],locator=[b['locator']],metadata=self.meta(source)))
                self.chunks.append(dict(id=sid+'-C01',section_id=sid,source_id=source,text=b['text'],
                                        locator=[b['locator']],metadata=self.meta(source),content_hash=digest(b['text']),
                                        embedding=None,embedding_model=None,embedding_status='pending_customer_embedding'))
        else:
            self.add_sections(source,blocks)
            for i,record in enumerate(indexed,1):
                record['evidence_section_id']=f'{source}-S{i:03}'

    @staticmethod
    def xcell(sheet,row,col):
        cell=sheet.cell(row,col)
        if cell.value is not None:
            return cell.value,cell.coordinate
        for merged in sheet.merged_cells.ranges:
            if merged.min_row<=row<=merged.max_row and merged.min_col<=col<=merged.max_col:
                anchor=sheet.cell(merged.min_row,merged.min_col)
                return anchor.value,anchor.coordinate
        return None,cell.coordinate

    def international(self):
        source='N025'
        wb=load_workbook(self.path(source),data_only=False)
        sheet=wb.active
        group=None
        records=[]
        for ri in range(1,sheet.max_row+1):
            values=[self.xcell(sheet,ri,c)[0] for c in range(1,7)]
            first=compact(values[0])
            if first.startswith(('（','(')):
                group=values[0]
            if not first.isdigit():
                continue
            memo=str(values[5] or '')
            targets=re.findall(r'对应([^，。；]*?职称)',memo)
            record=dict(id=f'{source}-M{len(records)+1:03}',source_id=source,kind='international',
                        serial=int(first),certificate_name=str(values[1] or ''),
                        target_title_raw='；'.join(dict.fromkeys(targets)),conditions_raw=memo,
                        policy_basis_raw=None,issuer_raw=values[2],country_raw=values[3],field_raw=values[4],
                        group_raw=group,locator=dict(sheet=sheet.title,range=f'A{ri}:F{ri}'),
                        alternate_source_id='N018',metadata=self.meta(source))
            records.append(record)
        assert len(records)==34, len(records)
        # Check every XLSX record against PDF row content, not only its count.
        pdfrows=[x for x in self.pdf_tables('N018') if len(x['values'])==6 and compact(x['values'][0]).isdigit()]
        unmatched=[]
        differences=[]
        assert len(pdfrows)==33 # Source PDF explicitly says 33, while XLSX says 34.
        excel_names={compact(r['certificate_name']) for r in records}
        pdf_only=[p for p in pdfrows if compact(p['values'][1]) not in excel_names]
        for ri,r in enumerate(records):
            matches=[p for p in pdfrows if compact(p['values'][1])==compact(r['certificate_name'])]
            if len(matches)!=1 or any(compact(matches[0]['values'][c])!=compact(r[k]) for c,k in
                                     [(2,'issuer_raw'),(3,'country_raw'),(4,'field_raw'),(5,'conditions_raw')]):
                unmatched.append(r['id'])
                r['record_status']='held_source_conflict'
                r['alternate_source_id']=None
                candidate=matches[0] if matches else next((p for p in pdf_only if compact(p['values'][2])==compact(r['issuer_raw'])),None)
                differences.append(dict(record_id=r['id'],xlsx_name=r['certificate_name'],xlsx_conditions=r['conditions_raw'],
                                        pdf_values=candidate['values'] if candidate else [],pdf_locator=candidate['locator'] if candidate else None,
                                        comparison='same_name_different_conditions' if matches else 'different_name_same_issuer_not_assumed_equivalent'))
            else:
                r['record_status']='review_ready'
                r['alternate_locator']=matches[0]['locator']
        if unmatched:
            self.issue(source,'international_carrier_difference',dumps(unmatched))
        self.metrics[source]=dict(records=34,pdf_records=33,pdf_comparison_mismatches=unmatched,
                                 recovered_open_border_row='FIA/FFA 英国正精算师')
        self.metrics[source]['differences']=differences
        self.mappings.extend(records)
        self.mapping_sections(source,records)
        for i,p in enumerate(pdf_only,1):
            values=p['values']
            self.mappings.append(dict(id=f'N018-ONLY{i:02}',source_id='N018',kind='international',serial=int(values[0]),
                                      certificate_name=values[1],target_title_raw='中级职称（原文条件待版本确认）',
                                      conditions_raw=values[5],policy_basis_raw=None,issuer_raw=values[2],country_raw=values[3],
                                      field_raw=values[4],group_raw='PDF独有记录',locator=p['locator'],
                                      alternate_source_id=None,metadata=self.meta('N018'),record_status='held_source_conflict'))
        wb.close()

    def reward(self):
        source='N034'
        wb=load_workbook(self.path(source),data_only=False,rich_text=True)
        sheet=wb.active
        shared=str(sheet['A2'].value)
        grade_map={}
        for match in re.finditer(r'(S|A-|A|B|C)级一次性奖励(\d+)元[，,](\d+)天奖励假',shared):
            grade_map[match[1]]=(int(match[2]),int(match[3]))
        assert grade_map=={'S':(12000,3),'A':(8000,3),'A-':(4000,3),'B':(2000,2),'C':(1000,1)}
        for i,line in enumerate(shared.splitlines(),1):
            if line.strip():
                self.rules.append(dict(id=f'{source}-G{i:02}',source_id=source,text=line.strip(),
                                       locator=dict(sheet=sheet.title,cell='A2'),role='global_reward_condition'))
        rules=[r['id'] for r in self.rules if r['source_id']==source]
        md=(self.batch/'extracted/N033.txt').read_text(encoding='utf-8')
        mdrows=[]
        for line in md.splitlines():
            if not line.startswith('|'):
                continue
            cells=[x.strip() for x in line.strip().strip('|').split('|')]
            if len(cells)==8 and cells[0] not in ('序号','---'):
                mdrows.append(cells)
        assert len(mdrows)==sheet.max_row-3, (len(mdrows),sheet.max_row)
        mismatches=[]
        color_mismatches=[]
        theme=ET.fromstring(wb.loaded_theme)
        ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        scheme=theme.find('.//a:clrScheme',ns)
        theme_order=['lt1','dk1','lt2','dk2','accent1','accent2','accent3','accent4','accent5','accent6','hlink','folHlink']
        def color_is_blue(color):
            if color is None:
                return False # This source's uncoloured runs use the default black font.
            rgb=None
            if color is not None and color.type=='rgb':
                rgb=color.rgb[-6:]
            elif color is not None and color.type=='theme':
                element=scheme.find('a:'+theme_order[color.theme],ns)
                item=list(element)[0]
                rgb=item.get('lastClr') or item.get('val')
            if not rgb or not re.fullmatch('[0-9A-Fa-f]{6}',rgb):
                return None
            red,green,blue=[int(rgb[i:i+2],16) for i in [0,2,4]]
            return blue>red+20 and blue>green+15
        # All current conditional formats only mark duplicates in red. Confirm
        # that none applies; a later source change must be reviewed, not ignored.
        for cf in sheet.conditional_formatting:
            values=[str(sheet.cell(row,col).value) for area in cf.sqref.ranges
                    for row in range(area.min_row,area.max_row+1)
                    for col in range(area.min_col,area.max_col+1)
                    if sheet.cell(row,col).value is not None]
            ruleset=sheet.conditional_formatting[cf]
            if any(rule.type!='duplicateValues' for rule in ruleset) or len(values)!=len(set(values)):
                raise ValueError('Active or unsupported certificate colour conditional format requires review')
        previous=['']*8
        for ri in range(4,sheet.max_row+1):
            vals=[]
            anchors=[]
            for ci in range(1,9):
                v,anchor=self.xcell(sheet,ri,ci)
                vals.append(str(v) if v is not None else '')
                anchors.append(anchor)
            mdrow=mdrows[ri-4]
            def mdtext(t):
                t=re.sub(r'\*\*|\[🟦视同职称\]', '',t)
                return compact(t.replace('<br>','\n'))
            for ci in range(8):
                value=mdrow[ci]
                if value=='同上':
                    value=previous[ci]
                if ci in (0,1) and not mdrow[ci] and vals[ci]:
                    value=vals[ci] # Presentation-only blank in MD serial/category.
                if mdtext(value)!=compact(vals[ci]):
                    mismatches.append(dict(row=ri,column=ci+1,excel=vals[ci],markdown=value))
                previous[ci]=value
            grade_raw=vals[3]
            options=[]
            for line in grade_raw.splitlines():
                m=re.fullmatch(r'(.+?)为(S|A-|A|B|C)',line.strip())
                if m:
                    options.append((m[1],m[2]))
            if compact(grade_raw) in grade_map:
                options=[(None,compact(grade_raw))]
            if not options:
                options=[(None,None)]
            if ri==4:
                options=[('机电工程','S'),('公路工程','A'),('水利水电','A')]
            blue_mark='[🟦视同职称]' in mdrow[2]
            excel_blue=color_is_blue(sheet.cell(ri,3).font.color)
            if excel_blue is None or excel_blue!=blue_mark:
                color_mismatches.append(dict(row=ri,excel_blue=excel_blue,markdown_blue=blue_mark))
            color=sheet.cell(ri,3).font.color
            font_metadata=dict(type=color.type,value=str(color.value)) if color else None
            for vi,(variant,grade) in enumerate(options,1):
                money,leave=grade_map[grade] if grade else (None,None)
                reimbursement=re.search(r'报销上限为(\d+)元/证',grade_raw)
                cap=re.search(r'年度累计奖励上限为(\d+)元',grade_raw)
                per_project=re.search(r'每支撑1个项目(\d+)元',grade_raw)
                def subsidy(raw):
                    m=re.fullmatch(r'(\d+)元/(月|年)',raw.strip())
                    return (int(m[1]),m[2]) if m else (None,None)
                reg,reg_period=subsidy(vals[5]); usage,usage_period=subsidy(vals[6])
                record=dict(id=f'{source}-R{ri:03}-{vi:02}',source_id=source,source_row=ri,
                            serial_raw=vals[0],category=vals[1],certificate_name=vals[2],variant=variant,
                            grade=grade,grade_raw=grade_raw,one_time_reward_yuan=money,leave_days=leave,
                            registration_yuan=reg,registration_period=reg_period,registration_raw=vals[5],
                            usage_cap_yuan=usage,usage_period=usage_period,usage_raw=vals[6],
                            reimbursement_cap_yuan=int(reimbursement[1]) if reimbursement else None,
                            annual_cap_yuan=int(cap[1]) if cap else None,
                            per_project_yuan=int(per_project[1]) if per_project else None,
                            acquisition_raw=vals[4],conditions_raw=vals[7],
                            title_hint_from_markdown=blue_mark,font_metadata=font_metadata,
                            title_hint_color_checked=(excel_blue==blue_mark),
                            shared_rule_ids=rules,cell_anchors=anchors,
                            locator=dict(sheet=sheet.title,range=f'A{ri}:H{ri}',shared_cell='A2'),
                            alternate_source_id='N033',metadata=self.meta(source),
                            effective_start='2025-01-01' if ri==54 else None,
                            effective_end='2025-12-31' if ri==54 else None,
                            validity='explicit_2025_only' if ri==54 else '2025_catalog_current_validity_unconfirmed')
                record['combination_alternatives']=[line.split('+') for line in vals[2].splitlines() if line.strip()] if vals[1]=='一人多证奖励' else []
                record['combination_logic']='OR between alternatives; AND within each listed combination; original text controls' if record['combination_alternatives'] else None
                record['evidence_section_id']=record['id']+'-S'
                cell_value=sheet.cell(ri,3).value
                record['certificate_font_runs']=[dict(text=str(run),explicit_color=str(getattr(getattr(run,'font',None),'color',None)),
                                                      blue=color_is_blue(getattr(getattr(run,'font',None),'color',None)))
                                                 for run in cell_value] if not isinstance(cell_value,str) else None
                record['title_hint_applies_to_whole_combination']=False if record['combination_alternatives'] else None
                if blue_mark and record['certificate_font_runs']:
                    blue_fragments=[run['text'] for run in record['certificate_font_runs']
                                    if run['blue'] and run['text'].strip() not in ('+','')]
                    record['title_hint_note']='原表蓝色文字片段：'+ ' / '.join(blue_fragments)+'。蓝色含义见目录说明，不代表组合内所有证书均可对应职称。'
                elif blue_mark:
                    record['title_hint_note']='原表证书名称标为蓝色，目录说明为考过后可视同相应级别职称；具体适用条件仍需查对应政策。'
                else:
                    record['title_hint_note']='原表此记录未作蓝色标记；不能据此推断该证书一定没有职称对应关系。'
                self.rewards.append(record)
                text=(f"{vals[2]}\n奖励类别：{vals[1]}\n适用分支：{variant or '见原文'}\n奖励原文：{grade_raw}"
                      f"\n取得方式：{vals[4]}\n注册补贴：{vals[5]}\n使用补贴上限：{vals[6]}\n补充条件：{vals[7]}\n颜色含义：{record['title_hint_note']}\n目录共同说明：\n{shared}"
                      '\n有效性说明：目录为2025年版，当前是否沿用待确认；年度专项奖励仅限原文指定期间。')
                self.add_record_section(record['id'],source,text,record['locator'])
        self.metrics[source]=dict(excel_data_rows=sheet.max_row-3,structured_variants=len(self.rewards),
                                  markdown_cell_mismatches=mismatches,color_mismatches=color_mismatches)
        if mismatches:
            self.issue(source,'reward_markdown_difference',f'{len(mismatches)}处单元格表达差异，见validation_details.json。')
        if color_mismatches:
            self.issue(source,'reward_color_difference',f'{len(color_mismatches)}行颜色标记需核对，见validation_details.json。')
        self.issue(source,'catalog_validity','奖励目录为2025年版，整体是否继续执行待资料方确认；2025年专项单独限制。')
        wb.close()

    def add_record_section(self,record_id,source,text,locator):
        sid=record_id+'-S'
        self.sections.append(dict(id=sid,source_id=source,title=Path(self.source[source]['path']).stem,
                                  heading=text.split('\n')[0],chapter='结构化记录及完整条件',text=text,
                                  locator=[locator],metadata=self.meta(source)))
        self.chunks.append(dict(id=sid+'-C01',section_id=sid,source_id=source,text=text,
                                locator=[locator],metadata=self.meta(source),content_hash=digest(text),
                                embedding=None,embedding_model=None,embedding_status='pending_customer_embedding'))

    def schedules(self):
        # Manually curated against exact source quotations. Ambiguous dates stay imprecise.
        configs={
            'N006':[
                ('业绩年限计算截止','2026-08-31','2026-08-31','day','明确','2026年8月31日'),
                ('中级评审部门报送截止',None,'2026-09-20','day','年度上下文补全年份','9月20日前'),
                ('考核认定部门报送截止',None,'2026-09-30','day','年度上下文补全年份','9月30日前')],
            'N044':[
                ('学历资历及成果计算截止','2026-12-31','2026-12-31','day','明确','2026年12月31日'),
                ('高工预报名截止',None,'2026-10-30','day','明确','2026年10月30日'),
                ('高工材料提交截止',None,None,'month_end','预计，关注后续通知','11月底'),
                ('专家初评完成',None,None,'month','预计','2027年1月')],
        }
        for source in ['N021','N023']:
            configs[source]=[
                ('个人网上申报','2026-07-07','2026-07-30','day_range','年度上下文补全年份','7月7日（周二）—7月30日（周四）'),
                ('单位审核推荐','2026-07-07','2026-08-06','day_range','年度上下文补全年份','7月7日（周二）—8月6日（周四）'),
                ('评审服务机构首次审核','2026-07-07','2026-09-17','day_range','年度上下文补全年份','7月7日（周二）—9月17日（周四）'),
                ('个人缴费',None,None,'notification','以机构通知为准','通知的规定时间内'),
                ('答辩评审完成',None,None,'month_start','原文未给精确日期','12月初')]
        for source,items in configs.items():
            for kind,start,end,precision,certainty,quote in items:
                found=[s for s in self.sections if s['source_id']==source and compact(quote) in compact(s['text'])]
                if not found:
                    raise ValueError(f'Missing date quote: {source} {quote}')
                self.events.append(dict(id=f'{source}-E{sum(x["source_id"]==source for x in self.events)+1:02}',
                                        source_id=source,event_type=kind,start_date=start,end_date=end,
                                        precision=precision,certainty=certainty,raw_date=quote,
                                        evidence_section_id=found[0]['id'],metadata=self.meta(source)))

    def register_relations(self):
        # Scope includes registration files as opaque attachments; their body is never opened.
        for number,parent in PARENTS.items():
            source=jid(number)
            labels={1:'1',2:'2',4:'2',5:'4',7:'1/1',8:'1/2',9:'1/3',10:'1/4',11:'1/5',12:'1/6',
                    13:'3/1',14:'3/2',15:'3/3',16:'3/4',35:'1',36:'2',37:'3/3-1',38:'3/3-2',39:'3/3-3',40:'3/3-4',41:'3'}
            self.relations.append(dict(id=f'A-{parent:03}-{number:03}',main_version_id=self.version(jid(parent)),
                                       attachment_version_id=self.version(source),source_id=source,
                                       display_name=Path(self.source[source]['path']).name,
                                       attachment_no=labels[number],purpose='报名模板' if number in TEMPLATES else ('修订对比资料' if number==2 else '知识性附件'),
                                       sort_order=number,download_policy='requires_authenticated_internal_access_and_release_review',
                                       content_indexed=number not in TEMPLATES and number!=2))
        # Preserve the nested requirement-package relationship; names/numbers must not be flattened.
        for parent,children in {8:[7,9,10,11,12],13:[14,15,16],41:[37,38,39,40]}.items():
            for child in children:
                self.relations.append(dict(id=f'A-{parent:03}-{child:03}',main_version_id=self.version(jid(parent)),
                                           attachment_version_id=self.version(jid(child)),source_id=jid(child),
                                           display_name=Path(self.source[jid(child)]['path']).name,
                                           attachment_no=Path(self.source[jid(child)]['path']).name.split('：')[0].split('.')[0],purpose='材料包内附件',sort_order=child,
                                           download_policy='requires_authenticated_internal_access_and_release_review',
                                           content_indexed=child not in TEMPLATES))

    def build(self):
        if self.out.exists():
            raise ValueError('Output already exists; choose a new run directory')
        if not self.out.is_relative_to(Path(__file__).resolve().parents[2] / 'data' / 'material_review'):
            raise ValueError('Output must remain in the Git-ignored material_review workspace')
        self.out.mkdir(parents=True)
        for source,r in self.source.items():
            actual=hashlib.sha256(self.path(source).read_bytes()).hexdigest()
            if actual!=r['sha256']:
                raise ValueError(f'Source hash mismatch: {source}')
            n=int(source[1:])
            self.statuses[source]='planned_later' if n not in SCOPE else 'review_dataset'
            if n in TEMPLATES: self.statuses[source]='opaque_attachment_no_body_parsing'
            if n==2: self.statuses[source]='history_metadata_only'
            if n in (42,43): self.statuses[source]='excluded'
        for number in sorted(POLICY_WORDS):
            self.word(jid(number))
        for number in sorted(POLICY_PDFS):
            self.pdf_policy(jid(number))
        rows=self.pdf_tables('N017')
        alt=self.pdf_tables('N024')
        if dumps([[compact(c) for c in x['values']] for x in rows])!=dumps([[compact(c) for c in x['values']] for x in alt]):
            raise ValueError('Domestic duplicate tables differ: do not merge automatically')
        self.domestic(rows,'N017',alternate='N024')
        self.statuses['N024']='same_table_content_as_N017_attachment_label_differs'
        self.issue('N017','carrier_label_difference','两份PDF表内容一致，但首页附件编号不同；合并知识记录，保留两来源。','info')
        # The group annex is a separate applicability context, even if some rows match Beijing.
        doc=Document(self.path('N035'))
        group_rows=[dict(values=[c.text for c in row.cells],locator=dict(table=ti,row=ri+1))
                    for ti,t in enumerate(doc.tables) for ri,row in enumerate(t.rows)]
        self.domestic(group_rows,'N035')
        self.international()
        self.statuses['N018']='pdf_alternate_carrier_for_N025'
        self.reward()
        self.statuses['N033']='markdown_alternate_carrier_for_N034'
        self.schedules()
        self.register_relations()
        self.issue('N039','outdated_example_header','论文格式原文提到2010年页眉；未擅自改年，已排除样例正文，具体格式需资料方确认。')
        self.issue('N036','screenshot_review','33张内联截图已按段落关联保存，尚未全部视觉复核，不自动公开截图下载。')
        self.issue('N044','restricted_attachment','通知引用的专家名单只保留受限来源元信息，不挂为公开下载附件。','info')
        self.issue('N006','document_number_alias','正文引用移集成人〔2026〕182号，来源名称为人通〔2026〕182号；原文保留，不强制改写。')
        self.issue('N034','reimbursement_process_missing','奖励目录含报销限额，不代表已有完整报销流程制度。')
        self.export()

    def export(self):
        datasets=dict(sections=self.sections,chunks=self.chunks,rewards=self.rewards,
                      shared_rules=self.rules,mappings=self.mappings,schedules=self.events,
                      attachments=self.relations,issues=self.issues)
        sources=[]
        for source,r in self.source.items():
            sources.append(dict(id=source,version_id=self.version(source),title=Path(r['path']).stem,
                                original_path=str(self.path(source)),sha256=r['sha256'],size_bytes=r['bytes'],
                                file_asset_id='sha256:'+r['sha256'],
                                included_in_batch1=int(source[1:]) in SCOPE,status=self.statuses[source],
                                metadata=self.meta(source)))
        datasets['sources']=sources
        (self.out/'jsonl').mkdir()
        for name,records in datasets.items():
            (self.out/'jsonl'/f'{name}.jsonl').write_text(''.join(dumps(r)+'\n' for r in records),encoding='utf-8')
        (self.out/'validation_details.json').write_text(dumps(self.metrics),encoding='utf-8')
        (self.out/'image_links.json').write_text(dumps(self.files),encoding='utf-8')
        cleaned=self.out/'cleaned'
        cleaned.mkdir()
        for source in sorted({s['source_id'] for s in self.sections}):
            recs=[s for s in self.sections if s['source_id']==source]
            text=f"# {Path(self.source[source]['path']).stem}\n\n来源ID：{source}；状态：本地处理，政策有效性未联网核验。\n"
            for r in recs:
                text+=f"\n## {r['id']} {r['heading']}\n\n出处：`{dumps(r['locator'])}`\n\n{r['text']}\n"
            (cleaned/f'{source}.md').write_text(text,encoding='utf-8')
        self.database(datasets)
        summary=dict(batch=EXPECTED_BATCH,generated_at=datetime.now().isoformat(timespec='seconds'),
                     first_batch_source_files=len(SCOPE),counts={k:len(v) for k,v in datasets.items()},
                     embedding_status='pending_customer_embedding',policy_authority_verified=False,
                     held_sources=[s for s,v in self.statuses.items() if v.startswith('held_')])
        summary['mapping_queryable_records']=sum(r.get('record_status','review_ready')=='review_ready' for r in self.mappings)
        summary['held_mapping_records']=[r['id'] for r in self.mappings if r.get('record_status')=='held_source_conflict']
        (self.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        self.review_report(summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2))

    def database(self,datasets):
        db=sqlite3.connect(self.out/'knowledge.sqlite')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE file_assets(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,original_path TEXT NOT NULL,size_bytes INTEGER NOT NULL);
        CREATE TABLE sources(id TEXT PRIMARY KEY, version_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
          original_path TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
          file_asset_id TEXT NOT NULL REFERENCES file_assets(id),data TEXT NOT NULL);
        CREATE TABLE sections(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), heading TEXT,
          text TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE chunks(id TEXT PRIMARY KEY, section_id TEXT NOT NULL REFERENCES sections(id),
          source_id TEXT NOT NULL REFERENCES sources(id), text TEXT NOT NULL, embedding_status TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE rewards(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), certificate_name TEXT,
          category TEXT, variant TEXT, grade TEXT, one_time_reward_yuan INTEGER, registration_yuan INTEGER,
          usage_cap_yuan INTEGER, reimbursement_cap_yuan INTEGER, data TEXT NOT NULL);
        CREATE TABLE shared_rules(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),text TEXT,data TEXT NOT NULL);
        CREATE TABLE reward_rules(reward_id TEXT REFERENCES rewards(id),rule_id TEXT REFERENCES shared_rules(id),
          PRIMARY KEY(reward_id,rule_id));
        CREATE TABLE mappings(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), kind TEXT,
          certificate_name TEXT, target_title_raw TEXT, conditions_raw TEXT, data TEXT NOT NULL);
        CREATE TABLE schedules(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),event_type TEXT,
          start_date TEXT,end_date TEXT,certainty TEXT,evidence_section_id TEXT NOT NULL REFERENCES sections(id),data TEXT NOT NULL);
        CREATE TABLE attachments(id TEXT PRIMARY KEY,main_version_id TEXT NOT NULL REFERENCES sources(version_id),
          attachment_version_id TEXT NOT NULL REFERENCES sources(version_id),display_name TEXT,purpose TEXT,
          download_policy TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE issues(id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources(id),code TEXT,severity TEXT,message TEXT,data TEXT NOT NULL);
        CREATE INDEX idx_chunks_source ON chunks(source_id);
        CREATE INDEX idx_rewards_name ON rewards(certificate_name);
        CREATE INDEX idx_mappings_name ON mappings(certificate_name);
        CREATE INDEX idx_attachment_main ON attachments(main_version_id);
        ''')
        for r in datasets['sources']:
            db.execute('INSERT OR IGNORE INTO file_assets VALUES(?,?,?,?)',(r['file_asset_id'],r['sha256'],r['original_path'],r['size_bytes']))
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',tuple(r[k] for k in ['id','version_id','title','original_path','sha256','status','file_asset_id'])+(dumps(r),))
        columns={
            'sections':['id','source_id','heading','text'],
            'chunks':['id','section_id','source_id','text','embedding_status'],
            'rewards':['id','source_id','certificate_name','category','variant','grade','one_time_reward_yuan','registration_yuan','usage_cap_yuan','reimbursement_cap_yuan'],
            'shared_rules':['id','source_id','text'],
            'mappings':['id','source_id','kind','certificate_name','target_title_raw','conditions_raw'],
            'schedules':['id','source_id','event_type','start_date','end_date','certainty','evidence_section_id'],
            'attachments':['id','main_version_id','attachment_version_id','display_name','purpose','download_policy'],
            'issues':['id','source_id','code','severity','message']}
        for table,keys in columns.items():
            for r in datasets[table]:
                values=tuple(r.get(k) for k in keys)+(dumps(r),)
                db.execute(f'INSERT INTO {table} VALUES({",".join("?" for _ in values)})',values)
        for r in self.rewards:
            for rule in r['shared_rule_ids']:
                db.execute('INSERT INTO reward_rules VALUES(?,?)',(r['id'],rule))
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        db.commit()
        db.close()

    def review_report(self,summary):
        lines=['# 第一批资料处理结果','',
               '本批是可查询的本地审核数据，尚未接入聊天系统和Embedding；不能据此宣称政策已获业务核验或线上发布。',
               '',f"- 范围：{len(SCOPE)}份原始载体，含12份只登记关系、不解析正文的报名模板。",
               f"- 已生成：{len(self.sections)}个完整内容单元、{len(self.chunks)}个待向量化检索片段。",
               f"- 结构化数据：{len(self.rewards)}条奖励分支、{len(self.mappings)}条资格对应记录（其中{summary['mapping_queryable_records']}条参与默认查询，{len(summary['held_mapping_records'])}条存在来源差异已隔离）、{len(self.events)}条时间事项。",
               f"- 附件关系：{len(self.relations)}条，绑定具体文件版本；尚未提供网页下载服务。",
               '- 原件未修改，客户原件与生成数据保存在Git忽略目录。',
               '', '## 直接查看', '',
               '- [证书奖励明细](证书奖励明细.md)',
               '- [职称对应明细](职称对应明细.md)',
               '- [附件与主通知](附件与主通知.md)',
               '- [时间事项](时间事项.md)',
               '- [结构化与关键词查询用数据库](knowledge.sqlite)',
               '', '## 需要继续核对', '', '| 来源 | 问题 | 级别 |','|---|---|---|']
        for issue in self.issues:
            lines.append(f"| {issue['source_id']} | {issue['message']} | {issue['severity']} |")
        lines+=['','## 国际对应表的实质差异','',
                '以下差异记录保留在审核数据中，不生成向量文本，默认结构化查询也不返回。PDF原文为33项，XLSX为34项，不能直接认定其中一个就是新版。FIA/FFA行因缺少表格下边框漏识别，已按列坐标恢复，不再误记为政策差异。','',
                '| XLSX记录 | XLSX名称/条件 | PDF对照结果 |','|---|---|---|']
        for d in self.metrics['N025']['differences']:
            excel=(d['xlsx_name']+'：'+d['xlsx_conditions']).replace('|','／').replace('\n',' ')
            pdf=' / '.join(str(x or '') for x in d['pdf_values'][1:]).replace('|','／').replace('\n',' ') if d['pdf_values'] else 'PDF中没有同名记录，不假定其与其他名称证书等价。'
            lines.append(f"| {d['record_id']} | {excel} | {pdf} |")
        lines+=['','## 已清洗正文','']
        for source in sorted({s['source_id'] for s in self.sections}):
            lines.append(f"- [{source} {Path(self.source[source]['path']).stem}](cleaned/{source}.md)")
        (self.out/'第一批处理结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        def safe(t): return str(t if t is not None else '未说明').replace('|','／').replace('\n','<br>')
        lines=['# 证书奖励明细','', '金额以2025年目录原文为准，当前是否沿用待确认；共同条件见文末。', '',
               '| ID/原表行 | 证书 | 分支 | 一次性奖励 | 注册补贴 | 使用补贴上限 | 原文附加条件 |', '|---|---|---|---|---|---|---|']
        for r in self.rewards:
            money=str(r['one_time_reward_yuan'])+'元' if r['one_time_reward_yuan'] is not None else r['grade_raw']
            lines.append('| '+' | '.join(safe(x) for x in [r['id'],r['certificate_name'],r['variant'],money,r['registration_raw'],r['usage_raw'],r['conditions_raw']])+' |')
        lines+=['','## 全表共同条件','']+[r['text'] for r in self.rules]
        (self.out/'证书奖励明细.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        lines=['# 职称对应明细','', '北京资料与集团附件保留不同适用上下文，不能相互替代。', '',
               '| ID | 来源 | 资格名称 | 对应职称原文 | 附加条件 | 原文位置 | 查询状态 |','|---|---|---|---|---|---|---|']
        for r in self.mappings:
            lines.append('| '+' | '.join(safe(x) for x in [r['id'],r['source_id'],r['certificate_name'],r['target_title_raw'],r['conditions_raw'],dumps(r['locator']),r.get('record_status','review_ready')])+' |')
        (self.out/'职称对应明细.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        lines=['# 附件与主通知','', '模板不解析正文；绑定主文件具体版本。下载权限标记为待发布检查，不表示已有网页下载接口。','',
               '| 主文件版本 | 附件 | 用途 | 内容是否索引 |','|---|---|---|---|']
        for r in self.relations:
            lines.append('| '+' | '.join(safe(x) for x in [r['main_version_id'],r['display_name'],r['purpose'],r['content_indexed']])+' |')
        (self.out/'附件与主通知.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        lines=['# 时间事项','', '预计/待通知事项保留原文，不生成虚构的精确截止日。','',
               '| 来源 | 事项 | 起始/截止 | 精度 | 确定性 | 原文 |','|---|---|---|---|---|---|']
        for r in self.events:
            lines.append('| '+' | '.join(safe(x) for x in [r['source_id'],r['event_type'],str(r['start_date'])+' / '+str(r['end_date']),r['precision'],r['certainty'],r['raw_date']])+' |')
        (self.out/'时间事项.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.batch.name!=EXPECTED_BATCH:
        parser.error('This reviewed parser is pinned to batch '+EXPECTED_BATCH)
    Builder(args.batch,args.output).build()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
