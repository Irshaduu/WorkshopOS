"""
The car card's ⋮ on the home board and on Completed — one control, 2026-09-29.

The owner's report: the rows inside both menus were too small to hit, the order
was wrong, and Completed's job-card row wore a red dashed warning for what is an
everyday act. Nothing in the Django suite executes CSS, so the sizes are held
by reading the one declaration both pages now share.
"""
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from workshop.models import JobCard

STYLE = Path(settings.BASE_DIR) / 'static' / 'css' / 'style.css'
TEMPLATES = Path(settings.BASE_DIR) / 'workshop' / 'templates' / 'workshop'


def _menu(page):
    """The first card-menu <ul> on the page, and nothing around it."""
    return page.split('class="dropdown-menu dropdown-menu-end card-menu', 1)[1].split('</ul>', 1)[0]


class TheCompletedMenuOpensTheJobCardFirstTests(TestCase):
    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.office = User.objects.create_user('menu_office', password='pw')
        self.office.groups.add(Group.objects.get(name='Office'))
        self.job = JobCard.objects.create(registration_number='KL07BZ4646',
                                          admitted_date=date.today(),
                                          brand_name='Audi', model_name='A4')
        self.job.mark_completed()

    def menu(self):
        self.client.force_login(self.office)
        return _menu(self.client.get(reverse('completed_list')).content.decode())

    def test_the_job_card_comes_before_undo(self):
        menu = self.menu()
        self.assertLess(menu.index(reverse('jobcard_edit', args=[self.job.pk])),
                        menu.index(reverse('undo_completed', args=[self.job.pk])))

    def test_the_job_card_row_is_not_drawn_as_a_warning(self):
        menu = self.menu()
        row = menu.split(reverse('jobcard_edit', args=[self.job.pk]), 1)[1].split('</a>', 1)[0]
        self.assertIn('Open Job Card', row)
        self.assertNotIn('exclamation-triangle', row)
        self.assertNotIn('access-jobcard', menu)

    def test_a_settled_card_says_its_form_opens_locked(self):
        self.assertNotIn('card-menu-lock', self.menu())
        JobCard.objects.filter(pk=self.job.pk).update(payment_status='PAID')
        self.assertIn('card-menu-lock', self.menu())


class BothCardMenusAreOneControlTests(TestCase):
    def rule(self, selector):
        css = STYLE.read_text(encoding='utf-8')
        return re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css).group(1)

    def test_both_pages_draw_the_shared_menu_and_trigger(self):
        for name in ('dashboard/dashboard_home.html',
                     'completed/completed_list_partial.html'):
            with self.subTest(template=name):
                source = (TEMPLATES / name).read_text(encoding='utf-8')
                self.assertIn('class="card-dots"', source)
                self.assertIn('card-menu', source)

    def test_a_row_is_a_thumb_sized_target(self):
        self.assertIn('min-height: 44px', self.rule('.card-menu .dropdown-item'))

    def test_the_trigger_grows_for_a_finger(self):
        css = STYLE.read_text(encoding='utf-8')
        self.assertRegex(css, r'@media \(hover: none\) \{\s*\.card-dots \{ min-width: 44px; min-height: 44px; \}')

    def test_the_trigger_hover_cannot_stick_on_a_touch_screen(self):
        css = STYLE.read_text(encoding='utf-8')
        self.assertNotRegex(css, r'(?m)^\.card-dots:hover')

    def test_the_row_colours_are_the_measured_dark_ones(self):
        """#16a34a and #d97706 are 3.3:1 and 3.2:1 on white — too faint for a
        ~14.7px label. #15803d and #b45309 are 5.0:1."""
        self.assertIn('#15803d', self.rule('.card-menu .card-menu-go'))
        self.assertIn('#b45309', self.rule('.card-menu .card-menu-warn'))

    def test_neither_page_keeps_a_copy_of_its_own(self):
        for name, old in (('dashboard/dashboard_home.html', '.dot-menu-btn'),
                          ('completed/completed_list_partial.html', '.del-dots-btn')):
            with self.subTest(template=name):
                source = (TEMPLATES / name).read_text(encoding='utf-8')
                self.assertIsNone(re.search(re.escape(old) + r'\s*\{', source))
