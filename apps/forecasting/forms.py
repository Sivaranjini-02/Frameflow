from decimal import Decimal

from django import forms


class CashflowForm(forms.Form):
    opening_balance = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        initial=Decimal("0.00"),
        help_text="Cash you hold today. Past expenses are assumed to be included in this figure.",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
