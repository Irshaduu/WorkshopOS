"""
Completed, Pending Bills and Car Profiles are one shape (2026-08-17).

They are three views of the same thing — a page of cards you scan down — opened
one after the other by the same person, and they had drifted into answering the
"how many across?" question differently: Car Profiles went two-up at 560px and
three-up at 992, the other two waited for Bootstrap's `md` (768) and `lg` (992).
So an iPad Mini held Car Profiles two across and Completed ONE across, at 712px
a card. Same screen, same minute, two answers.

Both numbers are measured rather than chosen, and the reasoning lives in
base.html beside the rule. What is worth a test is only that the three stay
equal: a breakpoint moved on one page and not the others is invisible until
somebody is holding a tablet.

Nothing here executes CSS. These read the declarations, which is the level the
drift actually happens at.
"""
import re

from django.test import TestCase

BASE = 'workshop/templates/workshop/base.html'
CAR_PROFILES = 'workshop/templates/workshop/car_profiles/car_profile_list.html'
#: Every list that is a page of cards. Paid Bills, Job Cards and the High
#: Discount Audit joined on 2026-08-18; the audit card had to be STACKED first,
#: because a two-column card squeezes its own number plate at three across.
BOOTSTRAP_LISTS = (
    'workshop/templates/workshop/completed/completed_list_partial.html',
    'workshop/templates/workshop/jobcard/pending_payments_partial.html',
    'workshop/templates/workshop/jobcard/paid_bills_partial.html',
    'workshop/templates/workshop/jobcard/job_list_partial.html',
    'workshop/templates/workshop/jobcard/audit_high_discounts.html',
)

#: The two widths every card list in the app turns on.
TWO_UP, THREE_UP = 560, 800


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


class TheThreeCardListsShareOneShapeTests(TestCase):

    def test_the_shared_rule_turns_on_the_two_agreed_widths(self):
        """
        `row-cards` in base.html is the one declaration Completed and Pending
        both use — shared rather than copied into each list's own <style>,
        because two of the three are AJAX partials whose styles ride along on
        every search, and because three copies of one number is three chances
        to change one and miss two.
        """
        style = read(BASE)
        for width, columns in ((TWO_UP, '50%'), (THREE_UP, '33.33333333%')):
            block = re.search(
                r'@media \(min-width: %dpx\) \{\s*\.row-cards > \[class\*="col-"\]'
                r'[^}]*\}' % width, style)
            self.assertIsNotNone(
                block, 'base.html has no .row-cards rule at %dpx' % width)
            self.assertIn(columns, block.group(0))

    def test_car_profiles_turns_on_the_same_two_widths(self):
        """
        This grid keeps its own declaration because it is CSS grid and the other
        two are Bootstrap columns. What must never differ is the NUMBERS.
        """
        style = read(CAR_PROFILES)
        for width, columns in ((TWO_UP, 2), (THREE_UP, 3)):
            self.assertRegex(
                style,
                r'@media \(min-width: %dpx\) *\{ *\.cp-grid *\{ *'
                r'grid-template-columns: repeat\(%d, 1fr\)' % (width, columns),
                'Car Profiles does not go %d-up at %dpx' % (columns, width))

    def test_car_profiles_does_not_reach_for_a_fourth_column(self):
        """
        A four-up rule used to sit at 1400px and could never have worked:
        `.cp-page`'s own `max-width: 1400px` is dead inside an 800px
        `.main-content`, so a 1400px screen still had a 768px grid and four
        columns would have made cards NARROWER on the biggest screen than three
        columns are on a tablet. Widening the container is the only thing that
        would earn a fourth, and that is a decision about the whole app.
        """
        self.assertNotIn('repeat(4, 1fr)', read(CAR_PROFILES))

    def test_the_two_bootstrap_lists_hand_their_widths_to_the_shared_rule(self):
        """
        The cards carry a bare `col-12` and no responsive `col-*`. Leaving
        `col-md-6 col-lg-4` on them would be two rules describing one grid,
        agreeing today and free to disagree the first time either is touched —
        with the winner decided by specificity, which is not where a layout
        decision should live.
        """
        for path in BOOTSTRAP_LISTS:
            markup = read(path)
            self.assertIn('row g-3 row-cards', markup,
                          '%s is not on the shared rule' % path)
            for stale in ('col-md-6', 'col-lg-4', 'col-sm-6'):
                self.assertNotIn(
                    stale, markup,
                    '%s still carries %s, which fights the shared rule'
                    % (path, stale))

    def test_the_audit_card_stacks_so_it_survives_three_across(self):
        """
        It was the one two-column card in the family — the car facing its
        figures — and that shape cannot go three across. Measured at a 245px
        card the left block collapsed to 76.9px, which SQUEEZED THE NUMBER PLATE
        from 96.5px to 76.9px, and heights went ragged by 85px as the longer
        names wrapped. Stacked it is 267px at one, two and three across.

        `min-height` on the name is Completed's own answer to that raggedness,
        copied rather than re-derived.
        """
        import re
        style = read('workshop/templates/workshop/jobcard/audit_high_discounts.html')
        style = style.split('<style>', 1)[1].split('</style>', 1)[0]
        style = re.sub(r'/\*.*?\*/', '', style, flags=re.S)

        def rule(selector):
            for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', style):
                if m.group(1).strip() == selector:
                    return m.group(2)
            raise AssertionError('no %s rule' % selector)

        self.assertIn('flex-direction: column', rule('.audit-card'))
        self.assertIn('min-height: 2.5em', rule('.car-name'))
        self.assertNotIn('margin-bottom', rule('.audit-card'),
                         'the card spaces itself as well as the row gutter, '
                         'so every gap is doubled')
