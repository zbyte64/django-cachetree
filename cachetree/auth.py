"""
Cachetree Auth Backend
"""

########################################################################

from django.contrib.auth.models import User
from django.contrib.auth.backends import ModelBackend

########################################################################

class CachedModelBackend(ModelBackend):
    """Authenticates against cached user objects.
    """
    BACKEND_PATH = "cachetree.auth.CachedModelBackend" 
    
    def authenticate(self, username=None, password=None):
        try:
            user = User.objects.get_cached(username=username)
            if user.check_password(password):
                return user
        except User.DoesNotExist:
            return None
    
    def get_user(self, user_id):
        try:
            return User.objects.get_cached(pk=user_id)
        except User.DoesNotExist:
            return None
