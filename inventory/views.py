# inventory/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import Category, Item, ConsumptionRecord
from workshop.decorators import staff_required
from django.db.models import F, Q
from django.core.paginator import Paginator

@staff_required
def inventory_home(request):
    return redirect('inventory_list')

@staff_required
def inventory_manage(request):
    q = request.GET.get('q', '').strip()
    categories_query = Category.objects.prefetch_related('items').all().order_by('name')
    
    if q:
        categories_query = categories_query.filter(
            Q(name__icontains=q) | Q(items__name__icontains=q)
        ).distinct()
        
    paginator = Paginator(categories_query, 10) # 10 categories per page (heavy nested view)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'inventory/manage.html', {'categories': page_obj, 'page_obj': page_obj, 'q': q})

@staff_required
def add_category(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            Category.objects.create(name=name)
            messages.success(request, f"Category '{name}' created.")
            return redirect('inventory_manage')
    return render(request, 'inventory/add_category.html')

@staff_required
def edit_category(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            category.name = name
            category.save()
            messages.success(request, f"Category updated to '{name}'")
            return redirect('inventory_manage')
    return render(request, 'inventory/edit_category.html', {'category': category})

@staff_required
def delete_category(request, category_id):
    if request.method != 'POST':
        return redirect('inventory_manage')
    category = get_object_or_404(Category, pk=category_id)
    name = category.name
    category.delete()
    messages.success(request, f"Category '{name}' deleted.")
    return redirect('inventory_manage')

@staff_required
def category_detail(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    return render(request, 'inventory/category_detail.html', {'category': category})

@staff_required
def add_item(request, category_id):
    category = get_object_or_404(Category, pk=category_id)
    if request.method == 'POST':
        name = request.POST.get('name')
        avg_stock = float(request.POST.get('average_stock') or 0)
        cur_stock = float(request.POST.get('current_stock') or 0)

        Item.objects.create(
            category=category,
            name=name,
            average_stock=avg_stock,
            current_stock=cur_stock,
        )
        messages.success(request, f"Item '{name}' added.")
        return redirect('inventory_category_detail', category_id=category.id)
    return redirect('inventory_category_detail', category_id=category.id)

@staff_required
def edit_item(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    if request.method == 'POST':
        item.name = request.POST.get('name')
        item.average_stock = float(request.POST.get('average_stock') or 0)
        cur = request.POST.get('current_stock')
        if cur is not None:
             item.current_stock = float(cur)
        item.save()
        messages.success(request, f"Item '{item.name}' updated.")
        return redirect('inventory_category_detail', category_id=item.category.id)
    return redirect('inventory_category_detail', category_id=item.category.id)

@staff_required
def delete_item(request, item_id):
    if request.method != 'POST':
        return redirect('inventory_manage')
    item = get_object_or_404(Item, pk=item_id)
    cat_id = item.category.id
    item.delete()
    messages.success(request, "Item deleted.")
    return redirect('inventory_category_detail', category_id=cat_id)

@staff_required
def inventory_list(request):
    q = request.GET.get('q', '').strip()
    categories_query = Category.objects.prefetch_related('items').all().order_by('name')
    
    if q:
        categories_query = categories_query.filter(
            Q(name__icontains=q) | Q(items__name__icontains=q)
        ).distinct()
        
    paginator = Paginator(categories_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'inventory/inventory_list.html', {'categories': page_obj, 'page_obj': page_obj, 'q': q})

@staff_required
def update_stock(request, item_id):
    """
    Manual stock correction view.
    INTENTIONAL BYPASS: This view directly sets current_stock without
    triggering signals. Use only for manual corrections (e.g., physical
    stock count reconciliation). Signal-based auto-sync still applies
    to all JobCard and Restock operations.
    """
    item = get_object_or_404(Item, pk=item_id)
    if request.method == 'POST':
        new_stock = request.POST.get('current_stock')
        if new_stock is not None:
            # Optionally add a record to StockLedger if keeping audit
            item.current_stock = float(new_stock or 0)
            item.save()
            messages.success(request, f"Stock updated for {item.name}")
            messages.warning(request, "⚠️ Manual stock correction saved. This overrides the automatic signal-based count.")
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('inventory_list')

@staff_required
def inventory_low_stock(request):
    low_stock_query = Item.objects.select_related('category').filter(
        average_stock__gt=0
    ).filter(
        Q(current_stock__lte=0) | 
        Q(current_stock__lt=F('average_stock') * 0.25)
    ).order_by('name')
    
    paginator = Paginator(low_stock_query, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'inventory/low_stock.html', {'items': page_obj, 'page_obj': page_obj})

@staff_required
def consumption_history(request):
    records_query = ConsumptionRecord.objects.select_related('user', 'item', 'item__category').order_by('-timestamp')
    paginator = Paginator(records_query, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'inventory/consumption_history.html', {'records': page_obj, 'page_obj': page_obj})
