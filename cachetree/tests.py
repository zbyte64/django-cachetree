"""Cachetree tests"""

########################################################################

from __future__ import with_statement
import time
from copy import deepcopy
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.test import TestCase
from django.utils.unittest import skipUnless, SkipTest
from django.conf import settings as django_settings
from django.core.cache import get_cache, DEFAULT_CACHE_ALIAS
from django.core.cache.backends import locmem
from . import install, uninstall, _Installer
from cache import cache
from auth import CachedModelBackend
import settings as cachetree_settings
from shortcuts import get_cached_object_or_404
from exceptions import ImproperlyConfigured
from invalidation import Invalidator, no_invalidation

########################################################################

# Cachetree test models
class Author(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    
class AuthorProfile(models.Model):
    author = models.OneToOneField("Author")
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zipcode = models.CharField(max_length=20)
    
class Entry(models.Model):
    author = models.ForeignKey("Author")
    title = models.CharField(max_length=100)
    content = models.TextField()
    categories = models.ManyToManyField("Category", through="EntryCategory")
    tags = models.ManyToManyField("Tag")
    similar_entries = models.ManyToManyField("self", symmetrical=True)
    linked_entries = models.ManyToManyField("self", symmetrical=False, through="EntryLink", related_name="linking_entries")
    
class Commenter(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    
class Comment(models.Model):
    commenter = models.ForeignKey("Commenter")
    entry = models.ForeignKey("Entry")
    comment = models.TextField()
    
class Category(models.Model):
    name = models.CharField(max_length=100)
    
class EntryCategory(models.Model):
    entry = models.ForeignKey("Entry", related_name="entrycategories")
    category = models.ForeignKey("Category", related_name="entrycategories")
    is_primary = models.BooleanField(default=False)
    
class Tag(models.Model):
    name = models.CharField(max_length=100)
    
class EntryLink(models.Model):
    from_entry = models.ForeignKey("Entry", related_name="links_from")
    to_entry = models.ForeignKey("Entry", related_name="links_to")
    opens_new_window = models.BooleanField()
    
########################################################################

class CachetreeBaseTestCase(TestCase):

    fixtures = ["testdata.json"]
    TEST_CACHE_NAME = "cachetree_test"
    CACHES = {
        TEST_CACHE_NAME: {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cachetree"
        }
    }
    CACHETREE = {
        "cachetree": {
            "Author": {
                "lookups":(
                    "pk",
                    ("first_name", "last_name"),
                ),
                "prefetch":{
                    "authorprofile": {},
                    "entry_set": {
                        "comment_set": {
                            "commenter": {},
                        },
                    },
                },
            },
            "AuthorProfile": {
                "lookups":(
                    "pk",
                    "author",
                ),
                "prefetch": {
                    "author": {},
                },
            },
            "Entry": {
                "lookups":("title",),
                "prefetch": {
                    "author": {},
                    "tags": {},
                    "categories": {},
                    "entrycategories": {},
                    "linked_entries": {},
                    "links_from": {},
                    "linking_entries": {},
                    "links_to": {},
                },
            },
            "Tag": {
                "lookups": ("name",),
                "prefetch": {
                    "entry_set": {},
                },
            },
            "Category": {
                "lookups": ("name",),
                "prefetch": {
                    "entry_set": {},
                    "entrycategories": {},
                },
            },
        }
    }
    
    ####################################################################
    
    def setUp(self):
        """Switches to a locmem cache, and reinstalls cachetree using the test
        settings.
        """
        self.CACHES_ORIG = django_settings.CACHES
        django_settings.CACHES.update(self.CACHES)
        self.cache_wrapped_orig = cache._wrapped
        cache._wrapped = get_cache(self.TEST_CACHE_NAME)
        
        new_settings = self.get_test_settings()
        self.orig_settings = self.reinstall(new_settings)
        
    ####################################################################
    
    def get_test_settings(self):
        """Returns the cachetree settings to be used for the test.
        """
        return dict(
            DISABLE=False,
            CACHETREE=self.CACHETREE,
            INVALIDATE=False
        )
        
    ####################################################################
    
    def tearDown(self):
        """Clears the locmem cache and reinstalls cachetree using the original
        settings.
        """
        self.assertEqual(cache.__class__, locmem.LocMemCache)
        cache.clear()
        django_settings.CACHES = self.CACHES_ORIG
        cache._wrapped = self.cache_wrapped_orig
        
        self.reinstall(self.orig_settings)
        
    ####################################################################
    
    def reinstall(self, new_settings):
        """Reinstalls cachetree using the ``new_settings``. Returns the old
        settings that were overwritten.
        """
        uninstall()
        old_settings = self.change_settings(new_settings)
        install()
        
        return old_settings
    
    ####################################################################
    
    def change_settings(self, new_settings):
        """
        Updates the cachetree settings based on ``new_settings``.
        """
        old_settings = {}
        for name, value in new_settings.iteritems():
            old_setting = getattr(cachetree_settings, name, None)
            old_settings[name] = old_setting    
            setattr(cachetree_settings, name, value)
            
        return old_settings

########################################################################

class CachetreeTestCase(CachetreeBaseTestCase):
    """Tests cachetree's caching functionality.
    """
    
    ####################################################################
    
    def test_disallowed_lookup(self):
        """Tests that looking up an instance by a disallowed lookup kwarg
        raises ImproperlyConfigured.
        """
        self.assertRaises(
            ValueError, 
            Author.objects.get_cached,
            name="Joe")
        
    ####################################################################
    
    def test_single_kwarg_lookup(self):
        """Tests caching a root instance using a single-kwarg lookup.
        """
        author = Author.objects.get_cached(pk=1)
        with self.assertNumQueries(0):
            Author.objects.get_cached(pk=1)
            
    ####################################################################

    def test_multi_kwarg_lookup(self):
        """Tests caching a root instance using a multi-kwarg lookup.
        """
        author = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        with self.assertNumQueries(0):
            Author.objects.get_cached(first_name="Joe", last_name="Blog")
            
    ####################################################################
    
    def test_one_to_one_field_lookup(self):
        """Tests caching a root instance using a one to one field lookup.
        """
        author = Author.objects.get(pk=1)
        authorprofile = AuthorProfile.objects.get_cached(author=author)
        with self.assertNumQueries(0):
            authorprofile = AuthorProfile.objects.get_cached(author=author)
        
    ####################################################################
     
    def test_reverse_one_to_one_field_lookup(self):
        """Tests caching a root instance using a reverse one to one field
        lookup. This type of lookup is only allowed if invalidation is
        disabled.
        """
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Author"]["lookups"] += ("authorprofile",)
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)
        
        authorprofile = AuthorProfile.objects.get(pk=1)
        author = Author.objects.get_cached(authorprofile=authorprofile)
        with self.assertNumQueries(0):
            author = Author.objects.get_cached(authorprofile=authorprofile)
        
    ####################################################################
    
    def test_reverse_foreign_key_field_lookup(self):
        """Tests caching a root instance using a reverse foreign key field
        lookup. This type of lookup is only allowed if invalidation is
        disabled.
        """
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Author"]["lookups"] += ("entry",)
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)
        
        entry = Entry.objects.get(pk=1)
        author = Author.objects.get_cached(entry=entry)
        with self.assertNumQueries(0):
            author = Author.objects.get_cached(entry=entry)
        
    ####################################################################
    
    def test_timeout(self):
        """Tests that cachetree uses the per-model timeout, if specified.
        """
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Tag"]["timeout"] = 1
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)
        
        author = Tag.objects.get_cached(name="models")
        with self.assertNumQueries(0):
            Tag.objects.get_cached(name="models")
            
        time.sleep(1)
        with self.assertNumQueries(2):
            Tag.objects.get_cached(name="models")
        
    ####################################################################

    def test_cache_DoesNotExist(self):
        """Tests that cachetree caches DoesNotExist exceptions.
        """
        self.assertRaises(
            Tag.DoesNotExist, 
            Tag.objects.get_cached,
            name="invalid tag"
        )
        
        with self.assertNumQueries(0):
            self.assertRaises(
                Tag.DoesNotExist, 
                Tag.objects.get_cached,
                name="invalid tag"
            )
    
    ####################################################################
    
    def test_cache_MultipleObjectsReturned(self):
        """Tests that cachetree caches MultipleObjectsReturned exceptions.
        """
        Tag.objects.create(name="models")
        
        self.assertRaises(
            Tag.MultipleObjectsReturned, 
            Tag.objects.get_cached,
            name="models"
        )
        
        with self.assertNumQueries(0):
            self.assertRaises(
                Tag.MultipleObjectsReturned, 
                Tag.objects.get_cached,
                name="models"
            )
    
    ####################################################################
    
    def test_cache_one_to_one_relation(self):
        """Tests that cachetree caches a one to one relation.
        """
        authorprofile = AuthorProfile.objects.get_cached(pk=2)
        
        with self.assertNumQueries(0):
            authorprofile = AuthorProfile.objects.get_cached(pk=2)
            self.assertEqual(authorprofile.author.first_name, "Arthur")
        
    ####################################################################
    
    def test_cache_reverse_one_to_one_relation(self):
        """Tests that cachetree caches the reverse side of a one to one relation.
        """
        author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
        
        with self.assertNumQueries(0):
            author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
            self.assertEqual(author.authorprofile.state, "CA")
        
    ####################################################################
    
    def test_cache_foreign_key_relation(self):
        """Tests that cachetree caches a foreign key relation.
        """
        entry = Entry.objects.get_cached(title="Best Practices for Python Web Development")
        
        with self.assertNumQueries(0):
            entry = Entry.objects.get_cached(title="Best Practices for Python Web Development")
            self.assertEqual(entry.author.first_name, "Arthur")
        
    ####################################################################
    
    def test_cache_reverse_foreign_key_relation(self):
        """Tests that cachetree caches the reverse side of a foreign key relation.
        """
        author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
        
        with self.assertNumQueries(0):
            author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
            self.assertEqual(len(author.entry_set.all()), 2)
        
    ####################################################################
    
    def test_cache_m2m_relation_with_auto_through(self):
        """Tests that cachetree caches a many to many relation with an
        auto-created intermediary table.
        """
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        
        with self.assertNumQueries(0):
            entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
            self.assertEqual(len(entry.tags.all()), 3)
        
    ####################################################################
    
    def test_cache_reverse_m2m_relation_with_auto_through(self):
        """Tests that cachetree caches the reverse side of a many to many
        relation with an auto-created intermediary table.
        """
        tag = Tag.objects.get_cached(name="views")
        
        with self.assertNumQueries(0):
            tag = Tag.objects.get_cached(name="views")
            self.assertEqual(len(tag.entry_set.all()), 1)
        
    ####################################################################
    
    def test_cache_m2m_relation_with_custom_through(self):
        """Tests that cachetree caches a many to many relation with a
        custom intermediary table.
        """
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        
        with self.assertNumQueries(0):
            entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
            self.assertEqual(len(entry.categories.all()), 3)
        
    ####################################################################
    
    def test_cache_reverse_m2m_relation_with_custom_through(self):
        """Tests that cachetree caches the reverse side of a many to many
        relation with a custom intermediary table.
        """
        category = Category.objects.get_cached(name="Django")
        
        with self.assertNumQueries(0):
            category = Category.objects.get_cached(name="Django")
            self.assertEqual(len(category.entry_set.all()), 3)
        
    ####################################################################
    
    def test_cache_recursion(self):
        """Tests that cachetree can recursively follow a tree of related
        models.
        """
        author = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        
        with self.assertNumQueries(0):
            author = Author.objects.get_cached(first_name="Joe", last_name="Blog")
            first_entry = author.entry_set.all()[0]
            first_comment = first_entry.comment_set.all()[0]
            commenter = first_comment.commenter
            self.assertEqual(commenter.first_name, "Alice")
        
    ####################################################################
    
    def test_disable(self):
        """Tests that cachetree can be disabled.
        """
        new_settings = dict(
            DISABLE=True)
        
        old_settings = self.reinstall(new_settings)
        
        author = Author.objects.get_cached(pk=1)
        with self.assertNumQueries(3):
            author = Author.objects.get_cached(pk=1)
            author.authorprofile
            list(author.entry_set.all())
        
########################################################################
    
class CachetreeAuthTestCase(CachetreeBaseTestCase):
    """Tests cachetree's auth functionality.
    """
    
    ####################################################################
    
    def setUp(self):
        
        self.backend_preinstalled = _Installer.auth_backend_installed()
        
        # If cachetree's auth backend is not installed, we try to install it,
        # but we can't do so if we don't know its location on the Python path.
        if not self.backend_preinstalled:
            try:
                import cachetree
            except ImportError:
                raise SkipTest('"%s" is not installed in AUTHENTICATION_BACKENDS, '
                               'and it is not on the Python path '
                               'so the test suite cannot auto-install it.' % CachedModelBackend.BACKEND_PATH)
            
            self.AUTHENTICATION_BACKENDS_ORIG = django_settings.AUTHENTICATION_BACKENDS
            django_settings.AUTHENTICATION_BACKENDS = (CachedModelBackend.BACKEND_PATH,)
        
        super(CachetreeAuthTestCase, self).setUp()
        self.username = "cacheuser"
        self.password = "cachepassword"
        self.user = User.objects.create_user(
            username=self.username,
            email="user@email.com",
            password=self.password
        )
        
    ####################################################################
    
    def tearDown(self):
        if not self.backend_preinstalled:
            django_settings.AUTHENTICATION_BACKENDS = self.AUTHENTICATION_BACKENDS_ORIG
        super(CachetreeAuthTestCase, self).tearDown()
        
    ####################################################################
    
    def test_authenticate(self):
        """Tests that the cachetree auth backend uses the cache.
        """
        user = authenticate(username=self.username, password=self.password)
        with self.assertNumQueries(0):
            user = authenticate(username=self.username, password=self.password)

    ####################################################################
    
    def test_get_user(self):
        """Tests that, with cachetree auth backend enabled, get_user uses the
        cache.
        """
        backend = CachedModelBackend()
        user = backend.get_user(user_id=self.user.id)
        with self.assertNumQueries(0):
            user = backend.get_user(user_id=self.user.id)

CachetreeAuthTestCase = skipUnless(
    "django.contrib.auth" in django_settings.INSTALLED_APPS, 
    "django.contrib.auth is not in INSTALLED_APPS")(CachetreeAuthTestCase)

########################################################################

class CachetreeInvalidationTestCase(CachetreeBaseTestCase):
    """Test cachetree's invalidation functionality.
    """
    
    ####################################################################
    
    def get_test_settings(self):
        """Returns the cachetree settings to be used for the test.
        """
        test_settings = super(CachetreeInvalidationTestCase, self).get_test_settings()
        test_settings['INVALIDATE'] = True
        return test_settings
        
    ####################################################################
    
    def test_lookup_separators_not_allowed(self):
        """Tests that using lookup separators in lookups raises
        ImproperlyConfigured.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Entry"]["lookups"] += ("title__contains",)
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            fieldnames = ["pk"] + [field.name for field in Entry._meta.fields]
            self.assertEqual(str(e), Invalidator.ERROR_MSG_INVALID_FIELD_LOOKUP % dict(
                model="Entry", 
                lookup="title__contains", 
                fields=', '.join(fieldnames)))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_reverse_field_lookups_not_allowed(self):
        """Tests that using reverse fields in lookups raises
        ImproperlyConfigured.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Author"]["lookups"] += ("authorprofile",)
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            fieldnames = ["pk"] + [field.name for field in Author._meta.fields]
            self.assertEqual(str(e), Invalidator.ERROR_MSG_INVALID_FIELD_LOOKUP % dict(
                model="Author", 
                lookup="authorprofile", 
                fields=', '.join(fieldnames)))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_m2m_field_lookups_not_allowed(self):
        """Tests that using m2m fields in lookups raises
        ImproperlyConfigured.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        CACHETREE["cachetree"]["Entry"]["lookups"] += ("tags",)
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            fieldnames = ["pk"] + [field.name for field in Entry._meta.fields]
            self.assertEqual(str(e), Invalidator.ERROR_MSG_INVALID_FIELD_LOOKUP % dict(
                model="Entry", 
                lookup="tags", 
                fields=', '.join(fieldnames)))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_dependencies_for_m2m_relation_with_custom_through(self):
        """Tests that, if a many to many relation with custom intermediary model
        is cached, the intermediary model must also be cached (this is
        required for reliable invalidation).
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Entry"]["prefetch"]["entrycategories"]
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            self.assertEqual(str(e), Invalidator.ERROR_MSG_CACHE_CUSTOM_THROUGH % dict(
                model="Entry", attr="categories", through_attr="entrycategories"))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_dependencies_for_reverse_m2m_relation_with_custom_through(self):
        """Tests that, if the reverse side of a many to many relation with custom
        intermediary model is cached, the intermediary model must also be
        cached.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Category"]["prefetch"]["entrycategories"]
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            self.assertEqual(str(e), Invalidator.ERROR_MSG_CACHE_CUSTOM_THROUGH % dict(
                model="Category", attr="entry_set", through_attr="entrycategories"))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_dependencies_for_m2m_relation_to_self_with_custom_through(self):
        """Tests that, if a many to many relation to self with a custom
        intermediary model is cached, the correct intermediary relation must
        also be cached.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Entry"]["prefetch"]["links_from"]
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            self.assertEqual(str(e), Invalidator.ERROR_MSG_CACHE_CUSTOM_THROUGH % dict(
                model="Entry", attr="linked_entries", through_attr="links_from"))
        else:
            self.fail("Exception not raised")
        
    ####################################################################
    
    def test_dependencies_for_reverse_m2m_relation_to_self_with_custom_through(self):
        """Tests that, if the reverse side of a many to many relation to self
        with a custom intermediary model is cached, the correct intermediary
        relation must also be cached.
        """
        uninstall()
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Entry"]["prefetch"]["links_to"]
        new_settings = dict(CACHETREE=CACHETREE)
        old_settings = self.change_settings(new_settings)
        try:
            install()
        except ImproperlyConfigured, e:
            self.assertEqual(str(e), Invalidator.ERROR_MSG_CACHE_CUSTOM_THROUGH % dict(
                model="Entry", attr="linking_entries", through_attr="links_to"))
        else:
            self.fail("Exception not raised")

    ####################################################################
    
    def test_invalidate_root_instance_multiple_states(self):
        """Tests that both the new and original state of a root instance are
        invalidated.
        
        Further tests that, if a root instance is changed (and invalidated),
        then changed again, the second change causes the new and intermediate
        state of the instance to be invalidated, not the new and original
        state. In other words, tests that upon the first invalidation, the
        instance's current state becomes its "original state" for the purpose
        of subsequent invalidation.
        """
        Author.objects.create(first_name="Joseph", last_name="Blog")
        
        # Populate the cache.
        author1 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        author2 = Author.objects.get_cached(first_name="Joseph", last_name="Blog")
        
        author1.first_name = "Joseph"
        author1.save()
        
        # Verify that the first invalidation is correct.
        self.assertRaises(
            Author.DoesNotExist,
            Author.objects.get_cached,
            first_name="Joe", 
            last_name="Blog")
        
        self.assertRaises(
            Author.MultipleObjectsReturned,
            Author.objects.get_cached,
            first_name="Joseph",
            last_name="Blog")
        
        # Create a new author with the same state as the original state of
        # author1.
        Author.objects.create(first_name="Joe", last_name="Blog")
        author3 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
    
        # Create a 4th author that should be invalidated based on the new
        # state of author1.
        Author.objects.create(first_name="George", last_name="Blog")
        author4 = Author.objects.get_cached(first_name="George", last_name="Blog")
        
        # Modify author1 again.
        author1.first_name = "George"
        author1.save()
        
        # This should allow author2 to be retrieved without
        # MultipleObjectsReturned, because the intermediate state of author1
        # (which is also the state of author2) was invalidated.
        Author.objects.get_cached(first_name="Joseph", last_name="Blog")

        # And it should raise MultipleObjectsReturned for author4/author1
        # (instead of returning the cached author4).
        self.assertRaises(
            Author.MultipleObjectsReturned,
            Author.objects.get_cached,
            first_name="George",
            last_name="Blog")
        
        # And it should not cause invalidation of author3 (since the original
        # state of author1, which is the same as author3, should not have been
        # invalidated).
        with self.assertNumQueries(0):
            author3 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
    
    ####################################################################
    
    def test_invalidate_non_root_instance_multiple_states(self):
        """Tests that both the new and original state of a non-root instance
        are invalidated.
        
        Further tests that, if a non-root instance is changed (and
        invalidated), then changed again, the second change causes the new and
        intermediate state of the instance to be invalidated, not the new and
        original state.
        """
        Author.objects.create(first_name="John", last_name="Smith")
        
        # Populate the cache.
        author1 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        author2 = Author.objects.get_cached(first_name="John", last_name="Smith")
        
        authorprofile = AuthorProfile.objects.get(author__first_name="Joe", author__last_name="Blog")
        authorprofile.author = author2
        authorprofile.save()
        
        # Make sure the author objects reflect their new state.
        author1 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        try:
            author1.authorprofile
        except AuthorProfile.DoesNotExist:
            pass
        else:
            self.fail("DoesNotExist not raised")
            
        author2 = Author.objects.get_cached(first_name="John", last_name="Smith")
        self.assertEqual(author2.authorprofile, authorprofile)

        # Create a third author and re-assign the authorprofile
        Author.objects.create(first_name="James", last_name="Johnson")
        author3 = Author.objects.get_cached(first_name="James", last_name="Johnson")
        
        authorprofile.author = author3
        authorprofile.save()
        
        # Make sure the affected author objects reflect their new state.
        author2 = Author.objects.get_cached(first_name="John", last_name="Smith")
        try:
            author2.authorprofile
        except AuthorProfile.DoesNotExist:
            pass
        else:
            self.fail("DoesNotExist not raised")
            
        author3 = Author.objects.get_cached(first_name="James", last_name="Johnson")
        self.assertEqual(author3.authorprofile, authorprofile)

        # And make sure author1 was not invalidated.
        with self.assertNumQueries(0):
            author1 = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        
    ####################################################################
    
    def test_not_invalidate_original_state_of_unchanged_non_root_instance(self):
        """Tests that, when following an invalidation path back to a root
        instance, cachetree ignores the original state of an unchanged
        non-root instance, since that would result in unnecessary duplicate
        queries.
        """
        
        CACHETREE = {
            "cachetree": {
                "Entry": {
                    "lookups":("title",),
                    "prefetch": {
                        "comment_set": {
                            "commenter": {}
                            },
                    },
                },
            }
        }
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)

        # Make an entry with one comment from a commenter with only one comment
        entry = Entry.objects.create(
            author_id=1,
            title="Using Django Utils",
        )
        commenter = Commenter.objects.create(first_name="Richard")
        comment = Comment.objects.create(
            commenter=commenter,
            entry=entry,
            comment="Thanks for the article."
        )
        
        commenter.first_name = "Linda"
        
        with self.assertNumQueries(4):
            
            commenter.save(force_update=True)
            # This triggers the following queries: 
            # UPDATE cachetree_commenter SET first_name = Linda WHERE cachetree_commenter.id = 6
            # The following query will run twice, once for the original state of 
            # commenter and once for the new state, because they're different (even 
            # though the commenter_id did not change):
            # SELECT * FROM cachetree_comment WHERE cachetree_comment.commenter_id = 6
            # SELECT * FROM cachetree_comment WHERE cachetree_comment.commenter_id = 6
            # This will return 1 comment, and since the comment is unchanged, 
            # the following query should not be duplicated:
            # SELECT * FROM cachetree_entry WHERE cachetree_entry.id = 5
            # The entry this returns will then be invalidated.
        
    ####################################################################

    def test_invalidate_one_to_one_relation(self):
        """Tests that a cached one to one relation is invalidated.        
        """
        # Populate the cache.
        authorprofile = AuthorProfile.objects.get_cached(pk=1)
        
        # Change the author.
        author = authorprofile.author
        author.first_name = "Sally"
        author.last_name = "Norman"
        author.save()
        
        # Make sure the invalidation path was followed back to the
        # authorprofile object.
        authorprofile = AuthorProfile.objects.get_cached(pk=1)
        self.assertEqual(authorprofile.author.first_name, author.first_name)
        self.assertEqual(authorprofile.author.last_name, author.last_name)
        
    ####################################################################
    
    def test_invalidate_reverse_one_to_one_relation(self):
        """Tests that a cached reverse one to one relation is invalidated.        
        """
        # Populate the cache
        author = Author.objects.get_cached(pk=1)
        
        # Change the authorprofile.
        authorprofile = author.authorprofile
        authorprofile.city = "New York"
        authorprofile.state = "NY"
        authorprofile.zipcode = "10001"
        authorprofile.save()
        
        # Make sure the invalidation path was followed back to the author
        # object.
        author = Author.objects.get_cached(pk=1)
        self.assertEqual(author.authorprofile.city, authorprofile.city)
        self.assertEqual(author.authorprofile.state, authorprofile.state)
        self.assertEqual(author.authorprofile.zipcode, authorprofile.zipcode)
        
    ####################################################################
    
    def test_invalidate_foreign_key_relation(self):
        """Tests that a cached foreign key relation is invalidated.        
        """
        # Populate the cache
        entry = Entry.objects.get_cached(title="Best Practices for Python Web Development")
        
        # Change the author.
        author = entry.author
        author.first_name = "Sally"
        author.last_name = "Norman"
        author.save()
        
        # Make sure the invalidation path was followed back to the entry
        # object.
        entry = Entry.objects.get_cached(title="Best Practices for Python Web Development")
        self.assertEqual(entry.author.first_name, author.first_name)
        self.assertEqual(entry.author.last_name, author.last_name)
        
    ####################################################################
    
    def test_invalidate_reverse_foreign_key_relation(self):
        """Tests that a cached reverse foreign key relation is invalidated.
        """
        # Populate the cache
        author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
        
        # Change an entry.
        entry = author.entry_set.all()[0]
        entry.title = "How to Get Involved with the Django Community"
        entry.save()
        
        # Make sure the invalidation path was followed back to the author
        # object.
        author = Author.objects.get_cached(first_name="Arthur", last_name="Smith")
        self.assertEqual(author.entry_set.all()[0].title, entry.title)
        
    ####################################################################
    
    def test_invalidate_m2m_relation_with_auto_through(self):
        """Tests that a cached many to many relation with an auto-created
        intermediary table is invalidated.
        """
        # Populate the cache
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        
        # Change a tag.
        tag = entry.tags.all()[0]
        tag.name = "HTTP"
        tag.save()
        
        # Make sure the invalidation path was followed back to the entry
        # object.
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        self.assertEqual(entry.tags.all()[0].name, tag.name)
        
    ####################################################################
    
    def test_invalidate_reverse_m2m_relation_with_auto_through(self):
        """Tests that a cached reverse many to many relation with an
        auto-created intermediary table is invalidated.
        """
        # Populate the cache
        tag = Tag.objects.get_cached(name="views")
        
        # Change an entry.
        entry = tag.entry_set.all()[0]
        entry.title = "Working with Middleware"
        entry.save()
        
        # Make sure the invalidation path was followed back to the tag
        # object.
        tag = Tag.objects.get_cached(name="views")
        self.assertEqual(tag.entry_set.all()[0].title, entry.title)
        
    ####################################################################
    
    def test_invalidate_m2m_relation_with_custom_through(self):
        """Tests that a cached many to many relation with a custom
        intermediary table is invalidated.
        """
        # Populate the cache
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        
        # Change a category.
        category = entry.categories.all()[0]
        category.name = "Scalability"
        category.save()
        
        # Make sure the invalidation path was followed back to the entry
        # object.
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        self.assertEqual(entry.categories.all()[0].name, category.name)
        
    ####################################################################
    
    def test_invalidate_reverse_m2m_relation_with_custom_through(self):
        """Tests that a cached reverse many to many relation with a custom
        intermediary table is invalidated.
        """
        # Populate the cache
        category = Category.objects.get_cached(name="Django")
        
        # Change an entry.
        entry = category.entry_set.all()[0]
        entry.title = "Writing Portable Django Apps"
        entry.save()
        
        # Make sure the invalidation path was followed back to the category
        # object.
        category = Category.objects.get_cached(name="Django")
        self.assertEqual(category.entry_set.all()[0].title, entry.title)
        
    ####################################################################

    def test_not_invalidate_non_caching_side_of_m2m_relation(self):
        """Tests that, if only the reverse side of a many to many relation
        caches the relation, the non-caching primary side is not invalidated
        when the other side changes.
        """
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Entry"]["prefetch"]["tags"]
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)
        
        # Populate the cache
        entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        tag = entry.tags.all()[0]
           
        # Change the tag.
        orig_name = tag.name
        tag.name = "HTTP"
        tag.save()
        
        # Make sure the tag was invalidated but the entry was not.
        self.assertRaises(
            Tag.DoesNotExist,
            Tag.objects.get_cached,
            name=orig_name)
        
        with self.assertNumQueries(0):
            entry = Entry.objects.get_cached(title="Mapping URLs to Views in Django")
        
    ####################################################################
    
    def test_not_invalidate_non_caching_reverse_side_of_m2m_relation(self):
        """Tests that, if only the primary side of a many to many relation
        caches the relation, the non-caching reverse side is not invalidated
        when the other side changes.
        """
        CACHETREE = deepcopy(self.CACHETREE)
        del CACHETREE["cachetree"]["Tag"]["prefetch"]["entry_set"]
        
        new_settings = dict(CACHETREE=CACHETREE)
        self.reinstall(new_settings)
        
        # Populate the cache
        tag = Tag.objects.get_cached(name="views")
        entry = Entry.objects.get_cached(title=tag.entry_set.all()[0].title)
           
        # Change the entry.
        orig_title = entry.title
        entry.title = "Working with Middleware"
        entry.save()
        
        # Make sure the entry was invalidated but the tag was not.
        self.assertRaises(
            Entry.DoesNotExist,
            Entry.objects.get_cached,
            title=orig_title)
        
        with self.assertNumQueries(0):
            tag = Tag.objects.get_cached(name="views")
        
    ####################################################################
    
    def test_invalidation_recursion(self):
        """Tests that cachetree can recursively follow an invalidation path of
        related models.
        """
        # Follow a tree of related models.
        author = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        first_entry = author.entry_set.all()[0]
        first_comment = first_entry.comment_set.all()[0]
        commenter = first_comment.commenter
        self.assertEqual(commenter.first_name, "Alice")
            
        # Change the leaf instance.
        commenter.first_name = "Bill"
        commenter.save()
        
        # Make sure the root instance was invalidated.
        author = Author.objects.get_cached(first_name="Joe", last_name="Blog")
        first_entry = author.entry_set.all()[0]
        first_comment = first_entry.comment_set.all()[0]
        commenter = first_comment.commenter
        self.assertEqual(commenter.first_name, "Bill")
        
    ####################################################################
    
    def test_m2m_add_uncaches_all(self):
        """Tests that calling the add() method on a many to many manager
        uncaches the all() method for that manager.
        """
        entry = Entry.objects.get_cached(title="Using Models in Tests")
        
        # Make sure the tags were prefetched.
        with self.assertNumQueries(0):
            tags = list(entry.tags.all())
        
        tag = Tag.objects.get_cached(name="views")        
        self.assertNotIn(tag, tags)
        self.assertEqual(len(tags), 3)
            
        # Add the new tag and make sure all() is uncached and returns the new
        # tag set.
        entry.tags.add(tag)
        tags = entry.tags.all()
        self.assertIn(tag, tags)
        self.assertEqual(len(tags), 4)
        
    ####################################################################
    
    def test_duplicate_add(self):
        """Tests that adding an object using add() that was already added
        doesn't cause an invalidation error. Fix for Issue #1 on Github.
        """
        entry = Entry.objects.get(title="Using Models in Tests")
        tag = Tag.objects.get(pk=1)
        self.assertIn(tag, entry.tags.all())
        
        # Re-add the tag. Django's related manager will send an empty pk_set,
        # and cachetree shouldn't choke on it.
        entry.tags.add(tag)
        
    ####################################################################
    
    def test_m2m_remove_uncaches_all(self):
        """Tests that calling the remove() method on a many to many manager
        uncaches the all() method for that manager.
        """
        entry = Entry.objects.get_cached(title="Using Models in Tests")
        
        # Make sure the tags were prefetched.
        with self.assertNumQueries(0):
            tags = list(entry.tags.all())
        
        tag = tags[0]
        self.assertEqual(len(tags), 3)
            
        # Remove the tag and make sure all() is uncached and returns the new
        # tag set.
        entry.tags.remove(tag)
        tags = entry.tags.all()
        self.assertNotIn(tag, tags)
        self.assertEqual(len(tags), 2)
        
    ####################################################################
    
    def test_m2m_clear_uncaches_all(self):
        """Tests that calling the clear() method on a many to many manager
        uncaches the all() method for that manager.
        """
        entry = Entry.objects.get_cached(title="Using Models in Tests")
        
        # Make sure the tags were prefetched.
        with self.assertNumQueries(0):
            tags = list(entry.tags.all())
        
        self.assertEqual(len(tags), 3)
            
        # Clear the tags and make sure all() is uncached and returns the new
        # tag set.
        entry.tags.clear()
        tags = entry.tags.all()
        self.assertEqual(len(tags), 0)
        
    ####################################################################
    
    @no_invalidation
    def test_no_invalidation_decorator(self):
        """Tests that the no_invalidation decorator works.
        """
        author = Author.objects.get_cached(pk=1)
        
        author.first_name = "Bob"
        author.last_name = "Robinson"
        author.save()
        
        author = Author.objects.get_cached(pk=1)
        self.assertNotEqual(author.first_name, "Bob")
        self.assertNotEqual(author.last_name, "Robinson")
        
    ####################################################################
    
    def test_disable(self):
        """Tests that cachetree invalidation can be disabled.
        """
        new_settings = dict(
            INVALIDATE=False)
        
        old_settings = self.reinstall(new_settings)
        
        author = Author.objects.get_cached(pk=1)
        author.first_name = "Bob"
        author.last_name = "Robinson"
        author.save()
        
        author = Author.objects.get_cached(pk=1)
        self.assertNotEqual(author.first_name, "Bob")
        self.assertNotEqual(author.last_name, "Robinson")
            
########################################################################

class CachetreeShortcutsTestCase(CachetreeBaseTestCase):
    """Tests cachetree's shortcuts.
    """
    
    ####################################################################
    
    def test_get_cached_object_or_404(self):
        """Tests that get_cached_object_or_404 uses the cache.
        """
        author = get_cached_object_or_404(Author, pk=1)
           
        # Make sure the function can accept a model, manager, or queryset,
        # like get_object_or_404
        for arg in (
            Author, 
            Author._default_manager, 
            Author._default_manager.get_query_set()):
            
            with self.assertNumQueries(0):
                author = get_cached_object_or_404(arg, pk=1)
        
########################################################################