from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from workshop.models import CarBrand, CarModel, SparePart, ConcernSolution
from inventory.models import Category, Item


class APIViewsTestCase(TestCase):
    """
    Tests for all autocomplete API endpoints (workshop/views.py).
    Key note: views use 'q' as the query param, not 'term'.
    autocomplete_spares returns list of dicts: [{"name": ..., "source": ...}]
    autocomplete_brands/models/concerns return list of strings.
    """

    def setUp(self):
        floor_group, _ = Group.objects.get_or_create(name='Floor')
        self.user = User.objects.create_user(username='api_staff', password='password')
        self.user.groups.add(floor_group)
        self.client = Client()
        self.client.login(username='api_staff', password='password')

        # Master list data
        self.brand = CarBrand.objects.create(name='Toyota')
        self.model = CarModel.objects.create(brand=self.brand, name='Corolla')
        self.spare = SparePart.objects.create(name='Brake Filter')
        self.concern = ConcernSolution.objects.create(concern='Brake Noise Issue')

        # Inventory item (higher priority in spares autocomplete)
        self.inv_category = Category.objects.create(name='Engine Parts')
        self.inv_item = Item.objects.create(
            category=self.inv_category,
            name='Brake Pad',
            current_stock=10,
            average_stock=10
        )

    # ------------------------------------------------------------------
    # autocomplete_brands  →  returns list of strings
    # ------------------------------------------------------------------
    def test_autocomplete_brands_match(self):
        url = reverse('autocomplete_brands')
        response = self.client.get(url, {'q': 'Toy'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ['Toyota'])

    def test_autocomplete_brands_no_match(self):
        url = reverse('autocomplete_brands')
        response = self.client.get(url, {'q': 'Honda'})
        self.assertEqual(response.json(), [])

    def test_autocomplete_brands_empty_query(self):
        """Empty q should return [] (min length guard)."""
        url = reverse('autocomplete_brands')
        response = self.client.get(url, {'q': ''})
        self.assertEqual(response.json(), [])

    # ------------------------------------------------------------------
    # autocomplete_models  →  returns list of strings
    # ------------------------------------------------------------------
    def test_autocomplete_models_with_brand(self):
        url = reverse('autocomplete_models')
        response = self.client.get(url, {'q': 'Cor', 'brand': 'Toyota'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), ['Corolla'])

    def test_autocomplete_models_wrong_brand(self):
        url = reverse('autocomplete_models')
        response = self.client.get(url, {'q': 'Cor', 'brand': 'Honda'})
        self.assertEqual(response.json(), [])

    def test_autocomplete_models_empty_query(self):
        """Empty q returns all models (no min-length guard on models view)."""
        url = reverse('autocomplete_models')
        response = self.client.get(url, {'q': ''})
        data = response.json()
        # Corolla must be in the results since q='' matches everything
        self.assertIn('Corolla', data)

    # ------------------------------------------------------------------
    # autocomplete_spares  →  returns list of dicts {"name":…,"source":…}
    # ------------------------------------------------------------------
    def test_autocomplete_spares_no_longer_mixes_in_stock_products(self):
        """
        Changed 2026-07-30. This endpoint used to return inventory products too,
        tagged source='inventory' and highlighted yellow — the only hint that
        picking that name would silently deduct warehouse stock. Warehouse draws
        have their own section and their own endpoint now, so mixing the two lists
        would reintroduce exactly the ambiguity the split removed.
        """
        url = reverse('autocomplete_spares')
        data = self.client.get(url, {'q': 'Brake'}).json()
        names = [d['name'] for d in data]

        self.assertIn('Brake Filter', names)          # master list, still offered
        self.assertNotIn('Brake Pad', names)          # inventory product, not here
        self.assertEqual({d['source'] for d in data}, {'master'})

    def test_the_inventory_picker_offers_stock_products_with_their_ids(self):
        """The other half of the split: a product is chosen by id, not by name."""
        data = self.client.get(reverse('autocomplete_inventory_items'), {'q': 'Brake'}).json()
        names = {d['name']: d for d in data}
        self.assertIn('Brake Pad', names)
        self.assertEqual(names['Brake Pad']['id'], self.inv_item.pk)
        self.assertNotIn('Brake Filter', names)       # master-list spare, not stock

    def test_autocomplete_spares_empty_query(self):
        """Empty q should return [] (min length guard)."""
        url = reverse('autocomplete_spares')
        response = self.client.get(url, {'q': ''})
        self.assertEqual(response.json(), [])

    # ------------------------------------------------------------------
    # autocomplete_concerns  →  returns list of strings
    # ------------------------------------------------------------------
    def test_autocomplete_concerns_match(self):
        url = reverse('autocomplete_concerns')
        response = self.client.get(url, {'q': 'Brake'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Brake Noise Issue', response.json())

    def test_autocomplete_concerns_empty_query(self):
        """Empty q should return [] (min length guard)."""
        url = reverse('autocomplete_concerns')
        response = self.client.get(url, {'q': ''})
        self.assertEqual(response.json(), [])
