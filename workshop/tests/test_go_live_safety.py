"""
What keeps the LIVE database safe from the tooling built while the system was
explored on Render, Railway, Neon and SQLite.

1. DEMO SEEDERS REFUSE TO RUN ANYWHERE BUT DEVELOPMENT. They write made-up job
   cards, wages and rent, several have no dry run, and one empties the
   inventory. On Railway a single mistyped console command would put them into
   the real books.
2. THE RENDER DEMO SETTINGS ARE GONE. `DJANGO_ENV=render_demo` used to boot the
   app on a committed SQLite file with console email, so a typo in that variable
   on the real host would have run the workshop on demo data. It now refuses to
   start, like any other unknown value.
3. NO MODEL CHANGE SHIPS WITHOUT ITS MIGRATION. A field pushed without its
   migration file makes every page reading that model fail with `column does not
   exist` the moment the code reaches Railway.
"""
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from inventory.models import Category, Item
from workshop.management.commands._dev_only import refuse_outside_development
from workshop.models import JobCard, RentDeposit, RentRate, SalaryAdvance, SalaryPayment

# Each demo seeder, with the options that would make it WRITE — so the refusal is
# proved to come before the command's own dry-run switch, not because of it.
DEMO_SEEDERS = {
    'seed_dummy_data': {},
    'seed_meeting_data': {'yes': True},
    'seed_salary_data': {},
    'seed_rent_data': {'yes': True},
    'seed_demo_meeting': {},
}


class DemoSeedersRefuseOutsideDevelopmentTests(TestCase):

    def _rows(self):
        return (JobCard.objects.count(), RentDeposit.objects.count(), RentRate.objects.count(),
                SalaryAdvance.objects.count(), SalaryPayment.objects.count(),
                Category.objects.count(), Item.objects.count())

    def test_every_demo_seeder_refuses_under_production_and_writes_nothing(self):
        before = self._rows()
        for name, options in DEMO_SEEDERS.items():
            with self.subTest(command=name), mock.patch.dict(os.environ, {'DJANGO_ENV': 'production'}):
                with self.assertRaises(CommandError) as ctx:
                    call_command(name, stdout=StringIO(), stderr=StringIO(), **options)
                self.assertIn('DJANGO_ENV=development', str(ctx.exception))
        self.assertEqual(self._rows(), before)

    def test_an_unset_environment_is_refused_too(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('DJANGO_ENV', None)
            with self.assertRaises(CommandError):
                refuse_outside_development('seed_dummy_data')

    def test_development_is_let_through(self):
        with mock.patch.dict(os.environ, {'DJANGO_ENV': 'development'}):
            self.assertIsNone(refuse_outside_development('seed_dummy_data'))


class TheRenderDemoSettingsAreGoneTests(SimpleTestCase):

    def test_a_render_demo_environment_refuses_to_start(self):
        env = dict(os.environ, DJANGO_ENV='render_demo')
        result = subprocess.run(
            [sys.executable, '-c', 'import formulad_workshop.settings'],
            cwd=str(settings.BASE_DIR), env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)

    def test_no_render_file_is_left_in_the_repository(self):
        base = Path(settings.BASE_DIR)
        self.assertFalse((base / 'formulad_workshop' / 'settings' / 'render_demo.py').exists())
        self.assertFalse((base / 'render.yaml').exists())


class NoModelChangeShipsWithoutItsMigrationTests(TestCase):

    def test_makemigrations_finds_nothing_left_to_write(self):
        out = StringIO()
        try:
            call_command('makemigrations', check=True, dry_run=True, stdout=out, stderr=StringIO())
        except SystemExit:
            self.fail('A model change has no migration file — run makemigrations:\n' + out.getvalue())
