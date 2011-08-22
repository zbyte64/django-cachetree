"""
Cachetree Utils
"""

########################################################################

from django.db import models
from django.db.models.loading import get_model
try:
    from hashlib import md5
except ImportError:
    from md5 import md5
import re
import settings as cachetree_settings
from exceptions import ImproperlyConfigured

########################################################################

WHITESPACE = re.compile("\s")
CACHETREE_PREFIX = "cachetree"

def generate_base_key(model, **kwargs):
    """Generates a base key to be used for caching, containing the model name,
    the lookup kwargs, plus a hexdigest. The base key will later be combined
    with any required version or prefix.
    """
    
    key_parts = []
    for name, value in sorted(kwargs.iteritems()):
        if isinstance(value, models.Model):
            value = value.pk
        key_parts.append("%s:%s" % (name, value))
        
    raw_key = "%(app_label)s.%(model)s.%(parts)s" % dict(
        app_label=model._meta.app_label, 
        model=model.__name__, 
        parts=";".join(key_parts))
    digest = md5(raw_key).hexdigest()
    
    # Whitespace is stripped but the hexdigest ensures uniqueness
    key = "%(prefix)s.%(raw_key)s_%(digest)s" % dict(
        prefix=CACHETREE_PREFIX,
        raw_key=WHITESPACE.sub("", raw_key)[:125], 
        digest=digest)
    
    return key
       
########################################################################

def get_cached_models():
    """Yields app_label and model from the CACHETREE setting.
    """
    for app_label, models in cachetree_settings.CACHETREE.iteritems():
        for model_name, cache_settings in models.iteritems():
            model = get_model(app_label, model_name)
            if model is None:
                raise ImproperlyConfigured(
                    "CACHETREE defines model %s.%s, which does not exist." % (
                        app_label, model_name))
            
            yield app_label, model
            
########################################################################

def get_cache_settings(model):
    """Returns the cache settings for the ``model``.
    """
    cache_settings = cachetree_settings.CACHETREE.get(
        model._meta.app_label, {}).get(model.__name__, None)
        
    if cache_settings is None:
        raise ValueError("Caching is not enabled for %(app)s.%(model)s. To enable it, add the %(model)s model to your CACHETREE setting." % dict(
        app=model._meta.app_label, model=model.__name__))
    
    if "lookups" not in cache_settings:
        cache_settings["lookups"] = ("pk", model._meta.pk.name)
    if "prefetch" not in cache_settings:
        cache_settings["prefetch"] = {}
    if "timeout" not in cache_settings:
        cache_settings["timeout"] = None
        
    return cache_settings
    
########################################################################