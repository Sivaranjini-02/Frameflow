
"""Studio service workflows for Stage 3a.

Creating a studio and its owner membership happens in one database
transaction. The studio is never chosen from a user-supplied ID: a new
studio is created for the given user, and existing studios are resolved
through StudioMembership (see scoping.py).

v1 rules enforced here:
  - one studio per user (StudioMembership.user is a OneToOneField)
  - owner-only membership (the creator becomes the sole owner)
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import PhotographerProfile, StudioMembership, User

OWNER_ROLE = "owner"

# Fields the caller must never set directly.
_FORBIDDEN_STUDIO_FIELDS = frozenset({"id", "pk"})


def _validate_user(user):
    """Raise ValidationError unless user is an authenticated, saved User."""
    if not isinstance(user, User):
        raise ValidationError("A custom User instance is required to create a studio.")
    if not getattr(user, "is_authenticated", False):
        raise ValidationError("An authenticated user is required to create a studio.")
    if user.pk is None:
        raise ValidationError("A saved user is required to create a studio.")


@transaction.atomic
def create_studio_with_owner(user, **studio_fields):
    """Create a PhotographerProfile and its owner membership atomically.

    The user row is locked for the duration of the transaction, so two
    concurrent requests cannot both create a studio for the same user.

    Raises ValidationError when:
      - user is not an authenticated, saved User instance,
      - the user already belongs to a studio (one studio per user),
      - studio_fields contains a forbidden key or invalid values.

    Any error raised after the first write rolls the transaction back.
    Returns the new PhotographerProfile.
    """
    _validate_user(user)

    forbidden = _FORBIDDEN_STUDIO_FIELDS.intersection(studio_fields)
    if forbidden:
        raise ValidationError(
            "Studio fields may not be set directly: %(names)s.",
            params={"names": ", ".join(sorted(forbidden))},
        )

    # Lock the user row so concurrent creations for the same user serialise.
    locked_user = User.objects.select_for_update().get(pk=user.pk)

    if StudioMembership.objects.filter(user=locked_user).exists():
        raise ValidationError("This user already belongs to a studio.")

    studio = PhotographerProfile(**studio_fields)
    studio.full_clean()
    studio.save()

    membership = StudioMembership(
        user=locked_user,
        studio=studio,
        role=OWNER_ROLE,
    )
    membership.full_clean()
    membership.save()

    return studio


def studio_has_owner(studio):
    """Return True if studio is a saved PhotographerProfile with an owner membership.

    Returns False for None, for any object that is not a PhotographerProfile,
    and for an unsaved PhotographerProfile.
    """
    if not isinstance(studio, PhotographerProfile) or studio.pk is None:
        return False
    return StudioMembership.objects.filter(
        studio=studio,
        role=OWNER_ROLE,
    ).exists()
