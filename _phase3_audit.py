import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.environ['DJANGO_SETTINGS_MODULE'] = 'formulad_workshop.settings'

import django
django.setup()

from workshop.models import JobCardSpareItem, JobCardLabourItem, JobCard
from django.db.models import Sum, Count, Avg, Q, DecimalField, ExpressionWrapper, F
from django.db.models.functions import Coalesce, TruncMonth, TruncDay
from decimal import Decimal
from datetime import date

print('=== JobCardSpareItem fields ===')
for f in JobCardSpareItem._meta.get_fields():
    try:
        print(f'  {f.name}: {f.get_internal_type()}')
    except:
        print(f'  {f.name}: (relation)')

print()
print('=== JobCardLabourItem fields ===')
for f in JobCardLabourItem._meta.get_fields():
    try:
        print(f'  {f.name}: {f.get_internal_type()}')
    except:
        print(f'  {f.name}: (relation)')

print()

# All-time spare revenue and cost
qs = JobCardSpareItem.objects.filter(
    job_card__is_deleted=False,
    job_card__delivered=True,
)
spare_agg = qs.aggregate(
    spare_revenue=Coalesce(Sum('total_price'), Decimal('0')),
    spare_cost=Coalesce(Sum('unit_price'), Decimal('0')),
    total_qty=Coalesce(Sum('quantity'), Decimal('0')),
    count=Count('id'),
)
print('=== Spare aggregates (all-time) ===')
for k, v in spare_agg.items():
    print(f'  {k} = {v}')

print()
# Labour all-time
labour_agg = JobCardLabourItem.objects.filter(
    job_card__is_deleted=False,
    job_card__delivered=True,
).aggregate(
    labour_revenue=Coalesce(Sum('amount'), Decimal('0')),
    count=Count('id'),
)
print('=== Labour aggregates (all-time) ===')
for k, v in labour_agg.items():
    print(f'  {k} = {v}')

print()
# Payment methods
print('=== Payment methods in DB ===')
pms = JobCard.objects.filter(is_deleted=False, delivered=True).values('payment_method').annotate(n=Count('id')).order_by('-n')
for pm in pms:
    print(f"  [{pm['payment_method']}] = {pm['n']}")

print()
# Discount data
disc_agg = JobCard.objects.filter(is_deleted=False, delivered=True).aggregate(
    total_discount=Coalesce(Sum('discount_amount'), Decimal('0')),
    avg_bill=Coalesce(Avg('total_bill_amount'), Decimal('0')),
    jobs_with_discount=Count('id', filter=Q(discount_amount__gt=0)),
)
print('=== Discount & Avg bill ===')
for k, v in disc_agg.items():
    print(f'  {k} = {v}')

print()
# Monthly trend — this year
print('=== Monthly trend (2026) ===')
monthly = JobCard.objects.filter(
    is_deleted=False,
    delivered=True,
    admitted_date__year=2026,
).annotate(month=TruncMonth('admitted_date')).values('month').annotate(
    revenue=Coalesce(Sum('total_bill_amount'), Decimal('0')),
    count=Count('id'),
).order_by('month')
for m in monthly:
    print(f"  {m['month'].strftime('%b %Y')}: rev={m['revenue']} jobs={m['count']}")

print()
# Top 5 discount recipients
print('=== Top 5 discount recipients ===')
top_disc = JobCard.objects.filter(
    is_deleted=False,
    delivered=True,
    discount_amount__gt=0,
).values('customer_name').annotate(
    total_discount=Sum('discount_amount')
).order_by('-total_discount')[:5]
for r in top_disc:
    print(f"  {r['customer_name']}: {r['total_discount']}")

print()
# Daily revenue for this month
print('=== Daily revenue June 2026 ===')
daily = JobCard.objects.filter(
    is_deleted=False,
    delivered=True,
    admitted_date__year=2026,
    admitted_date__month=6,
).annotate(day=TruncDay('admitted_date')).values('day').annotate(
    revenue=Coalesce(Sum('total_bill_amount'), Decimal('0')),
    count=Count('id'),
).order_by('day')
for d in daily:
    print(f"  {d['day'].strftime('%d %b')}: rev={d['revenue']} jobs={d['count']}")
