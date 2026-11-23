from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.clients.models import Client, Project

from .models import Payment


class LineItemForm(forms.Form):
    description = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        initial=Decimal("1"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    unit_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        description = (cleaned_data.get("description") or "").strip()
        quantity = cleaned_data.get("quantity")
        unit_price = cleaned_data.get("unit_price")

        if not description and quantity is None and unit_price is None:
            cleaned_data["description"] = ""
            cleaned_data["empty"] = True
            return cleaned_data

        if not description:
            raise ValidationError("Enter a description.")
        if quantity is None:
            self.add_error("quantity", "Enter a quantity.")
        if unit_price is None:
            self.add_error("unit_price", "Enter a unit price.")
        cleaned_data["description"] = description
        cleaned_data["empty"] = False
        return cleaned_data


class InvoiceCreateForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="Choose a client",
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="No project",
    )

    def __init__(self, *args, studio, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(studio=studio).order_by("name")
        self.fields["project"].queryset = Project.objects.filter(studio=studio).order_by("title")


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    received_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    method = forms.ChoiceField(
        choices=Payment.Method.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reference = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )


class ExpenseForm(forms.Form):
    category = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    vendor = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    expense_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="No project",
    )

    def __init__(self, *args, studio, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(studio=studio).order_by("title")
