from django import forms

from .models import Client, Project


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ['name', 'email', 'phone', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, studio, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.studio = studio


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['client', 'title', 'status', 'event_date', 'notes']
        labels = {'event_date': 'Due date'}
        widgets = {
            'client': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'event_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, studio, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.studio = studio
        self.fields['client'].queryset = Client.objects.filter(studio=studio).order_by('name')
        self.fields['client'].empty_label = 'Choose a client'
