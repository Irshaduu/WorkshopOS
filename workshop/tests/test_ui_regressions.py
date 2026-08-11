"""
Interface rules that are invisible when they break.

Each of these was reported from a real device, and each has the same shape: the
page still renders, every functional test still passes, and the thing is simply
unusable or gone. A rendered-markup assertion is the only thing that notices.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User, Group
from django.test import TestCase, Client
from django.urls import reverse

from workshop.models import JobCard, SpareShop


class NothingOnAJobCardOpensANewWindowTests(TestCase):
    """
    The Inventory section had a "Stock" link and the Spare Parts section a
    "Shops" link, both `target="_blank"`.

    In an installed PWA that does not open a tab — it leaves the app and hands
    the URL to the system browser, which is a different cookie jar, so the
    mechanic lands on a sign-in page with the half-typed job card stranded in an
    app they now have to find their way back to. Both were shortcuts to screens
    the drawer already reaches.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(username='office_ui', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='office_ui', password='pw')

    def _form_page(self):
        response = self.client.get(reverse('jobcard_create'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_job_card_form_has_no_blank_targets(self):
        self.assertNotIn('target="_blank"', self._form_page())

    def test_the_add_row_buttons_are_still_there(self):
        """
        The links went; the two controls beside them are the ones that matter
        and must not have gone with them.
        """
        page = self._form_page()

        self.assertIn('id="add-inventory-btn"', page)
        self.assertIn('id="add-spare-btn"', page)


class ASpareShopNeverTruncatesItsOwnNameTests(TestCase):
    """
    Two goes at this header, and the second one is the rule.

    First attempt pinned the actions beside the title at every width, on the
    reasoning that a control belongs next to the thing it acts on. On a phone
    that made the actions and the shop NAME compete for the same line, and the
    name lost: "Kochi Auto Spares" rendered as "Kochi Auto Spa…". The name is
    the one piece of text identifying what you are looking at, and a shop is
    identified by its name far more often than by where its ⋮ sits.

    So below 768px the actions take a row of their own, right-aligned — the
    name gets the full width, the ⋮ still lands in the corner under the thumb.
    Above 768px there is room for both and nothing has to give.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(username='owner_ui', password='pw')
        self.user.groups.add(Group.objects.get(name='Owner'))
        self.client = Client()
        self.client.login(username='owner_ui', password='pw')
        self.shop = SpareShop.objects.create(
            name='Pullara Spares & Lubricants Trading Company',
            phone='9207217978', address='Calicut Road, Pullara',
        )

    def _header(self, page):
        start = page.find('detail-header')
        self.assertNotEqual(start, -1, 'header block not found')
        return page[start:start + 2000]

    def _page(self):
        return self.client.get(
            reverse('spare_shop_detail', args=[self.shop.pk])
        ).content.decode()

    def test_the_name_and_the_actions_are_separately_addressable(self):
        """
        Both halves need their own hook, or the phone rule below has nothing to
        target and silently does nothing.
        """
        page = self._page()

        self.assertIn('shop-headrow', page)
        self.assertIn('shop-titleblock', page)
        self.assertIn('shop-actions', page)

    def test_on_a_phone_the_actions_take_their_own_row_aligned_right(self):
        page = self._page()

        self.assertIn('max-width: 767.98px', page)
        # 100% basis is what FORCES the break — without it the two boxes share
        # the line again the moment they happen to fit, which is the bug.
        self.assertIn('flex: 1 1 100%', page)
        self.assertIn('justify-content: flex-end', page)

    def test_on_a_phone_the_shop_name_is_not_cut_off(self):
        """The whole point of the second attempt. Truncation is lifted, not
        merely made less likely."""
        page = self._page()
        phone_rules = page[page.find('max-width: 767.98px'):]

        self.assertIn('white-space: normal', phone_rules)
        self.assertIn('text-overflow: clip', phone_rules)


class TheTwoPaymentHistoriesAreOneScreenTests(TestCase):
    """
    Spare Shops and Supplies Shops both keep a payment history in an offcanvas,
    and they had drifted into looking like two different products: the supplier
    side was a bare trash icon on the row (one mis-tap from reversing a settled
    payment on a tablet), the spare side a ⋮ menu; different typography,
    different wording, and the amounts in different colours.

    They are now the same markup, and these tests assert the PARITY rather than
    either implementation — the failure mode worth catching is the two drifting
    apart again, not which classes were used to stop it.

    One correction recorded here because it was got wrong first: a Bootstrap
    dropdown IS safe in this container. `.offcanvas-body` is `overflow-y: auto`,
    which is normally where Popper gets clipped, but the body is full viewport
    height, so at the bottom edge the menu simply flips upwards and stays fully
    visible — measured with a scrolled 19-row list, last row hard against the
    edge. The `.cb-list` trap in CLAUDE.md is a different shape: `overflow:
    hidden` on a box barely taller than one row, where there is nowhere to flip.
    """

    ROW_CLASSES = 'px-3 py-3 border-bottom d-flex justify-content-between align-items-center'

    def setUp(self):
        from inventory.models import SupplierShop, SupplierPayment
        from workshop.models import SpareShopPayment

        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        # OFFICE, not Owner — both delete views are @office_required, so Office
        # is the role that must be able to see the action. The spare-shop
        # template used to gate it on Owner alone and hid it from the role whose
        # job it is.
        self.user = User.objects.create_user(username='office_pay', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='office_pay', password='pw')

        self.supplier = SupplierShop.objects.create(name='Kerala Auto Distributors')
        SupplierPayment.objects.create(
            supplier=self.supplier, amount=Decimal('15000'), date=date.today(),
            payment_method='CASH',
        )
        self.spare_shop = SpareShop.objects.create(name='Pullara Spares')
        SpareShopPayment.objects.create(
            shop=self.spare_shop, amount=Decimal('15000'), payment_method='CASH',
        )

    def _supplier_page(self):
        response = self.client.get(reverse('supplier_shop_detail', args=[self.supplier.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _spare_page(self):
        response = self.client.get(reverse('spare_shop_detail', args=[self.spare_shop.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_both_use_the_same_row_markup(self):
        for label, page in (('supplier', self._supplier_page()), ('spare', self._spare_page())):
            with self.subTest(screen=label):
                self.assertIn(self.ROW_CLASSES, page)

    def test_both_put_the_delete_behind_a_dots_menu_with_the_same_wording(self):
        for label, page in (('supplier', self._supplier_page()), ('spare', self._spare_page())):
            with self.subTest(screen=label):
                self.assertIn('bi-three-dots-vertical', page)
                self.assertIn('Delete this Payment', page)

    def test_both_print_the_amount_in_green(self):
        for label, page in (('supplier', self._supplier_page()), ('spare', self._spare_page())):
            with self.subTest(screen=label):
                self.assertIn('fw-bold text-success', page)

    def test_office_can_see_the_delete_on_both(self):
        """
        The gate must mirror the decorator. Both `delete_shop_payment` and
        `spare_shop_payment_reverse` are `@office_required`; a template hiding
        the action from Office is the `InvoiceLinkVisibilityTests` bug again.
        """
        for label, page in (('supplier', self._supplier_page()), ('spare', self._spare_page())):
            with self.subTest(screen=label):
                self.assertIn('Delete this Payment', page)

    def test_the_confirmation_survived_on_both(self):
        """
        Deleting a payment recomputes a shop's balance. The ⋮ exists to stop a
        mis-tap, not to replace the confirmation that was already there.
        """
        for label, page in (('supplier', self._supplier_page()), ('spare', self._spare_page())):
            with self.subTest(screen=label):
                self.assertIn('confirmSubmit(event', page)

    def test_the_supplier_row_no_longer_shouts_bulk_pay(self):
        """
        A badge reading "Bulk Pay" was printed on every supplier payment
        unconditionally, so it distinguished nothing and had no counterpart on
        the spare-shop side.
        """
        self.assertNotIn('Bulk Pay', self._supplier_page())


class OutcomeSoundsRideOnTheMessagesFrameworkTests(TestCase):
    """
    The sounds are wired to Django's message tags, not to individual buttons.

    That is the point worth protecting: the app already tags every outcome
    through `messages.success` / `.error` / `.warning`, so one attribute on the
    banner covers every action in the system and anything added later is covered
    by default. Wiring ~180 call sites by hand would be 180 chances to attach the
    wrong tone — and every one of them would be firing at click time, announcing
    "done" before the server had done anything.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(username='office_snd', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='office_snd', password='pw')

    def test_a_success_banner_carries_its_tag_to_the_page(self):
        """
        Settling a bill is the archetypal case: money moved, the view reports it
        with `messages.success`, and the banner now carries the tag that decides
        the tone. No code in `billing.py` knows anything about sound.
        """
        job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01S0001', customer_name='X',
            total_bill_amount=Decimal('5000.00'),
        )

        response = self.client.post(
            reverse('update_bill_status', args=[job.pk]),
            {'received_amount': '5000', 'payment_method': 'CASH'},
            follow=True,
        )

        self.assertIn('data-sound-tag="success"', response.content.decode())

    def test_an_error_banner_carries_the_error_tag(self):
        """
        Different tag, different tone — the one distinction that has to survive,
        since telling "saved" from "refused" without looking up is the entire
        value of this on a shop floor.
        """
        job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01S0002', customer_name='X',
            total_bill_amount=Decimal('5000.00'),
        )

        response = self.client.post(
            reverse('update_bill_status', args=[job.pk]),
            {'received_amount': '-1', 'payment_method': 'CASH'},
            follow=True,
        )

        self.assertIn('data-sound-tag="error"', response.content.decode())

    def test_a_page_with_no_messages_carries_no_tag(self):
        """
        Nothing happened, so nothing sounds — and sound.js never even opens an
        AudioContext.
        """
        page = self.client.get(reverse('home')).content.decode()

        self.assertNotIn('data-sound-tag', page)

    def test_every_role_gets_the_script(self):
        """
        Not owner-gated like the bell. The sounds confirm Office's payments and
        Floor's saves, which is most of what happens in a day.
        """
        page = self.client.get(reverse('home')).content.decode()

        # `js/sound.` and not `js/sound.js`: the manifest storage is genuinely
        # active now, so {% static %} emits a content-hashed name like
        # `js/sound.951c822c33d6.js`. Asserting the un-hashed filename would
        # pass only for as long as static hashing stayed broken.
        self.assertIn('js/sound.', page)

    def test_the_toggle_is_in_the_drawer(self):
        page = self.client.get(reverse('home')).content.decode()

        self.assertIn('id="soundToggle"', page)

    def test_info_messages_are_deliberately_silent(self):
        """
        `info` and `debug` are not outcomes. sound.js maps only success, error
        and warning — a tone for every notice would train everyone to stop
        hearing the two that matter.
        """
        from django.conf import settings

        script = (settings.BASE_DIR / 'workshop' / 'static' / 'js' / 'sound.js').read_text(
            encoding='utf-8'
        )
        mapping = script.split('TAG_TONES = {')[1].split('}')[0]

        self.assertIn('success', mapping)
        self.assertIn('error', mapping)
        self.assertIn('warning', mapping)
        self.assertNotIn('info:', mapping)
        self.assertNotIn('debug:', mapping)

    def test_it_adds_no_third_party_dependency(self):
        """
        Tones are synthesised with Web Audio — no audio files, no CDN, no npm.
        The project's rule is that no new runtime dependency arrives without a
        defect it is the only fix for, and a beep is not that.
        """
        from django.conf import settings

        script = (settings.BASE_DIR / 'workshop' / 'static' / 'js' / 'sound.js').read_text(
            encoding='utf-8'
        )

        self.assertIn('createOscillator', script)
        self.assertNotIn('http://', script)
        self.assertNotIn('https://', script)
        self.assertNotIn('new Audio(', script)


class ANotificationLandsWhereItsSubjectActuallyIsTests(TestCase):
    """
    CLAUDE.md's rule, broken a second time and found by the owner deliberately
    locking an account to see what the alert did.

    Two separate things came out of that, and only one was a bug:

      * ARCHIVING A SUPPLIES SHOP linked to `supplier_shop_list`, which filters
        `is_active=True` — the one page guaranteed NOT to contain the shop the
        notification is about. The spare-shop and fleet versions of the same
        event already pointed at their archived lists. A real defect.
      * A LOCKOUT alert is correct when written and goes stale 15 minutes later,
        because that is how long a lockout lasts. The unlock button is right to
        disappear; the body was wrong to describe a permanent remedy.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.owner = User.objects.create_user(username='owner_n', password='pw')
        self.owner.groups.add(Group.objects.get(name='Owner'))
        self.other = User.objects.create_user(username='owner_n2', password='pw')
        self.other.groups.add(Group.objects.get(name='Owner'))
        self.client = Client()
        self.client.login(username='owner_n', password='pw')

    def test_archiving_a_supplies_shop_links_to_the_archived_list(self):
        from workshop.models import Notification
        from inventory.models import SupplierShop

        shop = SupplierShop.objects.create(name='Kerala Auto Distributors')
        self.client.post(reverse('deactivate_supplier_shop', args=[shop.pk]))

        note = Notification.objects.filter(event='ACCOUNT_ARCHIVED').first()
        self.assertIsNotNone(note)
        self.assertEqual(note.url, reverse('deactivated_supplier_shop_list'))

        # And the destination genuinely contains it — which is the whole rule,
        # and what a reverse() comparison alone would not prove.
        page = self.client.get(note.url).content.decode()
        self.assertIn('Kerala Auto Distributors', page)

    def test_the_active_list_really_would_not_have_contained_it(self):
        """Pins WHY the link had to change, so nobody reverts it as cosmetic."""
        from inventory.models import SupplierShop

        shop = SupplierShop.objects.create(name='Kerala Auto Distributors')
        # follow=True so the "deactivated" success banner is CONSUMED on the
        # redirect. Without it the very next page still carries that message,
        # which names the shop — and the assertion below would pass or fail on
        # the banner rather than on the list, which is not what it is about.
        self.client.post(reverse('deactivate_supplier_shop', args=[shop.pk]), follow=True)

        page = self.client.get(reverse('supplier_shop_list')).content.decode()

        self.assertNotIn('Kerala Auto Distributors', page)

    def test_a_lockout_alert_states_how_long_the_remedy_lasts(self):
        from workshop.models import AccountLockout, Notification

        office = User.objects.create_user(username='office_lock', password='right-password')
        office.groups.add(Group.objects.get(name='Office'))

        anon = Client()
        for _ in range(AccountLockout.MAX_FAILURES):
            anon.post(reverse('login'), {'username': 'office_lock', 'password': 'wrong'})

        note = Notification.objects.filter(event='ACCOUNT_LOCKED').first()
        self.assertIsNotNone(note, "five failures should lock the account and say so")
        self.assertIn(str(AccountLockout.LOCKOUT_MINUTES), note.body)
        self.assertIn('clears itself', note.body)


class TheSilentActionsNowReportThemselvesTests(TestCase):
    """
    `mark_completed`, `undo_completed` and `toggle_hold` wrote no message at
    all. On a tablet the card simply vanished off the board and the page
    reloaded, which is indistinguishable from a mis-tap that did nothing —
    every other action in the app reports itself.

    Fixing it at the view is also what earns them a confirmation sound, since
    those are driven off the message tag rather than wired per button.
    """

    def setUp(self):
        for name in ('Owner', 'Office', 'Floor'):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create_user(username='office_sil', password='pw')
        self.user.groups.add(Group.objects.get(name='Office'))
        self.client = Client()
        self.client.login(username='office_sil', password='pw')
        self.job = JobCard.objects.create(
            admitted_date=date.today(), brand_name='Toyota', model_name='Corolla',
            registration_number='KL01T0001', customer_name='X',
        )

    def _tags(self, response):
        return response.content.decode()

    def test_marking_completed_says_so(self):
        page = self._tags(self.client.post(
            reverse('mark_completed', args=[self.job.pk]), follow=True))

        self.assertIn('data-sound-tag="success"', page)
        self.assertIn('KL01T0001', page)

    def test_undoing_completion_says_so(self):
        self.client.post(reverse('mark_completed', args=[self.job.pk]))
        page = self._tags(self.client.post(
            reverse('undo_completed', args=[self.job.pk]), follow=True))

        self.assertIn('data-sound-tag="success"', page)

    def test_putting_a_car_on_hold_says_so_and_says_which_way(self):
        on = self._tags(self.client.post(reverse('toggle_hold', args=[self.job.pk]), follow=True))
        self.assertIn('on hold', on)

        off = self._tags(self.client.post(reverse('toggle_hold', args=[self.job.pk]), follow=True))
        self.assertIn('off hold', off)


class AConfirmationMakesItselfHeardTests(TestCase):
    """
    Every "are you sure?" in this app is one of THREE things — a Bootstrap
    modal, a native <dialog>, or a plain `window.confirm()` — so three hooks in
    sound.js cover all of them, including any added later. That is the same
    reasoning as driving the outcome tones off the message tags instead of ~180
    call sites.

    This docstring said "two" until 2026-08-11, and so did the code. The
    `window.confirm()` sites were the ones nobody counted: they are inline
    `onsubmit="return confirm(…)"` attributes rather than anything that looks
    like a dialog, and there were sixteen of them against nineteen of the other
    two kinds — so roughly half of every confirmation in the app asked its
    question silently. The scan below is the guard, because the failure mode is
    a *missing* hook, which nothing else can notice.
    """

    def _script(self):
        from django.conf import settings
        return (settings.BASE_DIR / 'workshop' / 'static' / 'js' / 'sound.js').read_text(
            encoding='utf-8'
        )

    def test_it_hooks_bootstrap_modals_and_native_dialogs(self):
        script = self._script()

        self.assertIn('show.bs.modal', script)
        self.assertIn('showModal', script)

    def test_it_hooks_window_confirm(self):
        script = self._script()

        self.assertIn('window.confirm', script)
        # Called through, not replaced: these are `return confirm(…)` on a
        # form's onsubmit, so swallowing the answer would silently submit or
        # silently refuse to.
        self.assertIn('nativeConfirm.apply', script)

    def test_every_way_the_app_asks_a_question_is_hooked(self):
        """
        Scans the templates for the three known shapes and asserts sound.js
        hooks each one it actually finds, so a hook cannot be dropped while
        the markup that needs it is still there.

        What it deliberately does NOT claim: a genuinely *fourth* way of asking
        a question would not be in `shapes` and would pass unnoticed — the same
        blind spot that let `window.confirm()` go unhooked. Nothing static can
        close that; add the shape here when you add the dialog.
        """
        from django.conf import settings

        shapes = {
            # marker found in templates -> what sound.js must contain for it
            'confirm(': 'window.confirm',
            'showModal(': 'showModal',
            'data-bs-toggle="modal"': 'show.bs.modal',
        }
        script = self._script()

        roots = [settings.BASE_DIR / 'workshop', settings.BASE_DIR / 'inventory']
        found = set()
        for root in roots:
            for path in root.rglob('*.html'):
                text = path.read_text(encoding='utf-8', errors='replace')
                for marker in shapes:
                    if marker in text:
                        found.add(marker)

        self.assertTrue(found, 'no confirmation markup found — the scan is broken')
        for marker in sorted(found):
            self.assertIn(
                shapes[marker], script,
                f"templates ask questions via `{marker}` but sound.js does not "
                f"hook it, so those confirmations are silent",
            )

    def test_a_workspace_modal_is_not_treated_as_a_question(self):
        """
        Only the confirm dialogs sound. An "add a payment" form modal is a
        workspace — a tone every time one opened would be noise, and noise is
        how the two tones that matter stop being heard.
        """
        script = self._script()

        self.assertIn('confirmActionModal', script)
        self.assertIn('data-sound-prompt', script)

    def test_a_blocking_confirm_never_beeps_after_the_answer(self):
        """
        `window.confirm()` freezes the main thread, so a tone queued behind
        `resume()`'s promise would land AFTER the question was answered — where
        it reads as the outcome sound for the decision just made. Announcing
        the wrong thing is worse than announcing nothing, so that one case
        stays quiet and only resumes the context for next time.
        """
        script = self._script()

        self.assertIn('function play(kind, blocking)', script)
        self.assertIn("play('prompt', true)", script)
