from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from workshop.models import SparePart, ConcernSolution, JobCard, JobCardSpareItem, JobCardConcern
from datetime import date

class DataCleanupViewsTestCase(TestCase):
    def setUp(self):
        # Create Office user
        self.user = User.objects.create_user(username='officeuser', password='password123')
        office_group, _ = Group.objects.get_or_create(name='Office')
        self.user.groups.add(office_group)
        
        self.client = Client()
        self.client.login(username='officeuser', password='password123')
        
        # Create Data
        self.spare1 = SparePart.objects.create(name='Brake Pad')
        self.spare2 = SparePart.objects.create(name='Break pad') # Typo
        
        self.concern1 = ConcernSolution.objects.create(concern='Engine Noise')
        self.concern2 = ConcernSolution.objects.create(concern='Engin noise') # Typo
        
        self.job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL-01-A-1234'
        )
        
        # Add items to job card
        JobCardSpareItem.objects.create(job_card=self.job, spare_part_name='Break pad', quantity=1)
        JobCardConcern.objects.create(job_card=self.job, concern_text='Engin noise')

    def test_data_cleanup_view(self):
        response = self.client.get(reverse('data_cleanup'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'workshop/manage/data_cleanup.html')
        
        # Check usage context
        spares = response.context['spares']
        # The spare2 ('Break pad') has 1 usage
        for s in spares:
            if s.name == 'Break pad':
                self.assertEqual(s.usage_count, 1)
            elif s.name == 'Brake Pad':
                self.assertEqual(s.usage_count, 0)

    def test_cleanup_delete_spare(self):
        url = reverse('cleanup_delete_spare', args=[self.spare1.id])
        # GET should not delete
        self.client.get(url)
        self.assertTrue(SparePart.objects.filter(id=self.spare1.id).exists())
        
        # POST should delete
        response = self.client.post(url)
        self.assertRedirects(response, reverse('data_cleanup'))
        self.assertFalse(SparePart.objects.filter(id=self.spare1.id).exists())

    def test_cleanup_rename_spare_simple(self):
        url = reverse('cleanup_rename_spare', args=[self.spare2.id])
        
        # Empty name
        self.client.post(url, {'new_name': ''})
        self.spare2.refresh_from_db()
        self.assertEqual(self.spare2.name, 'Break pad')
        
        # Valid rename
        self.client.post(url, {'new_name': 'New Brake Pad'})
        self.spare2.refresh_from_db()
        self.assertEqual(self.spare2.name, 'New Brake Pad')
        
        # Verify job card was updated
        item = JobCardSpareItem.objects.first()
        self.assertEqual(item.spare_part_name, 'New Brake Pad')

    def test_cleanup_rename_spare_merge(self):
        url = reverse('cleanup_rename_spare', args=[self.spare2.id])
        # Merge 'Break pad' into 'Brake Pad'
        self.client.post(url, {'new_name': 'Brake Pad'})
        
        # spare2 should be deleted
        self.assertFalse(SparePart.objects.filter(id=self.spare2.id).exists())
        # spare1 should still exist
        self.assertTrue(SparePart.objects.filter(id=self.spare1.id).exists())
        
        # Verify job card was updated
        item = JobCardSpareItem.objects.first()
        self.assertEqual(item.spare_part_name, 'Brake Pad')

    def test_cleanup_delete_concern(self):
        url = reverse('cleanup_delete_concern', args=[self.concern1.id])
        self.client.post(url)
        self.assertFalse(ConcernSolution.objects.filter(id=self.concern1.id).exists())

    def test_cleanup_rename_concern_simple(self):
        url = reverse('cleanup_rename_concern', args=[self.concern2.id])
        
        # Empty
        self.client.post(url, {'new_name': ''})
        
        # Valid
        self.client.post(url, {'new_name': 'Loud Engine Noise'})
        self.concern2.refresh_from_db()
        self.assertEqual(self.concern2.concern, 'Loud Engine Noise')
        
        # Verify job card
        jc_concern = JobCardConcern.objects.first()
        self.assertEqual(jc_concern.concern_text, 'Loud Engine Noise')

    def test_cleanup_rename_concern_merge(self):
        url = reverse('cleanup_rename_concern', args=[self.concern2.id])
        # Merge into concern1
        self.client.post(url, {'new_name': 'Engine Noise'})
        
        self.assertFalse(ConcernSolution.objects.filter(id=self.concern2.id).exists())
        
        # Verify job card
        jc_concern = JobCardConcern.objects.first()
        self.assertEqual(jc_concern.concern_text, 'Engine Noise')
