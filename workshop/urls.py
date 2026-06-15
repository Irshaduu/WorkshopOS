from django.urls import path
from django.contrib.auth import views as django_auth_views
from . import views
from . import auth_views
from . import management_views
from . import cleanup_views

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
    # NEW: DELIVERED (Workshop Dashboard)
    # ------------------
    path('delivered/', views.delivered_list, name='delivered_list'),
    path('trash/', views.trash_list, name='trash_list'),
    path('pending-payments/', views.pending_payments_list, name='pending_payments_list'),
    
    # Bulk Payer System (inside Pending Bills)
    path('pending-payments/bulk-payers/', views.bulk_payer_list, name='bulk_payer_list'),
    path('pending-payments/bulk-payers/create/', views.bulk_payer_create, name='bulk_payer_create'),
    path('pending-payments/bulk-payers/<int:pk>/', views.bulk_payer_detail, name='bulk_payer_detail'),
    path('pending-payments/bulk-payers/<int:pk>/add-card/', views.bulk_payer_add_card, name='bulk_payer_add_card'),
    path('pending-payments/bulk-payers/<int:pk>/remove-card/', views.bulk_payer_remove_card, name='bulk_payer_remove_card'),
    path('pending-payments/bulk-payers/<int:pk>/pay/', views.bulk_payer_pay, name='bulk_payer_pay'),
    path('pending-payments/bulk-payers/<int:pk>/delete/', views.bulk_payer_delete, name='bulk_payer_delete'),
    path('pending-payments/bulk-payers/<int:pk>/history/<int:history_pk>/delete/', views.bulk_payment_history_delete, name='bulk_payment_history_delete'),
    path('pending-payments/bulk-payers/trash/', views.bulk_payer_trash_list, name='bulk_payer_trash_list'),
    path('pending-payments/bulk-payers/<int:pk>/restore/', views.bulk_payer_restore, name='bulk_payer_restore'),
    path('pending-payments/bulk-payers/<int:pk>/permanent-delete/', views.bulk_payer_permanent_delete, name='bulk_payer_permanent_delete'),
    path('pending-payments/history/<int:history_pk>/permanent-delete/', views.permanent_delete_payment_history, name='permanent_delete_payment_history'),

    # ------------------
    # SPARE SHOP SYSTEM
    # ------------------
    path('spare-shops/', views.spare_shop_list, name='spare_shop_list'),
    path('spare-shops/create/', views.spare_shop_create, name='spare_shop_create'),
    path('spare-shops/unassigned/', views.unassigned_spares_hub, name='unassigned_spares_hub'),
    path('spare-shops/<int:pk>/', views.spare_shop_detail, name='spare_shop_detail'),
    path('spare-shops/<int:pk>/edit/', views.spare_shop_edit, name='spare_shop_edit'),
    path('spare-shops/<int:pk>/pay/', views.spare_shop_pay, name='spare_shop_pay'),
    path('spare-shops/<int:shop_pk>/payment/<int:payment_pk>/reverse/', views.spare_shop_payment_reverse, name='spare_shop_payment_reverse'),
    path('spare-shops/<int:pk>/delete/', views.spare_shop_delete, name='spare_shop_delete'),
    path('spare-shops/<int:pk>/restore/', views.spare_shop_restore, name='spare_shop_restore'),
    path('spare-shops/<int:pk>/permanent-delete/', views.spare_shop_permanent_delete, name='spare_shop_permanent_delete'),
    path('spare-shops/payment/<int:payment_pk>/permanent-delete/', views.spare_shop_payment_permanent_delete, name='spare_shop_payment_permanent_delete'),
    path('spare-shops/<int:pk>/print/', views.spare_shop_print, name='spare_shop_print'),
    path('spare-shops/<int:pk>/add-unassigned/', views.spare_shop_add_unassigned, name='spare_shop_add_unassigned'),
    path('spare-shops/items/<int:item_pk>/unassign/', views.spare_shop_unassign_item, name='spare_shop_unassign_item'),
    path('spare-shops/items/<int:item_pk>/update-price/', views.spare_shop_update_item_price, name='spare_shop_update_item_price'),
    
    path('jobcards/<int:pk>/deliver/', views.mark_delivered, name='mark_delivered'),
    path('jobcards/<int:pk>/undo-deliver/', views.undo_delivered, name='undo_delivered'),
    path('jobcards/<int:pk>/toggle-hold/', views.toggle_hold, name='toggle_hold'),
    path('jobcards/<int:pk>/update-bill/', views.update_bill_status, name='update_bill_status'),
    path('jobcards/<int:pk>/restore/', views.restore_jobcard, name='restore_jobcard'),
    path('jobcards/<int:pk>/permanent-delete/', views.permanent_delete_jobcard, name='permanent_delete_jobcard'),

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
    # AUTH: LOGIN/LOGOUT
    # ------------------
    path('login/', auth_views.staff_login_view, name='login'),
    path('admin-login/', auth_views.admin_login_view, name='admin_login'),
    path('forgot-password/', auth_views.owner_forgot_password_view, name='owner_forgot_password'),
    path('reset-password/', auth_views.owner_reset_password_view, name='owner_reset_password'),
    path('logout/', django_auth_views.LogoutView.as_view(next_page='home'), name='logout'),

    # ------------------
    # OWNER MANAGEMENT DASHBOARD
    # ------------------
    path('manage/', management_views.manage_dashboard, name='manage_dashboard'),
    path('manage/create-user/', management_views.manage_create_user, name='manage_create_user'),
    path('manage/users/<int:user_id>/reset-password/', management_views.manage_reset_password, name='manage_reset_password'),
    path('manage/users/<int:user_id>/delete/', management_views.manage_delete_user, name='manage_delete_user'),
    path('manage/mechanics/create/', management_views.manage_create_mechanic, name='manage_create_mechanic'),
    path('manage/mechanics/<int:mechanic_id>/toggle/', management_views.manage_toggle_mechanic, name='manage_toggle_mechanic'),
    path('manage/mechanics/<int:mechanic_id>/edit/', management_views.manage_edit_mechanic, name='manage_edit_mechanic'),
    path('manage/sessions/<int:session_id>/terminate/', management_views.manage_terminate_session, name='manage_terminate_session'),

    # ------------------
    # DATA CLEANUP TOOL
    # ------------------
    path('manage/cleanup/', cleanup_views.data_cleanup_view, name='data_cleanup'),
    path('manage/cleanup/spare/<int:spare_id>/delete/', cleanup_views.cleanup_delete_spare, name='cleanup_delete_spare'),
    path('manage/cleanup/spare/<int:spare_id>/rename/', cleanup_views.cleanup_rename_spare, name='cleanup_rename_spare'),
    path('manage/cleanup/concern/<int:concern_id>/delete/', cleanup_views.cleanup_delete_concern, name='cleanup_delete_concern'),
    path('manage/cleanup/concern/<int:concern_id>/rename/', cleanup_views.cleanup_rename_concern, name='cleanup_rename_concern'),
]
