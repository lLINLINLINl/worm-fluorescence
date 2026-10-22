"""Fast checks; all images are synthetic and temporary."""
from pathlib import Path
import copy
import hashlib
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rules
import workflow as wf
import metadata


def confirmed():
    value = rules.template()
    value['measurement']['confirmed'] = True
    value['inclusion'].update(confirmed=True, border_policy='ask', crossing_policy='ask', small_worm_policy='ask')
    value['evidence'].update(user_quote='SYNTHETIC TEST ONLY', scope='temporary synthetic fixture')
    return value


class RulesTests(unittest.TestCase):
    def test_no_inherited_confirmation(self):
        self.assertFalse(rules.validate(rules.template()))
        with self.assertRaises(ValueError): rules.validate(rules.template(), require_confirmed=True)

    def test_explicit_choices_and_scope(self):
        value = confirmed()
        self.assertTrue(rules.validate(value, require_confirmed=True))
        for section, key, bad in [('measurement','profile','green_channel'), ('measurement','confirmed','true'),
                                  ('inclusion','minimum_visible_fraction',1.5), ('inclusion','border_policy',None),
                                  ('evidence','scope','')]:
            changed = copy.deepcopy(value)
            changed[section][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): rules.validate(changed, True)


class GeometryTests(unittest.TestCase):
    def test_pixel_centers_round_trip(self):
        source = dict(image_id='TEST_001', width=1936, height=1460)
        selection = dict(view_width=1000, view_height=754)
        worm = dict(worm_id='W01', points_view=[[0,0],[999,0],[999,753],[0,753]])
        self.assertEqual(wf.points(selection, source, worm).tolist(), [[0,0],[1935,0],[1935,1459],[0,1459]])

    def test_invalid_and_outside_polygons_fail(self):
        source = dict(image_id='TEST_001', width=100, height=100)
        for points in [[[0,0],[10,10],[0,10],[10,0]], [[0,0],[100,0],[0,10]]]:
            with self.subTest(points=points), self.assertRaises(ValueError):
                wf.points({}, source, dict(worm_id='W01', points_original=points))

    def test_16bit_not_silently_downconverted(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix in ('.png', '.tif'):
                path = Path(folder) / ('depth16' + suffix)
                Image.fromarray(np.arange(100, dtype=np.uint16).reshape(10,10)).save(path)
                source = dict(source_path=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), width=10, height=10)
                with self.subTest(suffix=suffix), self.assertRaises(ValueError): wf.check_source(source)

    def test_source_change_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.png'
            Image.new('RGB',(10,10)).save(path)
            source = dict(source_path=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), width=10, height=10)
            Image.new('RGB',(10,10),(1,2,3)).save(path)
            with self.assertRaises(ValueError): wf.check_source(source)


class LineageTests(unittest.TestCase):
    def test_same_source_cannot_cross_splits(self):
        inventory = [dict(image_id='A',group='X',source_sha256='same'),dict(image_id='B',group='Y',source_sha256='same')]
        document = metadata.template()
        document['images'] = {'A':dict(split='TRAIN'), 'B':dict(split='TEST')}
        with self.assertRaises(ValueError): metadata.lineages(Path('.'), inventory, document)

    def test_declared_same_animal_linked(self):
        inventory = [dict(image_id='A',group='X',source_sha256='one'),dict(image_id='B',group='X',source_sha256='two')]
        document = metadata.template()
        document['dataset'].update(experiment_id='synthetic', animal_id='animal1')
        result = metadata.lineages(Path('.'),inventory,document)
        self.assertEqual(result['A']['SplitGroup'], result['B']['SplitGroup'])


if __name__ == '__main__': unittest.main()
