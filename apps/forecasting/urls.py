from django.urls import path

from . import views

app_name = 'forecasting'

urlpatterns = [
    path('', views.cashflow_dashboard, name='cashflow'),
    path('generate/', views.cashflow_generate, name='cashflow_generate'),
]
