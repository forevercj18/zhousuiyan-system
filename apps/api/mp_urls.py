"""微信小程序 API 路由"""
from django.urls import path
from . import mp_views

urlpatterns = [
    path('login/', mp_views.mp_login, name='mp_login'),
    path('staff/bind/', mp_views.mp_staff_bind, name='mp_staff_bind'),
    path('staff/profile/', mp_views.mp_staff_profile, name='mp_staff_profile'),
    path('staff/dashboard/', mp_views.mp_staff_dashboard, name='mp_staff_dashboard'),
    path('staff/reservations/', mp_views.mp_staff_reservations, name='mp_staff_reservations'),
    path('staff/reservations/<int:pk>/', mp_views.mp_staff_reservation_detail, name='mp_staff_reservation_detail'),
    path('staff/reservations/<int:pk>/status/', mp_views.mp_staff_reservation_update_status, name='mp_staff_reservation_update_status'),
    path('staff/reservations/<int:pk>/followup/', mp_views.mp_staff_reservation_update_followup, name='mp_staff_reservation_update_followup'),
    path('staff/reservations/<int:pk>/transfer/', mp_views.mp_staff_reservation_transfer_owner, name='mp_staff_reservation_transfer_owner'),
    path('staff/orders/', mp_views.mp_staff_orders, name='mp_staff_orders'),
    path('staff/orders/<int:pk>/', mp_views.mp_staff_order_detail, name='mp_staff_order_detail'),
    path('staff/orders/<int:pk>/deliver/', mp_views.mp_staff_mark_order_delivered, name='mp_staff_mark_order_delivered'),
    path('staff/orders/<int:pk>/return/', mp_views.mp_staff_mark_order_returned, name='mp_staff_mark_order_returned'),
    path('staff/orders/<int:pk>/balance/', mp_views.mp_staff_record_order_balance, name='mp_staff_record_order_balance'),
    path('staff/orders/<int:pk>/return-service/', mp_views.mp_staff_update_order_return_service, name='mp_staff_update_order_return_service'),
    path('skus/', mp_views.mp_sku_list, name='mp_sku_list'),
    path('skus/<int:pk>/', mp_views.mp_sku_detail, name='mp_sku_detail'),
    path('reservations/', mp_views.mp_create_reservation, name='mp_create_reservation'),
    path('my-reservations/', mp_views.mp_my_reservations, name='mp_my_reservations'),
    path('my-reservations/<int:pk>/', mp_views.mp_reservation_detail, name='mp_reservation_detail'),
]
