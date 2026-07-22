from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone
from workshop.models import Mechanic, UserSession
from django.contrib.sessions.models import Session

class ManagementViewTests(TestCase):
    """
    Tests for the HQ Command Center (management_views.py).
    Titan Standard 100% Verification.
    """

    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.office_group, _ = Group.objects.get_or_create(name='Office')
        self.floor_group, _ = Group.objects.get_or_create(name='Floor')
        
        self.owner = User.objects.create_user(username='Sahad', password='password')
        self.owner.groups.add(self.owner_group)
        
        self.staff = User.objects.create_user(username='staff_user', password='password')
        self.staff.groups.add(self.office_group)
        
        self.client = Client()
        self.client.login(username='Sahad', password='password')
        
    def test_manage_dashboard_sections(self):
        url = reverse('manage_dashboard')
        # All sections
        for section in ['accounts', 'workshop', 'security', 'inventory', 'invalid']:
            response = self.client.get(url, {'section': section})
            self.assertEqual(response.status_code, 200)

    def test_manage_create_user(self):
        url = reverse('manage_create_user')
        
        # 1. Invalid short password
        response = self.client.post(url, {
            'username': 'newuser', 'password': '123', 'role': 'Office'
        })
        self.assertFalse(User.objects.filter(username='newuser').exists())
        
        # 2. Duplicate username
        response = self.client.post(url, {
            'username': 'staff_user', 'password': 'StrongP@ssw0rd!', 'role': 'Floor'
        })
        self.assertEqual(User.objects.filter(username='staff_user').count(), 1)
        
        # 3. Invalid role
        response = self.client.post(url, {
            'username': 'newuser2', 'password': 'StrongP@ssw0rd!', 'role': 'InvalidRole'
        })
        self.assertFalse(User.objects.filter(username='newuser2').exists())
        
        # 4. Successful creation
        response = self.client.post(url, {
            'username': 'newuser', 'password': 'StrongP@ssw0rd!', 'role': 'Floor'
        })
        self.assertTrue(User.objects.filter(username='newuser').exists())
        self.assertTrue(User.objects.get(username='newuser').groups.filter(name='Floor').exists())

    def test_manage_delete_user(self):
        url = reverse('manage_delete_user', args=[self.staff.id])
        
        # GET shouldn't delete
        self.client.get(url)
        self.assertTrue(User.objects.filter(id=self.staff.id).exists())
        
        # POST should delete
        response = self.client.post(url)
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        self.assertFalse(User.objects.filter(id=self.staff.id).exists())

    def test_manage_reset_password(self):
        url = reverse('manage_reset_password', args=[self.staff.id])
        
        # Invalid short password
        self.client.post(url, {'new_password': '123'})
        self.assertFalse(self.client.login(username='staff_user', password='123'))
        
        # Valid reset
        response = self.client.post(url, {'new_password': 'StrongP@ssw0rd!'})
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        self.assertTrue(self.client.login(username='staff_user', password='StrongP@ssw0rd!'))
        
    def test_mechanic_management(self):
        # 1. Add invalid (empty)
        response = self.client.post(reverse('manage_create_mechanic'), {'name': ''})
        self.assertEqual(Mechanic.objects.count(), 0)
        
        # 1. Add valid
        response = self.client.post(reverse('manage_create_mechanic'), {'name': 'New Mech'})
        self.assertTrue(Mechanic.objects.filter(name='New Mech').exists())
        
        # 2. Rename invalid
        mech = Mechanic.objects.create(name='Old Mech')
        response = self.client.post(reverse('manage_edit_mechanic', args=[mech.id]), {'name': ''})
        mech.refresh_from_db()
        self.assertEqual(mech.name, 'Old Mech')

        # 2. Rename valid
        response = self.client.post(reverse('manage_edit_mechanic', args=[mech.id]), {'name': 'Renamed Mech'})
        mech.refresh_from_db()
        self.assertEqual(mech.name, 'Renamed Mech')
        
        # 3. Toggle valid
        response = self.client.post(reverse('manage_toggle_mechanic', args=[mech.id]))
        mech.refresh_from_db()
        self.assertFalse(mech.is_active)
        
        # Toggle back
        self.client.post(reverse('manage_toggle_mechanic', args=[mech.id]))
        mech.refresh_from_db()
        self.assertTrue(mech.is_active)

    def test_session_revocation_realtime(self):
        session = Session.objects.create(session_data='', expire_date=timezone.now())
        user_session = UserSession.objects.create(
            user=self.staff,
            session_key=session.session_key,
            ip_address='127.0.0.1'
        )
        
        url = reverse('manage_terminate_session', args=[user_session.pk])
        
        # GET doesn't delete
        self.client.get(url)
        self.assertTrue(UserSession.objects.filter(pk=user_session.pk).exists())
        
        # POST deletes
        response = self.client.post(url)
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=security')
        
        # Verify both are gone
        self.assertFalse(UserSession.objects.filter(pk=user_session.pk).exists())
        self.assertFalse(Session.objects.filter(session_key=session.session_key).exists())

    def test_session_revocation_already_gone(self):
        """Cover the Session.DoesNotExist branch (line 350-351) — session key already deleted"""
        # Create a UserSession whose Django session has already been deleted
        user_session = UserSession.objects.create(
            user=self.staff,
            session_key='nonexistent-key-xyz',
            ip_address='127.0.0.1'
        )
        url = reverse('manage_terminate_session', args=[user_session.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=security')
        self.assertFalse(UserSession.objects.filter(pk=user_session.pk).exists())

    def test_create_user_weak_password(self):
        """Admin has full freedom to set any password for staff (AUD-0019).
        Only the 8-character minimum is enforced — common/weak passwords are now allowed."""
        url = reverse('manage_create_user')
        # 'password1' is 9 chars and passes length check — should now succeed
        response = self.client.post(url, {
            'username': 'weakpwduser', 'password': 'password1', 'role': 'Floor'
        })
        # Admin freedom: user is created with whatever password admin chooses
        self.assertTrue(User.objects.filter(username='weakpwduser').exists())

    def test_reset_password_for_owner_blocked(self):
        """Cover the Owner-protection branch in reset_password (lines 231-232)"""
        owner2 = User.objects.create_user(username='owner2', password='password')
        owner2.groups.add(self.owner_group)
        url = reverse('manage_reset_password', args=[owner2.id])
        response = self.client.post(url, {'new_password': 'NewPassword123!'})
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        # Password should NOT have changed
        owner2.refresh_from_db()
        self.assertFalse(owner2.check_password('NewPassword123!'))

    def test_reset_password_weak_password(self):
        """Admin has full freedom to reset staff passwords (AUD-0019).
        Only the 8-character minimum is enforced — common/weak passwords are now allowed."""
        url = reverse('manage_reset_password', args=[self.staff.id])
        response = self.client.post(url, {'new_password': 'password1'})
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        # Admin freedom: password should have been updated successfully
        self.assertTrue(self.client.login(username='staff_user', password='password1'))

    def test_delete_owner_blocked(self):
        """Cover the Owner-protection branch in delete_user (lines 264-265)"""
        owner2 = User.objects.create_user(username='owner2', password='password')
        owner2.groups.add(self.owner_group)
        url = reverse('manage_delete_user', args=[owner2.id])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        self.assertTrue(User.objects.filter(id=owner2.id).exists())  # Still exists

    def test_create_mechanic_duplicate(self):
        """Cover duplicate mechanic branch in create_mechanic (lines 287-288)"""
        Mechanic.objects.create(name='Ali')
        url = reverse('manage_create_mechanic')
        response = self.client.post(url, {'name': 'Ali'})
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        self.assertEqual(Mechanic.objects.filter(name='Ali').count(), 1)  # Not duplicated

    def test_edit_mechanic_duplicate_name(self):
        """Cover duplicate-name branch in edit_mechanic (lines 325-326)"""
        mech1 = Mechanic.objects.create(name='Ahmed')
        mech2 = Mechanic.objects.create(name='Bilal')
        url = reverse('manage_edit_mechanic', args=[mech2.id])
        # Try to rename mech2 to the same name as mech1
        response = self.client.post(url, {'name': 'Ahmed'})
        self.assertRedirects(response, reverse('manage_dashboard') + '?section=accounts')
        mech2.refresh_from_db()
        self.assertEqual(mech2.name, 'Bilal')  # Name unchanged

