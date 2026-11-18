"""Validate prepared data against selected original-source facts and query contracts."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest


class DatasetChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=DATASET
        cls.db=sqlite3.connect(cls.folder/'knowledge.sqlite')
        cls.db.row_factory=sqlite3.Row
        cls.datasets={name:[json.loads(l) for l in (cls.folder/'jsonl'/f'{name}.jsonl').read_text(encoding='utf-8').splitlines()]
                      for name in ['sources','sections','chunks','rewards','shared_rules','mappings','schedules','attachments']}
        cls.details=json.loads((cls.folder/'validation_details.json').read_text(encoding='utf-8'))
        spec=importlib.util.spec_from_file_location('batchquery',Path(__file__).with_name('query_certificate_batch1.py'))
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.query=staticmethod(lambda kind,**kwargs: module.query(cls.folder/'knowledge.sqlite',kind,**kwargs))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_database_relations_and_integrity(self):
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_all_scope_files_registered_without_later_batches(self):
        included=[r for r in self.datasets['sources'] if r['included_in_batch1']]
        self.assertEqual(len(included),35)
        indexed={r['source_id'] for r in self.datasets['chunks']}
        for n in [2,26,27,28,29,30,31,32,42,43]:
            self.assertNotIn(f'N{n:03}',indexed)

    def test_registration_files_are_opaque_and_bound_to_correct_notice(self):
        templates={4,5,9,10,11,12,14,15,16,37,38,40}
        for n in templates:
            key=f'N{n:03}'
            self.assertFalse(any(r['source_id']==key for r in self.datasets['sections']))
            links=[r for r in self.datasets['attachments'] if r['source_id']==key]
            expected='N006@' if n<20 else 'N044@'
            self.assertTrue(any(r['main_version_id'].startswith(expected) for r in links))
            self.assertTrue(all(not r['content_indexed'] for r in links))

    def test_duplicate_files_share_assets_but_keep_relations(self):
        for a,b in [('N010','N015'),('N011','N016')]:
            rows=self.db.execute('SELECT file_asset_id FROM sources WHERE id IN (?,?)',(a,b)).fetchall()
            self.assertEqual(rows[0][0],rows[1][0])
            self.assertTrue(all(any(r['source_id']==key for r in self.datasets['attachments']) for key in [a,b]))

    def test_engineer_conditions_keep_or_and_performance_context(self):
        sections=[r for r in self.datasets['sections'] if r['source_id']=='N001' and r['text'].startswith('第十三条')]
        self.assertEqual(len(sections),1)
        text=sections[0]['text']
        self.assertIn('至少两项',text)
        self.assertIn('取得助理工程师职称后',text)
        self.assertIn('破格申报',text)
        self.assertIn('满4年',text)

    def test_forms_and_thesis_examples_not_in_policy_body(self):
        company=''.join(r['text'] for r in self.datasets['sections'] if r['source_id']=='N001')
        essay=''.join(r['text'] for r in self.datasets['sections'] if r['source_id']=='N039')
        self.assertNotIn('填  表  说  明',company)
        self.assertNotIn('国有企业“错位”与非国有化问题',essay)
        self.assertIn('页边距',essay)

    def test_reward_values_and_units_match_original_row4(self):
        rows=[r for r in self.datasets['rewards'] if r['source_row']==4]
        self.assertEqual({r['variant']:r['one_time_reward_yuan'] for r in rows},
                         {'机电工程':12000,'公路工程':8000,'水利水电':8000})
        for r in rows:
            self.assertEqual((r['registration_yuan'],r['registration_period']),(500,'月'))
            self.assertEqual((r['usage_cap_yuan'],r['usage_period']),(18000,'年'))
            self.assertIn('安全员B证',r['conditions_raw'])
            self.assertIn('不叠加',r['conditions_raw'])

    def test_reward_all_data_cells_and_colors_cross_checked(self):
        self.assertEqual(self.details['N034']['excel_data_rows'],51)
        self.assertFalse(self.details['N034']['markdown_cell_mismatches'])
        self.assertFalse(self.details['N034']['color_mismatches'])

    def test_no_global_condition_lost_by_query(self):
        result=self.query('rewards',name='一建',limit=1)
        self.assertEqual(len(result),1)
        text=''.join(x['text'] for x in result[0]['shared_conditions'])
        self.assertIn('同一年度内不超过5天',text)
        self.assertIn('超过国家线',text)

    def test_annual_reward_not_silently_extended(self):
        rows=[r for r in self.datasets['rewards'] if r['source_row']==54]
        self.assertEqual(len(rows),1)
        r=rows[0]
        self.assertEqual((r['effective_start'],r['effective_end']),('2025-01-01','2025-12-31'))
        self.assertEqual((r['annual_cap_yuan'],r['per_project_yuan']),(3000,300))

    def test_combinations_retain_alternatives_and_exclusivity(self):
        rows=[r for r in self.datasets['rewards'] if r['serial_raw']=='39']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['combination_alternatives'],[['一级建造师','CISP'],['一级建造师','信息安全工程师']])
        self.assertIn('只能申请一次',rows[0]['conditions_raw'])
        self.assertFalse(rows[0]['title_hint_applies_to_whole_combination'])
        self.assertIn('不代表组合内所有证书',rows[0]['title_hint_note'])

    def test_domestic_tables_cover_54_groups_and_preserve_common_note(self):
        for source in ['N017','N035']:
            rows=[r for r in self.datasets['mappings'] if r['source_id']==source]
            self.assertEqual({r['serial'] for r in rows},set(range(1,55)))
            self.assertEqual(len(rows),62) # includes eight design-specialty branches
        row=next(r for r in self.datasets['mappings'] if r['source_id']=='N017' and r['serial']==4)
        self.assertIn('学历学位、工作年限',row['conditions_raw'])
        self.assertIn('证书批准时间',row['conditions_raw'])
        self.assertTrue(self.query('mappings',name='建造师',source='N017'))
        self.assertTrue(self.query('mappings',name='一建',source='N017'))

    def test_pdf_open_border_row_recovered_and_actual_conflicts_held(self):
        fia=self.query('mappings',name='FIA/FFA')
        self.assertEqual(len(fia),1)
        self.assertEqual(fia[0]['alternate_locator']['extraction'],'open_bottom_row_column_crop')
        self.assertFalse(self.query('mappings',name='CFA'))
        self.assertTrue(self.query('mappings',name='CFA',include_held=True))
        self.assertFalse(self.query('mappings',name='数据管理工程师'))

    def test_beijing_pdf_transcription_has_45_articles_and_no_encoding_garbage(self):
        rows=[r for r in self.datasets['sections'] if r['source_id']=='N019']
        self.assertEqual(sum(bool(re.match('^第[一二三四五六七八九十]+条',r['text'])) for r in rows),45)
        text=''.join(r['text'] for r in rows)
        self.assertIn('本办法自2020年8月1日起施行',text)
        self.assertIn('能够提供符合要求的答辩',text)
        self.assertNotIn('(cid:',text)
        self.assertNotIn('/G21',text)
        self.assertNotIn('社会保障保障局',text)

    def test_dates_keep_distinct_meanings_and_uncertainty(self):
        rows=self.datasets['schedules']
        end=lambda source,event: next(r for r in rows if r['source_id']==source and r['event_type']==event)
        self.assertEqual(end('N044','高工预报名截止')['end_date'],'2026-10-30')
        self.assertEqual(end('N044','学历资历及成果计算截止')['end_date'],'2026-12-31')
        self.assertIsNone(end('N044','高工材料提交截止')['end_date'])
        self.assertIn('预计',end('N044','高工材料提交截止')['certainty'])
        self.assertEqual(end('N021','个人网上申报')['end_date'],'2026-07-30')
        self.assertEqual(end('N023','单位审核推荐')['end_date'],'2026-08-06')

    def test_employee_example_can_retrieve_full_group_condition_with_downloads(self):
        rows=self.query('policy',name='本科 5年',source='N044',limit=5)
        self.assertTrue(rows)
        text=''.join(r['text'] for r in rows)
        self.assertIn('具备工程师职称后从事技术工作满5年',text)
        self.assertTrue(any(r['related_notice_attachments'] for r in rows))
        attachments=self.query('attachments',source='N044',limit=100)
        self.assertTrue(any('推荐书' in r['display_name'] for r in attachments))
        self.assertFalse(any('专家名单' in r['display_name'] for r in attachments))

    def test_no_embedding_fabricated_and_no_contact_phone_leak(self):
        for row in self.datasets['chunks']:
            self.assertIsNone(row['embedding'])
            self.assertEqual(row['embedding_status'],'pending_customer_embedding')
            self.assertFalse(re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)',row['text']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('dataset',type=Path)
    a=p.parse_args()
    DATASET=a.dataset
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DatasetChecks))
    sys.exit(not result.wasSuccessful())
