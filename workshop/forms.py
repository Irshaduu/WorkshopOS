from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet

from .models import (
    CarBrand,
    CarModel,
    SparePart,
    ConcernSolution,
    SpareShop,
    JobCard,
    JobCardConcern,
    JobCardSpareItem,
    JobCardLabourItem,
    Mechanic,
)

# =============================================================================
# MIXINS & WIDGETS
# =============================================================================

class BootstrapFormMixin:
    """
    Mixin to apply Bootstrap 'form-control' class to all fields.
    Crucially, it APPENDS the class to existing classes to preserve custom hooks.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            # Determine the correct Bootstrap class
            bootstrap_class = 'form-control'
            if isinstance(field.widget, forms.CheckboxInput):
                bootstrap_class = 'form-check-input'
            
            # Get any existing class (e.g., 'autocomplete-brand')
            existing_class = field.widget.attrs.get('class', '')
            
            # Append or set the new class
            if existing_class:
                new_class = f"{existing_class} {bootstrap_class}"
            else:
                new_class = bootstrap_class
            
            # Update the widget attributes
            field.widget.attrs.update({
                'class': new_class,
                'placeholder': field.label
            })


# =============================================================================
# STUDY FORMS
# =============================================================================

class CarBrandForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CarBrand
        fields = ['name', 'logo_image']
        labels = {
            'name': 'Brand Name',
            'logo_image': 'Brand Logo',
        }


class CarModelForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CarModel
        fields = ['brand', 'name']
        widgets = {
            'brand': forms.Select(attrs={'class': 'form-select'}),
        }


class SparePartForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SparePart
        fields = ['name']


class ConcernSolutionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ConcernSolution
        fields = ['concern']
        widgets = {
            'concern': forms.Textarea(attrs={'rows': 2}),
        }


class SpareShopForm(BootstrapFormMixin, forms.ModelForm):
    """
    Form for creating / editing a SpareShop entry.
    """
    class Meta:
        model = SpareShop
        fields = ['name', 'phone', 'address']
        labels = {
            'name': 'Shop Name',
            'phone': 'Phone (optional)',
            'address': 'Address (optional)',
        }


# =============================================================================
# JOB CARD FORM (The Core)
# =============================================================================

class MechanicChoiceIterator(forms.models.ModelChoiceIterator):
    """
    Flat list, no optgroup headers (tested confusing) — Mechanics first,
    Assistant Mechanics second; MechanicSelect.create_option bolds the
    Mechanic-role options so the ordering reads as priority without a group
    label. Anything else in the queryset (a job card's historical mechanic
    whose role/active status has since changed) still renders, trailing at
    the end, so reopening an old card to edit something unrelated never
    silently clears the assignment.
    """
    def __iter__(self):
        if self.field.empty_label is not None:
            yield ("", self.field.empty_label)

        by_role = {}
        for obj in self.queryset.order_by('name'):
            by_role.setdefault(obj.role, []).append(self.choice(obj))

        for role in Mechanic.JOBCARD_ELIGIBLE_ROLES:
            yield from by_role.get(role, [])

        for role, choices in by_role.items():
            if role not in Mechanic.JOBCARD_ELIGIBLE_ROLES:
                yield from choices


class MechanicSelect(forms.Select):
    """Bolds Mechanic-role options; everything else renders at normal weight."""
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None and instance.role == Mechanic.ROLE_MECHANIC:
            option['attrs']['style'] = 'font-weight: 700;'
        return option


class MechanicChoiceField(forms.ModelChoiceField):
    iterator = MechanicChoiceIterator

    def label_from_instance(self, obj):
        if obj.role not in Mechanic.JOBCARD_ELIGIBLE_ROLES or not obj.is_active:
            return f"{obj.name} — {obj.get_role_display()} (not current)"
        return obj.name


class JobCardForm(BootstrapFormMixin, forms.ModelForm):
    """
    Main job card form.
    Note: completed_date is auto-filled on completion, not manually entered.
    """
    lead_mechanic = MechanicChoiceField(
        queryset=Mechanic.objects.none(),
        required=False,
        label='Assigned Mechanic',
        widget=MechanicSelect(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = JobCard
        fields = [
            'admitted_date',
            'brand_name',
            'model_name',
            'registration_number',
            'mileage',
            'customer_name',
            'customer_contact',
            'lead_mechanic',
            'car_color',
            'car_color_other',
        ]
        widgets = {
            'admitted_date': forms.DateInput(attrs={'type': 'date'}),
            'brand_name': forms.TextInput(attrs={
                'autocomplete': 'off',
                'class': 'autocomplete-brand',
            }),
            'model_name': forms.TextInput(attrs={
                'autocomplete': 'off',
                'class': 'autocomplete-model',
            }),
            'registration_number': forms.TextInput(attrs={
                'style': 'text-transform: uppercase;',
                'autocapitalize': 'characters'
            }),
            'mileage': forms.TextInput(attrs={
                'placeholder': 'e.g. 50000 or 50k',
                'inputmode': 'numeric'
            }),
            'customer_contact': forms.NumberInput(attrs={
                # numeric keypad
            }),
            'car_color_other': forms.TextInput(attrs={
                'placeholder': 'Specify "Other" color...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        eligible_ids = list(
            Mechanic.objects.filter(
                is_active=True, role__in=Mechanic.JOBCARD_ELIGIBLE_ROLES
            ).values_list('pk', flat=True)
        )
        current_id = self.instance.lead_mechanic_id if self.instance and self.instance.pk else None
        if current_id and current_id not in eligible_ids:
            eligible_ids.append(current_id)
        self.fields['lead_mechanic'].queryset = Mechanic.objects.filter(pk__in=eligible_ids)


# =============================================================================
# FORMSETS
# =============================================================================

JobCardConcernFormSet = inlineformset_factory(
    JobCard,
    JobCardConcern,
    fields=['concern_text', 'status'],
    extra=0,
    can_delete=True,
    validate_min=False,
    widgets={
        'concern_text': forms.TextInput(attrs={
            'class': 'form-control autocomplete-concern',
            'placeholder': 'Start typing concern...',
            'autocomplete': 'off',
        }),
        'status': forms.Select(attrs={
            'class': 'form-select form-select-sm',
            'style': 'height: 38px;'
        })
    }
)

# -----------------------------------------------------------------------------
# The two spare routes — one model, one relation, two formsets
# -----------------------------------------------------------------------------
# `JobCardSpareItem` holds both a shop purchase and a warehouse draw, told apart
# by `source`. The Job Card edits them as two separate sections because they have
# almost nothing in common on screen: a draw has no shop, no price to negotiate
# and no ordering workflow, so eight columns of ordering fields sat empty and
# invited staff to fill boxes that meant nothing.
#
# They stay ONE model on purpose — roughly twenty places read `jobcard.spares`
# (the bill total, the invoice, the shop ledger, Stock History, the analysis
# engine, the delete guard). A second model would need every one of them taught
# to union two relations, and a single miss would short a customer's bill.
#
# Each formset therefore scopes itself to its own `source`, both when reading
# existing rows and when stamping new ones.

class SourceScopedSpareFormSet(BaseInlineFormSet):
    """Shows only its own route's rows, and stamps `source` onto anything new."""

    spare_source = None

    def get_queryset(self):
        return super().get_queryset().filter(source=self.spare_source)

    def save_new(self, form, commit=True):
        # `source` is deliberately not an editable field — a row cannot be moved
        # between routes from the UI, because that would have to move warehouse
        # stock and a shop ledger balance at the same time.
        obj = super().save_new(form, commit=False)
        obj.source = self.spare_source
        if commit:
            obj.save()
        return obj


class ShopSpareFormSet(SourceScopedSpareFormSet):
    spare_source = JobCardSpareItem.SOURCE_SHOP


class InventoryDrawFormSet(SourceScopedSpareFormSet):
    spare_source = JobCardSpareItem.SOURCE_INVENTORY


class InventoryDrawForm(forms.ModelForm):
    """
    One warehouse draw. The product is chosen from the existing stock list and
    never typed freely — unlike a shop spare, whose name is deliberately free
    text for shop-floor speed. Inventory products are a closed set (they exist
    only because someone created them through Supplier → Add Product), so there
    is no entry speed to protect and a real link to gain.
    """

    class Meta:
        model = JobCardSpareItem
        fields = ['item', 'quantity', 'customer_rate', 'total_price']
        widgets = {
            # The visible control is a search box in the template; this carries
            # the actual choice. A ModelChoiceField validates the pk for us, so
            # a hand-typed or stale id cannot get through.
            'item': forms.HiddenInput(attrs={'class': 'inventory-item-id'}),
            'quantity': forms.TextInput(attrs={
                'class': 'form-control text-center',
                'placeholder': 'Qty',
            }),
            'customer_rate': forms.TextInput(attrs={
                'class': 'form-control text-end inventory-rate',
                'placeholder': 'Unit Price (₹)',
            }),
            'total_price': forms.TextInput(attrs={
                'class': 'form-control text-end fw-bold inventory-total',
                'placeholder': 'Customer Price (₹)',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Every stock product is drawable. Deliberately NOT filtered by
        # ShopCatalogItem.is_active: that flag governs which supplier restock
        # bills may list a product, not whether something already on the shelf
        # can be fitted to a car.
        from inventory.models import Item
        self.fields['item'].queryset = Item.objects.select_related('category')

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get('item')
        money_or_qty = [cleaned.get(f) for f in ('quantity', 'customer_rate', 'total_price')]
        row_has_content = any(v not in (None, '') for v in money_or_qty)

        # A row someone started filling but never picked a product for would
        # otherwise save as a nameless, stockless line on the customer's bill.
        if row_has_content and not item:
            raise forms.ValidationError(
                "Choose the product from the suggestions — inventory items can't be typed in freely."
            )
        if item and cleaned.get('quantity') in (None, ''):
            self.add_error('quantity', "Enter how many were taken.")
        return cleaned


JobCardSpareFormSet = inlineformset_factory(
    JobCard,
    JobCardSpareItem,
    formset=ShopSpareFormSet,
    fields=['spare_part_name', 'quantity', 'shop_name', 'status', 'unit_price', 'total_price', 'ordered_date', 'received_date'],
    extra=0,
    can_delete=True,
    validate_min=False,
    widgets={
        'spare_part_name': forms.TextInput(attrs={
            'class': 'form-control autocomplete-spare',
            'autocomplete': 'off',
            'placeholder': 'Part Name',
        }),
        'quantity': forms.TextInput(attrs={
            'class': 'form-control text-center',
            'placeholder': 'Qty'
        }),
        'shop_name': forms.Select(attrs={
            'class': 'form-select form-select-sm shop-name-select',
            'style': 'min-width: 130px;',
        }),
        'status': forms.Select(attrs={
            'class': 'form-select form-select-sm status-dropdown',
            'style': 'min-width: 90px;'
        }),
        'unit_price': forms.TextInput(attrs={
            'class': 'form-control text-end',
            'placeholder': 'Shop Price (₹)'
        }),
        'total_price': forms.TextInput(attrs={
            'class': 'form-control text-end fw-bold',
            'placeholder': 'Price (₹)'
        }),
        'ordered_date': forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control ordered-date'
        }),
        'received_date': forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control received-date'
        }),
    }
)

JobCardInventoryFormSet = inlineformset_factory(
    JobCard,
    JobCardSpareItem,
    form=InventoryDrawForm,
    formset=InventoryDrawFormSet,
    fields=['item', 'quantity', 'customer_rate', 'total_price'],
    extra=0,
    can_delete=True,
    validate_min=False,
)

JobCardLabourFormSet = inlineformset_factory(
    JobCard,
    JobCardLabourItem,
    fields=['job_description', 'amount'],
    extra=0,
    can_delete=True,
    validate_min=False,
    widgets={
        'job_description': forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Job Performed',
        }),
        'amount': forms.TextInput(attrs={
            'class': 'form-control text-end',
            'placeholder': 'Amount'
        }),
    }
)
