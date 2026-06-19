from .models import UserSession
from django.utils import timezone

class SessionTrackingMiddleware:
    """
    Background monitor that tracks which devices are accessing the HQ Portal.
    Updates the 'Last Active' timestamp and device metadata on every request.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # We only track sessions for authenticated users (Sahad/Rijas)
        if request.user.is_authenticated:
            session_key = request.session.session_key
            
            # Ensure session is saved if it's new
            if not session_key:
                request.session.save()
                session_key = request.session.session_key
            
            if session_key:
                # Capture Real IP (even behind proxies like Cloudflare/Nginx)
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    ip = x_forwarded_for.split(',')[0].strip()
                else:
                    ip = request.META.get('REMOTE_ADDR')

                now = timezone.now()
                from datetime import timedelta
                cooldown = timedelta(minutes=5)
                
                existing = UserSession.objects.filter(session_key=session_key).first()
                if not existing or (now - existing.last_activity) >= cooldown:
                    # Update or Create the session record
                    UserSession.objects.update_or_create(
                        session_key=session_key,
                        defaults={
                            'user': request.user,
                            'ip_address': ip,
                            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                            'last_activity': now
                        }
                    )

        return response
