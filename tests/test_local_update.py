"""Coordinate and final-delivery regressions using explicitly synthetic fixtures."""
from pathlib import Path
from argparse import Namespace
import copy
import csv
import json
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import workflow as wf
import rules
import preview_coordinates as coordinates
import statistical_eligibility as stats
import delivery_preferences
import package_skill
import zipfile


class PreviewCoordinatesTests(unittest.TestCase):
    def test_right_panel_header_and_pixel_center_roundtrip(self):
        transform = dict(canvas_size=[2000,780], panel_origin=[1000,26], panel_size=[1000,754])
        p = [[1000,26],[1999,26],[1999,779],[1000,779]]
        self.assertEqual(coordinates.to_original(p, transform, [1936,1460]),
                         [[0,0],[1935,0],[1935,1459],[0,1459]])
        local = coordinates.to_original([[1000+25,26+30],[1100,56],[1100,90]], transform, [1000,754])
        self.assertEqual(local[0], [25,30])

    def test_title_neighbor_nan_and_bad_transform_rejected(self):
        t = dict(canvas_size=[2000,780], panel_origin=[1000,26], panel_size=[1000,754])
        for bad in [[999,40],[1005,25],[2000,50],[1005,float('nan')]]:
            with self.subTest(point=bad), self.assertRaises(ValueError):
                coordinates.to_original([bad,[1100,30],[1100,100]],t,[1936,1460])
        for bad in [dict(t,panel_size=[1001,754]),dict(t,panel_size=[1,754]),dict(t,rotation=90)]:
            with self.assertRaises(ValueError):coordinates.to_original([[1000,26],[1100,26],[1100,100]],bad,[1936,1460])

    def test_conversion_preserves_input_and_rejects_ambiguous_fields(self):
        t = dict(canvas_size=[2000,780], panel_origin=[1000,26], panel_size=[1000,754])
        s = [dict(image_id='TEST',preview_transform=t, worms=[dict(worm_id='W01',points_canvas=[[1010,36],[1100,36],[1100,100]])])]
        old=copy.deepcopy(s); inventory=[dict(image_id='TEST',width=1936,height=1460,source_sha256='synthetic')]
        out=coordinates.convert(s,inventory)
        self.assertEqual(s,old)
        self.assertIn('coordinate_conversion',out[0]['worms'][0])
        out[0]['worms'][0]['points_canvas']=s[0]['worms'][0]['points_canvas']
        s[0]['worms'][0]['points_original']=[[0,0],[1,0],[1,1]]
        with self.assertRaises(ValueError):coordinates.convert(s,inventory)


class PrivatePreferencesTests(unittest.TestCase):
    def test_sharing_filter_omits_local_preferences_and_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'source';(root/'references').mkdir(parents=True)
            (root/'SKILL.md').write_text('---\nname: worm-fluorescence\nmetadata:\n  version: "0.0.0-test"\n---\nSynthetic package filter test',encoding='utf-8')
            for name in ['local_preferences.json','local_environment.json','local_accepted_cases.md']:
                (root/'references'/name).write_text('SYNTHETIC PRIVATE TOKEN',encoding='utf-8')
            (root/'references/public.md').write_text('Public fixture',encoding='utf-8')
            (root/'release_files.json').write_text(json.dumps(dict(schema_version=1,files=[
                'SKILL.md','LICENSE','release_files.json','references/public.md'])),encoding='utf-8')
            (root/'LICENSE').write_text('Synthetic test license',encoding='utf-8')
            with mock.patch.object(package_skill,'ROOT',root):
                result=package_skill.build(Path(temporary)/'output')
            with zipfile.ZipFile(result['archive']) as z:
                self.assertIn('worm-fluorescence/references/public.md',z.namelist())
                self.assertFalse(any('/local_' in name for name in z.namelist()))


class FinalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.run=Path(self.temp.name)/'run';self.run.mkdir()
        raw=Path(self.temp.name)/'synthetic.png';Image.new('RGB',(80,60),(10,10,10)).save(raw)
        self.source=dict(image_id='SYN_001',group='SYN',source_file=raw.name,source_path=str(raw),
                         source_sha256=wf.sha(raw),width=80,height=60)
        self.selection=dict(image_id='SYN_001',status='ready',worms=[dict(worm_id='W01',points_original=[[5,5],[40,5],[40,30],[5,30]])])
        wf.write(self.run/'source_inventory.json',[self.source]);wf.write(self.run/'selections.json',[self.selection])
        wf.write(self.run/'workflow.json',{});wf.write(self.run/'cache.json',{'SYN_001':{}})
        r=rules.template();r['measurement']['confirmed']=True
        r['inclusion'].update(confirmed=True,border_policy='ask',crossing_policy='ask',small_worm_policy='ask')
        r['evidence'].update(user_quote='SYNTHETIC fixture only',scope='unit test');wf.write(self.run/'analysis_rules.json',r)
        stats.command(wf,self.run,Namespace(file=None));doc=wf.read(self.run/'statistical_eligibility_template.json')
        self.row=doc['worms'][0];self.row.update(status='include',object_type='worm',visible_class='complete',
            reason='SYNTHETIC fixture',evidence='SYNTHETIC rule',decision_source='agent_applied_rule')
        self.save_decision()
        directory=wf.image_dir(self.run,self.source);directory.mkdir(parents=True)
        value=dict(Group='SYN',ImageID='SYN_001',SourceFile=raw.name,WormID='SYN_001_W01',Mean=10.1234567890123,
            Min=1,Max=20,Area_px=875,Area_ImageJ=875,AreaUnit='pixel^2',BackgroundSubtracted=False,
            Method='ImageJ_RGB_unweighted',InclusionNote='SYNTHETIC fixture')
        wf.write(directory/'verified_measurements.json',[value])
        wf.csvwrite(directory/'ImageJ_Results.csv',[dict(Label='SYN_001_W01',Area='875.000',Mean='10.123',Min='1',Max='20')])
        (directory/'SYN_001_W01.roi').write_bytes(b'SYNTHETIC unit fixture, not an ImageJ ROI')
        self.prefs=Path(self.temp.name)/'local.json';wf.write(self.prefs,{'precise_csv_name':'最终统计_原始精度.csv'})
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(wf,'profile',return_value={}).start()
        mock.patch.object(wf,'cache_ok',return_value=True).start()
        mock.patch.object(delivery_preferences,'LOCAL',self.prefs).start()

    def save_decision(self):
        wf.write(self.run/'statistical_eligibility.json',{'schema_version':1,'worms':[self.row]})

    def finalize(self):return stats.finalize(wf,self.run,Namespace(audit=False))

    def test_precise_schema_alias_and_display_precision_are_distinct(self):
        self.finalize();folder=self.run/'final_statistics'
        self.assertEqual((folder/'included_precise.csv').read_bytes(),(folder/'最终统计_原始精度.csv').read_bytes())
        with (folder/'included_precise.csv').open(encoding='utf-8-sig') as f:
            reader=csv.DictReader(f);self.assertEqual(reader.fieldnames,stats.PRECISE_FIELDS)
            self.assertEqual(next(reader)['Mean'],'10.1234567890123')
        self.assertIn('10.123,',(folder/'included_ImageJ_Results.csv').read_text(encoding='utf-8-sig'))

    def test_text_and_quality_changes_reuse_frame_but_color_and_corruption_do_not(self):
        first=self.finalize();self.assertEqual(first['preview_generated'],1)
        frame=self.run/'final_statistics/full_frames/SYN_001.png';before=wf.sha(frame)
        self.row.update(reason='new text',quality_flags=['motion_blur'],measurement_caveat='SYNTHETIC motion may affect mean')
        self.save_decision();second=self.finalize()
        self.assertEqual(second['preview_reused'],1);self.assertEqual(wf.sha(frame),before)
        self.assertIn('motion_blur',(self.run/'final_statistics/index.html').read_text(encoding='utf-8'))
        frame.write_bytes(b'broken');self.assertEqual(self.finalize()['preview_generated'],1)
        self.row.update(status='exclude');self.save_decision()
        self.assertEqual(self.finalize()['preview_generated'],1)
        with (self.run/'final_statistics/included_precise.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(next(csv.reader(f)),stats.PRECISE_FIELDS)
        with (self.run/'final_statistics/excluded_no_intensity.csv').open(encoding='utf-8-sig') as f:
            reader=csv.DictReader(f);self.assertNotIn('Mean',reader.fieldnames)
            self.assertEqual(next(reader)['QualityFlags'],'motion_blur')

    def test_motion_without_caveat_and_unsafe_names_fail(self):
        self.row['quality_flags']=['motion_blur']
        with self.assertRaises(ValueError):stats.validate_row(self.row)
        for name in ['../x.csv','included_ImageJ_Results.csv','INCLUDED_IMAGEJ_RESULTS.csv','bad\n.csv']:
            wf.write(self.prefs,{'precise_csv_name':name})
            with self.assertRaises(ValueError):delivery_preferences.load()


if __name__=='__main__':unittest.main()
