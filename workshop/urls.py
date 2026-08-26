from django.urls import path
from django.contrib.auth import views as django_auth_views
from django.views.generic import TemplateView, RedirectView
from . import views
from . import auth_views
from . import management_views
from . import cashbook_views
from . import cleanup_views
from . import analysis_views
from .views import audits

urlpatterns = [

    # ------------------
    # SECTION 1: HOME (Job Card Entry)
    # ------------------
    path('', views.home, name='home'),
    path('jobcards/create/', views.jobcard_create, name='jobcard_create'),

    # ------------------
    # SECTION 2: JOBS (Review)
    # ------------------
    path('jobcards/', views.jobcard_list, name='jobcard_list'),
    path('jobcards/live-report/', views.live_report, name='live_report'),
    path('jobcards/<int:pk>/', views.jobcard_detail, name='jobcard_detail'),
    path('jobcards/<int:pk>/edit/', views.jobcard_edit, name='jobcard_edit'),
    path('jobcards/<int:pk>/delete/', views.jobcard_delete, name='jobcard_delete'),

    # ------------------
    # COMPLETED (Workshop Dashboard)
    # ------------------
    path('completed/', views.completed_list, name='completed_list'),
    path('deletion-history/', views.deletion_history_list, name='deletion_history'),
    path('deletion-history/<int:pk>/', views.deletion_history_detail, name='deletion_history_detail'),
    path('pending-payments/', views.pending_payments_list, name='pending_payments_list'),
    path('paid-bills/', views.paid_bills_list, name='paid_bills_list'),
    
    # Bulk Payer System (inside Pending Bills)
    path('pending-payments/bulk-payers/', views.bulk_payer_list, name='bulk_payer_list'),
    path('pending-payments/bulk-payers/create/', views.bulk_payer_create, name='bulk_payer_create'),
    path('pending-payments/bulk-payers/<int:pk>/', views.bulk_payer_detail, name='bulk_payer_detail'),
    path('pending-payments/jobcards/move-to-bulk/', views.move_jobcard_to_bulk, name='move_jobcard_to_bulk'),
    path('pending-payments/bulk-payers/<int:pk>/edit/', views.bulk_payer_edit, name='bulk_payer_edit'),
    path('pending-payments/bulk-payers/<int:pk>/remove-card/', views.bulk_payer_remove_card, name='bulk_payer_remove_card'),
    path('pending-payments/bulk-payers/<int:pk>/pay/', views.bulk_payer_pay, name='bulk_payer_pay'),
    path('pending-payments/bulk-payers/<int:pk>/delete/', views.bulk_payer_delete, name='bulk_payer_delete'),
    path('pending-payments/bulk-payers/<int:pk>/history/<int:history_pk>/delete/', views.bulk_payment_history_delete, name='bulk_payment_history_delete'),
    path('pending-payments/bulk-payers/archived/', views.bulk_payer_archived, name='bulk_payer_archived'),
    path('pending-payments/bulk-payers/<int:pk>/restore/', views.bulk_payer_restore, name='bulk_payer_restore'),

    # Audits
    path('audits/high-discounts/', audits.audit_high_discounts, name='audit_high_discounts'),

    # ------------------
    # SPARE SHOP SYSTEM
    # ------------------
    path('spare-shops/', views.spare_shop_list, name='spare_shop_list'),
    path('spare-shops/create/', views.spare_shop_create, name='spare_shop_create'),
    path('spare-shops/unassigned/', views.unassigned_spares_hub, name='unassigned_spares_hub'),
    path('spare-shops/unassigned/add/', views.unassigned_spare_add, name='unassigned_spare_add'),
    path('spare-shops/<int:pk>/', views.spare_shop_detail, name='spare_shop_detail'),
    path('spare-shops/<int:pk>/edit/', views.spare_shop_edit, name='spare_shop_edit'),
    path('spare-shops/<int:pk>/pay/', views.spare_shop_pay, name='spare_shop_pay'),
    path('spare-shops/<int:shop_pk>/payment/<int:payment_pk>/reverse/', views.spare_shop_payment_reverse, name='spare_shop_payment_reverse'),
    path('spare-shops/archived/', views.spare_shop_archived, name='spare_shop_archived'),
    path('spare-shops/<int:pk>/delete/', views.spare_shop_delete, name='spare_shop_delete'),
    path('spare-shops/<int:pk>/restore/', views.spare_shop_restore, name='spare_shop_restore'),
    path('spare-shops/<int:pk>/print/', views.spare_shop_print, name='spare_shop_print'),
    path('spare-shops/<int:pk>/add-unassigned/', views.spare_shop_add_unassigned, name='spare_shop_add_unassigned'),
    path('spare-shops/items/<int:item_pk>/unassign/', views.spare_shop_unassign_item, name='spare_shop_unassign_item'),
    path('spare-shops/items/<int:item_pk>/update-price/', views.spare_shop_update_item_price, name='spare_shop_update_item_price'),
    path('spare-shops/items/<int:item_pk>/edit/', views.unassigned_spare_edit, name='unassigned_spare_edit'),
    path('spare-shops/items/<int:item_pk>/delete/', views.spare_shop_delete_unassigned, name='spare_shop_delete_unassigned'),
    
    path('jobcards/<int:pk>/complete/', views.mark_completed, name='mark_completed'),
    path('jobcards/<int:pk>/undo-complete/', views.undo_completed, name='undo_completed'),
    path('jobcards/<int:pk>/toggle-hold/', views.toggle_hold, name='toggle_hold'),
    path('jobcards/<int:pk>/update-bill/', views.update_bill_status, name='update_bill_status'),

    # ------------------
    # SECTION 3: MASTER LISTS
    # ------------------
    path('master-lists/', views.master_lists_home, name='master_lists_home'),

    # 3A. Cars (Brand -> Models Drilldown)
    path('master-lists/brands/', views.brand_list, name='brand_list'),
    path('master-lists/brands/add/', views.brand_create, name='brand_add'),
    path('master-lists/brands/<int:pk>/edit/', views.brand_edit, name='brand_edit'),
    path('master-lists/brands/<int:pk>/delete/', views.brand_delete, name='brand_delete'),
    path('master-lists/brands/<int:brand_id>/models/', views.brand_model_list, name='brand_model_list'),
    
    # Model Management
    path('master-lists/models/add/', views.model_create, name='model_add_generic'), # Fallback
    path('master-lists/brands/<int:brand_id>/models/add/', views.model_create, name='model_add'), # Context aware
    path('master-lists/models/<int:pk>/edit/', views.model_edit, name='model_edit'),
    path('master-lists/models/<int:pk>/delete/', views.model_delete, name='model_delete'),

    # 3B. Spares
    path('master-lists/spares/', views.spare_list, name='spare_list'),
    path('master-lists/spares/add/', views.spare_create, name='spare_add'),
    path('master-lists/spares/<int:pk>/edit/', views.spare_edit, name='spare_edit'),

    # 3C. Concerns Database
    path('master-lists/concerns/', views.concern_list, name='concern_list'),
    path('master-lists/concerns/add/', views.concern_create, name='concern_add'),
    path('master-lists/concerns/<int:pk>/edit/', views.concern_edit, name='concern_edit'),

    # ------------------
    # API: AUTOCOMPLETE
    # ------------------
    path('api/autocomplete/brands/', views.autocomplete_brands, name='autocomplete_brands'),
    path('api/autocomplete/models/', views.autocomplete_models, name='autocomplete_models'),
    path('api/autocomplete/spares/', views.autocomplete_spares, name='autocomplete_spares'),
    path('api/autocomplete/concerns/', views.autocomplete_concerns, name='autocomplete_concerns'),
    path('api/autocomplete/inventory-items/', views.autocomplete_inventory_items, name='autocomplete_inventory_items'),
    # Suggested selling price for a part, from what it last sold for. Feeds a
    # PLACEHOLDER on the Estimate screen and nothing else — see the view.
    path('api/spare-price-hint/', views.spare_price_hint, name='spare_price_hint'),

    # ------------------
    # CAR PROFILES
    # ------------------
    path('car-profiles/', views.car_profile_list, name='car_profile_list'),
    path('car-profiles/<str:registration>/', views.car_profile_detail, name='car_profile_detail'),

    # ------------------
    # INVOICE
    # ------------------
    path('invoice/<int:pk>/', views.invoice_view, name='invoice_view'),

    # ------------------
    # ESTIMATES (quotations — standalone, connected to nothing)
    # ------------------
    path('estimates/', views.estimate_list, name='estimate_list'),
    path('estimates/create/', views.estimate_create, name='estimate_create'),
    path('estimates/<int:pk>/', views.estimate_print, name='estimate_print'),
    path('estimates/<int:pk>/edit/', views.estimate_edit, name='estimate_edit'),
    path('estimates/<int:pk>/delete/', views.estimate_delete, name='estimate_delete'),

    # ------------------
    # AUTH: LOGIN/LOGOUT
    # ------------------
    # ONE door. `/admin-login/` used to be a second face on the same view — same
    # authentication, same lockouts, only the heading and the accent differed —
    # so it protected nothing (either face accepted any role) while publishing
    # the org chart to anyone who typed the address: privileged accounts exist,
    # and here is their door. The staff face went further and named the tiers in
    # its placeholder ("Office/Floor username").
    #
    # The URL *name* is kept because the owners' bookmarks and existing
    # `reverse()` calls point at it. `query_string=True` carries `?next=` across
    # the hop, so an old bookmark that a decorator appended a destination to
    # still lands where it was going.
    path('login/', auth_views.login_view, name='login'),
    path('admin-login/',
         RedirectView.as_view(pattern_name='login', query_string=True),
         name='admin_login'),
    path('change-password/', auth_views.change_password_view, name='change_password'),
    path('forgot-password/', auth_views.owner_forgot_password_view, name='owner_forgot_password'),
    path('reset-password/', auth_views.owner_reset_password_view, name='owner_reset_password'),
    path('logout/', django_auth_views.LogoutView.as_view(next_page='home'), name='logout'),

    # ------------------
    # NOTIFICATIONS (Owner only — see workshop/notifications.py for the catalogue)
    # ------------------
    path('notifications/', views.notification_list, name='notification_list'),
    path('notifications/panel/', views.notification_panel, name='notification_panel'),
    path('notifications/<int:pk>/open/', views.notification_open, name='notification_open'),
    path('notifications/<int:pk>/read/', views.notification_mark_read, name='notification_mark_read'),
    path('notifications/read-all/', views.notification_mark_all_read, name='notification_mark_all_read'),

    # Web Push. `sw.js` MUST stay at the origin root — a service worker can only
    # control pages at or below its own path, so serving it under /static/ would
    # limit its scope to /static/ and it would never receive a push for the app.
    path('sw.js', views.service_worker, name='service_worker'),

    # Keep the app out of search results. This is a private system for one
    # workshop's staff; the business's public site is a separate host.
    # `Disallow` and the `X-Robots-Tag: noindex` header from
    # NoIndexMiddleware are deliberately BOTH present, and the interaction is
    # worth knowing before anyone "simplifies" it: a crawler that obeys
    # Disallow never fetches the page, so it never sees the header — the two
    # are not belt-and-braces on the same crawler, they cover different ones.
    # Disallow stops well-behaved bots; the header is what de-indexes a URL
    # that got in anyway (someone pasting a link in a public place is the
    # realistic route). Nothing here is a security control — every page behind
    # this is behind a login.
    path(
        'robots.txt',
        TemplateView.as_view(
            template_name='workshop/robots.txt',
            content_type='text/plain',
        ),
        name='robots_txt',
    ),
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),

    # ------------------
    # PHOTOS — sign, commit, list, delete. All @staff_required: Floor is who
    # takes them. Sign and commit are separate so a row can never point at an
    # object that failed to upload; see workshop/views/photos.py.
    # ------------------
    path('photos/sign/', views.photo_sign, name='photo_sign'),
    path('photos/commit/', views.photo_commit, name='photo_commit'),
    path('photos/list/', views.photo_list, name='photo_list'),
    path('photos/delete/', views.photo_delete, name='photo_delete'),

    # The local-disk backend's stand-in for a bucket. Live only on a DEBUG
    # server with no S3 credentials; both 404 otherwise. Their permission is the
    # signed link, not the session — same shape as a presigned URL.
    path('photos/blob/put/', views.photo_blob_put, name='photo_blob_put'),
    path('photos/blob/get/', views.photo_blob_get, name='photo_blob_get'),

    # ------------------
    # MANAGEMENT & SECURITY (Owner/Office)
    # ------------------
    path('manage/', management_views.manage_dashboard, name='manage_dashboard'),
    path('manage/create-user/', management_views.manage_create_user, name='manage_create_user'),
    path('manage/users/<int:user_id>/reset-password/', management_views.manage_reset_password, name='manage_reset_password'),
    path('manage/users/<int:user_id>/delete/', management_views.manage_delete_user, name='manage_delete_user'),
    path('manage/users/<int:user_id>/unlock/', management_views.manage_unlock_account, name='manage_unlock_account'),
    path('manage/mechanics/create/', management_views.manage_create_mechanic, name='manage_create_mechanic'),
    path('manage/mechanics/<int:mechanic_id>/toggle/', management_views.manage_toggle_mechanic, name='manage_toggle_mechanic'),
    path('manage/mechanics/<int:mechanic_id>/edit/', management_views.manage_edit_mechanic, name='manage_edit_mechanic'),
    path('manage/sessions/<int:session_id>/terminate/', management_views.manage_terminate_session, name='manage_terminate_session'),

    # ------------------
    # SALARY & ADVANCE (Office/Owner)
    # ------------------
    path('salary-advance/', views.salary_advance_home, name='salary_advance_home'),
    path('salary-advance/add/', views.salary_advance_add, name='salary_advance_add'),
    path('salary-advance/<int:pk>/delete/', views.salary_advance_delete, name='salary_advance_delete'),
    path('salary-advance/staff/<int:staff_id>/', views.salary_advance_staff_detail, name='salary_advance_staff_detail'),
    path('salary-advance/staff/<int:staff_id>/set-salary/', views.salary_set_amount, name='salary_set_amount'),
    path('salary-advance/payment/<int:year>/<int:month>/', views.salary_payment_form, name='salary_payment_form'),
    path('salary-advance/payment/<int:pk>/delete/', views.salary_payment_delete, name='salary_payment_delete'),

    # ------------------
    # CASHBOOK (Office/Owner) — Standalone ledger, NOT part of Manage Accounts
    # ------------------
    path('cashbook/', cashbook_views.cashbook_view, name='cashbook'),
    path('cashbook/add/', cashbook_views.add_cashbook_entry, name='manage_add_cashbook_entry'),
    path('cashbook/<int:pk>/delete/', cashbook_views.delete_cashbook_entry, name='manage_delete_cashbook_entry'),
    path('cashbook/<int:pk>/edit/',   cashbook_views.edit_cashbook_entry,   name='manage_edit_cashbook_entry'),

    # ------------------
    # ANALYSIS & REPORTS (Owner Only)
    # ------------------
    path('analysis/', analysis_views.analysis_dashboard, name='analysis_dashboard'),
    path('analysis/insights/', analysis_views.analysis_insights, name='analysis_insights'),
    path('analysis/insights/<str:section>/', analysis_views.analysis_insight_section, name='analysis_insight_section'),

    # ------------------
    # DATA CLEANUP TOOL
    # ------------------
    path('manage/cleanup/', cleanup_views.data_cleanup_view, name='data_cleanup'),
    path('manage/cleanup/spare/<int:spare_id>/delete/', cleanup_views.cleanup_delete_spare, name='cleanup_delete_spare'),
    path('manage/cleanup/spare/<int:spare_id>/rename/', cleanup_views.cleanup_rename_spare, name='cleanup_rename_spare'),
    path('manage/cleanup/concern/<int:concern_id>/delete/', cleanup_views.cleanup_delete_concern, name='cleanup_delete_concern'),
    path('manage/cleanup/concern/<int:concern_id>/rename/', cleanup_views.cleanup_rename_concern, name='cleanup_rename_concern'),
]
