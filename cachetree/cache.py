"""
Cachetree Cache Wrapper
"""

from django.core.cache import get_cache, DEFAULT_CACHE_ALIAS
from django.utils.functional import SimpleLazyObject

# Wrap django.core.cache.get_cache in SimpleLazyObject. The purpose of this is
# to allow the cachetree test suite to switch to the locmem backend when
# running tests and not interfere with the real cache. See
# https://code.djangoproject.com/ticket/16006
cache = SimpleLazyObject(lambda: get_cache(DEFAULT_CACHE_ALIAS))