
"""Studio scoping helpers for Stage 3a.

Studio-owned data is always resolved through the authenticated user's
StudioMembership. URL parameters and form-supplied studio IDs are never
used to choose the studio.
"""

from django.core.exceptions import ValidationError

from .models import PhotographerProfile, StudioMembership, StudioOwnedModel


def get_studio_for_user(user):
    """Return the PhotographerProfile the user belongs to, or None.

    Returns None for None, anonymous users, and users without a membership.
    The studio is read only through StudioMembership.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    membership = (
        StudioMembership.objects.select_related("studio")
        .filter(user=user)
        .first()
    )
    if membership is None:
        return None
    return membership.studio


def ensure_same_studio(obj, studio):
    """Raise ValidationError unless obj is a studio-owned object in `studio`.

    Raises ValidationError when:
      - studio is not a saved PhotographerProfile,
      - obj is not a StudioOwnedModel instance (unsupported object),
      - obj belongs to a different studio.

    Compares primary keys, so no extra query is needed.
    """
    if not isinstance(studio, PhotographerProfile) or studio.pk is None:
        raise ValidationError("A valid studio is required.")

    if not isinstance(obj, StudioOwnedModel):
        raise ValidationError(
            "Unsupported object for studio scoping: %(name)s.",
            params={"name": type(obj).__name__},
        )

    if obj.studio_id != studio.pk:
        raise ValidationError("Object does not belong to this studio.")


def scope_queryset(queryset, studio):
    """Restrict a studio-owned queryset to `studio`.

    Returns an empty queryset when studio is None, so an unresolved user
    cannot read another studio's rows.
    """
    if studio is None:
        return queryset.none()
    return queryset.filter(studio=studio)
