from django.conf import settings as django_settings

CACHETREE_MANY_RELATED_PREFIX = getattr(django_settings, "CACHETREE_MANY_RELATED_PREFIX", "_cached_")
INVALIDATE = getattr(django_settings, "CACHETREE_INVALIDATE", True)
DISABLE = getattr(django_settings, "CACHETREE_DISABLE", False)
CACHETREE = getattr(django_settings, "CACHETREE", {})
