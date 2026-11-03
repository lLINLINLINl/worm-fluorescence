"""Read-only revision comparisons using synthetic measurements and source images."""
from argparse import Namespace
from pathlib import Path
import csv
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import workflow as wf
import review_assets
import rules
import statistical_eligibility as stats


class RevisionComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.source_path = self.root / 'synthetic.tif'
        self.rgb = np.zeros((60, 80, 3), dtype=np.uint8)
        self.rgb[:, :, 1] = np.arange(80, dtype=np.uint8) * 3
        self.write_source()
        self.selection = dict(image_id='DEMO_001', status='ready', worms=[
            dict(worm_id='W01', points_original=[[10, 10], [30, 10], [30, 22], [10, 22]]),
            dict(worm_id='W02', points_original=[[42, 30], [52, 30], [52, 38], [42, 38]])])
        wf.write(self.run / 'selections.json', [self.selection])
        wf.write(self.run / 'workflow.json', {})
        policy = rules.template()
        policy['measurement']['confirmed'] = True
        policy['inclusion'].update(confirmed=True, border_policy='ask', crossing_policy='ask', small_worm_policy='ask')
        policy['evidence'].update(user_quote='SYNTHETIC FIXTURE ONLY', scope='synthetic unit test')
        wf.write(self.run / 'analysis_rules.json', policy)
        self.profile = dict(synthetic_test_profile=True)
        patch = mock.patch.object(wf, 'profile', return_value=self.profile)
        patch.start()
        self.addCleanup(patch.stop)
        self.save_measurements()
        self.decide()

    def write_source(self):
        Image.fromarray(self.rgb).save(self.source_path)
        self.source = dict(image_id='DEMO_001', group='DEMO', source_file=self.source_path.name,
                           source_path=str(self.source_path), source_sha256=wf.sha(self.source_path), width=80, height=60)
        wf.write(self.run / 'source_inventory.json', [self.source])

    def save_measurements(self):
        # This fixture models already-measured rows; native ImageJ integration is
        # exercised separately by scripts/verify_install.py.
        wf.write(self.run / 'selections.json', [self.selection])
        directory = self.run / 'imagej/DEMO/DEMO_001'
        mask_dir = self.run / 'training/instance_masks/DEMO_001'
        directory.mkdir(parents=True, exist_ok=True)
        mask_dir.mkdir(parents=True, exist_ok=True)
        values, artifacts = [], {}
        gray = (self.rgb.astype('uint16').sum(axis=2) + 1) // 3
        for worm in self.selection['worms']:
            points = np.array(worm['points_original'])
            x0, y0 = points.min(axis=0)
            x1, y1 = points.max(axis=0)
            mask = np.zeros((60, 80), dtype=bool)
            mask[y0:y1, x0:x1] = True
            label = 'DEMO_001_' + worm['worm_id']
            path = mask_dir / (label + '.png')
            Image.fromarray(mask.astype('uint8') * 255).save(path)
            artifacts[path.relative_to(self.run).as_posix()] = wf.sha(path)
            values.append(dict(WormID=label, Mean=float(gray[mask].mean()), Area_px=int(mask.sum())))
        self.measured = directory / 'verified_measurements.json'
        wf.write(self.measured, values)
        artifacts[self.measured.relative_to(self.run).as_posix()] = wf.sha(self.measured)
        wf.write(self.run / 'cache.json', {'DEMO_001': dict(
            calculation_key=wf.calculation_key(self.selection, self.source, self.profile), artifacts=artifacts)})
        return {v['WormID']: v for v in values}

    def decide(self, first='include'):
        stats.command(wf, self.run, Namespace(file=None))
        document = wf.read(self.run / 'statistical_eligibility_template.json')
        for row in document['worms']:
            row.update(status=first if row['worm_id']=='W01' else 'include', object_type='worm',
                       visible_class='complete', reason='SYNTHETIC decision', evidence='SYNTHETIC ONLY',
                       decision_source='agent_applied_rule')
        path = self.root / 'decisions.json'
        wf.write(path, document)
        stats.command(wf, self.run, Namespace(file=str(path)))

    def inspect(self, padding=None):
        result = review_assets.build(wf, self.run, Namespace(images=None, context_padding_px=padding))
        rows = wf.read(self.run / 'review/qc_report.json')['rows']
        return result, {r['WormID']: r for r in rows}

    def expand(self, right):
        self.selection['worms'][0]['points_original'][1][0] = right
        self.selection['worms'][0]['points_original'][2][0] = right
        return self.save_measurements()

    def test_first_inspection_does_not_invent_previous_values_or_edit_inputs(self):
        fingerprints = {p: wf.sha(p) for p in [self.source_path, self.measured, self.run/'statistical_eligibility.json']}
        _, rows = self.inspect()
        row = rows['DEMO_001_W01']
        self.assertIsNone(row['PreviousMean'])
        self.assertIsNone(row['PreviousArea_px'])
        self.assertIsNone(row['PreviousStatisticsStatus'])
        self.assertEqual(row['StatisticsStatus'], 'include')
        self.assertEqual(fingerprints, {p: wf.sha(p) for p in fingerprints})

    def test_boundary_revision_compares_values_and_exposes_stale_eligibility(self):
        old = self.save_measurements()
        self.inspect()
        current = self.expand(35)
        decision_hash = wf.sha(self.run/'statistical_eligibility.json')
        _, rows = self.inspect()
        row = rows['DEMO_001_W01']
        self.assertEqual(row['PreviousMean'], old['DEMO_001_W01']['Mean'])
        self.assertEqual(row['PreviousArea_px'], old['DEMO_001_W01']['Area_px'])
        self.assertEqual(row['MeanChange'], current['DEMO_001_W01']['Mean'] - old['DEMO_001_W01']['Mean'])
        self.assertEqual((row['PreviousStatisticsStatus'], row['StatisticsStatus']), ('include', 'pending'))
        self.assertTrue(row['StatisticsStatusChanged'])
        self.assertEqual(rows['DEMO_001_W02']['MeanChange'], 0)
        self.assertFalse(rows['DEMO_001_W02']['StatisticsStatusChanged'])
        self.assertEqual(decision_hash, wf.sha(self.run/'statistical_eligibility.json'))
        with self.assertRaisesRegex(ValueError, 'pending/stale'):
            stats.finalize(wf, self.run, Namespace(audit=False))

    def test_repeated_inspection_and_display_change_keep_comparison_baseline(self):
        self.inspect()
        self.expand(35)
        _, first = self.inspect()
        cached, second = self.inspect()
        self.assertEqual(cached['reused_images'], 1)
        self.assertEqual(first, second)
        _, changed_display = self.inspect(padding=5)
        self.assertEqual(first['DEMO_001_W01']['PreviousMean'], changed_display['DEMO_001_W01']['PreviousMean'])
        self.assertEqual(first['DEMO_001_W01']['MeanChange'], changed_display['DEMO_001_W01']['MeanChange'])

    def test_successive_revision_compares_with_immediate_previous_measurement(self):
        self.inspect()
        previous = self.expand(35)
        self.inspect()
        self.expand(39)
        _, rows = self.inspect()
        self.assertEqual(rows['DEMO_001_W01']['PreviousMean'], previous['DEMO_001_W01']['Mean'])
        self.assertEqual(rows['DEMO_001_W01']['PreviousArea_px'], previous['DEMO_001_W01']['Area_px'])

    def test_qualification_only_change_refreshes_report_without_measurement_edit(self):
        self.inspect()
        measurement_hash = wf.sha(self.measured)
        source_hash = wf.sha(self.source_path)
        self.decide(first='exclude')
        result, rows = self.inspect()
        self.assertEqual(result['generated_images'], 1)
        row = rows['DEMO_001_W01']
        self.assertEqual((row['PreviousStatisticsStatus'], row['StatisticsStatus']), ('include', 'exclude'))
        self.assertEqual(row['MeanChange'], 0)
        self.assertEqual(measurement_hash, wf.sha(self.measured))
        self.assertEqual(source_hash, wf.sha(self.source_path))
        self.assertEqual(self.inspect()[0]['reused_images'], 1)

    def test_reconfirming_eligibility_preserves_numerical_boundary_revision(self):
        old = self.save_measurements()
        self.inspect()
        self.expand(35)
        self.inspect()
        self.decide()
        _, rows = self.inspect()
        row = rows['DEMO_001_W01']
        self.assertEqual(row['PreviousMean'], old['DEMO_001_W01']['Mean'])
        self.assertEqual(row['PreviousArea_px'], old['DEMO_001_W01']['Area_px'])
        self.assertGreater(row['MeanChange'], 0)
        self.assertEqual((row['PreviousStatisticsStatus'], row['StatisticsStatus']), ('pending', 'include'))

    def test_legacy_area_history_has_no_fabricated_mean(self):
        old = self.save_measurements()
        wf.write(self.run/'review/area_references.json', {'DEMO_001': dict(
            source_sha256=self.source['source_sha256'], boundary_key=wf.boundary_key(self.selection, self.source),
            areas={k: v['Area_px'] for k, v in old.items()}, comparison={})})
        self.expand(35)
        _, rows = self.inspect()
        row = rows['DEMO_001_W01']
        self.assertEqual(row['PreviousArea_px'], old['DEMO_001_W01']['Area_px'])
        self.assertIsNone(row['PreviousMean'])
        self.assertIsNone(row['MeanChange'])
        self.assertIsNone(row['PreviousStatisticsStatus'])

    def test_zero_previous_mean_keeps_absolute_delta_without_dividing_by_zero(self):
        self.rgb[10:22, 10:30] = 0
        self.write_source()
        self.save_measurements()
        self.decide()
        self.inspect()
        self.expand(35)
        _, rows = self.inspect()
        row = rows['DEMO_001_W01']
        self.assertEqual(row['PreviousMean'], 0)
        self.assertGreater(row['MeanChange'], 0)
        self.assertIsNone(row['MeanChangeFraction'])

    def test_different_original_source_resets_all_comparisons(self):
        self.inspect()
        self.rgb[:, :, 1] += 1
        self.write_source()
        self.save_measurements()
        _, rows = self.inspect()
        for row in rows.values():
            self.assertIsNone(row['PreviousMean'])
            self.assertIsNone(row['PreviousArea_px'])
            self.assertIsNone(row['PreviousStatisticsStatus'])


if __name__ == '__main__':
    unittest.main()
