"""Client and Project models for FrameFlow, scoped to a photographer studio."""

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import StudioOwnedModel


class Client(StudioOwnedModel):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "clients_client"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Project(StudioOwnedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        related_name="projects",
    )
    title = models.CharField(max_length=150)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    event_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "clients_project"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.client_id and self.studio_id:
            if self.client.studio_id != self.studio_id:
                raise ValidationError(
                    {"client": "Client must belong to the same studio."}
                )
