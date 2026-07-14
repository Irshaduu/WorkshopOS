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
    Central hub for the Owner to manage all staff accounts and mechanics.
    Supports sectioned views: None (Menu), 'accounts', or 'security'.
    """
    section = request.GET.get('section')
    
    office_group = Group.objects.filter(name='Office').first()
    floor_group = Group.objects.filter(name='Floor').first()
    
    office_users = list(User.objects.filter(groups=office_group)) if office_group else []
    floor_users = list(User.objects.filter(groups=floor_group)) if floor_group else []
    mechanics = Mechanic.objects.all().order_by('name')
    
    # 1. SMART CLEANUP: Auto-delete 'ghost' sessions older than 40 days (Sync with Owner Session limit)
    cleanup_date = timezone.now() - timedelta(days=40)
    UserSession.objects.filter(last_activity__lt=cleanup_date).delete()
    
    # 2. ACTIVE WINDOW: Only show devices seen in the last 40 days
    active_window = cleanup_date 
    
    # Security Monitor: Separate Admin/Owner sessions from Staff sessions
    admin_groups = Group.objects.filter(name__in=['Owner', 'Admins'])
    
    owner_sessions = UserSession.objects.select_related('user').prefetch_related('user__groups').filter(
        (models.Q(user__groups__in=admin_groups) | models.Q(user__is_superuser=True)),
        last_activity__gte=active_window
    ).distinct().order_by('-last_activity')
    
    staff_sessions = UserSession.objects.select_related('user').prefetch_related('user__groups').exclude(
        models.Q(user__groups__in=admin_groups) | models.Q(user__is_superuser=True)
    ).filter(
        last_activity__gte=active_window
    ).distinct().order_by('-last_activity')
    
    
    return render(request, 'workshop/manage/manage_dashboard.html', {
        'section': section,
        'office_users': office_users,
        'floor_users': floor_users,
        'mechanics': mechanics,
        'owner_sessions': owner_sessions,
        'staff_sessions': staff_sessions,
        'current_session_key': request.session.session_key,
    })


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
    Add a new Mechanic to the workshop roster.
    """
    if request.method == 'POST':
        name = request.POST.get('name', '').strip().title()  # Auto-capitalize name
        
        if not name:
            messages.error(request, "Mechanic name cannot be empty.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        if Mechanic.objects.filter(name__iexact=name).exists():
            messages.error(request, f"Mechanic '{name}' already exists in the roster.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        Mechanic.objects.create(name=name)
        messages.success(request, f"✅ Mechanic '{name}' added to the roster!")
    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


@office_required
def manage_toggle_mechanic(request, mechanic_id):
    """
    Toggle a mechanic's active/retired status.
    """
    if request.method == 'POST':
        mechanic = get_object_or_404(Mechanic, pk=mechanic_id)
        mechanic.is_active = not mechanic.is_active
        mechanic.save()
        status = "activated" if mechanic.is_active else "deactivated"
        messages.success(request, f"✅ Mechanic '{mechanic.name}' has been {status}.")
    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


@office_required
def manage_edit_mechanic(request, mechanic_id):
    """
    Rename a mechanic in the roster.
    """
    if request.method == 'POST':
        mechanic = get_object_or_404(Mechanic, pk=mechanic_id)
        new_name = request.POST.get('name', '').strip().title()

        if not new_name:
            messages.error(request, "Mechanic name cannot be empty.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        if Mechanic.objects.filter(name__iexact=new_name).exclude(pk=mechanic_id).exists():
            messages.error(request, f"A mechanic named '{new_name}' already exists.")
            return redirect(reverse('manage_dashboard') + '?section=accounts')
        
        old_name = mechanic.name
        mechanic.name = new_name
        mechanic.save()
        messages.success(request, f"✅ Mechanic renamed from '{old_name}' to '{new_name}'.")
    
    return redirect(reverse('manage_dashboard') + '?section=accounts')


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
