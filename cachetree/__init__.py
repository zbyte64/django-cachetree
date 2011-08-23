"""
Cachetree
"""

########################################################################

from django.db.models.loading import get_model
from django.utils.functional import wraps
from django.db.models.fields.related import (
    ForeignRelatedObjectsDescriptor, ManyRelatedObjectsDescriptor, 
    ReverseManyRelatedObjectsDescriptor)
from django.conf import settings as django_settings
import settings as cachetree_settings
from manager import CacheManagerMixin
from utils import get_cached_models
from invalidation import Invalidator, invalidate, no_invalidation
from exceptions import ImproperlyConfigured
from auth import CachedModelBackend
from shortcuts import get_cached_object_or_404

########################################################################

class _Installer(object):
    """Sets up the cachetree framework.
    """
    
    def __init__(self):
        self.method_cache = {}
        self.fill_method_cache()
        self.installed = False

    ####################################################################

    def fill_method_cache(self):
        """Caches original methods so they can be restored if needed.
        """
        self.method_cache[CacheManagerMixin] = {"get_cached": CacheManagerMixin.get_cached}
        for descriptor_class in (ManyRelatedObjectsDescriptor, 
                                 ForeignRelatedObjectsDescriptor,
                                 ReverseManyRelatedObjectsDescriptor):
            self.method_cache[descriptor_class] = {"__get__": descriptor_class.__get__}
    
    ####################################################################
    
    def _install(self):
        """Adds CacheManagerMixin to the default manager class for each model
        defined in CACHETREE, wraps the descriptor classes for many related
        objects to use the cache, and sets up invalidation.
        
        If CACHETREE_DISABLE is set to True, setup still adds the
        CacheManagerMixin to the appropriate classes, but makes get_cached an
        alias of get, allowing code that uses get_cached to continue to work.
        Invalidation setup is skipped.
        """
        if self.installed:
            return
        
        if cachetree_settings.DISABLE:
            CacheManagerMixin.get_cached = lambda self, *args, **kwargs: self.get(*args, **kwargs)
        else:
            CacheManagerMixin.get_cached = self.method_cache.get(CacheManagerMixin).get("get_cached")
        
        for app_label, model in get_cached_models():
            if CacheManagerMixin not in model._default_manager.__class__.__bases__:
                model._default_manager.__class__.__bases__ += (CacheManagerMixin,)
               
        for descriptor_cls in (ForeignRelatedObjectsDescriptor,
                               ManyRelatedObjectsDescriptor, 
                               ReverseManyRelatedObjectsDescriptor):
            self.wrap_many_related_descriptor(descriptor_cls)
            
        self._install_auth_dependencies()
        
        if cachetree_settings.INVALIDATE and not cachetree_settings.DISABLE:
            Invalidator.install()
            
        self.installed = True
       
    ####################################################################
    
    def _install_auth_dependencies(self):
        """If the cachetree authentication backend is installed, makes sure
        that the User model is in the CACHETREE setting, and adds it if not.
        """
        if self.auth_backend_installed():
            if "auth" not in cachetree_settings.CACHETREE:
                cachetree_settings.CACHETREE["auth"] = {}
            if "User" not in cachetree_settings.CACHETREE["auth"]:
                cachetree_settings.CACHETREE["auth"]["User"] = {}
            if "lookups" not in cachetree_settings.CACHETREE["auth"]["User"]:
                cachetree_settings.CACHETREE["auth"]["User"]["lookups"] = ()
            for lookup in ("pk", "username"):
                if lookup not in cachetree_settings.CACHETREE["auth"]["User"]["lookups"]:
                    cachetree_settings.CACHETREE["auth"]["User"]["lookups"] = tuple(
                        list(cachetree_settings.CACHETREE["auth"]["User"]["lookups"]) 
                        + [lookup]
                    )
        
    ####################################################################
    
    @staticmethod
    def auth_backend_installed():
        return any(CachedModelBackend.BACKEND_PATH in backend_path
                   for backend_path in django_settings.AUTHENTICATION_BACKENDS)
        
    ####################################################################
        
    def _uninstall(self):
        """Uninstalls by restoring the descriptor classes to their original
        state, disconnecting invalidation signal handlers if necessary, and
        aliasing CacheManagerMixin.get_cached to get. (Aliases rather than
        removes CacheManagerMixin in order not to break code that uses it.)
        """
        if not self.installed:
            return
        
        CacheManagerMixin.get_cached = lambda self, *args, **kwargs: self.get(*args, **kwargs)
        
        for descriptor_cls in (ForeignRelatedObjectsDescriptor,
                               ManyRelatedObjectsDescriptor, 
                               ReverseManyRelatedObjectsDescriptor):
            descriptor_cls.__get__ = self.method_cache.get(descriptor_cls).get("__get__")
            
        if cachetree_settings.INVALIDATE and not cachetree_settings.DISABLE:
            Invalidator.uninstall()
            
        self.installed = False
    
    ####################################################################
    
    @staticmethod
    def wrap_many_related_descriptor(descriptor_cls):
        """Wraps the ``descriptor_cls``'s __get__ method to return a modified
        manager whose all() method will return the cached related objects, if
        populated. Additionally, add an uncache method to clear the all()
        cache, and pathes add, remove, and clear to call uncached_all before
        adding, removing, or clearing.
        """
    
        if descriptor_cls in (ManyRelatedObjectsDescriptor, ForeignRelatedObjectsDescriptor):
            def get_attr_name(descriptor):
                return descriptor.related.get_accessor_name()
            
        elif descriptor_cls == ReverseManyRelatedObjectsDescriptor:
            def get_attr_name(descriptor):
                return descriptor.field.name
        else:
            raise ValueError("invalid descriptor class: %s" % descriptor_cls.__name__)
                          
        original__get__ = descriptor_cls.__get__
        def __get__(self, instance, *args, **kwargs):
    
            if instance is None:
                return original__get__(self, instance, *args, **kwargs)
            
            manager = original__get__(self, instance, *args, **kwargs)
            
            attr_name = get_attr_name(self)
            cached_attr_name = "%s%s" % (cachetree_settings.CACHETREE_MANY_RELATED_PREFIX, attr_name)
            
            original_all = manager.__class__.all
            def all_(*args, **kwargs):
                try:
                    return getattr(instance, cached_attr_name)
                except AttributeError:
                    return original_all(*args, **kwargs)
            manager.__class__.all = wraps(original_all)(all_)
            
            def uncache(*args, **kwargs):
                """Uncaches the manager's all method, if it's cached."""
                try:
                    delattr(instance, cached_attr_name)
                except AttributeError:
                    pass
            manager.__class__.uncache = uncache
                
            # The add, remove, and clear methods should uncache the manager's
            # all(), since they change the related objects.
            if hasattr(manager, "add"):
                original_add = manager.add
                def add(*args, **kwargs):
                    manager.uncache()
                    return original_add(*args, **kwargs)
                manager.add = wraps(original_add)(add)
                
            if hasattr(manager, "remove"):
                original_remove = manager.remove
                def remove(*args, **kwargs):
                    manager.uncache()
                    return original_remove(*args, **kwargs)
                manager.remove = wraps(original_remove)(remove)
                
            if hasattr(manager, "clear"):
                original_clear = manager.clear
                def clear(*args, **kwargs):
                    manager.uncache()
                    return original_clear(*args, **kwargs)
                manager.clear = wraps(original_clear)(clear)
                    
            return manager
        
        descriptor_cls.__get__ = wraps(original__get__)(__get__)
    
########################################################################

_installer = _Installer()
install = _installer._install
uninstall = _installer._uninstall