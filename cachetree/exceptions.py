"""
Cachetree exceptions
"""

from django.core.exceptions import ImproperlyConfigured as _ImproperlyConfigured

class ImproperlyConfigured(_ImproperlyConfigured):
    pass
