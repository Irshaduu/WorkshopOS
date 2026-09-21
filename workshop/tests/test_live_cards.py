"""
AUD-0007 — "which job cards count?" has ONE answer: `models.live_cards()`.

It was hand-typed as `is_deleted=False` / `job_card__is_deleted=False` /
`not card.is_deleted` in 25 places across 13 files. The column is DORMANT —
cards are hard-deleted now, nothing writes True — so every copy was right and
none of them did anything. The danger was the day somebody revives a trash, or
changes what "live" means: the rule had to be found 25 times.

The load-bearing test is the SCAN, because a twenty-sixth copy is invisible to
every other kind of test.
"""
import ast
import io
from datetime import date
from decimal import Decimal as D

from django.conf import settings
from django.db.models import Q
from django.test import TestCase

from workshop.models import JobCard, JobCardSpareItem, live_cards


class LiveCardsMeansWhatItSaysTests(TestCase):
    """The rule, in each of the shapes it is asked in."""

    def setUp(self):
        self.live = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota',
            model_name='Corolla', registration_number='KL01A0001')
        self.flagged = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota',
            model_name='Corolla', registration_number='KL01A0002')
        # `.update()`, deliberately: nothing in the app writes this flag, and a
        # save would wake the dormant soft-delete signals for no reason.
        JobCard.objects.filter(pk=self.flagged.pk).update(is_deleted=True)
        self.flagged.refresh_from_db()

    def test_it_keeps_a_live_card_and_drops_a_flagged_one(self):
        self.assertEqual(
            list(JobCard.objects.filter(live_cards()).values_list('pk', flat=True)),
            [self.live.pk])

    def test_a_card_in_hand_answers_the_same(self):
        self.assertTrue(self.live.is_live)
        self.assertFalse(self.flagged.is_live)

    def test_it_reaches_through_a_relation(self):
        for card in (self.live, self.flagged):
            JobCardSpareItem.objects.create(
                job_card=card, source=JobCardSpareItem.SOURCE_SHOP,
                spare_part_name='Brake Pad', quantity=D('1'), total_price=D('900'))
        rows = JobCardSpareItem.objects.filter(live_cards('job_card__'))
        self.assertEqual({r.job_card_id for r in rows}, {self.live.pk})

    def test_it_combines_with_other_conditions(self):
        both = JobCard.objects.filter(Q(completed=False) & live_cards())
        self.assertEqual(list(both.values_list('pk', flat=True)), [self.live.pk])

    def test_a_flagged_card_does_not_block_its_plate(self):
        """`get_active_conflict` — the one-active-card-per-plate rule — reads it too."""
        self.assertEqual(JobCard.get_active_conflict('KL01A0001'), self.live)
        self.assertIsNone(JobCard.get_active_conflict('KL01A0002'))


class NobodyRestatesTheLiveCardRuleTests(TestCase):
    """
    Nothing outside `models.py` names the column. `inventory/signals.py` is the
    one exception and it is not a copy of the rule: it is the dormant soft-delete
    reversal, which has to read the flag to know whether it moved.
    """

    ALLOWED = {
        'workshop/models.py',
        'inventory/signals.py',
    }

    def _app_files(self):
        base = settings.BASE_DIR
        for app in ('workshop', 'inventory', 'formulad_workshop'):
            for path in (base / app).rglob('*.py'):
                rel = path.relative_to(base).as_posix()
                if '/tests' in rel or rel.rsplit('/', 1)[-1].startswith('test'):
                    continue
                if '/migrations/' in rel:
                    continue
                yield rel, io.open(path, encoding='utf-8').read()

    def _templates(self):
        base = settings.BASE_DIR
        for app in ('workshop', 'inventory'):
            for path in (base / app / 'templates').rglob('*.html'):
                yield path.relative_to(base).as_posix(), io.open(path, encoding='utf-8').read()

    def test_the_scan_actually_reads_the_codebase(self):
        """A floor, so a scan that reads nothing cannot pass for being clean."""
        files = dict(self._app_files())
        self.assertGreater(len(files), 40, 'the scan read almost nothing - check BASE_DIR')
        self.assertIn('workshop/models.py', files)
        self.assertGreater(len(list(self._templates())), 80)

    def test_only_models_names_the_column(self):
        offenders = []
        for rel, src in self._app_files():
            if rel in self.ALLOWED:
                continue
            for node in ast.walk(ast.parse(src)):
                # is_deleted=False, job_card__is_deleted=False
                if isinstance(node, ast.keyword) and node.arg and 'is_deleted' in node.arg:
                    offenders.append('%s:%d  %s=' % (rel, node.value.lineno, node.arg))
                # card.is_deleted
                elif isinstance(node, ast.Attribute) and node.attr == 'is_deleted':
                    offenders.append('%s:%d  .is_deleted' % (rel, node.lineno))
                # .values('is_deleted'), F('job_card__is_deleted') - a lookup
                # string, never prose in a docstring
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and node.value.endswith('is_deleted') and node.value.replace('_', '').isalnum()):
                    offenders.append('%s:%d  %r' % (rel, node.lineno, node.value))
        self.assertEqual(offenders, [], 'use models.live_cards() / card.is_live instead')

    def test_no_template_reads_the_column(self):
        offenders = [rel for rel, text in self._templates() if 'is_deleted' in text]
        self.assertEqual(offenders, [], 'use card.is_live instead')
