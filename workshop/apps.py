from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_migrate

def create_user_groups(sender, **kwargs):
    """
    Automatically create the three core Role-Based Access Groups
    if they do not already exist when the app starts.
    """
    from django.contrib.auth.models import Group
    groups = ['Owner', 'Office', 'Floor']
    for group_name in groups:
        Group.objects.get_or_create(name=group_name)

class WorkshopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workshop'

    def ready(self):
        # Register the signal to create groups after migrations
        post_migrate.connect(create_user_groups, sender=self)

        # `decorators.role_names` caches a user's roles ON THE USER INSTANCE, so
        # one request asks the database once however many times the question is
        # put. This is what keeps that cache honest: change somebody's groups and
        # the cached answer is thrown away, rather than surviving until the next
        # request. Imported here rather than at module level — models are not
        # loaded while this file is being read.
        from django.contrib.auth.models import User
        from .decorators import _forget_roles
        m2m_changed.connect(_forget_roles, sender=User.groups.through,
                            dispatch_uid='workshop.forget_roles')

