# =============================================================================
# VIEWS PACKAGE — Backward-Compatible Re-Export Layer
# =============================================================================
# This package splits the original monolithic views.py into logical modules.
# All view functions are re-exported here so that `from . import views`
# and `views.some_function` continue to work without any URL changes.
# =============================================================================

from .dashboard import home, live_report
from .jobcard import (
    jobcard_create, jobcard_list, jobcard_detail, jobcard_edit, jobcard_delete,
)
from .delivered import (
    delivered_list, mark_delivered, undo_delivered, toggle_hold,
)
from .trash import (
    trash_list, restore_jobcard, permanent_delete_jobcard,
)
from .billing import invoice_view, update_bill_status
from .bulk_payer import (
    bulk_payer_list, bulk_payer_create, bulk_payer_detail,
    bulk_payer_add_card, bulk_payer_remove_card, bulk_payer_pay,
    bulk_payer_delete, bulk_payer_trash_list, bulk_payer_restore,
    bulk_payer_permanent_delete, bulk_payment_history_delete,
    permanent_delete_payment_history,
)
from .spare_shop import (
    spare_shop_list, spare_shop_create, spare_shop_edit, spare_shop_detail,
    spare_shop_pay, spare_shop_pay_item, spare_shop_payment_reverse,
    spare_shop_delete, spare_shop_restore, spare_shop_permanent_delete,
    spare_shop_payment_permanent_delete,
)
from .pending import pending_payments_list
from .car_profiles import car_profile_list, car_profile_detail
from .master_lists import (
    master_lists_home,
    brand_list, brand_create, brand_edit, brand_delete, brand_model_list,
    model_create, model_edit, model_delete,
    spare_list, spare_create, spare_edit,
    concern_list, concern_create, concern_edit,
)
from .autocomplete import (
    autocomplete_brands, autocomplete_models,
    autocomplete_spares, autocomplete_concerns,
)
