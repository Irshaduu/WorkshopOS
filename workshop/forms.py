from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.forms.formsets import DELETION_FIELD_NAME

from .models import (
    CarBrand,
    CarModel,
    SparePart,
    ConcernSolution,
    SpareShop,
    Estimate,
    EstimateJobLine,
    EstimatePartLine,
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

    It does the same for the placeholder: a widget that declares one KEEPS it,
    and only fields without one fall back to the label. It used to overwrite
    unconditionally, which quietly threw away every hint an author had written —
    `mileage` declares 'e.g. 50000 or 50k' and rendered as 'Mileage', and
    `car_color_other` declares 'Specify "Other" color...' and rendered as
    'Car color other'. The docstring already claimed custom hooks were
    preserved; now that is true of the placeholder as well as the class.
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

            field.widget.attrs['class'] = new_class
            field.widget.attrs.setdefault('placeholder', field.label)


# =============================================================================
# STUDY FORMS
# =============================================================================

# ---------------------------------------------------------------------------
# MASTER DATA
#
# All four of these dedupe on `__iexact`, never on the model's plain `unique=True`.
# That constraint is case-sensitive, so "Toyota" and "toyota" were both
# insertable, as were "Oil Filter" and "oil filter" — and ConcernSolution had no
# uniqueness at all, so the same concern could be added any number of times. The
# result was a polluted autocomplete where the same thing appeared twice and
# staff picked whichever came first, which is precisely what the taxonomy rule in
# CLAUDE.md exists to prevent. The auto-learn path in the job-card views has
# always deduped this way; these forms are the manual entry points that did not.
#
# The check excludes the row being edited, so re-saving a form without touching
# the name is never blocked by the name it already has.
# ---------------------------------------------------------------------------

def _reject_case_variant(model, field_name, value, instance, label):
    """
    Raise if another row already holds this value, ignoring case.

    CREATE only. On an edit, naming a row after one that already exists is a
    deliberate MERGE — the edit views route it through workshop/master_data.py,
    which folds the two together and relabels the job cards that used the old
    wording. Rejecting it here would take away the only tool for cleaning up a
    duplicate that already exists.
    """
    if instance is not None and instance.pk:
        return value
    clash = model.objects.filter(**{f'{field_name}__iexact': value}).first()
    if clash:
        raise forms.ValidationError(
            f"'{getattr(clash, field_name)}' is already in the {label} list."
        )
    return value


class CarBrandForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CarBrand
        fields = ['name', 'logo_image']
        labels = {
            'name': 'Brand Name',
            'logo_image': 'Brand Logo',
        }

    def clean_name(self):
        name = ' '.join((self.cleaned_data.get('name') or '').split())
        return _reject_case_variant(CarBrand, 'name', name, self.instance, 'brand')

    def validate_unique(self):
        """
        Skip the model's `unique=True` check on `name` when EDITING.

        Django runs it during `_post_clean()`, so renaming "Toyta" onto an
        existing "Toyota" was rejected as a duplicate before the view ever got
        to `master_data.rename_brand()` — which is precisely the call that
        merges the two and relabels the job cards. The check still applies on
        create, where a duplicate really is an error.
        """
        if not self.instance.pk:
            return super().validate_unique()
        exclude = self._get_validation_exclusions()
        exclude.add('name')
        try:
            self.instance.validate_unique(exclude=exclude)
        except forms.ValidationError as e:
            self._update_errors(e)


class CarModelForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CarModel
        fields = ['brand', 'name']
        widgets = {
            'brand': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean(self):
        cleaned = super().clean()
        brand, name = cleaned.get('brand'), cleaned.get('name')
        # Scoped to the brand: "Corolla" under Toyota and under some other make
        # are different cars, and unique_together already says so.
        if brand and name:
            name = ' '.join(name.split())
            cleaned['name'] = name
            # Create only — an edit onto an existing name is a merge, handled by
            # master_data.rename_model. See _reject_case_variant.
            if not self.instance.pk:
                clash = CarModel.objects.filter(brand=brand, name__iexact=name).first()
                if clash:
                    self.add_error('name', f"'{clash.name}' is already listed under {brand.name}.")
        return cleaned


class SparePartForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SparePart
        fields = ['name']

    def clean_name(self):
        name = ' '.join((self.cleaned_data.get('name') or '').split())
        return _reject_case_variant(SparePart, 'name', name, self.instance, 'spare parts')


class ConcernSolutionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ConcernSolution
        fields = ['concern']
        widgets = {
            'concern': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_concern(self):
        text = ' '.join((self.cleaned_data.get('concern') or '').split())
        return _reject_case_variant(ConcernSolution, 'concern', text, self.instance, 'concerns')


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
            # One charge for all the work — see JobCard.labour_amount. Rendered
            # inside the Jobs section, not with the vehicle details, and only for
            # Office/Owner (the template gates it; `_price_locked_data` enforces
            # that on the server, because a hidden input is still a posted one).
            'labour_amount',
        ]
        widgets = {
            'admitted_date': forms.DateInput(attrs={'type': 'date'}),
            'labour_amount': forms.TextInput(attrs={
                'class': 'form-control text-end fw-bold',
                'inputmode': 'decimal',
                'placeholder': 'Total Amount',
            }),
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

    def clean_labour_amount(self):
        """
        Empty means no labour, not an error — and never NULL.

        The box is blank on every parts-only card and is not rendered at all for
        Floor, so "absent" has to be a valid answer. The column is NOT NULL, so
        an empty field cleaning to None would take the save down with an
        IntegrityError rather than a message.

        Negative is refused outright rather than clamped: a clamp saves a number
        nobody typed, and a negative labour charge would reduce the bill below
        the parts on it — the same reasoning as the leave-days bound in
        Salary & Advance. `max_digits` is enforced by the field itself, so an
        oversized figure is a validation message, never a Postgres overflow.
        """
        value = self.cleaned_data.get('labour_amount')
        if value in (None, ''):
            # ABSENT and BLANK are different answers, and conflating them
            # destroys money. A POST that never carried the field at all comes
            # from a form that did not render it — Floor's job card, or a
            # disabled input on a locked record — and must leave the stored
            # charge alone. A field that WAS rendered and left empty means the
            # card has no labour on it.
            if 'labour_amount' not in self.data and self.instance.pk:
                return self.instance.labour_amount
            return Decimal('0')
        if value < 0:
            raise forms.ValidationError("A labour charge cannot be negative.")
        return value


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

# Descriptions only. `amount` is deliberately NOT a field here: the workshop
# charges for the work as a whole, so the figure lives once on
# `JobCard.labour_amount` and this section just lists what was done.
#
# Dropping it also closes a hole rather than opening one. The per-line amount
# used to be rendered for Floor inside a `d-none` cell (same reason the spare
# price fields are — an absent field saves as blank and wipes what Office
# entered), but `_price_locked_data` only ever rewrote the `spares` and
# `inventory` prefixes. So a Floor login POSTing `labours-0-amount=1` could
# rewrite the labour charge, exactly the defect AUD-0081 fixed for parts. A
# field that does not exist cannot be posted.
JobCardLabourFormSet = inlineformset_factory(
    JobCard,
    JobCardLabourItem,
    fields=['job_description'],
    extra=0,
    can_delete=True,
    validate_min=False,
    widgets={
        'job_description': forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Job Performed',
        }),
    }
)


# =============================================================================
# ESTIMATE
# =============================================================================
# A quotation, connected to nothing (see the Estimate model). The forms
# deliberately reuse the job card's autocomplete hooks — `autocomplete-brand`,
# `autocomplete-model` — because those endpoints already exist and an estimate
# names a car exactly the way a job card does. No new lookup was invented here.


def _tidy_money_initial(form, *names):
    """
    Render `8500`, not `8500.00` — and blank, not `0`, on a new record.

    Purely about typing. A money box that arrives holding `0` makes the first
    keystroke produce `08500`, and one holding `8500.00` puts two zeros and a
    point between the caret and the next digit, so entering a figure means
    deleting characters first. Both are the box fighting the person filling it
    in, on the field they touch most.

    Only the DISPLAY changes. Nothing is stored differently: `clean_labour_amount`
    still turns an empty box into `Decimal('0')`, and the column still holds two
    decimal places. Paise are kept whenever there are any (`1250.50`), because
    dropping those would change the number rather than tidy it.

    Bound forms are untouched by design — `BoundField.value()` reads submitted
    data, not `initial`, so a rejected POST still shows exactly what was typed
    rather than a reformatted guess at it.
    """
    for name in names:
        raw = form.initial.get(name)
        if raw in (None, ''):
            form.initial[name] = ''
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not value.is_finite() or value == 0:
            form.initial[name] = ''
        elif value == value.to_integral_value():
            form.initial[name] = f'{value.to_integral_value():f}'
        else:
            form.initial[name] = f'{value:.2f}'

class EstimateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Estimate
        fields = [
            'date',
            'customer_name',
            'customer_contact',
            'brand_name',
            'model_name',
            'registration_number',
            'mileage',
            # Chosen through the shared swatch picker, exactly as on a Job Card.
            # The visible control is a <div>; these are what post.
            'car_color',
            'car_color_other',
            'labour_amount',
            'notes',
        ]
        labels = {
            'brand_name': 'Car Brand',
            'model_name': 'Car Model',
            'registration_number': 'Registration Number',
            'notes': 'Internal note (never printed)',
        }
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'brand_name': forms.TextInput(attrs={
                'autocomplete': 'off',
                'class': 'autocomplete-brand',
                'placeholder': 'e.g. Toyota',
            }),
            'model_name': forms.TextInput(attrs={
                'autocomplete': 'off',
                'class': 'autocomplete-model',
                'placeholder': 'e.g. Corolla',
            }),
            'registration_number': forms.TextInput(attrs={
                'style': 'text-transform: uppercase;',
                'autocapitalize': 'characters',
                'placeholder': 'e.g. KL 10 AB 1234',
            }),
            'mileage': forms.TextInput(attrs={
                'placeholder': 'e.g. 50000 or 50k',
                'inputmode': 'numeric',
            }),
            'customer_contact': forms.TextInput(attrs={
                'inputmode': 'tel',
                'placeholder': 'Phone (optional)',
            }),
            'labour_amount': forms.TextInput(attrs={
                'class': 'form-control text-end fw-bold',
                'inputmode': 'decimal',
                'placeholder': 'Total Amount',
            }),
            'notes': forms.TextInput(attrs={
                'placeholder': 'Only you see this',
            }),
            'car_color_other': forms.TextInput(attrs={
                'placeholder': 'Specify "Other" colour…',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Total Labour is the box Office types into on almost every estimate.
        # Left alone it arrives holding `0` on a new quote and `8500.00` on an
        # edit — both of which have to be deleted before a figure can be typed.
        _tidy_money_initial(self, 'labour_amount')

    def clean_labour_amount(self):
        """
        Empty means no labour quoted, not an error — and never NULL.

        Word for word the rule on JobCardForm.labour_amount: plenty of estimates
        are parts only, the column is NOT NULL so cleaning to None would be an
        IntegrityError rather than a message, and a negative is refused outright
        rather than clamped (a clamp saves a number nobody typed).
        """
        value = self.cleaned_data.get('labour_amount')
        if value in (None, ''):
            return Decimal('0')
        if value < 0:
            raise forms.ValidationError("A labour charge cannot be negative.")
        return value


class BlankRowIsNoRowFormSet(BaseInlineFormSet):
    """
    A row left empty — or emptied out — is a row that does not exist.

    **Clearing the name and saving IS the delete gesture.** There is no ✕ on a
    row, deliberately: a per-row delete control is a one-tap way to lose work on
    a tablet, and a quote is typed in a hurry. So two rules:

      * A row where nothing was typed is not saved. `description` and `name` are
        NOT NULL at the column, so a row of empty strings would otherwise put an
        unnamed line on a document a customer reads.
      * **An existing row whose name has been cleared is DELETED — even if its
        figures are still there.** That is the whole gesture. Leaving the money
        behind and refusing the save would make "clear the name" mean nothing on
        exactly the rows people want to remove, which are the priced ones.

    Marking the row DELETE resolves both at once: Django's own delete path skips
    it when new (`save_new_objects`) and removes it when stored
    (`save_existing_objects`). The line forms drop `required` so a blank row
    reaches here cleanly in the first place.

    What is still refused: a **new** row carrying figures with no name
    (`EstimatePartLineForm.clean`), and any negative figure. A new row is being
    filled in, so a missing name there is a slip, not an erasure — and silently
    dropping it would throw away a price someone just typed.
    """

    #: Fields that decide whether the row holds anything. Set per subclass.
    content_fields = ()
    #: The field that names the row. Clearing it on a stored row deletes it.
    identity_field = None

    def clean(self):
        # BEFORE super(), and that order is load-bearing.
        #
        # `BaseModelFormSet.clean()` calls `validate_unique()`, which reads
        # `self.deleted_forms` — and that property CACHES its answer in
        # `_deleted_form_indexes` on first access. Marking the rows after
        # super() therefore marks them too late: the cache has already been
        # built from the unmarked forms, `deleted_forms` stays empty forever,
        # and `save_existing_objects` never deletes anything.
        #
        # The failure is worse than a no-op, which is why it is worth a comment
        # this long. `_post_clean` excludes a blank value on a not-required
        # field from model validation, so the emptied row raises no error
        # either — it is simply SAVED, writing `description=''` onto the
        # estimate. An unnamed line then prints on a document a customer reads.
        # Guarded by `test_clearing_an_existing_line_removes_it_instead_of_erroring`.
        for form in self.forms:
            # Absent when the form failed validation — those rows are not blank
            # by definition, and their errors are the right answer.
            cleaned = getattr(form, 'cleaned_data', None)
            if not cleaned:
                continue
            if self._row_is_gone(form, cleaned):
                cleaned[DELETION_FIELD_NAME] = True
        super().clean()

    @classmethod
    def _row_is_gone(cls, form, cleaned):
        # A stored row that has lost its name is a deliberate erasure, whatever
        # else is still in it — that is the delete gesture. A NEW row is only
        # dropped when it is empty all through, so a price typed into a row
        # whose name was forgotten raises an error instead of vanishing.
        if form.instance.pk and cls.identity_field:
            return not cls._filled(cleaned.get(cls.identity_field))
        return not any(cls._filled(cleaned.get(f)) for f in cls.content_fields)

    @staticmethod
    def _filled(value):
        if isinstance(value, str):
            return bool(value.strip())
        return value not in (None, '')


class EstimateJobLineFormSet(BlankRowIsNoRowFormSet):
    content_fields = ('description',)
    identity_field = 'description'


class EstimatePartLineFormSet(BlankRowIsNoRowFormSet):
    content_fields = ('name', 'quantity', 'customer_rate', 'amount')
    identity_field = 'name'


class EstimateJobLineForm(forms.ModelForm):
    """One line of work being quoted. A description, never a price."""

    class Meta:
        model = EstimateJobLine
        fields = ['description']
        widgets = {
            'description': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Job to be performed',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Blank is a legitimate answer here — see BlankRowIsNoRowFormSet. The
        # column stays NOT NULL; a blank row is deleted, never written empty.
        self.fields['description'].required = False


class EstimatePartLineForm(forms.ModelForm):
    """
    One quoted part. Every box is optional — the reference document prints
    parts with an empty price, which is how a workshop quotes something it still
    has to ring a supplier about.
    """

    class Meta:
        model = EstimatePartLine
        fields = ['name', 'quantity', 'customer_rate', 'amount']
        widgets = {
            'name': forms.TextInput(attrs={
                # A native <datalist>, not the Job Card's fetch-based
                # autocomplete: it needs no wiring, so it works identically on a
                # row added after page load. `estimate-part-name` is the hook
                # the price hint delegates on (see estimate.js).
                'class': 'form-control estimate-part-name',
                'list': 'estimate-part-names',
                'autocomplete': 'off',
                'placeholder': 'Part Name',
            }),
            'quantity': forms.TextInput(attrs={
                'class': 'form-control text-center estimate-qty',
                'inputmode': 'decimal',
                'placeholder': 'Qty',
            }),
            'customer_rate': forms.TextInput(attrs={
                'class': 'form-control text-end estimate-rate',
                'inputmode': 'decimal',
                'placeholder': 'Unit Price (₹)',
                # The label to restore when a part has no sales history. Without
                # it, clearing the name would leave the previous part's
                # suggestion sitting under the new one.
                'data-placeholder': 'Unit Price (₹)',
            }),
            'amount': forms.TextInput(attrs={
                'class': 'form-control text-end fw-bold estimate-amount',
                'inputmode': 'decimal',
                'placeholder': 'Amount (₹)',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = False   # see BlankRowIsNoRowFormSet
        # Same reasoning as Total Labour: reopening a quote to change 7 litres
        # to 4 should not mean deleting `.00` first, on every row.
        _tidy_money_initial(self, 'quantity', 'customer_rate', 'amount')

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get('name') or '').strip()
        has_money = any(
            cleaned.get(f) not in (None, '')
            for f in ('quantity', 'customer_rate', 'amount')
        )
        # A priced row with no name prints an amount beside a blank line and
        # inflates the total by something the customer cannot identify.
        #
        # NEW rows only. On a STORED row, clearing the name is the delete
        # gesture (see BlankRowIsNoRowFormSet) — raising here would make it fail
        # on exactly the rows people want to remove, which are the priced ones.
        if has_money and not name and not self.instance.pk:
            self.add_error('name', "Name the part, or clear the figures on this row.")
        for field in ('quantity', 'customer_rate', 'amount'):
            value = cleaned.get(field)
            if value is not None and value != '' and value < 0:
                self.add_error(field, "Cannot be negative.")
        return cleaned


# `extra=3` on both, unlike the job card's `extra=0` + "Add row" button. The
# paper form these replace opens with a block of empty lines and Office fills
# down it, so a new estimate that showed nothing until you pressed Add would be
# slower than the pad it is meant to replace. Django skips an extra row that was
# never touched (`has_changed()`), so unused ones cost nothing and save nothing.
# The Add button is still there for a long quote.
ESTIMATE_BLANK_ROWS = 3

EstimateJobFormSet = inlineformset_factory(
    Estimate,
    EstimateJobLine,
    form=EstimateJobLineForm,
    formset=EstimateJobLineFormSet,
    fields=['description'],
    extra=ESTIMATE_BLANK_ROWS,
    can_delete=True,
    validate_min=False,
)

EstimatePartFormSet = inlineformset_factory(
    Estimate,
    EstimatePartLine,
    form=EstimatePartLineForm,
    formset=EstimatePartLineFormSet,
    fields=['name', 'quantity', 'customer_rate', 'amount'],
    extra=ESTIMATE_BLANK_ROWS,
    can_delete=True,
    validate_min=False,
)
