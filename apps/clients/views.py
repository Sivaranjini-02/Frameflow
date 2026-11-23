from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.core.scoping import get_studio_for_user

from .forms import ClientForm, ProjectForm
from .models import Client, Project


@login_required
def client_list(request):
    """List clients of the authenticated user's studio."""
    studio = get_studio_for_user(request.user)
    if studio is None:
        return redirect('core:create_studio')
    clients = Client.objects.filter(studio=studio).order_by('name')
    return render(request, 'clients/client_list.html', {'clients': clients})


@login_required
def client_create(request):
    """Create a client in the authenticated user's studio."""
    studio = get_studio_for_user(request.user)
    if studio is None:
        return redirect('core:create_studio')

    form = ClientForm(request.POST or None, studio=studio)
    if request.method == 'POST':
        if form.is_valid():
            client = form.save()
            messages.success(request, f'Client "{client.name}" created.')
            return redirect('clients:client_list')
        messages.error(request, 'Please correct the errors below.')

    return render(request, 'clients/client_form.html', {
        'form': form,
        'title': 'New client',
        'cancel_url': reverse('clients:client_list'),
    })


@login_required
def project_list(request):
    """List projects of the authenticated user's studio."""
    studio = get_studio_for_user(request.user)
    if studio is None:
        return redirect('core:create_studio')
    projects = (
        Project.objects.filter(studio=studio)
        .select_related('client')
        .order_by('-created_at')
    )
    return render(request, 'clients/project_list.html', {'projects': projects})


@login_required
def project_create(request):
    """Create a project in the authenticated user's studio."""
    studio = get_studio_for_user(request.user)
    if studio is None:
        return redirect('core:create_studio')

    form = ProjectForm(request.POST or None, studio=studio)
    if request.method == 'POST':
        if form.is_valid():
            project = form.save()
            messages.success(request, f'Project "{project.title}" created.')
            return redirect('clients:project_list')
        messages.error(request, 'Please correct the errors below.')

    return render(request, 'clients/project_form.html', {
        'form': form,
        'title': 'New project',
        'cancel_url': reverse('clients:project_list'),
    })
