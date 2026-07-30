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
from .completed import (
    completed_list, mark_completed, undo_completed, toggle_hold,
)
from .deletion_history import (
    deletion_history_list, deletion_history_detail,
)
from .billing import invoice_view, update_bill_status
from .bulk_payer import (
    bulk_payer_list, bulk_payer_create, bulk_payer_detail,
    move_jobcard_to_bulk, bulk_payer_remove_card, bulk_payer_pay,
    bulk_payer_delete, bulk_payer_archived, bulk_payer_restore,
    bulk_payment_history_delete,
)
from .spare_shop import (
    spare_shop_list, spare_shop_create, spare_shop_edit, spare_shop_detail,
    spare_shop_pay, spare_shop_payment_reverse,
    spare_shop_delete, spare_shop_archived, spare_shop_restore,
    spare_shop_print,
    spare_shop_add_unassigned, spare_shop_unassign_item, spare_shop_update_item_price, spare_shop_delete_unassigned,
    unassigned_spares_hub, unassigned_spare_add,
)
from .pending import pending_payments_list
from .paid import paid_bills_list
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
    autocomplete_inventory_items,
)
from .salary_advance import (
    salary_advance_home, salary_advance_add, salary_advance_delete,
    salary_advance_staff_detail, salary_set_amount,
    salary_payment_form, salary_payment_delete,
)
from .notifications import (
    notification_list, notification_panel, notification_open,
    notification_mark_read, notification_mark_all_read,
)
from .push import service_worker, push_subscribe, push_unsubscribe
