"""Behavioral checks for review scope, stale decisions, and pre-measure views."""
from argparse import Namespace
from pathlib import Path
import copy
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import workflow as wf
import rules
import scoped_reviews
import statistical_eligibility as stats


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        image = self.root / 'source.png'
        Image.fromarray(np.arange(120 * 90 * 3, dtype=np.uint8).reshape(90, 120, 3)).save(image)
        self.source = dict(image_id='DEMO_001', group='DEMO', source_file=image.name, source_path=str(image),
                           source_sha256=wf.sha(image), width=120, height=90)
        self.selection = dict(image_id='DEMO_001', status='ready', worms=[
            dict(worm_id='W01', points_original=[[5, 5], [70, 5], [70, 20], [5, 20]]),
            dict(worm_id='W02', points_original=[[10, 40], [20, 40], [20, 50], [10, 50]])])
        wf.write(self.run / 'source_inventory.json', [self.source])
        wf.write(self.run / 'selections.json', [self.selection])
        policy = rules.template()
        policy['measurement']['confirmed'] = True
        policy['inclusion'].update(confirmed=True, border_policy='visible_identifiable_body_exclude_short',
                                   crossing_policy='independent_shared_pixels', small_worm_policy='include_unless_abnormal')
        policy['evidence'].update(user_quote='SYNTHETIC ONLY: retain small complete worms, exclude short fragments', scope='fixture')
        wf.write(self.run / 'analysis_rules.json', policy)
        wf.write(self.run / 'workflow.json', {})

    def review(self, scope, decision='accept', worms=None, feature=None):
        return wf.review(Namespace(run=str(self.run), images=['DEMO_001'], scope=scope, decision=decision,
                                   worms=worms, feature=feature, evidence='SYNTHETIC TEST ONLY'))

    def accepted(self, index):
        return scoped_reviews.worm_accepted(wf, self.run, self.source, self.selection, self.selection['worms'][index])

    def filled_template(self):
        stats.command(wf, self.run, Namespace(file=None))
        document = wf.read(self.run / 'statistical_eligibility_template.json')
        for row in document['worms']:
            row.update(status='include', object_type='worm', visible_class='complete', reason='SYNTHETIC small complete target',
                       evidence='SYNTHETIC rule', decision_source='agent_applied_rule')
        return document

    def import_decisions(self, document):
        path = self.root / 'decisions.json'
        wf.write(path, document)
        return stats.command(wf, self.run, Namespace(file=str(path)))

    def test_feature_and_worm_do_not_promote_frame(self):
        self.review('feature', worms=['W01'], feature='tail_B')
        self.assertFalse(self.accepted(0))
        self.assertFalse(scoped_reviews.frame_accepted(wf, self.run, self.source, self.selection))
        self.review('worm', worms=['W01'])
        self.assertTrue(self.accepted(0))
        self.assertFalse(self.accepted(1))
        self.assertFalse(scoped_reviews.frame_accepted(wf, self.run, self.source, self.selection))

    def test_local_revocation_blocks_frame_and_new_frame_can_resolve(self):
        self.review('frame')
        self.assertTrue(self.accepted(0))
        self.review('feature', decision='revise', worms=['W01'], feature='tail_B')
        self.assertFalse(self.accepted(0))
        self.assertTrue(self.accepted(1))
        self.assertFalse(scoped_reviews.frame_accepted(wf, self.run, self.source, self.selection))
        self.review('frame')
        self.assertTrue(self.accepted(0))

    def test_bad_scope_cannot_mutate_reviews(self):
        self.review('frame')
        checksum = wf.sha(self.run / 'reviews.json')
        with self.assertRaises(ValueError):
            self.review('frame', worms=['W01'])
        self.assertEqual(checksum, wf.sha(self.run / 'reviews.json'))

    def test_worm_acceptance_binds_coordinates(self):
        self.review('worm', worms=['W01'])
        self.selection['worms'][0]['points_original'][0][0] += 1
        self.assertFalse(self.accepted(0))

    def test_normal_small_complete_worm_not_removed_by_size(self):
        self.import_decisions(self.filled_template())
        rows = stats.resolve(wf, self.run)[3]
        self.assertEqual(rows[('DEMO_001', 'W02')]['effective_status'], 'include')

    def test_single_boundary_change_only_invalidates_changed_roi(self):
        self.import_decisions(self.filled_template())
        self.selection['worms'][0]['points_original'][0][0] += 1
        wf.write(self.run / 'selections.json', [self.selection])
        result = stats.command(wf, self.run, Namespace(file=None))
        self.assertEqual(result['pending'], [dict(image_id='DEMO_001', worm_id='W01')])
        self.assertEqual(result['affected'][0]['worm_id'], 'W01')
        with self.assertRaisesRegex(ValueError, 'pending/stale'):
            stats.finalize(wf, self.run, Namespace(audit=False))

    def test_rule_change_does_not_silently_reuse_previous_eligibility(self):
        document = self.filled_template()
        self.import_decisions(document)
        policy = wf.read(self.run / 'analysis_rules.json')
        policy['inclusion']['fragment_policy'] = 'all_identifiable'
        wf.write(self.run / 'analysis_rules.json', policy)
        result = stats.command(wf, self.run, Namespace(file=None))
        self.assertEqual(len(result['affected']), 2)
        with self.assertRaisesRegex(ValueError, 'Stale/missing'):
            self.import_decisions(document)

    def test_uncertain_include_and_duplicate_rows_refused(self):
        original = self.filled_template()
        bad = copy.deepcopy(original)
        bad['worms'][0]['object_type'] = 'uncertain'
        with self.assertRaisesRegex(ValueError, 'Uncertain'):
            self.import_decisions(bad)
        bad = copy.deepcopy(original)
        bad['worms'].append(bad['worms'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.import_decisions(bad)
        self.assertFalse((self.run / 'statistical_eligibility.json').exists())

    def test_tampered_stored_state_not_treated_as_exclusion(self):
        self.import_decisions(self.filled_template())
        doc = wf.read(self.run / 'statistical_eligibility.json')
        doc['worms'][0]['status'] = 'typo'
        wf.write(self.run / 'statistical_eligibility.json', doc)
        with self.assertRaisesRegex(ValueError, 'Invalid eligibility'):
            stats.resolve(wf, self.run)

    def test_question_works_before_measure_and_retains_raw_pixels(self):
        before = wf.sha(Path(self.source['source_path']))
        args = Namespace(run=str(self.run), images=['DEMO_001'], worms=['W01'], box=[1, 2, 80, 30],
                         markers=None, factor=3, note='SYNTHETIC boundary question')
        first = wf.question(args)
        files = first['images'][0]['files']
        with Image.open(self.source['source_path']) as source:
            with Image.open(files['detail_raw']) as detail, Image.open(files['full']) as full:
                np.testing.assert_array_equal(np.asarray(detail), np.asarray(source)[2:30, 1:80])
                self.assertEqual(full.size, source.size)
        self.assertEqual(before, wf.sha(Path(self.source['source_path'])))
        self.assertFalse((self.run / 'imagej').exists())
        self.selection['worms'][0]['points_original'][0][0] += 1
        wf.write(self.run / 'selections.json', [self.selection])
        second = wf.question(args)
        self.assertNotEqual(files['full'], second['images'][0]['files']['full'])
        self.assertTrue(Path(files['full']).is_file())

    def test_publish_recovers_from_transient_windows_file_lock(self):
        stage = self.run / '.staging/new'
        stage.mkdir(parents=True)
        (stage / 'new.txt').write_text('new')
        original = stats.os.replace
        attempts = []
        def flaky(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError('SYNTHETIC transient lock')
            return original(source, destination)
        with mock.patch.object(stats.os, 'replace', side_effect=flaky), mock.patch.object(stats.time, 'sleep'):
            final = stats.publish(wf, self.run, stage)
        self.assertEqual((final / 'new.txt').read_text(), 'new')

    def test_failed_publication_keeps_previous_delivery_and_staging(self):
        final = self.run / 'final_statistics'
        final.mkdir()
        (final / 'old.txt').write_text('old')
        stage = self.run / '.staging/new'
        stage.mkdir(parents=True)
        (stage / 'new.txt').write_text('new')
        original = stats.os.replace
        def fail_new(source, destination):
            if source == stage:
                raise PermissionError('SYNTHETIC persistent lock')
            return original(source, destination)
        with mock.patch.object(stats.os, 'replace', side_effect=fail_new), mock.patch.object(stats.time, 'sleep'):
            with self.assertRaises(PermissionError):
                stats.publish(wf, self.run, stage)
        self.assertEqual((final / 'old.txt').read_text(), 'old')
        self.assertEqual((stage / 'new.txt').read_text(), 'new')


if __name__ == '__main__':
    unittest.main()
