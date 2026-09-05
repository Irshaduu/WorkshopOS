"""
The app asks its own questions now — no browser dialog anywhere.

Twenty-one native dialogs were left when this was written: sixteen
`window.confirm()`, four `alert()` and one `prompt()`. They opened with
"127.0.0.1:8000 says", which is the browser talking rather than the app, and
they rendered the question, the reason and the way out as one flat grey block
that cannot carry a glyph, a colour or a field.

⚠ EVERY TEST HERE IS A MARKUP OR SOURCE ASSERTION, AND THAT IS THE POINT.
Nothing in the Django suite executes a line of CSS or JavaScript, so a card that
opens behind the photo lightbox, a theme with no colour, or a form that quietly
lost its question all leave every functional test green. These are the only
things that notice.
"""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse


TEMPLATE_ROOTS = [settings.BASE_DIR / 'workshop', settings.BASE_DIR / 'inventory']

# `confirm(` also matches `wsConfirm(`, `confirmSubmit(` and `openAddConfirm()`,
# and `alert(` matches nothing in `alert-danger` — so the boundary in front is
# load-bearing rather than tidiness.
NATIVE_CALL = re.compile(r'(?<![.\w$])(?:window\.)?(confirm|alert|prompt)\s*\(')

SCRIPT_BLOCK = re.compile(r'<script\b[^>]*>(.*?)</script>', re.S | re.I)
JS_LINE_COMMENT = re.compile(r'//[^\n]*')
JS_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
DJANGO_COMMENT = re.compile(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}', re.S)


def _templates():
    for root in TEMPLATE_ROOTS:
        for path in sorted(root.rglob('*.html')):
            yield path


def _executable_js(text):
    """Everything in a template that a browser would actually run.

    Comments are stripped because this codebase explains its own history in
    them, and several say the words `window.confirm()` on purpose — describing
    what was removed must not read as still doing it.
    """
    text = DJANGO_COMMENT.sub(' ', text)
    js = ' '.join(SCRIPT_BLOCK.findall(text))
    js = JS_BLOCK_COMMENT.sub(' ', js)
    js = JS_LINE_COMMENT.sub(' ', js)
    return js


def _inline_handlers(text):
    """`onclick="return confirm(...)"` and friends — no <script> tag in sight.

    This is the shape that hid sixteen of the twenty-one for so long: nothing
    about an `onsubmit` attribute looks like a dialog that needs wiring.
    """
    text = DJANGO_COMMENT.sub(' ', text)
    return ' '.join(re.findall(r'\bon[a-z]+\s*=\s*"[^"]*"', text))


class NoScreenAsksTheBrowserAnyMoreTests(TestCase):
    """
    The load-bearing test in this file. A page that pastes back an
    `onsubmit="return confirm(…)"` is invisible to every other kind of test —
    it works, it just looks like a different product for one second on the one
    screen where somebody is about to destroy something.
    """

    def test_no_template_calls_a_native_dialog(self):
        offenders = []
        for path in _templates():
            text = path.read_text(encoding='utf-8', errors='replace')
            for source in (_executable_js(text), _inline_handlers(text)):
                for match in NATIVE_CALL.finditer(source):
                    offenders.append('%s -> %s(' % (path.name, match.group(1)))
        self.assertEqual(
            offenders, [],
            'these ask the browser instead of the app; use wsConfirm/wsAlert or '
            'data-confirm on the form: %s' % offenders,
        )

    def test_only_the_two_deliberate_fallbacks_survive_in_shared_js(self):
        """
        `wsConfirm` falls back to `window.confirm` when its markup or bundle did
        not arrive, and photos.js keeps its own for the same reason. An ugly
        dialog beats an action that happens with no question at all — but there
        must be exactly two of them, and both must be a FALLBACK rather than a
        first choice.
        """
        js_dir = settings.BASE_DIR / 'workshop' / 'static' / 'js'
        found = {}
        for path in sorted(js_dir.glob('*.js')):
            source = JS_LINE_COMMENT.sub(' ', JS_BLOCK_COMMENT.sub(' ', path.read_text(encoding='utf-8')))
            hits = NATIVE_CALL.findall(source)
            if hits:
                found[path.name] = len(hits)
        self.assertEqual(found, {'confirm.js': 1, 'photos.js': 1}, found)


class TheCardIsOnEveryPageTests(TestCase):
    """
    One card, included once. A page that reaches `wsConfirm()` without the
    markup falls back to the browser dialog this replaced — which is safe, and
    is not what anybody should be looking at.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user(username='wcf_owner', password='pw', is_superuser=True)
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.client = Client()
        self.client.login(username='wcf_owner', password='pw')

    def test_the_dialog_and_its_script_ride_on_base(self):
        page = self.client.get(reverse('home')).content.decode()
        self.assertIn('id="wcfDialog"', page)
        self.assertIn('js/confirm.', page)   # content-hashed in production

    def test_the_card_carries_the_sound_hook(self):
        """
        sound.js plays the `prompt` tone for any modal carrying this attribute.
        Without it half the app's questions would arrive in silence — the exact
        defect that left `window.confirm()` unhooked for months.
        """
        page = self.client.get(reverse('home')).content.decode()
        card = page[page.index('id="wcfDialog"') - 200:page.index('id="wcfDialog"') + 200]
        self.assertIn('data-sound-prompt', card)

    def test_neither_button_can_submit_the_form_it_is_asked_about(self):
        """
        A bare <button> inside a form submits it. The control whose whole job is
        to let somebody back out must never be able to commit the thing.
        """
        partial = (settings.BASE_DIR / 'workshop' / 'templates' / 'workshop'
                   / 'includes' / '_confirm_dialog.html').read_text(encoding='utf-8')
        # The note explaining this rule says the words "<button>" itself, so the
        # comments come out before the markup is read.
        partial = DJANGO_COMMENT.sub(' ', partial)
        buttons = re.findall(r'<button\b[^>]*>', partial)
        self.assertTrue(buttons)
        for button in buttons:
            self.assertIn('type="button"', button, button)


class EveryQuestionSaysWhatItIsAboutTests(TestCase):
    """
    A `data-confirm` with nothing else renders the default card: an amber
    warning triangle over "Are you sure?" and a button reading "Confirm". That
    is exactly the anonymous dialog this replaced, so a form that carries the
    question and none of its identity has gained nothing.
    """

    FORM_TAG = re.compile(r'<form\b[^>]*data-confirm=[^>]*>', re.S)

    def test_every_declarative_question_names_its_own_card(self):
        thin = []
        for path in _templates():
            text = path.read_text(encoding='utf-8', errors='replace')
            for tag in self.FORM_TAG.findall(text):
                missing = [a for a in ('data-confirm-title', 'data-confirm-icon',
                                       'data-confirm-theme', 'data-confirm-ok')
                           if a not in tag]
                if missing:
                    thin.append('%s missing %s' % (path.name, missing))
        self.assertEqual(thin, [], thin)

    def test_the_themes_named_in_the_markup_are_the_ones_the_stylesheet_paints(self):
        """
        A theme with no rule behind it falls back to the neutral slate default,
        silently — so a delete would render in the same colour as a reactivate.
        """
        css = (settings.BASE_DIR / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')
        painted = set(re.findall(r'\.wcf-card\[data-theme="([a-z]+)"\]', css))

        used = set()
        for path in _templates():
            text = path.read_text(encoding='utf-8', errors='replace')
            used.update(re.findall(r'data-confirm-theme="([a-z]+)"', text))
            used.update(re.findall(r"theme:\s*'([a-z]+)'", _executable_js(text)))
        used.discard('')

        self.assertTrue(used, 'the scan found no themes at all — it is broken')
        self.assertTrue(used <= painted, 'unpainted themes: %s' % sorted(used - painted))


class NoCardEverShowsFloorMoneyTests(TestCase):
    """
    ⚠ FLOOR IS SHOWN NO PRICE, NO COST AND NO PAYMENT STATE ANYWHERE IN THIS
    APP, SO A DIALOG IS NOT THE PLACE TO INTRODUCE ONE.

    Reported by the owner against the Mark Completed card, which read "The bill
    can still be settled afterwards." Every word of that was true and it was
    the wrong thing to say: this button is pressed mostly from the Floor
    tablet, by somebody who cannot settle a bill, cannot see one, and is shown
    no money on any other screen. It was also the rent steer's own defect —
    answering a question nobody had asked.

    The rule is not "avoid the word bill". It is that a confirmation inherits
    the visibility rules of the screen it opens on, and Floor's screens carry
    no money at all.
    """

    MONEY = re.compile(r'\b(bill|bills|billed|settle|settled|settlement|paid|pay|'
                       r'price|prices|cost|costs|discount|invoice|payment)\b|₹', re.I)

    def setUp(self):
        from django.utils import timezone
        from workshop.models import JobCard

        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.floor = User.objects.create_user(username='wcf_floor', password='pw')
        self.floor.groups.add(Group.objects.get(name='Floor'))
        self.client = Client()
        self.client.login(username='wcf_floor', password='pw')

        # ⚠ A CARD ON THE BOARD, OR THE SCAN BELOW PASSES ON AN EMPTY PAGE.
        # Caught exactly that way: the bill sentence was put back and the test
        # still went green, because a test database with no job cards renders
        # no car cards and therefore no questions to read.
        self.live = JobCard.objects.create(
            registration_number='KL 07 FL 0002',
            brand_name='Audi', model_name='A4',
            admitted_date=timezone.localdate(),
        )

    def _cards_on(self, url):
        page = self.client.get(url, follow=True).content.decode()
        return re.findall(r'data-confirm="([^"]*)"', page)

    def test_no_question_on_the_floor_board_mentions_money(self):
        """
        Mark Completed is the one Floor presses all day. Undo Completion is
        gated to Office and Owner, so it must not appear here either — a card
        for a door this role cannot open is the same defect one level down.
        """
        found = self._cards_on(reverse('home'))
        self.assertTrue(found, 'the floor board rendered no questions — the scan is broken')
        offenders = [t for t in found if self.MONEY.search(t)]
        self.assertEqual(offenders, [], offenders)

    def test_the_locked_card_sends_floor_to_a_person_not_to_a_button(self):
        """
        `jobcard_edit` is `@staff_required` and the auto-lock runs for every
        role, but UNLOCK RECORD is rendered only for Office and Owner. So a
        mechanic saving a settled card meets this message, and pointing them at
        that button is the "a door somebody can see but cannot open" defect the
        frozen-advance menu already records.
        """
        from django.utils import timezone
        from workshop.models import JobCard
        card = JobCard.objects.create(
            registration_number='KL 07 FL 0001',
            brand_name='Audi', model_name='A4',
            admitted_date=timezone.localdate(),
            payment_status='PAID',
        )
        page = self.client.get(reverse('jobcard_edit', args=[card.pk])).content.decode()

        self.assertNotIn('UNLOCK RECORD at the top of the card', page)
        self.assertIn('Ask the office', page)


class TheCardCanAlwaysBeSeenAndAnsweredTests(TestCase):
    """
    Nothing in this suite executes CSS, and every rule below failed in a browser
    before it was written down.
    """

    def setUp(self):
        self.css = (settings.BASE_DIR / 'static' / 'css' / 'style.css').read_text(encoding='utf-8')

    def test_it_outranks_the_photo_lightbox(self):
        """
        The lightbox is z-index 2000 — deliberately above the nav bar and the
        spare-date panel. At Bootstrap's own 1055 the card asking "delete this
        photo?" opened BEHIND it: invisible, with the page apparently frozen.
        A dialog that can be covered is worse than no dialog, because the act
        still happens the moment somebody finds the Confirm they cannot see.
        """
        rule = re.search(r'#wcfDialog\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule, 'the card declares no z-index at all')
        depth = re.search(r'z-index:\s*(\d+)', rule.group(1))
        self.assertIsNotNone(depth)
        self.assertGreater(int(depth.group(1)), 2000)

    def test_both_buttons_clear_the_touch_minimum(self):
        rule = re.search(r'\.wcf-btn\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        height = re.search(r'min-height:\s*(\d+)px', rule.group(1))
        self.assertIsNotNone(height)
        self.assertGreaterEqual(int(height.group(1)), 44)

    def test_a_busy_form_is_greyed_by_paint_and_never_by_disabled(self):
        """
        ⚠ A disabled control is dropped from the payload, so disabling a submit
        button that carries a `name` would silently change what is posted — and
        nothing here knows which buttons do. The refusal is in confirm.js; this
        rule only says so on screen, and paint cannot have that effect.
        """
        rule = re.search(r'form\[data-ws-busy="1"\][^{]*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule, 'nothing says a submitted form is on its way')
        self.assertIn('pointer-events: none', rule.group(1))


class OnePressIsOnePostTests(TestCase):
    """
    The second half of the same problem. On a slow connection a person taps the
    same control again, and again — and every tap was another POST.
    """

    def setUp(self):
        self.js = (settings.BASE_DIR / 'workshop' / 'static' / 'js'
                   / 'confirm.js').read_text(encoding='utf-8')

    def test_a_form_already_on_its_way_refuses_the_second_submit(self):
        self.assertIn("form.dataset.wsBusy === '1'", self.js)
        self.assertIn('e.preventDefault()', self.js)

    def test_a_refused_submit_never_latches(self):
        """
        ⚠ The Cashbook's steer STOPS a submit and re-issues it. Latching on the
        first would kill the entry the question was protecting, so the check
        reads `defaultPrevented` after the event has settled — which is also
        what keeps the latch out of the handler's own tick, where disabling a
        control cancels the submission in some browsers.
        """
        self.assertRegex(
            self.js,
            r'window\.setTimeout\(function \(\) \{\s*if \(e\.defaultPrevented\)'
            r' \{ return; \}\s*markBusy\(form\);',
        )

    def test_the_confirm_button_stops_taking_taps_the_moment_it_is_pressed(self):
        """
        The dialog is the one control every converted question goes through, so
        locking it once covers all of them — including the programmatic submits
        that fire no submit event for the form guard to catch.
        """
        pressed = self.js[self.js.index("yes.addEventListener('click'"):]
        self.assertIn("pointerEvents = 'none'", pressed[:600])

    def test_a_page_restored_from_the_back_cache_comes_back_alive(self):
        self.assertIn("'pageshow'", self.js)
        self.assertIn('e.persisted', self.js)
