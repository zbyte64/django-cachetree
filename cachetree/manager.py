"""
Cachetree Manager
"""

########################################################################

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.db.models.manager import Manager
from utils import generate_base_key, get_cache_settings
from exceptions import ImproperlyConfigured
import settings as cachetree_settings
from cache import cache

########################################################################

class CacheManagerMixin:
    
    ####################################################################
    
    def get_cached(self, **kwargs):
        """Gets the model instance from the cache, or, if the instance is not in
        the cache, gets it from the database and puts it in the cache.
        """
        cache_settings = get_cache_settings(self.model)
        lookups = cache_settings.get("lookups")
        #TODO intelligently do the select related
        base_qs = self.all().select_related()
        
        keys = kwargs.keys()
        single_kwarg_match = len(keys) == 1 and keys[0] in lookups
        multi_kwarg_match = len(keys) != 1 and any(
            sorted(keys) == sorted(lookup) for lookup in lookups if isinstance(lookup, (list, tuple)))
        if not single_kwarg_match and not multi_kwarg_match:
            raise ValueError("Caching not allowed with kwargs %s" % ", ".join(keys))
        
        # Get object from cache or db.
        key = generate_base_key(self.model, **kwargs)
        obj = cache.get(key)
        if obj is not None:
            if isinstance(obj, ObjectDoesNotExist):
                raise self.model.DoesNotExist(repr(obj))
            elif isinstance(obj, MultipleObjectsReturned):
                raise self.model.MultipleObjectsReturned(repr(obj))
            else:
                return obj
        try:
            obj = base_qs.get(**kwargs)
        except (ObjectDoesNotExist, MultipleObjectsReturned), e:
            # The model-specific subclasses of these exceptions are not
            # pickleable, so we cache the base exception and reconstruct the
            # specific exception when fetching from the cache.
            obj = e.__class__.__base__(repr(e))
            cache.set(key, obj, cache_settings.get("timeout"))
            raise
        
        self._prefetch_related(obj, cache_settings.get("prefetch"))
        self._tag_object_as_from_cache(obj)
        cache.set(key, obj, cache_settings.get("timeout"))
        return obj
    
    def get_many_cached(self, list_of_kwargs):
        """Gets the model instance from the cache, or, if the instance is not in
        the cache, gets it from the database and puts it in the cache.
        """
        cache_settings = get_cache_settings(self.model)
        lookups = cache_settings.get("lookups")
        #TODO intelligently do the select related
        base_qs = self.all().select_related()
        
        cache_keys = dict()
        
        for kwargs in list_of_kwargs:
            keys = kwargs.keys()
            single_kwarg_match = len(keys) == 1 and keys[0] in lookups
            multi_kwarg_match = len(keys) != 1 and any(
                sorted(keys) == sorted(lookup) for lookup in lookups if isinstance(lookup, (list, tuple)))
            if not single_kwarg_match and not multi_kwarg_match:
                raise ValueError("Caching not allowed with kwargs %s" % ", ".join(keys))
            
            # Get object from cache or db.
            key = generate_base_key(self.model, **kwargs)
            cache_keys[key] = kwargs
        
        objects = cache.get_many(cache_keys.keys())
        pending_cache_update = dict()
        cached_objects = list()
        
        for key, kwargs in cache_keys.iteritems():
            obj = objects.get(key, None)
            if obj is not None:
                if isinstance(obj, ObjectDoesNotExist):
                    raise self.model.DoesNotExist(repr(obj))
                elif isinstance(obj, MultipleObjectsReturned):
                    raise self.model.MultipleObjectsReturned(repr(obj))
                else:
                    cached_objects.append(obj)
                    continue
            try:
                obj = base_qs.get(**kwargs)
            except (ObjectDoesNotExist, MultipleObjectsReturned), e:
                # The model-specific subclasses of these exceptions are not
                # pickleable, so we cache the base exception and reconstruct the
                # specific exception when fetching from the cache.
                obj = e.__class__.__base__(repr(e))
                cache.set(key, obj, cache_settings.get("timeout"))
                raise
            
            self._prefetch_related(obj, cache_settings.get("prefetch"))
            self._tag_object_as_from_cache(obj)
            pending_cache_update[key] = obj
            cached_objects.append(obj)
        
        if pending_cache_update:
            cache.set_many(pending_cache_update, cache_settings.get("timeout"))
        return cached_objects
   
    ####################################################################
    
    def _prefetch_related(self, objs, attrs):
        """Recursively follows the `attrs` on each of the `objs` in order to
        populate the objects' caches.
        """
        if not isinstance(objs, (list, tuple)):
            objs = [objs]
            
        for obj in objs:
        
            for attr_name, child_attrs in attrs.iteritems():
                
                try:
                    attr = getattr(obj, attr_name)
                    
                # If the object doesn't exist, we can't prefetch it.
                except ObjectDoesNotExist:
                    continue
                
                # attr might be a method that we need to call (presumably to
                # fill its own local cache). This is an undocumented feature.
                if callable(attr):
                    attr = attr()
                    
                # If attr returns a subclass of models.Manager, use all() to
                # get a queryset of all results and iterate over it to fill
                # the queryset cache with those results. Then, on the parent
                # object, stored the queryset in a cached attribute.
                elif isinstance(attr, Manager):
                    #TODO do select related based on child_attrs
                    queryset = attr.all().select_related()
                    related_instances = []
                    for instance in queryset:
                        related_instances.append(instance)
                    cached_attr_name = "%s%s" % (cachetree_settings.CACHETREE_MANY_RELATED_PREFIX, attr_name)
                    if hasattr(obj, cached_attr_name):
                        raise ImproperlyConfigured(
                            "Cannot store %s on %s instance because it already has an attribute with that name. Try setting CACHETREE_MANY_RELATED_PREFIX to a different value." % (
                                cached_attr_name, obj.__class__.__name__))
                    setattr(obj, cached_attr_name, queryset)
                    attr = related_instances
                
                if child_attrs:
                    self._prefetch_related(attr, child_attrs)
    
    def _tag_object_as_from_cache(self, obj):
        obj._from_cachetree = True

########################################################################

