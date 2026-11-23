from django import forms

from .models import PhotographerProfile
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class StudioForm(forms.Form):
    """Studio details collected at creation time (Stage 4 Step 1).

    Only these fields are accepted. Ownership, studio IDs and GST fields are
    not part of this form.
    """

    CURRENCY_CHOICES = [
        ('INR', 'INR - Indian Rupee'),
        ('USD', 'USD - US Dollar'),
    ]

    studio_name = forms.CharField(
        max_length=150,
        label='Business name',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    business_type = forms.ChoiceField(
        choices=PhotographerProfile.BusinessType.choices,
        initial=PhotographerProfile.BusinessType.PHOTOGRAPHY,
        label='Business type',
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def clean_business_type(self):
        return self.cleaned_data.get('business_type') or PhotographerProfile.BusinessType.PHOTOGRAPHY

    business_email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    business_phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial='INR',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    timezone = forms.CharField(
        max_length=64,
        initial='Asia/Kolkata',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    country = forms.CharField(
        max_length=2,
        initial='IN',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )


class SignUpForm(UserCreationForm):
    """Account registration: username, email and password. Studio details are a separate step."""

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
