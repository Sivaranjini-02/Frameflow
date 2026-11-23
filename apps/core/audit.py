
"""Audit helpers for Stage 3a.

record_audit writes one AuditLog row. Before and after values are stored
inside AuditLog.changes as {"before": ..., "after": ...}.

Checks performed before a row is written:
  - studio is a saved PhotographerProfile
  - entity is saved and belongs to studio
  - a non-null actor has a StudioMembership in studio
  - before and after contain no floats, at any depth

AuditLog.save() and AuditQuerySet block updates and deletes, so entries
are append-only at ORM level.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import AuditLog, PhotographerProfile, StudioMembership
from .money import to_decimal


def to_jsonable(value):
    """Return a JSON-safe copy of value.

    - bool, int, str and None pass through unchanged.
    - float raises TypeError, at any depth.
    - Decimal is validated with to_decimal and stored as a string.
    - date and datetime are stored in ISO 8601 format.
    - dict keys must be str; values are converted recursively.
    - list and tuple become lists.
    - Anything else raises TypeError.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise TypeError("Floats are not allowed in audit data; use Decimal or str.")
    if isinstance(value, Decimal):
        return str(to_decimal(value))
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Audit dict keys must be strings.")
            result[key] = to_jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"Unsupported type in audit data: {type(value).__name__}.")


def _entity_studio_id(entity):
    """Return the studio primary key that an entity belongs to, or None.

    A PhotographerProfile belongs to itself. Other models must expose a
    `studio` foreign key (for example, a StudioOwnedModel subclass).
    """
    if isinstance(entity, PhotographerProfile):
        return entity.pk
    return getattr(entity, "studio_id", None)


def record_audit(*, studio, actor, action, entity, before, after):
    """Create and return an AuditLog entry after validating scope.

    Raises:
        ValidationError: studio is unsaved, entity is unsaved or belongs to
            another studio, or actor is not a member of studio.
        TypeError: before or after contains a float or an unsupported type.
    """
    if not isinstance(studio, PhotographerProfile) or studio.pk is None:
        raise ValidationError("A saved studio is required for an audit entry.")

    if entity is None or getattr(entity, "pk", None) is None:
        raise ValidationError("The audited entity must be saved before it is audited.")
    if _entity_studio_id(entity) != studio.pk:
        raise ValidationError("The audited entity does not belong to this studio.")

    if actor is not None:
        if getattr(actor, "pk", None) is None:
            raise ValidationError("A saved actor is required for an audit entry.")
        if not StudioMembership.objects.filter(user=actor, studio=studio).exists():
            raise ValidationError("The actor does not belong to this studio.")

    changes = {
        "before": to_jsonable(before) if before is not None else {},
        "after": to_jsonable(after) if after is not None else {},
    }

    return AuditLog.objects.create(
        studio=studio,
        actor=actor,
        entity_type=entity._meta.label_lower,
        entity_id=str(entity.pk),
        action=action,
        changes=changes,
    )
