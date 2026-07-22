from django.test import TestCase
from django.contrib.auth.models import User, Group
from workshop.templatetags.custom_filters import has_group

class TemplateTagTests(TestCase):
    """
    Titan Standard 100% Verification.
    Verify all custom template tags.
    """
    def setUp(self):
        self.owner_group, _ = Group.objects.get_or_create(name='Owner')
        self.user = User.objects.create_user(username='tag_user', password='password')

    def test_has_group_filter(self):
        # 1. Negative Case (No Group)
        self.assertFalse(has_group(self.user, 'Owner'))
        
        # 2. Positive Case (Group member)
        self.user.groups.add(self.owner_group)
        self.assertTrue(has_group(self.user, 'Owner'))
        
        # 3. Superuser (True for everything)
        self.superuser = User.objects.create_superuser(username='super', password='password', email='s@s.com')
        self.assertTrue(has_group(self.superuser, 'Owner'))
        self.assertTrue(has_group(self.superuser, 'RandomGroup'))
        
        # 4. Unauthenticated user
        from django.contrib.auth.models import AnonymousUser
        anon = AnonymousUser()
        self.assertFalse(has_group(anon, 'Owner'))
        
        # 5. Non-existent group
        self.assertFalse(has_group(self.user, 'GhostGroup'))
