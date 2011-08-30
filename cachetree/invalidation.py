"""
Cachetree Invalidation
"""

########################################################################

from copy import copy
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.signals import post_init, post_save, post_delete, m2m_changed
from django.db.models.manager import Manager
from django.db.models.fields.related import (
    SingleRelatedObjectDescriptor, ReverseSingleRelatedObjectDescriptor, 
    ForeignRelatedObjectsDescriptor, ManyRelatedObjectsDescriptor,
    ReverseManyRelatedObjectsDescriptor, ForeignKey)
from django.utils.functional import wraps
from cache import cache
from utils import generate_base_key, get_cached_models, get_cache_settings
from exceptions import ImproperlyConfigured
import settings as cachetree_settings
    
########################################################################

class Invalidator(object):

    # INVALIDATION_PATHS is a dictionary of lists of lists of tuples:
    # -Each key in the dictionary is a single model
    # -Each value is a list of invalidation paths for that model
    # -Each invalidation path is a list of tuples whose first items represent
    #  the sequence of attribute names to traverse in order to get from an
    #  instance of that model to the instance that needs to be invalidated, and
    #  whose second items are the model classes represented by each attribute
    #  name.    
    # -An empty list of invalidation paths would implies there is nothing to invalidate; 
    #  this should never get into the data structure.
    # -However, an invalidation path that is an empty list means that the model instance 
    #  itself is to be invalidated.
    INVALIDATION_PATHS = {}

    ####################################################################
    
    @classmethod
    def invalidate_instance(cls, sender, instance, **kwargs):
        cls()._invalidate_instance(instance)
        
    ####################################################################
    
    def _invalidate_instance(self, instance):
        """Uses the ``instance``'s ``model`` to look up its invalidation paths and
        find all the root instances that need to be invalidated.
        """
        
        model = instance.__class__
        
        invalidation_paths = self.INVALIDATION_PATHS.get(model, None)
        
        self.seen_instances = set()
        
        if not invalidation_paths:
            raise ImproperlyConfigured(
                "Cannot invalidate %(model)s instance because the %(model)s model was not found in the CACHETREE setting." % dict(
                model=model.__name__))
        
        root_instances = set()
        for invalidation_path in invalidation_paths:
            path = copy(invalidation_path)
            root_instances.update(self._get_root_instances(path, instance))
                
        self.invalidate_root_instances(*root_instances)
        
        for seen_instance in self.seen_instances:
            # The current state becomes the orig state for any future invalidations.
            delattr(seen_instance, "_orig_state")
            seen_instance._orig_state = seen_instance.__dict__.copy()
    
    ####################################################################
    
    def _get_root_instances(self, invalidation_path, *instances):
        """Recursively traverses the ``invalidation_path`` on each of the
        ``instances`` in order to get the root instances.
        """
        self.seen_instances.update(instances)
         
        # An empty invalidation path means we've reached the root instances.
        if not invalidation_path:
            return instances
        attr_name = invalidation_path.pop(0)[0]        
        related_instances = set()
        
        for instance in instances:
            instance_variants = [instance]
            if hasattr(instance, "_orig_state"):
                orig_state = instance.__dict__.pop("_orig_state")
                # If the instance __dict__ has changed, invalidate the
                # original as well.
                if orig_state != instance.__dict__:
                    orig = instance.__class__()
                    orig.__dict__ = orig_state
                    # If the instance was newly created (no orig.pk), its original
                    # state doesn't need to be invalidated.
                    if orig.pk:
                        instance_variants.append(orig)
                instance.__dict__["_orig_state"] = orig_state
                
            for instance in instance_variants:
            
                try:
                    attr = getattr(instance, attr_name)
                except ObjectDoesNotExist:
                    continue
                else:
                    if attr is None:
                        continue
                
                if isinstance(attr, Manager):
                    for related_instance in attr.all():
                        related_instances.add(related_instance)
                else:
                    related_instances.add(attr)
               
        if related_instances:
            return self._get_root_instances(invalidation_path, *related_instances)
        else:
            # If we couldn't find any related instances, then we can't get to
            # any root instances along the invalidation path.
            return []
        
    ####################################################################
    
    def invalidate_root_instances(self, *instances):
        """Invalidates all possible versions of the cached ``instances``,
        using the ``instances``'s lookups and their current field
        values. Each instance in ``instances`` must be a root instance,
        that is, an instance of one of the top-level models stored in the
        cache, not a related model instance.
        """
        self.seen_instances.update(instances)
        
        keys = set()
        
        for instance in instances:
            model = instance.__class__
            instance_variants = [instance]
            if hasattr(instance, "_orig_state"):
                orig = model()
                orig.__dict__ = instance._orig_state
                instance_variants.append(orig)
                
            for instance in instance_variants:
                
                cache_settings = get_cache_settings(instance.__class__)
                lookups = cache_settings.get("lookups")
                
                for lookup in lookups:
                    if not isinstance(lookup, (list, tuple)):
                        lookup = [lookup]
                        
                    kwargs = {}
                    for fieldname in lookup:
                        if fieldname == "pk":
                            field = model._meta.pk
                        else:
                            field = model._meta.get_field(fieldname)
                        attname = field.get_attname()
                        kwargs[fieldname] = getattr(instance, attname)
                        
                    key = generate_base_key(instance.__class__, **kwargs)
                    keys.add(key)
        
        cache.delete_many(keys)
        
    ####################################################################

    ERROR_MSG_INVALID_FIELD_LOOKUP = ('Cannot invalidate %(model)s model with lookup "%(lookup)s". '
                                  'Lookups must be one or more of: %(fields)s.')
    
    @classmethod
    def validate_lookups(cls, model):
        """Validates that the lookups correspond to fields within
        model._meta.fields, as invalidation cannot be reliably performed
        otherwise.
        
        Limiting lookups to model._meta.fields is more restrictive than
        Django, which allows model fields defined on a related model to be
        used in lookups (e.g. Author.objects.get(authorprofile=authorprofile),
        where AuthorProfile has a foreign key pointing to author). In order to
        invalidate the original state of a modified instance, cachetree copies
        the instance __dict__ in a post-init signal handler and uses it for
        invalidation. This __dict__ only contains the values for fields in
        model._meta.fields, not values for reverse fields. 
        
        Limiting lookups also prevents using lookup separators
        (double-underscore). Invalidation determines the cache key to
        invalidate using the values on the invalidated instance, so all
        lookups must be exact lookups (the default). E.g., if a key was stored
        using username__contains="stanley", it would be difficult or
        impossible to reconstruct the key to be invalidated based simply on
        having an instance with username="brianjaystanley".
        
        Lookup fields are not required to be unique. Since the only use of the
        cache is via get_cached, which calls Manager.get, uniqueness is
        guaranteed prior to setting instances in the cache. If another
        instance is subsequently assigned the same lookup value as a cached
        instance, it will trigger invalidation of the cached instance.
        Subequent calls to get_cached will raise MultipleObjectsReturned.
        """
        lookups = get_cache_settings(model).get("lookups")
        
        valid_fieldnames = ["pk"] + [field.name for field in model._meta.fields]
        
        for lookup in lookups:
            if not isinstance(lookup, (list, tuple)):
                lookup = [lookup]
                
            for kwarg in lookup:
                if kwarg not in valid_fieldnames:
                    raise ImproperlyConfigured(
                        cls.ERROR_MSG_INVALID_FIELD_LOOKUP % dict(
                            model=model.__name__, lookup=kwarg, fields=', '.join(valid_fieldnames)))
        
    ####################################################################
    
    @classmethod
    def invalidate_m2m(cls, sender, instance, action, reverse, model, pk_set, using, **kwargs):
        """Invalidates changes to m2m fields. All m2m_changed signals are
        received by this method, and are filtered here to ignore signals for
        uncached models. ``instance`` represents the instance from which the
        m2m was changed, while ``model`` and ``pk_set`` represent the related
        instances that were added, removed, or cleared from ``instance``.
        """
        # We run the invalidation on after add and remove, but before clear so
        # we can get the related instances that need to be invalidated before
        # they're cleared.
        if action not in ("post_add", "post_remove", "pre_clear"):
            return 

        # Unless both sides of the m2m relation are in INVALIDATION_PATHS, the
        # relation is not cached, so it's not a relation we care about.
        if instance.__class__ not in cls.INVALIDATION_PATHS or model not in cls.INVALIDATION_PATHS:
            return
            
        def requires_invalidation(candidate_model, related_model):
            """Neither ``instance`` nor the instances in ``pk_set`` have
            changed, only the through model relating them, so we only need
            to invalidate a given side of the relation if that side caches
            the relation, as evidenced by the opposite side of the
            relation having an invalidation path back to it.
            """
            for path in cls.INVALIDATION_PATHS[related_model]:
                try:
                    attr_name, attr_model = path[0]
                except IndexError:
                    continue
                else:
                    if attr_model is candidate_model:
                        return True
            return False
        

        if requires_invalidation(instance.__class__, model):
            # Invalidate the instance from which the m2m change was made.
            cls.invalidate_instance(instance.__class__, instance)
   
        if requires_invalidation(model, instance.__class__):
            # Get the related instances that were added or removed using the pk_set.
            if action in ("post_add", "post_remove"):
                if pk_set:
                    related_instances = model._default_manager.using(using).filter(pk__in=pk_set)
                else:
                    related_instances = []
            
            # Get the related instances that are to be cleared.   
            elif action == "pre_clear":
                if reverse is True:
                    for field in model._meta.many_to_many:
                        if field.rel.through is sender and field.rel.to is instance.__class__:
                            if field.rel.through._meta.auto_created:
                                related_instances = model._default_manager.using(using).filter(
                                    **{field.name: instance})
                            # For custom through models, invalidation
                            # occurs via the deleting of the through model
                            # instances (which are required to be cached
                            # if the related model instances are), so no
                            # invalidation is needed via m2mchanged.
                            else:
                                related_instances = []
                            break
                else:
                    for field in instance.__class__._meta.many_to_many:
                        if field.rel.through is sender and field.rel.to is model:
                            if field.rel.through._meta.auto_created:
                                related_instances = getattr(instance, field.name).all()
                            else:
                                related_instances = []
                            break
                
            for related_instance in related_instances:
                cls.invalidate_instance(related_instance.__class__, related_instance)
                    
    ####################################################################
    
    @classmethod
    def install(cls):
        """Sets up the invalidation paths for the cached models in CACHETREE,
        and registers signal handlers for each of them.
        """
        cls.INVALIDATION_PATHS = {}
        for app_label, model in get_cached_models():
            cls.validate_lookups(model)
            cache_settings = get_cache_settings(model)
            attrs = cache_settings.get("prefetch")
            cls._add_invalidation_path(model, [], attrs)
            
        cls.connect_signals()
            
    #################################################################### 
    
    @classmethod
    def uninstall(cls):
        """Uninstalling disconnects the signal handlers.
        """
        cls.disconnect_signals()
            
    #################################################################### 

    @staticmethod
    def copy_instance(sender, instance, **kwargs):
        """Copies the starting state of the instance and stores it on the
        instance, so the Invalidator can invalidate based on the original
        state (as well as the ending state).
        """
        instance._orig_state = instance.__dict__.copy()
    
    ####################################################################
    
    @classmethod
    def connect_signals(cls):
        cls._connect_signals()
        
    ####################################################################
        
    @classmethod
    def disconnect_signals(cls):
        cls._connect_signals(action="disconnect")
        
    ####################################################################
        
    @classmethod
    def _connect_signals(cls, action="connect"):
        """Connects or disconnects the signals receivers.
        """
        for model in cls.INVALIDATION_PATHS.iterkeys():
            dispatch_uid = "%s:%s" % (model._meta.app_label, model.__name__)
            if action == "connect":
                # Never disconnect the post_init handler because if the other
                # handlers are later reconnected, post_init will need to have
                # been called.
                getattr(post_init, action)(cls.copy_instance, sender=model, dispatch_uid=dispatch_uid)
            getattr(post_save, action)(cls.invalidate_instance, sender=model, dispatch_uid=dispatch_uid)
            getattr(post_delete, action)(cls.invalidate_instance, sender=model, dispatch_uid=dispatch_uid)
            
        getattr(m2m_changed, action)(cls.invalidate_m2m, dispatch_uid=__file__)
            
    ####################################################################

    ERROR_MSG_CACHE_CUSTOM_THROUGH = ("To reliably invalidate %(model)s.%(attr)s, "
                                      "modify your CACHETREE setting to also "
                                      "cache %(model)s.%(through_attr)s.")
    
    @classmethod
    def _add_invalidation_path(cls, model, path, attrs):
        """Adds the invalidation ``path`` to INVALIDATION_PATHS for the specified
        ``model``, then recursively follows the ``attrs``, if any, on the
        ``model`` in order to add the invalidation paths for the ``model``'s
        related models.
        """
        
        if model not in cls.INVALIDATION_PATHS:
            cls.INVALIDATION_PATHS[model] = []
            
        cls.INVALIDATION_PATHS[model].append(path)
        
        if not attrs:
            return 
        
        for attr_name, child_attrs in attrs.items():
            
            # Each attr must separately inherit and extend the existing path
            # back to the root instance.
            attr_path = copy(path)
            
            descriptor = getattr(model, attr_name)
            if isinstance(descriptor, (ReverseSingleRelatedObjectDescriptor,
                                       ReverseManyRelatedObjectsDescriptor)):
                related_model = descriptor.field.rel.to
                related_name = descriptor.field.related.get_accessor_name()
            elif isinstance(descriptor, (SingleRelatedObjectDescriptor,
                                         ForeignRelatedObjectsDescriptor,
                                         ManyRelatedObjectsDescriptor)):
                related_model = descriptor.related.model
                related_name = descriptor.related.field.name
            else:
                # Not a descriptor for a related model
                continue
            
            def validate_m2m_paths():
                if isinstance(descriptor, ReverseManyRelatedObjectsDescriptor):
                    related_through = descriptor.field.rel.through
                    related_m2m_fieldname = descriptor.field.m2m_field_name()
                elif isinstance(descriptor, ManyRelatedObjectsDescriptor):
                    related_through = descriptor.related.field.rel.through
                    related_m2m_fieldname = descriptor.related.field.m2m_reverse_field_name()
                        
                if related_through._meta.auto_created:
                    return
                
                # Find the through model's ForeignKey back to the model
                for field in related_through._meta.fields:
                    if (isinstance(field, ForeignKey) 
                        and field.rel.to is model 
                        and field.name == related_m2m_fieldname):
                        fk_field = field
                        break
                    
                accessor_name = fk_field.related.get_accessor_name()
                if accessor_name not in attrs:
                    raise ImproperlyConfigured(
                        cls.ERROR_MSG_CACHE_CUSTOM_THROUGH % dict(
                            model=model.__name__, attr=attr_name, through_attr=accessor_name)
                        )
                
            if isinstance(descriptor, (ReverseManyRelatedObjectsDescriptor,
                                       ManyRelatedObjectsDescriptor)):
                validate_m2m_paths()
            
            attr_path.insert(0, (related_name, model))
            cls._add_invalidation_path(related_model, attr_path, child_attrs)
                    
########################################################################

def invalidate(*instances):
    if cachetree_settings.INVALIDATE and not cachetree_settings.DISABLE:
        for instance in instances:
            Invalidator.invalidate_instance(instance.__class__, instance)
        
########################################################################

def no_invalidation(function):
    """Function decorator that disables invalidation for the duration of the
    function.
    """
    def wrapper(*args, **kwargs):
        if cachetree_settings.INVALIDATE and not cachetree_settings.DISABLE:
            Invalidator.disconnect_signals()
        returned = function(*args, **kwargs)
        if cachetree_settings.INVALIDATE and not cachetree_settings.DISABLE:
            Invalidator.connect_signals()
        return returned
    return wraps(function)(wrapper)

########################################################################
