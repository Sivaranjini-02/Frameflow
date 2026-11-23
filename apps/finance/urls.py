from django.urls import path

from . import views

app_name = 'finance'

urlpatterns = [
    path('', views.invoice_list, name='invoice_list'),
    path('new/', views.invoice_create, name='invoice_create'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/issue/', views.invoice_issue, name='invoice_issue'),
    path('<int:pk>/payments/', views.invoice_record_payment, name='invoice_record_payment'),
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/new/', views.expense_create, name='expense_create'),
    path('expenses/<int:pk>/edit/', views.expense_edit, name='expense_edit'),
]
