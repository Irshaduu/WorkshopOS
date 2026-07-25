from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.contrib.sessions.models import Session
from django.db import models
from .decorators import owner_required, office_required
from .models import Mechanic, UserSession


@office_required
def manage_dashboard(request):
    """
    Central hub for the Owner to manage staff accounts, the staff roster, and
    device security. Supports sectioned views: None (Menu), 'accounts',
    'staff', or 'security'.
    """
    section = request.GET.get('section')

    # SMART CLEANUP: auto-delete 'ghost' sessions older than 40 days. Runs on
    # every visit regardless of section — cheap, and keeps the Security tab
    # accurate whenever it's next opened.
    cleanup_date = timezone.now() - timedelta(days=40)
    UserSession.objects.filter(last_activity__lt=cleanup_date).delete()

    context = {
        'section': section,
        'current_session_key': request.session.session_key,
    }

    if section == 'accounts':
        office_group = Group.objects.filter(name='Office').first()
        floor_group = Group.objects.filter(name='Floor').first()
        context['office_users'] = list(User.objects.filter(groups=office_group)) if office_group else []
        context['floor_users'] = list(User.objects.filter(groups=floor_group)) if floor_group else []

    elif section == 'staff':
        mechanics = list(Mechanic.objects.all().order_by('name'))
        context['staff_by_role'] = [
            (role_value, role_label, [m for m in mechanics if m.role == role_value])
            for role_value, role_label in Mechanic.ROLE_CHOICES
        ]
        context['role_choices'] = Mechanic.ROLE_CHOICES
        context['staff_count'] = len(mechanics)

    elif section == 'security':
        # Only show devices seen within the same 40-day window just cleaned up.
        active_window = cleanup_date

        # Separate Admin/Owner sessions from Staff sessions
        admin_groups = Group.objects.filter(name__in=['Owner', 'Admins'])

        context['owner_sessions'] = UserSession.objects.select_related('user').prefetch_related('user__groups').filter(
            (models.Q(user__groups__in=admin_groups) | models.Q(user__is_superuser=True)),
            last_activity__gte=active_window
        ).distinct().order_by('-last_activity')

        context['staff_sessions'] = UserSession.objects.select_related('user').prefetch_related('user__groups').exclude(
            models.Q(user__groups__in=admin_groups) | models.Q(user__is_superuser=True)
        ).filter(
            last_activity__gte=active_window
        ).distinct().order_by('-last_activity')

    return render(request, 'workshop/manage/manage_dashboard.html', context)


@office_required
def manage_create_user(request):
    """
    Create a new Office or Floor staff account.
    Owner sets the username, password, and role.
    """
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        role = request.POST.get('role', '')
        
        if not username or not password or role not in ['Office', 'Floor']:
            messages.error(request, "All fields are required. Role must be Office or Floor.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' is already taken. Choose another.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        # Admin has full freedom to set any password for staff.
        # Only enforce a minimum length of 8 characters.
        # For security, encourage strong passwords by convention (e.g., at least one number).
        if len(password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')

        user = User.objects.create_user(username=username, password=password)
        group = Group.objects.get(name=role)
        user.groups.add(group)
        user.save()
        messages.success(request, f"✅ {role} account '{username}' created successfully!")

    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


@office_required
def manage_reset_password(request, user_id):
    """
    Reset the password for an Office or Floor staff account.
    """
    if request.method == 'POST':
        user = get_object_or_404(User, pk=user_id)
        
        # Safety: prevent owners from editing other owner/superuser accounts here
        if user.groups.filter(name='Owner').exists() or user.is_superuser:
            messages.error(request, "Cannot modify Owner accounts from this panel.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        new_password = request.POST.get('new_password', '').strip()

        # Admin has full freedom to set any password for staff.
        # Only enforce a minimum length of 8 characters.
        if not new_password or len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')

        user.set_password(new_password)
        user.save()
        messages.success(request, f"✅ Password for '{user.username}' has been reset.")

    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


@office_required
def manage_delete_user(request, user_id):
    """
    Delete an Office or Floor staff account.
    """
    if request.method == 'POST':
        user = get_object_or_404(User, pk=user_id)
        
        if user.groups.filter(name='Owner').exists() or user.is_superuser:
            messages.error(request, "Cannot delete Owner accounts from this panel.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        username = user.username
        user.delete()
        messages.success(request, f"✅ Account '{username}' has been deleted.")
    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


@office_required
def manage_create_mechanic(request):
    """
    Register a new staff member (Mechanic, Assistant Mechanic, Office Staff,
    or General Helper) to the roster. Only Mechanic/Assistant Mechanic ever
    appear in the Job Card mechanic picker — see Mechanic.JOBCARD_ELIGIBLE_ROLES.
    """
    if request.method == 'POST':
        name = request.POST.get('name', '').strip().title()  # Auto-capitalize name
        role = request.POST.get('role', '').strip()
        role_labels = dict(Mechanic.ROLE_CHOICES)

        if not name:
            messages.error(request, "Staff name cannot be empty.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        if role not in role_labels:
            messages.error(request, "Choose a valid role.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        if Mechanic.objects.filter(name__iexact=name).exists():
            messages.error(request, f"'{name}' is already registered.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        Mechanic.objects.create(name=name, role=role)
        messages.success(request, f"✅ {role_labels[role]} '{name}' registered!")

    return redirect(reverse('manage_dashboard') + '?section=staff')


@office_required
def manage_toggle_mechanic(request, mechanic_id):
    """
    Toggle a staff member's active/retired status. Deactivate only — there is
    deliberately no delete, since job cards may still reference them.
    """
    if request.method == 'POST':
        mechanic = get_object_or_404(Mechanic, pk=mechanic_id)
        mechanic.is_active = not mechanic.is_active
        mechanic.save()
        status = "activated" if mechanic.is_active else "deactivated"
        messages.success(request, f"✅ '{mechanic.name}' has been {status}.")

    return redirect(reverse('manage_dashboard') + '?section=staff')


@office_required
def manage_edit_mechanic(request, mechanic_id):
    """
    Rename a staff member and/or change their role. Changing role never
    touches existing Job Cards — lead_mechanic points at this same row by id,
    so past assignments stay intact regardless of role/active changes.
    """
    if request.method == 'POST':
        mechanic = get_object_or_404(Mechanic, pk=mechanic_id)
        new_name = request.POST.get('name', '').strip().title()
        new_role = request.POST.get('role', '').strip()
        role_labels = dict(Mechanic.ROLE_CHOICES)

        if not new_name:
            messages.error(request, "Staff name cannot be empty.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        if new_role not in role_labels:
            messages.error(request, "Choose a valid role.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        if Mechanic.objects.filter(name__iexact=new_name).exclude(pk=mechanic_id).exists():
            messages.error(request, f"'{new_name}' is already registered.")
            return redirect(reverse('manage_dashboard') + '?section=staff')

        mechanic.name = new_name
        mechanic.role = new_role
        mechanic.save()
        messages.success(request, f"✅ '{new_name}' updated — {role_labels[new_role]}.")

    return redirect(reverse('manage_dashboard') + '?section=staff')


@owner_required
def manage_terminate_session(request, session_id):
    """
    Remotely terminate a login session. 
    Deletes both the UserSession record and the underlying Django session.
    """
    if request.method == 'POST':

        user_session = get_object_or_404(UserSession, pk=session_id)
        
        # 1. Kill the actual Django session (logs them out)
        try:
            django_session = Session.objects.get(session_key=user_session.session_key)
            django_session.delete()
        except Session.DoesNotExist:
            pass # Already gone
            
        # 2. Delete our tracking record
        affected_user = user_session.user.username
        user_session.delete()
        
        messages.success(request, f"🛑 Access Revoked: Session for '{affected_user}' has been terminated.")
        
    return redirect(reverse('manage_dashboard') + '?section=security')
