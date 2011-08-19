"""
Cachetree shortcuts
"""

########################################################################

from django.db.models.manager import Manager
from django.db.models.query import QuerySet
from django.http import Http404

########################################################################

def get_cached_object_or_404(klass, *args, **kwargs):
    """Like Django's get_object_or_404, but uses get_cached() instead of
    get(). 
    
    The model's default manager will be used, even if another manager is
    passed.
    """
    if isinstance(klass, QuerySet):
        manager = klass.model._default_manager
    elif isinstance(klass, Manager):
        manager = klass.model._default_manager
    else:
        manager = klass._default_manager
    
    try:
        return manager.get_cached(*args, **kwargs)
    except manager.model.DoesNotExist:
        raise Http404("No %s matches the given query." % manager.model._meta.object_name)
    
########################################################################