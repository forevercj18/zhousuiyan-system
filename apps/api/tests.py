from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Order, Part, SKU


User = get_user_model()


class ApiEndpointsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='api_tester',
            password='test123',
            role='admin',
            is_superuser=True,
            is_staff=True,
        )
        self.client.login(username='api_tester', password='test123')

        self.sku = SKU.objects.create(
            code='SKU-API-1',
            name='API套餐',
            category='主题套餐',
            rental_price=Decimal('120.00'),
            deposit=Decimal('60.00'),
            stock=3,
            is_active=True,
        )
        self.part = Part.objects.create(
            name='API部件',
            spec='A1',
            category='accessory',
            unit='个',
            current_stock=2,
            safety_stock=3,
            is_active=True,
        )
        Order.objects.create(
            customer_name='API客户',
            customer_phone='13600000000',
            delivery_address='API地址',
            event_date=date.today(),
            rental_days=1,
            status='pending',
            created_by=self.user,
        )

    def test_orders_api_returns_database_records(self):
        resp = self.client.get(reverse('api_orders_list'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['data']), 1)
        self.assertIn('customer_name', payload['data'][0])

    def test_skus_api_returns_database_records(self):
        resp = self.client.get(reverse('api_skus_list'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        codes = [item['code'] for item in payload['data']]
        self.assertIn('SKU-API-1', codes)

    def test_dashboard_stats_api_returns_real_counts(self):
        resp = self.client.get(reverse('api_dashboard_stats'))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data']['total_orders'], 1)
        self.assertEqual(payload['data']['total_skus'], 1)
        self.assertEqual(payload['data']['low_stock_parts'], 1)
