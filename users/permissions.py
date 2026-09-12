def can_manage_staff(actor, target):
    """Business staff management never grants control of technical accounts."""
    return bool(
        actor.is_active and actor.is_admin_user
        and actor.pk != target.pk and target.username != 'beybit'
        and (actor.is_superuser or not (target.is_staff or target.is_superuser))
    )
