from django.test import TestCase
from django.contrib.auth.models import User
from workshop.models import (
    UserProfile, FailedAttempt, UserSession, Mechanic, CarBrand, CarModel,
    SparePart, ConcernSolution, JobCard, JobCardConcern, JobCardSpareItem, JobCardLabourItem
)
from datetime import date

class ExtendedModelsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testowner', password='password123')
        self.profile = UserProfile.objects.create(user=self.user, mobile_number='1234567890')
        self.job = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Toyota',
            model_name='Corolla',
            registration_number='KL-01-A-1234'
        )

    def test_str_methods(self):
        self.assertEqual(str(self.profile), "testowner's Profile")
        
        fa = FailedAttempt.objects.create(ip_address='192.168.1.1', failures=3)
        self.assertEqual(str(fa), "IP 192.168.1.1: 3 failures")
        
        us = UserSession.objects.create(user=self.user, session_key='abc123key')
        self.assertEqual(str(us), f"Session abc123key for {self.user.username}")
        
        mech = Mechanic.objects.create(name='John Doe')
        self.assertEqual(str(mech), "John Doe")
        
        brand = CarBrand.objects.create(name='Honda')
        self.assertEqual(str(brand), "Honda")
        
        model = CarModel.objects.create(brand=brand, name='Civic')
        self.assertEqual(str(model), "Honda Civic")
        
        spare = SparePart.objects.create(name='Oil Filter')
        self.assertEqual(str(spare), "Oil Filter")
        
        concern = ConcernSolution.objects.create(concern='Brake noise')
        self.assertEqual(str(concern), "Brake noise...")
        
        jc_concern = JobCardConcern.objects.create(job_card=self.job, concern_text='Fix brake', status='PENDING')
        self.assertEqual(str(jc_concern), "Fix brake (Pending)")
        
        jc_spare = JobCardSpareItem.objects.create(job_card=self.job, spare_part_name='Brake Pad', quantity=2)
        self.assertEqual(str(jc_spare), "Brake Pad (2)")
        
        jc_labour = JobCardLabourItem.objects.create(job_card=self.job, job_description='Washing', amount=100)
        self.assertEqual(str(jc_labour), "Washing")

    def test_usersession_get_device_name(self):
        ua_tests = [
            # iPad: has 'ipad' in UA — device=iPad, no safari/chrome in this UA so browser=Web Browser
            ("Mozilla/5.0 (iPad; CPU OS 12_2 like Mac OS X)", "Web Browser on iPad"),
            # Samsung Galaxy
            ("Mozilla/5.0 (Linux; Android 10; SM-G981B)", "Web Browser on Samsung Galaxy"),
            # Google Pixel
            ("Mozilla/5.0 (Linux; Android 10; Pixel 4)", "Web Browser on Google Pixel"),
            # Nexus
            ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 6P)", "Web Browser on Nexus"),
            # Edge on Windows
            ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0 Edg/90.0", "Microsoft Edge on Windows PC"),
            # Firefox on Windows
            ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0", "Mozilla Firefox on Windows PC"),
            # Linux
            ("Mozilla/5.0 (X11; Linux x86_64)", "Web Browser on Linux Workstation"),
            # iPhone with Safari in UA
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1", "Apple Safari on iPhone"),
            # Macbook with Safari
            ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15", "Apple Safari on Macbook"),
            # None
            (None, "Web Browser on Desktop")
        ]
        for ua, expected in ua_tests:
            result = UserSession.get_device_name(ua)
            self.assertEqual(result, expected, f"Failed for UA: {ua}")

    def test_jobcard_weird_bill_number_fallback(self):
        # Insert a job card with a malformed bill number to trigger the exception fallback
        job_weird = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Honda',
            model_name='Civic',
            registration_number='KL-1234'
        )
        year = str(date.today().year)[2:]
        job_weird.bill_number = f"JB-{year}-XYZ"
        job_weird.save()
        
        job_new = JobCard.objects.create(
            admitted_date=date.today(),
            brand_name='Ford',
            model_name='Figo',
            registration_number='KL-5678'
        )
        self.assertTrue(job_new.bill_number.startswith(f"JB-{year}-"))

    def test_jobcard_properties(self):
        self.job.car_color = 'Other'
        self.job.car_color_other = '#ff00ff'
        self.assertEqual(self.job.get_car_color_hex, '#ff00ff')
        self.assertEqual(self.job.get_car_color_display, '#ff00ff')
        
        self.job.car_color_other = 'Magenta'
        self.assertEqual(self.job.get_car_color_display, 'Magenta')
        self.assertEqual(self.job.get_car_color_hex, '#475569') # fallback
        
        self.job.car_color = 'Unknown'
        self.job.car_color_other = ''
        self.assertEqual(self.job.get_car_color_display, 'Unknown')
        
        # Test completion percentage
        self.assertEqual(self.job.get_completion_percentage, 0)
        c1 = JobCardConcern.objects.create(job_card=self.job, concern_text='C1', status='FIXED')
        self.assertEqual(self.job.get_completion_percentage, 100)
        c2 = JobCardConcern.objects.create(job_card=self.job, concern_text='C2', status='PENDING')
        self.assertEqual(self.job.get_completion_percentage, 50)
        
        # Test total amount and balance
        self.assertEqual(self.job.get_total_amount, 0)
        self.assertEqual(self.job.get_balance_amount, 0)
        
        JobCardLabourItem.objects.create(job_card=self.job, job_description='Labour', amount=200)
        self.assertEqual(self.job.get_total_amount, 200)
        self.assertEqual(self.job.get_balance_amount, 200)
        
        self.job.received_amount = 50
        self.assertEqual(self.job.get_balance_amount, 150)
        
        # Test deleting labour item triggers update_totals
        labour = self.job.labours.first()
        labour.delete()
        self.job.refresh_from_db()
        self.assertEqual(self.job.get_total_amount, 0)
