``django-cachetree`` provides caching of configurable trees of related model
instances in Django. For example, with ``django-cachetree`` you could easily
cache a user instance and the user's photos and comments on a photo-sharing
site as a single item in the cache. When a user is fetched from the database,
its related objects are "prefetched" before the user object is set in the
cache. This means that when you retrieve the user object from the cache, the
related objects are there with it and you can access them without hitting the
database (or hitting the cache again)::

    user = User.objects.get_cached(pk=1) # user not yet in cache, hits the 
                                         # database and prefetches related objects

    ....
    
    user = User.objects.get_cached(pk=1) # hits the cache
    photos = user.photo_set.all()
    print photos[0].title # doesn't hit database or cache
    comments = user.comment_set.all()
    print comments[0].date # doesn't hit database or cache

You can configure ``django-cachetree`` to cache related objects of related
objects of related objects, to any depth. For example, on a blog, you could
cache author objects along with their set of blog entries, the set of comments
for each of those entries, and the commenter for each of those comments.

``django-cachetree`` automatically invalidates cached objects when they or the
related objects cached with them are changed, deleted, or created.

Requirements 
============ 
``django-cachetree`` requires Django 1.3 and Python 2.5, 2.6, or 2.7.

Installation
============
You can install ``django-cachetree`` with ::

    pip install django-cachetree

or ::

    easy_install django-cachetree

This will add ``cachetree`` to your Python path. After you have configured
your settings file as described later, run ::

    cachetree.install()
    
at the bottom of your models file. This will add a ``get_cached`` method to
the default manager for each of your cached models. (Note that because
``get_cached`` is added to the manager class, it will be available on all
instances of that manager class. However, attempting to use it on a model not
defined in your ``CACHETREE`` setting will raise a ``ValueError``.) If
invalidation is enabled, ``cachetree.install()`` also registers signal
handlers that are used for invalidation.

Adding ``'cachetree'`` to your ``INSTALLED_APPS`` is only required if you want to
run ``django-cachetree``'s test suite.

The ``CACHETREE`` setting
=================================
To use ``django-cachetree``, add a ``CACHETREE`` setting to ``settings.py``.
The ``CACHETREE`` setting consists of nested dictionaries that tell
``django-cachetree`` what to cache. The keys in the topmost dictionary should
contain the ``app_label`` for each app that has models you wish to cache. Each
``app_label``'s dictionary should contain a key with the class name (as a
string) for each cached model in the app. These are the models whose managers
will provide a ``get_cached`` method, and are known as "root models" in
``django-cachetree``'s terminology. For example, to cache ``Author`` and
``Entry`` models but none of their related objects in an app called ``myapp``,
you would write::

    CACHETREE = {
        "myapp": {
            "Author": {},
            "Entry": {},
        }
    }

The dictionary for each root model can contain three optional keys,
``"timeout"``, ``"lookups"``, and ``"prefetch"``.

``timeout`` 
    The timeout, in seconds, to use when caching instances of this model.
    Overrides your global timeout setting in ``CACHES``.
    
``lookups``
    A tuple containing the field names that can be used as kwargs when calling
    ``get_cached`` for this model. By default, lookups are allowed by primary
    key. If your model's primary key field is ``id``, the default setting
    would be ``("pk", "id")``.
 
    To lookup by a combination of fields, include the field names as a tuple
    within your ``lookups`` tuple. For example, to look up ``User`` instances
    by ``id`` or by ``first_name`` and ``last_name``::

        CACHETREE = {
            "auth": {
                "User": {
                    "lookups": (
                        "id",
                        ("first_name", "last_name"),
                    )
                }
            }
        }
    
    If invalidation is enabled, lookups are restricted to fields defined on
    the model, including ``ForeignKey`` fields and ``OneToOneField``\s but
    excluding ``ManyToManyField``\s. Specifying ``ManyToManyField``\s or
    reverse ``ForeignKey`` or ``OneToOneField``\s will raise
    ``cachetree.ImproperlyConfigured``. Lookup separators (for example,
    ``username__contains``) are also not allowed and will raise
    ``ImproperlyConfigured``. To know what keys to invalidate,
    ``django-cachetree`` requires exact lookups (which is the default when no
    lookup separator is used).
    
``prefetch``
    A dictionary specifying the tree of related objects to prefetch and cache
    with the root model instance. Each key should be the attribute name (as a
    string) of the related instance(s) to be prefetched. Each key's value
    should be a dictionary of attribute names to prefetch on the related
    instance(s), or an empty dictionary (or ``None``) if no further
    relationships should be prefetched. Any relationship can be prefetched:
    ``OneToOneField``, ``ForeignKey``, and ``ManyToManyField``, forward or
    reverse. For example, to cache author objects, their set of entries, those
    entries' comments, and each comment's commenter, you might write::

        CACHETREE = {
            "myapp": {
                "Author": {
                    "lookups": (
                        "pk",
                        "id",
                        ("first_name", "last_name"),
                    ),
                    "prefetch": {
                        "entry_set": {
                            "comment_set": {
                                "commenter": {},
                            },
                        },
                    },
                },
            },
        }
    
    The above example assumes that each ``Author`` object is related to its
    entries by an ``entry_set`` attribute, each entry object is related to its
    comments by a ``comment_set`` attribute, and each comment object relates
    to its commenter by a ``commenter`` field. 
    
    If invalidation is enabled, there is one restriction on prefetching. If
    you prefetch a ``ManyToManyField`` (forward or reverse) that defines a
    custom intermediary model (as specified with the ``through`` argument on
    the model field definition), you must also prefetch the attribute that
    points to the intermediary instances. For example, if you have an
    ``Entry`` model related to a ``Category`` model through a custom
    intermediary model called ``EntryCategory``, and you prefetch
    ``Entry.categories`` (a ``ManyToManyField``), you must also prefetch the
    ``Entry.entrycategory_set`` attribute that Django adds to your ``Entry``
    model, or ``ImproperlyConfigured`` will be raised.
    
You can find example ``CACHETREE`` settings in ``django-cachetree``'s test
module, which defines models and settings covering all possible relationships.

Prefetching ``ManyToManyField``\s and Reverse ``ForeignKey``\s
==============================================================
When you configure ``django-cachetree`` to cache a ``ManyToManyField`` or
reverse ``ForeignKey``, such as ``user.photo_set`` (where ``Photo`` has a
foreign key to ``User``), ``django-cachetree`` calls ``user.photo_set.all()``,
evaluates the queryset, and caches the results on the ``user`` when
prefetching. Subsequent calls to ``user.photo_set.all()`` will return the
cached results, rather than returning a new queryset (which would require
hitting the database again to evaluate). ``django-cachetree`` patches the manager on
``ManyToManyField`` and ``ForeignKey`` descriptors to make this behavior
possible. However, only the ``all()`` method is patched. If you call
``user.photo_set.count()`` or ``user.photo_set.filter()`` or any other method
besides ``all()``, you will bypass the cached results and hit the database.
Assuming your object set is not huge, you can avoid hitting the database by
calling ``all()`` and counting or filtering the results within your code.

How Invalidation Works
======================
When you call ``cachetree.install()``, ``django-cachetree`` analyzes your
``CACHETREE`` setting and determines which relationships must be followed in
order to traverse the tree backwards from prefetched related instances to
their root model instances. Using this information, whenever a model defined
in your ``CACHETREE`` setting (either as a root model or as a prefetched
relationship) is created, saved, or deleted (and in the case of
``ManyToManyField``\s and reverse ``ForeignKey``\s, added, removed, or cleared
using the field manager's ``add()``, ``remove()``, or ``clear()`` methods),
``django-cachetree`` traverses its relationships back to the root model
instance(s) that need to be invalidated. ``django-cachetree`` uses a
``post_init`` signal handler to keep track of each instance's initial state,
and when the instance changes and is saved, ``django-cachetree`` follows both
the instance's new and initial values to find the root model instances that
need to be invalidated. For example, if you cache ``Author`` objects along
with their ``entry_set``, and you change an ``Entry`` object's author,
``django-cachetree`` will invalidate both the new and the initial ``Author``
objects for that entry.

**Important Caveat**: ``django-cachetree`` does not perform invalidation when
you run an ``UPDATE`` query using a manager's ``update()`` method. You will
either need to invalidate the affected instances yourself by calling
``invalidate()`` (described below), rely on the cached objects to expire naturally,
or avoid using ``update()``.
    
Cachetree Authentication Backend
================================
If ``django.contrib.auth`` is installed in your project, you can use
``django-cachetree``'s authentication backend::

    AUTHENTICATION_BACKENDS = (
        "cachetree.auth.CachedModelBackend",
    )

This will look in the cache before hitting the database when authenticating
users. Adding the ``auth.User`` model to your ``CACHETREE`` setting is
optional. Not adding it implies the following settings::
    
    CACHETREE = {

            ...
            
            "auth": {
                "User": {
                    "lookups":(
                        "pk",
                        "username",
                    ),
                },
            },
        }

If you wish to allow additional lookups on ``User`` or to prefetch related
instances, explicitly define ``User`` in your ``CACHETREE`` setting.

Utils
=====
The following functions can be imported from ``cachetree``:

``get_cached_object_or_404``
    Works like ``get_object_or_404``, but uses ``get_cached`` instead of ``get``. 
    
``invalidate(*instances)``
    Traverses relationships on each of the ``instances`` to find and invalidate
    its root model instance(s).

``no_invalidation``
    Decorator that disables invalidation for the duration of the function it decorates.

Additional Settings
===================
``CACHETREE_DISABLE``
    Set to ``True`` to disable ``django-cachetree``. Calls to ``get_cached()``
    or ``get_cached_object_or_404()`` will use ``get()``. Calls to
    ``invalidate()`` and uses of the ``no_invalidation`` decorator will have
    no effect. This allows you to temporarily disable ``django-cachetree``
    without modifying any code. Default: ``False``.

``CACHETREE_INVALIDATE``
    Set to ``False`` to disable invalidation. ``django-cachetree`` will
    continue to cache model objects but will not invalidate them when they
    change. Calls to ``invalidate()`` and uses of the ``no_invalidation``
    decorator will have no effect. Default: ``True``.

``CACHETREE_MANY_RELATED_PREFIX``
    Controls the prefix ``django-cachetree`` uses when it prefetches a set of
    related objects and caches it on a model instance. In the example of
    ``author.entry_set.all()``, ``django-cachetree`` caches the author's set
    of entries as ``author._cached_entry_set``, and subsequent calls to
    ``author.entry_set.all()`` return this attribute. Normally you will not
    need to access this attribute directly, but this setting allows you to
    change the prefix in case of name conflicts. Default: ``_cached_``.
