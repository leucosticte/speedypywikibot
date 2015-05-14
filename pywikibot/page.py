# -*- coding: utf-8  -*-
"""
Objects representing various types of MediaWiki, including Wikibase, pages.

This module also includes objects:
* Link: an internal or interwiki link in wikitext.
* Revision: a single change to a wiki page.
* Property: a type of semantic data.
* Claim: an instance of a semantic assertion.

"""
#
# (C) Pywikibot team, 2008-2015
#
# Distributed under the terms of the MIT license.
#
from __future__ import unicode_literals

__version__ = '$Id$'
#

import hashlib
import logging
import re
import sys
import unicodedata

from collections import defaultdict, namedtuple
from warnings import warn

if sys.version_info[0] > 2:
    unicode = basestring = str
    long = int
    from html import entities as htmlentitydefs
    from urllib.parse import quote_from_bytes, unquote_to_bytes
else:
    chr = unichr  # noqa
    import htmlentitydefs
    from urllib import quote as quote_from_bytes, unquote as unquote_to_bytes

import pywikibot

from pywikibot import config
from pywikibot.comms import http
from pywikibot.family import Family
from pywikibot.site import Namespace
from pywikibot.exceptions import (
    AutoblockUser,
    _EmailUserError,
    NotEmailableError,
    SiteDefinitionError,
    UserRightsError,
)
from pywikibot.tools import (
    UnicodeMixin, DotReadableDict,
    ComparableMixin, deprecated, deprecate_arg, deprecated_args,
    first_upper, remove_last_args, _NotImplementedWarning,
    OrderedDict, Counter,
)
from pywikibot.tools.ip import ip_regexp  # noqa & deprecated
from pywikibot.tools.ip import is_IP
from pywikibot import textlib


logger = logging.getLogger("pywiki.wiki.page")


# Note: Link objects (defined later on) represent a wiki-page's title, while
# Page objects (defined here) represent the page itself, including its contents.

class BasePage(UnicodeMixin, ComparableMixin):

    """BasePage: Base object for a MediaWiki page.

    This object only implements internally methods that do not require
    reading from or writing to the wiki.  All other methods are delegated
    to the Site object.

    Will be subclassed by Page and WikibasePage.

    """

    def __init__(self, source, title=u"", ns=0):
        """Instantiate a Page object.

        Three calling formats are supported:

          - If the first argument is a Page, create a copy of that object.
            This can be used to convert an existing Page into a subclass
            object, such as Category or FilePage.  (If the title is also
            given as the second argument, creates a copy with that title;
            this is used when pages are moved.)
          - If the first argument is a Site, create a Page on that Site
            using the second argument as the title (may include a section),
            and the third as the namespace number. The namespace number is
            mandatory, even if the title includes the namespace prefix. This
            is the preferred syntax when using an already-normalized title
            obtained from api.php or a database dump.  WARNING: may produce
            invalid objects if page title isn't in normal form!
          - If the first argument is a Link, create a Page from that link.
            This is the preferred syntax when using a title scraped from
            wikitext, URLs, or another non-normalized source.

        @param source: the source of the page
        @type source: Link, Page (or subclass), or Site
        @param title: normalized title of the page; required if source is a
            Site, ignored otherwise
        @type title: unicode
        @param ns: namespace number; required if source is a Site, ignored
            otherwise
        @type ns: int

        """
        if title is None:
            raise ValueError(u'Title cannot be None.')

        if isinstance(source, pywikibot.site.BaseSite):
            self._link = Link(title, source=source, defaultNamespace=ns)
            self._revisions = {}
        elif isinstance(source, Page):
            # copy all of source's attributes to this object
            # without overwriting non-None values
            self.__dict__.update((k, v) for k, v in source.__dict__.items()
                                 if k not in self.__dict__ or
                                 self.__dict__[k] is None)
            if title:
                # overwrite title
                self._link = Link(title, source=source.site,
                                  defaultNamespace=ns)
        elif isinstance(source, Link):
            self._link = source
            self._revisions = {}
        else:
            raise pywikibot.Error(
                "Invalid argument type '%s' in Page constructor: %s"
                % (type(source), source))

    @property
    def site(self):
        """Return the Site object for the wiki on which this Page resides."""
        return self._link.site

    def version(self):
        """Return MediaWiki version number of the page site.

        This is needed to use @need_version() decorator for methods of
        Page objects.
        """
        return self.site.version()

    @property
    def image_repository(self):
        """Return the Site object for the image repository."""
        return self.site.image_repository()

    @property
    def data_repository(self):
        """Return the Site object for the data repository."""
        return self.site.data_repository()

    def namespace(self):
        """Return the number of the namespace of the page.

        @return: namespace of the page
        @rtype: int
        """
        return self._link.namespace

    @property
    def content_model(self):
        """Return the content model for this page.

        If it cannot be reliably determined via the API,
        None is returned.
        """
        if not hasattr(self, '_contentmodel'):
            self.site.loadpageinfo(self)
        return self._contentmodel

    @deprecated_args(decode=None, savetitle="asUrl")
    def title(self, underscore=False, withNamespace=True,
              withSection=True, asUrl=False, asLink=False,
              allowInterwiki=True, forceInterwiki=False, textlink=False,
              as_filename=False, insite=None):
        """Return the title of this Page, as a Unicode string.

        @param underscore: (not used with asLink) if true, replace all ' '
            characters with '_'
        @param withNamespace: if false, omit the namespace prefix. If this
            option is false and used together with asLink return a labeled
            link like [[link|label]]
        @param withSection: if false, omit the section
        @param asUrl: (not used with asLink) if true, quote title as if in an
            URL
        @param asLink: if true, return the title in the form of a wikilink
        @param allowInterwiki: (only used if asLink is true) if true, format
            the link as an interwiki link if necessary
        @param forceInterwiki: (only used if asLink is true) if true, always
            format the link as an interwiki link
        @param textlink: (only used if asLink is true) if true, place a ':'
            before Category: and Image: links
        @param as_filename: (not used with asLink) if true, replace any
            characters that are unsafe in filenames
        @param insite: (only used if asLink is true) a site object where the
            title is to be shown. default is the current family/lang given by
            -family and -lang option i.e. config.family and config.mylang

        """
        title = self._link.canonical_title()
        label = self._link.title
        if withSection and self._link.section:
            section = u"#" + self._link.section
        else:
            section = u''
        if asLink:
            if insite:
                target_code = insite.code
                target_family = insite.family.name
            else:
                target_code = config.mylang
                target_family = config.family
            if forceInterwiki or \
               (allowInterwiki and
                (self.site.family.name != target_family or
                 self.site.code != target_code)):
                if self.site.family.name != target_family \
                   and self.site.family.name != self.site.code:
                    title = u'%s:%s:%s' % (self.site.family.name,
                                           self.site.code,
                                           title)
                else:
                    # use this form for sites like commons, where the
                    # code is the same as the family name
                    title = u'%s:%s' % (self.site.code, title)
            elif textlink and (self.isImage() or self.isCategory()):
                title = u':%s' % title
            elif self.namespace() == 0 and not section:
                withNamespace = True
            if withNamespace:
                return u'[[%s%s]]' % (title, section)
            else:
                return u'[[%s%s|%s]]' % (title, section, label)
        if not withNamespace and self.namespace() != 0:
            title = label + section
        else:
            title += section
        if underscore or asUrl:
            title = title.replace(u' ', u'_')
        if asUrl:
            encodedTitle = title.encode(self.site.encoding())
            title = quote_from_bytes(encodedTitle)
        if as_filename:
            # Replace characters that are not possible in file names on some
            # systems.
            # Spaces are possible on most systems, but are bad for URLs.
            for forbidden in ':*?/\\ ':
                title = title.replace(forbidden, '_')
        return title

    @remove_last_args(('decode', 'underscore'))
    def section(self):
        """Return the name of the section this Page refers to.

        The section is the part of the title following a '#' character, if
        any. If no section is present, return None.

        """
        return self._link.section

    def __unicode__(self):
        """Return a unicode string representation."""
        return self.title(asLink=True, forceInterwiki=True)

    def __repr__(self):
        """Return a more complete string representation."""
        title = self.title().encode(config.console_encoding)
        return str('{0}({1})').format(self.__class__.__name__, title)

    def _cmpkey(self):
        """
        Key for comparison of Page objects.

        Page objects are "equal" if and only if they are on the same site
        and have the same normalized title, including section if any.

        Page objects are sortable by site, namespace then title.
        """
        return (self.site, self.namespace(), self.title())

    def __hash__(self):
        """
        A stable identifier to be used as a key in hash-tables.

        This relies on the fact that the string
        representation of an instance can not change after the construction.
        """
        return hash(unicode(self))

    def full_url(self):
        """Return the full URL."""
        return self.site.base_url(self.site.nice_get_address(self.title(
            asUrl=True)))

    def autoFormat(self):
        """Return L{date.getAutoFormat} dictName and value, if any.

        Value can be a year, date, etc., and dictName is 'YearBC',
        'Year_December', or another dictionary name. Please note that two
        entries may have exactly the same autoFormat, but be in two
        different namespaces, as some sites have categories with the
        same names. Regular titles return (None, None).

        """
        if not hasattr(self, '_autoFormat'):
            from pywikibot import date
            self._autoFormat = date.getAutoFormat(
                self.site.code,
                self.title(withNamespace=False)
            )
        return self._autoFormat

    def isAutoTitle(self):
        """Return True if title of this Page is in the autoFormat dictionary."""
        return self.autoFormat()[0] is not None

    @deprecated_args(throttle=None, change_edit_time=None)
    def get(self, force=False, get_redirect=False, sysop=False):
        """Return the wiki-text of the page.

        This will retrieve the page from the server if it has not been
        retrieved yet, or if force is True. This can raise the following
        exceptions that should be caught by the calling code:

        @exception NoPage:         The page does not exist
        @exception IsRedirectPage: The page is a redirect. The argument of the
                                   exception is the title of the page it
                                   redirects to.
        @exception SectionError:   The section does not exist on a page with
                                   a # link

        @param force:           reload all page attributes, including errors.
        @param get_redirect:    return the redirect text, do not follow the
                                redirect, do not raise an exception.
        @param sysop:           if the user has a sysop account, use it to
                                retrieve this page

        """
        if force:
            # When forcing, we retry the page no matter what:
            # * Old exceptions do not apply any more
            # * Deleting _revid to force reload
            # * Deleting _redirtarget, that info is now obsolete.
            for attr in ['_redirtarget', '_getexception', '_revid']:
                if hasattr(self, attr):
                    delattr(self, attr)
        try:
            self._getInternals(sysop)
        except pywikibot.IsRedirectPage:
            if not get_redirect:
                raise

        return self.latest_revision.text

    def _latest_cached_revision(self):
        """Get the latest revision if cached and has text, otherwise None."""
        if (hasattr(self, '_revid') and self._revid in self._revisions and
                self._revisions[self._revid].text is not None):
            return self._revisions[self._revid]
        else:
            return None

    def _getInternals(self, sysop):
        """Helper function for get().

        Stores latest revision in self if it doesn't contain it, doesn't think.
        * Raises exceptions from previous runs.
        * Stores new exceptions in _getexception and raises them.

        """
        # Raise exceptions from previous runs
        if hasattr(self, '_getexception'):
            raise self._getexception

        # If not already stored, fetch revision
        if self._latest_cached_revision() is None:
            try:
                self.site.loadrevisions(self, getText=True, sysop=sysop)
            except (pywikibot.NoPage, pywikibot.SectionError) as e:
                self._getexception = e
                raise

        # self._isredir is set by loadrevisions
        if self._isredir:
            self._getexception = pywikibot.IsRedirectPage(self)
            raise self._getexception

    @deprecated_args(throttle=None, change_edit_time=None)
    def getOldVersion(self, oldid, force=False, get_redirect=False,
                      sysop=False):
        """Return text of an old revision of this page; same options as get().

        @param oldid: The revid of the revision desired.

        """
        if force or oldid not in self._revisions \
                or self._revisions[oldid].text is None:
            self.site.loadrevisions(self,
                                    getText=True,
                                    revids=oldid,
                                    sysop=sysop)
        # TODO: what about redirects, errors?
        return self._revisions[oldid].text

    def permalink(self, oldid=None):
        """Return the permalink URL of an old revision of this page.

        @param oldid: The revid of the revision desired.

        """
        return "//%s%s/index.php?title=%s&oldid=%s" \
               % (self.site.hostname(),
                  self.site.scriptpath(),
                  self.title(asUrl=True),
                  (oldid if oldid is not None else self.latest_revision_id))

    @property
    def latest_revision_id(self):
        """Return the current revision id for this page."""
        if not hasattr(self, '_revid'):
            self.revisions(self)
        return self._revid

    @deprecated('latest_revision_id')
    def latestRevision(self):
        """Return the current revision id for this page."""
        return self.latest_revision_id

    @deprecated('latest_revision_id')
    def pageAPInfo(self):
        """Return the current revision id for this page."""
        if self.isRedirectPage():
            raise pywikibot.IsRedirectPage(self)
        return self.latest_revision_id

    @property
    def latest_revision(self):
        """Return the current revision for this page."""
        rev = self._latest_cached_revision()
        if rev is not None:
            return rev
        return next(self.revisions(content=True, total=1))

    @property
    def text(self):
        """Return the current (edited) wikitext, loading it if necessary.

        @return: text of the page
        @rtype: unicode
        """
        if not hasattr(self, '_text') or self._text is None:
            try:
                self._text = self.get(get_redirect=True)
            except pywikibot.NoPage:
                # TODO: what other exceptions might be returned?
                self._text = u""
        return self._text

    @text.setter
    def text(self, value):
        """Update the current (edited) wikitext.

        @param value: New value or None
        @param value: basestring
        """
        self._text = None if value is None else unicode(value)

    @text.deleter
    def text(self):
        """Delete the current (edited) wikitext."""
        if hasattr(self, "_text"):
            del self._text

    def preloadText(self):
        """The text returned by EditFormPreloadText.

        See API module "info".

        Application: on Wikisource wikis, text can be preloaded even if
        a page does not exist, if an Index page is present.

        @return: unicode
        """
        self.site.loadpageinfo(self, preload=True)
        return self._preloadedtext

    def properties(self, force=False):
        """
        Return the properties of the page.

        @param force: force updating from the live site

        @return: dict
        """
        if not hasattr(self, '_pageprops') or force:
            self._pageprops = {}  # page may not have pageprops (see bug 54868)
            self.site.loadpageprops(self)
        return self._pageprops

    def defaultsort(self, force=False):
        """
        Extract value of the {{DEFAULTSORT:}} magic word from the page.

        @param force: force updating from the live site

        @return: unicode or None
        """
        return self.properties(force=force).get('defaultsort')

    @deprecate_arg('refresh', 'force')
    def expand_text(self, force=False, includecomments=False):
        """Return the page text with all templates and parser words expanded.

        @param force: force updating from the live site
        @param includecomments: Also strip comments if includecomments
            parameter is not True.

        @return: unicode or None
        """
        if not hasattr(self, '_expanded_text') or (
                self._expanded_text is None) or force:
            if not self.text:
                self._expanded_text = ''
                return ''

            self._expanded_text = self.site.expand_text(
                self.text,
                title=self.title(withSection=False),
                includecomments=includecomments)
        return self._expanded_text

    def userName(self):
        """Return name or IP address of last user to edit page.

        @return: unicode
        """
        return self.latest_revision.user

    def isIpEdit(self):
        """Return True if last editor was unregistered.

        @return: bool
        """
        return self.latest_revision.anon

    def lastNonBotUser(self):
        """Return name or IP address of last human/non-bot user to edit page.

        Determine the most recent human editor out of the last revisions.
        If it was not able to retrieve a human user, returns None.

        If the edit was done by a bot which is no longer flagged as 'bot',
        i.e. which is not returned by Site.botusers(), it will be returned
        as a non-bot edit.

        @return: unicode
        """
        if hasattr(self, '_lastNonBotUser'):
            return self._lastNonBotUser

        self._lastNonBotUser = None
        for entry in self.getVersionHistory():
            if entry.user and (not self.site.isBot(entry.user)):
                self._lastNonBotUser = entry.user
                break

        return self._lastNonBotUser

    @remove_last_args(('datetime', ))
    def editTime(self):
        """Return timestamp of last revision to page.

        @rtype: pywikibot.Timestamp
        """
        return self.latest_revision.timestamp

    @property
    def previous_revision_id(self):
        """Return the revision id for the previous revision of this Page.

        If the page has only one revision, it shall return -1.

        @return: long
        """
        history = self.getVersionHistory(total=2)

        if len(history) == 1:
            return -1
        else:
            return min(x.revid for x in history)

    @deprecated('previous_revision_id')
    def previousRevision(self):
        """
        Return the revision id for the previous revision.

        DEPRECATED: Use previous_revision_id instead.

        @return: long
        """
        return self.previous_revision_id

    def exists(self):
        """Return True if page exists on the wiki, even if it's a redirect.

        If the title includes a section, return False if this section isn't
        found.

        @return: bool
        """
        return self.site.page_exists(self)

    @property
    def oldest_revision(self):
        """
        Return the first revision of this page.

        @rtype: L{Revision}
        """
        return next(self.revisions(reverseOrder=True, total=1))

    def isRedirectPage(self):
        """Return True if this is a redirect, False if not or not existing."""
        return self.site.page_isredirect(self)

    def isStaticRedirect(self, force=False):
        """
        Determine whether the page is a static redirect.

        A static redirect must be a valid redirect, and contain the magic word
        __STATICREDIRECT__.

        @param force: Bypass local caching
        @type force: bool

        @return: bool
        """
        found = False
        if self.isRedirectPage():
            staticKeys = self.site.getmagicwords('staticredirect')
            text = self.get(get_redirect=True, force=force)
            if staticKeys:
                for key in staticKeys:
                    if key in text:
                        found = True
                        break
        return found

    def isCategoryRedirect(self):
        """Return True if this is a category redirect page, False otherwise.

        @return: bool
        """
        if not self.isCategory():
            return False
        if not hasattr(self, "_catredirect"):
            catredirs = self.site.category_redirects()
            for (template, args) in self.templatesWithParams():
                if template.title(withNamespace=False) in catredirs:
                    # Get target (first template argument)
                    try:
                        p = pywikibot.Page(self.site, args[0].strip(), ns=14)
                        if p.namespace() == 14:
                            self._catredirect = p.title()
                        else:
                            pywikibot.warning(
                                u"Target %s on %s is not a category"
                                % (p.title(asLink=True),
                                   self.title(asLink=True)))
                            self._catredirect = False
                    except IndexError:
                        pywikibot.warning(
                            u"No target for category redirect on %s"
                            % self.title(asLink=True))
                        self._catredirect = False
                    break
            else:
                self._catredirect = False
        return bool(self._catredirect)

    def getCategoryRedirectTarget(self):
        """If this is a category redirect, return the target category title.

        @return: Category
        """
        if self.isCategoryRedirect():
            return Category(Link(self._catredirect, self.site))
        raise pywikibot.IsNotRedirectPage(self)

    def isEmpty(self):
        """Return True if the page text has less than 4 characters.

        Character count ignores language links and category links.
        Can raise the same exceptions as get().

        @rtype: bool
        """
        txt = self.get()
        txt = textlib.removeLanguageLinks(txt, site=self.site)
        txt = textlib.removeCategoryLinks(txt, site=self.site)
        return len(txt) < 4

    def isTalkPage(self):
        """Return True if this page is in any talk namespace."""
        ns = self.namespace()
        return ns >= 0 and ns % 2 == 1

    def toggleTalkPage(self):
        """Return other member of the article-talk page pair for this Page.

        If self is a talk page, returns the associated content page;
        otherwise, returns the associated talk page.  The returned page need
        not actually exist on the wiki.

        @return: Page or None if self is a special page.
        """
        ns = self.namespace()
        if ns < 0:  # Special page
            return
        if self.isTalkPage():
            if self.namespace() == 1:
                return Page(self.site, self.title(withNamespace=False))
            else:
                return Page(self.site,
                            "%s:%s" % (self.site.namespace(ns - 1),
                                       self.title(withNamespace=False)))
        else:
            return Page(self.site,
                        "%s:%s" % (self.site.namespace(ns + 1),
                                   self.title(withNamespace=False)))

    def isCategory(self):
        """Return True if the page is a Category, False otherwise."""
        return self.namespace() == 14

    def isImage(self):
        """Return True if this is an image description page, False otherwise."""
        return self.namespace() == 6

    def isDisambig(self, get_Index=True):
        """Return True if this is a disambiguation page, False otherwise.

        Relies on the presence of specific templates, identified in
        the Family file or on a wiki page, to identify disambiguation
        pages.

        By default, loads a list of template names from the Family file;
        if the value in the Family file is None no entry was made, looks for
        the list on [[MediaWiki:Disambiguationspage]]. If this page does not
        exist, take the MediaWiki message.

        If get_Index is True then also load the templates for index articles
        which are given on en-wiki

        'Template:Disambig' is always assumed to be default, and will be
        appended regardless of its existence.

        @rtype: bool
        """
        if self.site.has_extension('Disambiguator'):
            # If the Disambiguator extension is loaded, use it
            return 'disambiguation' in self.properties()

        if not hasattr(self.site, "_disambigtemplates"):
            try:
                default = set(self.site.family.disambig('_default'))
            except KeyError:
                default = set([u'Disambig'])
            try:
                distl = self.site.family.disambig(self.site.code,
                                                  fallback=False)
            except KeyError:
                distl = None
            if distl is None:
                disambigpages = Page(self.site,
                                     "MediaWiki:Disambiguationspage")
                indexes = set()
                if disambigpages.exists():
                    disambigs = set(link.title(withNamespace=False)
                                    for link in disambigpages.linkedPages()
                                    if link.namespace() == 10)
                    # cache index article templates separately
                    if self.site.sitename() == 'wikipedia:en':
                        regex = re.compile('\(\((.+?)\)\)')
                        content = disambigpages.get()
                        for index in regex.findall(content):
                            indexes.add(first_upper(index))
                        self.site._indextemplates = indexes
                else:
                    message = self.site.mediawiki_message(
                        'disambiguationspage').split(':', 1)[1]
                    # add the default template(s) for default mw message
                    # only
                    disambigs = set([first_upper(message)]) | default
                self.site._disambigtemplates = disambigs
            else:
                # Normalize template capitalization
                self.site._disambigtemplates = set(
                    first_upper(t) for t in distl
                )
        templates = set(tl.title(withNamespace=False)
                        for tl in self.templates())
        disambigs = set()
        # always use cached disambig templates
        disambigs.update(self.site._disambigtemplates)
        # if get_Index is True, also use cached index templates
        if get_Index and hasattr(self.site, '_indextemplates'):
            disambigs.update(self.site._indextemplates)
        # see if any template on this page is in the set of disambigs
        disambigInPage = disambigs.intersection(templates)
        return self.namespace() != 10 and len(disambigInPage) > 0

    def getReferences(self, follow_redirects=True, withTemplateInclusion=True,
                      onlyTemplateInclusion=False, redirectsOnly=False,
                      namespaces=None, step=None, total=None, content=False):
        """Return an iterator all pages that refer to or embed the page.

        If you need a full list of referring pages, use
        C{pages = list(s.getReferences())}

        @param follow_redirects: if True, also iterate pages that link to a
            redirect pointing to the page.
        @param withTemplateInclusion: if True, also iterate pages where self
            is used as a template.
        @param onlyTemplateInclusion: if True, only iterate pages where self
            is used as a template.
        @param redirectsOnly: if True, only iterate redirects to self.
        @param namespaces: only iterate pages in these namespaces
        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each referring page (default False)

        """
        # N.B.: this method intentionally overlaps with backlinks() and
        # embeddedin(). Depending on the interface, it may be more efficient
        # to implement those methods in the site interface and then combine
        # the results for this method, or to implement this method and then
        # split up the results for the others.
        return self.site.pagereferences(
            self,
            followRedirects=follow_redirects,
            filterRedirects=redirectsOnly,
            withTemplateInclusion=withTemplateInclusion,
            onlyTemplateInclusion=onlyTemplateInclusion,
            namespaces=namespaces,
            step=step,
            total=total,
            content=content
        )

    def backlinks(self, followRedirects=True, filterRedirects=None,
                  namespaces=None, step=None, total=None, content=False):
        """Return an iterator for pages that link to this page.

        @param followRedirects: if True, also iterate pages that link to a
            redirect pointing to the page.
        @param filterRedirects: if True, only iterate redirects; if False,
            omit redirects; if None, do not filter
        @param namespaces: only iterate pages in these namespaces
        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each referring page (default False)

        """
        return self.site.pagebacklinks(
            self,
            followRedirects=followRedirects,
            filterRedirects=filterRedirects,
            namespaces=namespaces,
            step=step,
            total=total,
            content=content
        )

    def embeddedin(self, filter_redirects=None, namespaces=None, step=None,
                   total=None, content=False):
        """Return an iterator for pages that embed this page as a template.

        @param filter_redirects: if True, only iterate redirects; if False,
            omit redirects; if None, do not filter
        @param namespaces: only iterate pages in these namespaces
        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each embedding page (default False)

        """
        return self.site.page_embeddedin(
            self,
            filterRedirects=filter_redirects,
            namespaces=namespaces,
            step=step,
            total=total,
            content=content
        )

    def protection(self):
        """Return a dictionary reflecting page protections.

        @return: dict
        """
        return self.site.page_restrictions(self)

    def applicable_protections(self):
        """
        Return the protection types allowed for that page.

        If the page doesn't exists it only returns "create". Otherwise it
        returns all protection types provided by the site, except "create".
        It also removes "upload" if that page is not in the File namespace.

        It is possible, that it returns an empty set, but only if original
        protection types were removed.

        @return: set of unicode
        """
        # Currently hard coded, but a future API update might allow us to
        # properly determine the applicable protection types
        p_types = set(self.site.protection_types())
        if not self.exists():
            return set(['create']) if 'create' in p_types else set()
        else:
            p_types.remove('create')  # no existing page allows that
            if not self.isImage():  # only file pages allow upload
                p_types.remove('upload')
            return p_types

    def canBeEdited(self):
        """Determine whether the page may be edited.

        This returns True if and only if:
          - page is unprotected, and bot has an account for this site, or
          - page is protected, and bot has a sysop account for this site.

        @return: bool
        """
        return self.site.page_can_be_edited(self)

    def botMayEdit(self):
        """Determine whether the active bot is allowed to edit the page.

        This will be True if the page doesn't contain {{bots}} or
        {{nobots}}, or it contains them and the active bot is allowed to
        edit this page. (This method is only useful on those sites that
        recognize the bot-exclusion protocol; on other sites, it will always
        return True.)

        The framework enforces this restriction by default. It is possible
        to override this by setting ignore_bot_templates=True in
        user-config.py, or using page.put(force=True).

        @return: bool
        """
        # TODO: move this to Site object?
        if config.ignore_bot_templates:  # Check the "master ignore switch"
            return True
        username = self.site.user()
        try:
            templates = self.templatesWithParams()
        except (pywikibot.NoPage,
                pywikibot.IsRedirectPage,
                pywikibot.SectionError):
            return True

        # go through all templates and look for any restriction
        # multiple bots/nobots templates are allowed
        for template in templates:
            title = template[0].title(withNamespace=False)
            if title == 'Nobots':
                if len(template[1]) == 0:
                    return False
                else:
                    bots = template[1][0].split(',')
                    if 'all' in bots or pywikibot.calledModuleName() in bots \
                       or username in bots:
                        return False
            elif title == 'Bots':
                if len(template[1]) == 0:
                    return True
                else:
                    (ttype, bots) = template[1][0].split('=', 1)
                    bots = bots.split(',')
                    if ttype == 'allow':
                        return 'all' in bots or username in bots
                    if ttype == 'deny':
                        return not ('all' in bots or username in bots)
                    if ttype == 'allowscript':
                        return 'all' in bots or pywikibot.calledModuleName() in bots
                    if ttype == 'denyscript':
                        return not ('all' in bots or pywikibot.calledModuleName() in bots)
        # no restricting template found
        return True

    @deprecated_args(comment='summary', sysop=None)
    def save(self, summary=None, watch=None, minor=True, botflag=None,
             force=False, async=False, callback=None,
             apply_cosmetic_changes=None, **kwargs):
        """Save the current contents of page's text to the wiki.

        @param summary: The edit summary for the modification (optional, but
            most wikis strongly encourage its use)
        @type summary: unicode
        @param watch: Specify how the watchlist is affected by this edit, set
            to one of "watch", "unwatch", "preferences", "nochange":
            * watch: add the page to the watchlist
            * unwatch: remove the page from the watchlist
            * preferences: use the preference settings (Default)
            * nochange: don't change the watchlist
            If None (default), follow bot account's default settings

            For backward compatibility watch parameter may also be boolean:
            if True, add or if False, remove this Page to/from bot
            user's watchlist.
        @type watch: string, bool (deprecated) or None
        @param minor: if True, mark this edit as minor
        @type minor: bool
        @param botflag: if True, mark this edit as made by a bot (default:
            True if user has bot status, False if not)
        @param force: if True, ignore botMayEdit() setting
        @type force: bool
        @param async: if True, launch a separate thread to save
            asynchronously
        @param callback: a callable object that will be called after the
            page put operation. This object must take two arguments: (1) a
            Page object, and (2) an exception instance, which will be None
            if the page was saved successfully. The callback is intended for
            use by bots that need to keep track of which saves were
            successful.
        @param apply_cosmetic_changes: Overwrites the cosmetic_changes
            configuration value to this value unless it's None.
        @type apply_cosmetic_changes: bool or None
        """
        if not summary:
            summary = config.default_edit_summary
        if watch is True:
            watch = 'watch'
        elif watch is False:
            watch = 'unwatch'
        if not force and not self.botMayEdit():
            raise pywikibot.OtherPageSaveError(
                self, "Editing restricted by {{bots}} template")
        if async:
            pywikibot.async_request(self._save, summary=summary, minor=minor,
                                    watch=watch, botflag=botflag,
                                    async=async, callback=callback,
                                    cc=apply_cosmetic_changes, **kwargs)
        else:
            self._save(summary=summary, minor=minor, watch=watch,
                       botflag=botflag, async=async, callback=callback,
                       cc=apply_cosmetic_changes, **kwargs)

    def _save(self, summary, minor, watch, botflag, async, callback,
              cc, **kwargs):
        """Helper function for save()."""
        err = None
        link = self.title(asLink=True)
        if cc or cc is None and config.cosmetic_changes:
            summary = self._cosmetic_changes_hook(summary) or summary
        try:
            done = self.site.editpage(self, summary=summary, minor=minor,
                                      watch=watch, bot=botflag, **kwargs)
            if not done:
                pywikibot.warning(u"Page %s not saved" % link)
                raise pywikibot.PageNotSaved(self)
            else:
                pywikibot.output(u"Page %s saved" % link)
        # TODO: other "expected" error types to catch?
        except pywikibot.Error as edit_err:
            err = edit_err  # edit_err will be deleted in the end of the scope
            pywikibot.log(u"Error saving page %s (%s)\n" % (link, err),
                          exc_info=True)
            if not callback and not async:
                if isinstance(err, pywikibot.PageSaveRelatedError):
                    raise err
                raise pywikibot.OtherPageSaveError(self, err)
        if callback:
            callback(self, err)

    def _cosmetic_changes_hook(self, comment):
        if self.isTalkPage() or \
           pywikibot.calledModuleName() in config.cosmetic_changes_deny_script:
            return
        family = self.site.family.name
        config.cosmetic_changes_disable.update({'wikidata': ('repo', )})
        if config.cosmetic_changes_mylang_only:
            cc = ((family == config.family and
                   self.site.lang == config.mylang) or
                  family in list(config.cosmetic_changes_enable.keys()) and
                  self.site.lang in config.cosmetic_changes_enable[family])
        else:
            cc = True
        cc = (cc and not
              (family in list(config.cosmetic_changes_disable.keys()) and
               self.site.lang in config.cosmetic_changes_disable[family]))
        if not cc:
            return

        # cc depends on page directly and via several other imports
        from pywikibot.cosmetic_changes import CosmeticChangesToolkit  # noqa
        old = self.text
        pywikibot.log(u'Cosmetic changes for %s-%s enabled.'
                      % (family, self.site.lang))
        ccToolkit = CosmeticChangesToolkit(self.site,
                                           redirect=self.isRedirectPage(),
                                           namespace=self.namespace(),
                                           pageTitle=self.title(),
                                           ignore=3)  # CANCEL_MATCH
        self.text = ccToolkit.change(old)
        if comment and \
           old.strip().replace('\r\n',
                               '\n') != self.text.strip().replace('\r\n', '\n'):
            from pywikibot import i18n
            comment += i18n.twtranslate(self.site, 'cosmetic_changes-append')
            return comment

    @deprecated_args(comment='summary')
    def put(self, newtext, summary=u'', watchArticle=None, minorEdit=True,
            botflag=None, force=False, async=False, callback=None, **kwargs):
        """Save the page with the contents of the first argument as the text.

        This method is maintained primarily for backwards-compatibility.
        For new code, using Page.save() is preferred.  See save() method
        docs for all parameters not listed here.

        @param newtext: The complete text of the revised page.
        @type newtext: unicode

        """
        self.text = newtext
        self.save(summary=summary, watch=watchArticle, minor=minorEdit,
                  botflag=botflag, force=force, async=async, callback=callback,
                  **kwargs)

    @deprecated_args(comment='summary')
    def put_async(self, newtext, summary=u'', watchArticle=None,
                  minorEdit=True, botflag=None, force=False, callback=None,
                  **kwargs):
        """Put page on queue to be saved to wiki asynchronously.

        Asynchronous version of put (takes the same arguments), which places
        pages on a queue to be saved by a daemon thread. All arguments are
        the same as for .put().  This version is maintained solely for
        backwards-compatibility.

        """
        self.put(newtext, summary=summary, watchArticle=watchArticle,
                 minorEdit=minorEdit, botflag=botflag, force=force, async=True,
                 callback=callback, **kwargs)

    def watch(self, unwatch=False):
        """Add or remove this page to/from bot account's watchlist.

        @param unwatch: True to unwatch, False (default) to watch.
        @type unwatch: bool

        @return: bool; True if successful, False otherwise.
        """
        return self.site.watchpage(self, unwatch)

    def purge(self, **kwargs):
        """Purge the server's cache for this page.

        @return: bool
        """
        return self.site.purgepages([self], **kwargs)

    def touch(self, callback=None, **kwargs):
        """Make a touch edit for this page.

        See save() method docs for all parameters.
        The following parameters will be overridden by this method:
        summary, watch, minor, botflag, force, async

        minor and botflag parameters are set to False which prevents hiding
        the edit when it becomes a real edit due to a bug.
        """
        if self.exists():
            # ensure always get the page text and not to change it.
            del self.text
            self.save(summary='Pywikibot touch edit', watch='nochange',
                      minor=False, botflag=False, force=True, async=False,
                      callback=callback, apply_cosmetic_changes=False,
                      **kwargs)
        else:
            raise pywikibot.NoPage(self)

    def linkedPages(self, namespaces=None, step=None, total=None,
                    content=False):
        """Iterate Pages that this Page links to.

        Only returns pages from "normal" internal links. Image and category
        links are omitted unless prefixed with ":". Embedded templates are
        omitted (but links within them are returned). All interwiki and
        external links are omitted.

        @param namespaces: only iterate links in these namespaces
        @param namespaces: int, or list of ints
        @param step: limit each API call to this number of pages
        @type step: int
        @param total: iterate no more than this number of pages in total
        @type total: int
        @param content: if True, retrieve the content of the current version
            of each linked page (default False)
        @type content: bool

        @return: a generator that yields Page objects.
        """
        return self.site.pagelinks(self, namespaces=namespaces, step=step,
                                   total=total, content=content)

    def interwiki(self, expand=True):
        """Iterate interwiki links in the page text, excluding language links.

        @param expand: if True (default), include interwiki links found in
            templates transcluded onto this page; if False, only iterate
            interwiki links found in this page's own wikitext
        @type expand: bool

        @return: a generator that yields Link objects
        """
        # This function does not exist in the API, so it has to be
        # implemented by screen-scraping
        if expand:
            text = self.expand_text()
        else:
            text = self.text
        for linkmatch in pywikibot.link_regex.finditer(
                textlib.removeDisabledParts(text)):
            linktitle = linkmatch.group("title")
            link = Link(linktitle, self.site)
            # only yield links that are to a different site and that
            # are not language links
            try:
                if link.site != self.site:
                    if linktitle.lstrip().startswith(":"):
                        # initial ":" indicates not a language link
                        yield link
                    elif link.site.family != self.site.family:
                        # link to a different family is not a language link
                        yield link
            except pywikibot.Error:
                # ignore any links with invalid contents
                continue

    def langlinks(self, include_obsolete=False):
        """Return a list of all inter-language Links on this page.

        @param include_obsolete: if true, return even Link objects whose site
                                 is obsolete
        @type include_obsolete: bool

        @return: list of Link objects.
        """
        # Note: We preload a list of *all* langlinks, including links to
        # obsolete sites, and store that in self._langlinks. We then filter
        # this list if the method was called with include_obsolete=False
        # (which is the default)
        if not hasattr(self, '_langlinks'):
            self._langlinks = list(self.iterlanglinks(include_obsolete=True))

        if include_obsolete:
            return self._langlinks
        else:
            return [i for i in self._langlinks if not i.site.obsolete]

    def iterlanglinks(self, step=None, total=None, include_obsolete=False):
        """Iterate all inter-language links on this page.

        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param include_obsolete: if true, yield even Link object whose site
                                 is obsolete
        @type include_obsolete: bool

        @return: a generator that yields Link objects.
        """
        if hasattr(self, '_langlinks'):
            return iter(self.langlinks(include_obsolete=include_obsolete))
        # XXX We might want to fill _langlinks when the Site
        # method is called. If we do this, we'll have to think
        # about what will happen if the generator is not completely
        # iterated upon.
        return self.site.pagelanglinks(self, step=step, total=total,
                                       include_obsolete=include_obsolete)

    def data_item(self):
        """
        Convenience function to get the Wikibase item of a page.

        @return: ItemPage
        """
        return ItemPage.fromPage(self)

    @deprecate_arg('tllimit', None)
    @deprecated("Page.templates()")
    def getTemplates(self):
        """DEPRECATED. Use templates()."""
        return self.templates()

    def templates(self, content=False):
        """Return a list of Page objects for templates used on this Page.

        Template parameters are ignored.  This method only returns embedded
        templates, not template pages that happen to be referenced through
        a normal link.

        @param content: if True, retrieve the content of the current version
            of each template (default False)
        @param content: bool
        """
        # Data might have been preloaded
        if not hasattr(self, '_templates'):
            self._templates = list(self.itertemplates(content=content))

        return self._templates

    def itertemplates(self, step=None, total=None, content=False):
        """Iterate Page objects for templates used on this Page.

        Template parameters are ignored.  This method only returns embedded
        templates, not template pages that happen to be referenced through
        a normal link.

        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each template (default False)
        @param content: bool

        """
        if hasattr(self, '_templates'):
            return iter(self._templates)
        return self.site.pagetemplates(self, step=step, total=total,
                                       content=content)

    @deprecated_args(followRedirects=None, loose=None)
    def imagelinks(self, step=None, total=None, content=False):
        """Iterate FilePage objects for images displayed on this Page.

        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each image description page (default False)
        @return: a generator that yields FilePage objects.

        """
        return self.site.pageimages(self, step=step, total=total,
                                    content=content)

    @deprecated_args(nofollow_redirects=None, get_redirect=None)
    def categories(self, withSortKey=False, step=None, total=None,
                   content=False):
        """Iterate categories that the article is in.

        @param withSortKey: if True, include the sort key in each Category.
        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, retrieve the content of the current version
            of each category description page (default False)
        @return: a generator that yields Category objects.

        """
        # FIXME: bug 73561: withSortKey is ignored by Site.pagecategories
        if withSortKey:
            raise NotImplementedError('withSortKey is not implemented')

        return self.site.pagecategories(self, step=step, total=total,
                                        content=content)

    def extlinks(self, step=None, total=None):
        """Iterate all external URLs (not interwiki links) from this page.

        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @return: a generator that yields unicode objects containing URLs.

        """
        return self.site.page_extlinks(self, step=step, total=total)

    def coordinates(self, primary_only=False):
        """Return a list of Coordinate objects for points on the page.

        Uses the MediaWiki extension GeoData.

        @param primary_only: Only return the coordinate indicated to be primary
        @return: A list of Coordinate objects
        """
        if not hasattr(self, '_coords'):
            self._coords = []
            self.site.loadcoordinfo(self)
        if primary_only:
            return self._coords[0] if len(self._coords) > 0 else None
        else:
            return self._coords

    def getRedirectTarget(self):
        """Return a Page object for the target this Page redirects to.

        If this page is not a redirect page, will raise an IsNotRedirectPage
        exception. This method also can raise a NoPage exception.

        @return: Page
        """
        return self.site.getredirtarget(self)

    @deprecated_args(getText='content', reverseOrder='reverse')
    def revisions(self, reverse=False, step=None, total=None, content=False,
                  rollback=False):
        """Generator which loads the version history as Revision instances."""
        # TODO: Only request uncached revisions
        self.site.loadrevisions(self, getText=content, rvdir=reverse,
                                step=step, total=total, rollback=rollback)
        return (self._revisions[rev] for rev in
                sorted(self._revisions, reverse=not reverse)[:total])

    # BREAKING CHANGE: in old framework, default value for getVersionHistory
    #                  returned no more than 500 revisions; now, it iterates
    #                  all revisions unless 'total' argument is used
    @deprecated('Page.revisions()')
    @deprecated_args(forceReload=None, revCount='total', getAll=None,
                     reverseOrder='reverse')
    def getVersionHistory(self, reverse=False, step=None, total=None):
        """Load the version history page and return history information.

        Return value is a list of tuples, where each tuple represents one
        edit and is built of revision id, edit date/time, user name, and
        edit summary. Starts with the most current revision, unless
        reverse is True.

        @param step: limit each API call to this number of revisions
        @param total: iterate no more than this number of revisions in total

        """
        return [rev.hist_entry()
                for rev in self.revisions(reverse=reverse,
                                          step=step, total=total)
                ]

    @deprecated_args(forceReload=None, reverseOrder='reverse')
    def getVersionHistoryTable(self, reverse=False, step=None, total=None):
        """Return the version history as a wiki table."""
        result = '{| class="wikitable"\n'
        result += '! oldid || date/time || username || edit summary\n'
        for entry in self.revisions(reverse=reverse, step=step, total=total):
            result += '|----\n'
            result += ('| {r.revid} || {r.timestamp} || {r.user} || '
                       '<nowiki>{r.comment}</nowiki>\n'.format(r=entry))
        result += '|}\n'
        return result

    @deprecated("Page.revisions(content=True)")
    @deprecated_args(reverseOrder='reverse', rollback=None)
    def fullVersionHistory(self, reverse=False, step=None, total=None):
        """Iterate previous versions including wikitext.

        Takes same arguments as getVersionHistory.

        """
        return [rev.full_hist_entry()
                for rev in self.revisions(content=True, reverse=reverse,
                                          step=step, total=total)
                ]

    def contributors(self, step=None, total=None):
        """
        Compile contributors of this page with edit counts.

        @param step: limit each API call to this number of revisions
        @param total: iterate no more than this number of revisions in total

        @return: number of edits for each username
        @rtype: L{collections.Counter}
        """
        return Counter(rev.user for rev in
                       self.revisions(step=step, total=total))

    @deprecated('contributors()')
    def contributingUsers(self, step=None, total=None):
        """Return a set of usernames (or IPs) of users who edited this page.

        @param step: limit each API call to this number of revisions
        @param total: iterate no more than this number of revisions in total

        """
        return self.contributors(step=step, total=total).keys()

    def revision_count(self, contributors=None):
        """
        Determine number of edits from a set of contributors.

        @param contributors: contributor usernames
        @type contributors: iterable of str

        @return: number of edits for all provided usernames
        @rtype: int
        """
        if not contributors:
            return len(list(self.revisions()))

        cnt = self.contributors()
        return sum(cnt[username] for username in contributors)

    @deprecated('oldest_revision')
    def getCreator(self):
        """Get the first revision of the page.

        DEPRECATED: Use Page.oldest_revision.

        @rtype: tuple(username, Timestamp)
        """
        result = self.oldest_revision
        return result.user, result.timestamp

    @deprecated('contributors() or revisions()')
    @deprecated_args(limit="total")
    def getLatestEditors(self, total=1):
        """Get a list of revision informations of the last total edits.

        DEPRECATED: Use Page.revisions.

        @param total: iterate no more than this number of revisions in total
        @rtype: list of dict, each dict containing the username and Timestamp
        """
        return [{'user': rev.user, 'timestamp': rev.timestamp}
                for rev in self.revisions(total=total)]

    @deprecate_arg("throttle", None)
    def move(self, newtitle, reason=None, movetalkpage=True, sysop=False,
             deleteAndMove=False, safe=True):
        """Move this page to a new title.

        @param newtitle: The new page title.
        @param reason: The edit summary for the move.
        @param movetalkpage: If true, move this page's talk page (if it exists)
        @param sysop: Try to move using sysop account, if available
        @param deleteAndMove: if move succeeds, delete the old page
            (usually requires sysop privileges, depending on wiki settings)
        @param safe: If false, attempt to delete existing page at newtitle
            (if there is one) and then move this page to that title

        """
        if reason is None:
            pywikibot.output(u'Moving %s to [[%s]].'
                             % (self.title(asLink=True), newtitle))
            reason = pywikibot.input(u'Please enter a reason for the move:')
        # TODO: implement "safe" parameter (Is this necessary ?)
        # TODO: implement "sysop" parameter
        return self.site.movepage(self, newtitle, reason,
                                  movetalk=movetalkpage,
                                  noredirect=deleteAndMove)

    @deprecate_arg("throttle", None)
    def delete(self, reason=None, prompt=True, mark=False):
        """Delete the page from the wiki. Requires administrator status.

        @param reason: The edit summary for the deletion, or rationale
            for deletion if requesting. If None, ask for it.
        @param prompt: If true, prompt user for confirmation before deleting.
        @param mark: If true, and user does not have sysop rights, place a
            speedy-deletion request on the page instead. If false, non-sysops
            will be asked before marking pages for deletion.

        """
        if reason is None:
            pywikibot.output(u'Deleting %s.' % (self.title(asLink=True)))
            reason = pywikibot.input(u'Please enter a reason for the deletion:')

        # If user is a sysop, delete the page
        if self.site.username(sysop=True):
            answer = u'y'
            if prompt and not hasattr(self.site, '_noDeletePrompt'):
                answer = pywikibot.input_choice(
                    u'Do you want to delete %s?' % self.title(
                        asLink=True, forceInterwiki=True),
                    [('Yes', 'y'), ('No', 'n'), ('All', 'a')],
                    'n', automatic_quit=False)
                if answer == 'a':
                    answer = 'y'
                    self.site._noDeletePrompt = True
            if answer == 'y':
                return self.site.deletepage(self, reason)
        else:  # Otherwise mark it for deletion
            if mark or hasattr(self.site, '_noMarkDeletePrompt'):
                answer = 'y'
            else:
                answer = pywikibot.input_choice(
                    u"Can't delete %s; do you want to mark it "
                    "for deletion instead?" % self.title(asLink=True,
                                                         forceInterwiki=True),
                    [('Yes', 'y'), ('No', 'n'), ('All', 'a')],
                    'n', automatic_quit=False)
                if answer == 'a':
                    answer = 'y'
                    self.site._noMarkDeletePrompt = True
            if answer == 'y':
                template = '{{delete|1=%s}}\n' % reason
                self.text = template + self.text
                return self.save(summary=reason)

    def loadDeletedRevisions(self, step=None, total=None):
        """Retrieve deleted revisions for this Page.

        Stores all revisions' timestamps, dates, editors and comments in
        self._deletedRevs attribute.

        @return: iterator of timestamps (which can be used to retrieve
            revisions later on).

        """
        if not hasattr(self, "_deletedRevs"):
            self._deletedRevs = {}
        for item in self.site.deletedrevs(self, step=step, total=total):
            for rev in item.get("revisions", []):
                self._deletedRevs[rev['timestamp']] = rev
                yield rev['timestamp']

    def getDeletedRevision(self, timestamp, retrieveText=False):
        """Return a particular deleted revision by timestamp.

        @return: a list of [date, editor, comment, text, restoration
            marker]. text will be None, unless retrieveText is True (or has
            been retrieved earlier). If timestamp is not found, returns
            None.

        """
        if hasattr(self, "_deletedRevs"):
            if timestamp in self._deletedRevs and (
                    (not retrieveText) or
                    'content' in self._deletedRevs[timestamp]):
                return self._deletedRevs[timestamp]
        for item in self.site.deletedrevs(self, start=timestamp,
                                          get_text=retrieveText, total=1):
            # should only be one item with one revision
            if item['title'] == self.title:
                if "revisions" in item:
                    return item["revisions"][0]

    def markDeletedRevision(self, timestamp, undelete=True):
        """Mark the revision identified by timestamp for undeletion.

        @param undelete: if False, mark the revision to remain deleted.
        @type undelete: bool
        """
        if not hasattr(self, "_deletedRevs"):
            self.loadDeletedRevisions()
        if timestamp not in self._deletedRevs:
            raise ValueError(u'Timestamp %d is not a deleted revision' % timestamp)
        self._deletedRevs[timestamp]['marked'] = undelete

    @deprecate_arg('throttle', None)
    @deprecate_arg('comment', 'reason')
    def undelete(self, reason=None):
        """Undelete revisions based on the markers set by previous calls.

        If no calls have been made since loadDeletedRevisions(), everything
        will be restored.

        Simplest case::

            Page(...).undelete('This will restore all revisions')

        More complex::

            pg = Page(...)
            revs = pg.loadDeletedRevisions()
            for rev in revs:
                if ... #decide whether to undelete a revision
                    pg.markDeletedRevision(rev) #mark for undeletion
            pg.undelete('This will restore only selected revisions.')

        @param reason: Reason for the action.
        @type reason: basestring

        """
        if hasattr(self, "_deletedRevs"):
            undelete_revs = [ts for ts, rev in self._deletedRevs.items()
                             if 'marked' in rev and rev['marked']]
        else:
            undelete_revs = []
        if reason is None:
            warn('Not passing a reason for undelete() is deprecated.',
                 DeprecationWarning)
            pywikibot.output(u'Undeleting %s.' % (self.title(asLink=True)))
            reason = pywikibot.input(u'Please enter a reason for the undeletion:')
        self.site.undelete_page(self, reason, undelete_revs)

    @deprecate_arg("throttle", None)
    def protect(self, edit=False, move=False, create=None, upload=None,
                unprotect=False, reason=None, prompt=None, protections=None,
                **kwargs):
        """(Un)protect a wiki page. Requires administrator status.

        Valid protection levels (in MediaWiki 1.12) are '' (equivalent to
        'none'), 'autoconfirmed', and 'sysop'. If None is given, however,
        that protection will be skipped.

        @param protections: A dict mapping type of protection to protection
            level of that type.
        @type  protections: dict
        @param reason: Reason for the action
        @type  reason: basestring
        @param prompt: Whether to ask user for confirmation (deprecated).
                       Defaults to protections is None
        @type  prompt: bool
        """
        def process_deprecated_arg(value, arg_name):
            # if protections was set and value is None, don't interpret that
            # argument. But otherwise warn that the parameter was set
            # (even implicit)
            if called_using_deprecated_arg:
                if value is False:  # explicit test for False (don't use not)
                    value = "sysop"
                if value == "none":  # 'none' doesn't seem do be accepted
                    value = ""
                if value is not None:  # empty string is allowed
                    protections[arg_name] = value
                    warn(u'"protections" argument of protect() replaces "{0}"'
                         .format(arg_name),
                         DeprecationWarning)
            else:
                if value:
                    warn(u'"protections" argument of protect() replaces "{0}";'
                         u' cannot use both.'.format(arg_name),
                         RuntimeWarning)

        # buffer that, because it might get changed
        called_using_deprecated_arg = protections is None
        if called_using_deprecated_arg:
            protections = {}
        process_deprecated_arg(edit, "edit")
        process_deprecated_arg(move, "move")
        process_deprecated_arg(create, "create")
        process_deprecated_arg(upload, "upload")

        if reason is None:
            pywikibot.output(u'Preparing to protection change of %s.'
                             % (self.title(asLink=True)))
            reason = pywikibot.input(u'Please enter a reason for the action:')
        if unprotect:
            warn(u'"unprotect" argument of protect() is deprecated',
                 DeprecationWarning, 2)
            protections = dict(
                [(p_type, "") for p_type in self.applicable_protections()])
        answer = 'y'
        if called_using_deprecated_arg and prompt is None:
            prompt = True
        if prompt:
            warn(u'"prompt" argument of protect() is deprecated',
                 DeprecationWarning, 2)
        if prompt and not hasattr(self.site, '_noProtectPrompt'):
            answer = pywikibot.input_choice(
                u'Do you want to change the protection level of %s?'
                % self.title(asLink=True, forceInterwiki=True),
                [('Yes', 'y'), ('No', 'n'), ('All', 'a')],
                'n', automatic_quit=False)
            if answer == 'a':
                answer = 'y'
                self.site._noProtectPrompt = True
        if answer == 'y':
            return self.site.protect(self, protections, reason, **kwargs)

    def change_category(self, oldCat, newCat, comment=None, sortKey=None,
                        inPlace=True, include=[]):
        """
        Remove page from oldCat and add it to newCat.

        @param oldCat: category to be removed
        @type oldCat: Category
        @param newCat: category to be added, if any
        @type newCat: Category or None

        @param comment: string to use as an edit summary

        @param sortKey: sortKey to use for the added category.
            Unused if newCat is None, or if inPlace=True
            If sortKey=True, the sortKey used for oldCat will be used.

        @param inPlace: if True, change categories in place rather than
                      rearranging them.

        @param include: list of tags not to be disabled by default in relevant
            textlib functions, where CategoryLinks can be searched.
        @type include: list

        @return: True if page was saved changed, otherwise False.
        @rtype: bool

        """
        # get list of Category objects the article is in and remove possible
        # duplicates
        cats = []
        for cat in textlib.getCategoryLinks(self.text, site=self.site,
                                            include=include):
            if cat not in cats:
                cats.append(cat)

        if not self.canBeEdited():
            pywikibot.output(u"Can't edit %s, skipping it..."
                             % self.title(asLink=True))
            return False

        if oldCat not in cats:
            pywikibot.error(u'%s is not in category %s!'
                            % (self.title(asLink=True), oldCat.title()))
            return False

        # This prevents the bot from adding newCat if it is already present.
        if newCat in cats:
            newCat = None

        oldtext = self.text
        if inPlace or self.namespace() == 10:
            newtext = textlib.replaceCategoryInPlace(oldtext, oldCat, newCat,
                                                     site=self.site)
        else:
            old_cat_pos = cats.index(oldCat)
            if newCat:
                if sortKey is True:
                    # Fetch sortKey from oldCat in current page.
                    sortKey = cats[old_cat_pos].sortKey
                cats[old_cat_pos] = Category(self.site, newCat.title(),
                                             sortKey=sortKey)
            else:
                cats.pop(old_cat_pos)

            try:
                newtext = textlib.replaceCategoryLinks(oldtext, cats)
            except ValueError:
                # Make sure that the only way replaceCategoryLinks() can return
                # a ValueError is in the case of interwiki links to self.
                pywikibot.output(u'Skipping %s because of interwiki link to '
                                 u'self' % self.title())
                return False

        if oldtext != newtext:
            try:
                self.put(newtext, comment)
                return True
            except pywikibot.PageSaveRelatedError as error:
                pywikibot.output(u'Page %s not saved: %s'
                                 % (self.title(asLink=True),
                                    error))
            except pywikibot.NoUsername:
                pywikibot.output(u'Page %s not saved; sysop privileges '
                                 u'required.' % self.title(asLink=True))
        return False

    @deprecated('Page.is_flow_page()')
    def isFlowPage(self):
        """DEPRECATED: use self.is_flow_page instead."""
        return self.is_flow_page()

    def is_flow_page(self):
        """
        Whether a page is a Flow page.

        @rtype: bool
        """
        return self.content_model == 'flow-board'

# ####### DEPRECATED METHODS ########

    @deprecated("Site.encoding()")
    def encoding(self):
        """DEPRECATED: use self.site.encoding instead."""
        return self.site.encoding()

    @deprecated("Page.title(withNamespace=False)")
    def titleWithoutNamespace(self, underscore=False):
        """DEPRECATED: use self.title(withNamespace=False) instead."""
        return self.title(underscore=underscore, withNamespace=False,
                          withSection=False)

    @deprecated("Page.title(as_filename=True)")
    def titleForFilename(self):
        """DEPRECATED: use self.title(as_filename=True) instead."""
        return self.title(as_filename=True)

    @deprecated("Page.title(withSection=False)")
    def sectionFreeTitle(self, underscore=False):
        """DEPRECATED: use self.title(withSection=False) instead."""
        return self.title(underscore=underscore, withSection=False)

    @deprecated("Page.title(asLink=True)")
    def aslink(self, forceInterwiki=False, textlink=False, noInterwiki=False):
        """DEPRECATED: use self.title(asLink=True) instead."""
        return self.title(asLink=True, forceInterwiki=forceInterwiki,
                          allowInterwiki=not noInterwiki, textlink=textlink)

    @deprecated("Page.title(asUrl=True)")
    def urlname(self):
        """Return the Page title encoded for use in an URL.

        DEPRECATED: use self.title(asUrl=True) instead.

        """
        return self.title(asUrl=True)

# ###### DISABLED METHODS (warnings provided) ######
    # these methods are easily replaced by editing the page's text using
    # textlib methods and then using put() on the result.

    def removeImage(self, image, put=False, summary=None, safe=True):
        """Old method to remove all instances of an image from page."""
        warn('Page.removeImage() is no longer supported.',
             _NotImplementedWarning, 2)

    def replaceImage(self, image, replacement=None, put=False, summary=None,
                     safe=True):
        """Old method to replace all instances of an image with another."""
        warn('Page.replaceImage() is no longer supported.',
             _NotImplementedWarning, 2)


class Page(BasePage):

    """Page: A MediaWiki page."""

    @deprecate_arg("insite", None)
    @deprecate_arg("defaultNamespace", "ns")
    def __init__(self, source, title=u"", ns=0):
        """Instantiate a Page object."""
        if isinstance(source, pywikibot.site.BaseSite):
            if not title:
                raise ValueError(u'Title must be specified and not empty '
                                 'if source is a Site.')
        super(Page, self).__init__(source, title, ns)

    @deprecate_arg("get_redirect", None)
    def templatesWithParams(self):
        """Iterate templates used on this Page.

        @return: a generator that yields a tuple for each use of a template
        in the page, with the template Page as the first entry and a list of
        parameters as the second entry.

        """
        # WARNING: may not return all templates used in particularly
        # intricate cases such as template substitution
        titles = list(t.title() for t in self.templates())
        templates = textlib.extract_templates_and_params(self.text)
        # backwards-compatibility: convert the dict returned as the second
        # element into a list in the format used by old scripts
        result = []
        for template in templates:
            try:
                link = pywikibot.Link(template[0], self.site,
                                      defaultNamespace=10)
                if link.canonical_title() not in titles:
                    continue
            except pywikibot.Error:
                # this is a parser function or magic word, not template name
                # the template name might also contain invalid parts
                continue
            args = template[1]
            intkeys = {}
            named = {}
            positional = []
            for key in sorted(args):
                try:
                    intkeys[int(key)] = args[key]
                except ValueError:
                    named[key] = args[key]
            for i in range(1, len(intkeys) + 1):
                # only those args with consecutive integer keys can be
                # treated as positional; an integer could also be used
                # (out of order) as the key for a named argument
                # example: {{tmp|one|two|5=five|three}}
                if i in intkeys:
                    positional.append(intkeys[i])
                else:
                    for k in intkeys:
                        if k < 1 or k >= i:
                            named[str(k)] = intkeys[k]
                    break
            for name in named:
                positional.append("%s=%s" % (name, named[name]))
            result.append((pywikibot.Page(link, self.site), positional))
        return result

    def set_redirect_target(self, target_page, create=False, force=False,
                            keep_section=False, save=True, **kwargs):
        """
        Change the page's text to point to the redirect page.

        @param target_page: target of the redirect, this argument is required.
        @type target_page: pywikibot.Page or string
        @param create: if true, it creates the redirect even if the page
            doesn't exist.
        @type create: bool
        @param force: if true, it set the redirect target even the page
            doesn't exist or it's not redirect.
        @type force: bool
        @param keep_section: if the old redirect links to a section
            and the new one doesn't it uses the old redirect's section.
        @type keep_section: bool
        @param save: if true, it saves the page immediately.
        @type save: bool
        @param kwargs: Arguments which are used for saving the page directly
            afterwards, like 'summary' for edit summary.
        """
        if isinstance(target_page, basestring):
            target_page = pywikibot.Page(self.site, target_page)
        elif self.site != target_page.site:
            raise pywikibot.InterwikiRedirectPage(self, target_page)
        if not self.exists() and not (create or force):
            raise pywikibot.NoPage(self)
        if self.exists() and not self.isRedirectPage() and not force:
            raise pywikibot.IsNotRedirectPage(self)
        redirect_regex = self.site.redirectRegex()
        if self.exists():
            old_text = self.get(get_redirect=True)
        else:
            old_text = u''
        result = redirect_regex.search(old_text)
        if result:
            oldlink = result.group(1)
            if keep_section and '#' in oldlink and target_page.section() is None:
                sectionlink = oldlink[oldlink.index('#'):]
                target_page = pywikibot.Page(
                    self.site,
                    target_page.title() + sectionlink
                )
            prefix = self.text[:result.start()]
            suffix = self.text[result.end():]
        else:
            prefix = ''
            suffix = ''

        target_link = target_page.title(asLink=True, textlink=True,
                                        allowInterwiki=False)
        target_link = u'#{0} {1}'.format(self.site.redirect(), target_link)
        self.text = prefix + target_link + suffix
        if save:
            self.save(**kwargs)


class FilePage(Page):

    """A subclass of Page representing a file description page.

    Supports the same interface as Page, with some added methods.
    """

    @deprecate_arg("insite", None)
    def __init__(self, source, title=u""):
        """Constructor."""
        self._file_revisions = {}  # dictionary to cache File history.
        super(FilePage, self).__init__(source, title, 6)
        if self.namespace() != 6:
            raise ValueError(u"'%s' is not in the file namespace!" % title)

    def _load_file_revisions(self, imageinfo):
        for file_rev in imageinfo:
            file_revision = FileInfo(file_rev)
            self._file_revisions[file_revision.timestamp] = file_revision

    @property
    def latest_file_info(self):
        """Retrieve and store information of latest Image rev. of FilePage.

        At the same time, the whole history of Image is fetched and cached in
        self._file_revisions

        @return: instance of FileInfo()

        """
        if not len(self._file_revisions):
            self.site.loadimageinfo(self, history=True)
        latest_ts = max(self._file_revisions)
        return self._file_revisions[latest_ts]

    @property
    def oldest_file_info(self):
        """Retrieve and store information of oldest Image rev. of FilePage.

        At the same time, the whole history of Image is fetched and cached in
        self._file_revisions

        @return: instance of FileInfo()

        """
        if not len(self._file_revisions):
            self.site.loadimageinfo(self, history=True)
        oldest_ts = min(self._file_revisions)
        return self._file_revisions[oldest_ts]

    def get_file_history(self):
        """Return the file's version history.

        @return: dictionary with:
            key: timestamp of the entry
            value: instance of FileInfo()

        """
        if not hasattr(self, '_file_revisions'):
            self.site.loadimageinfo(self, history=True)
        return self._file_revisions

    def getImagePageHtml(self):
        """
        Download the file page, and return the HTML, as a unicode string.

        Caches the HTML code, so that if you run this method twice on the
        same FilePage object, the page will only be downloaded once.
        """
        if not hasattr(self, '_imagePageHtml'):
            path = "%s/index.php?title=%s" \
                   % (self.site.scriptpath(), self.title(asUrl=True))
            self._imagePageHtml = http.request(self.site, path)
        return self._imagePageHtml

    def fileUrl(self):
        """Return the URL for the file described on this page."""
        # TODO add scaling option?
        return self.latest_file_info.url

    @deprecated("fileIsShared")
    def fileIsOnCommons(self):
        """DEPRECATED. Check if the image is stored on Wikimedia Commons.

        @return: bool
        """
        return self.fileIsShared()

    def fileIsShared(self):
        """Check if the file is stored on any known shared repository.

        @return: bool
        """
        # as of now, the only known repositories are commons and wikitravel
        # TODO: put the URLs to family file
        if not self.site.has_image_repository:
            return False
        elif 'wikitravel_shared' in self.site.shared_image_repository():
            return self.fileUrl().startswith(
                u'http://wikitravel.org/upload/shared/')
        else:
            return self.fileUrl().startswith(
                'https://upload.wikimedia.org/wikipedia/commons/')

    @deprecated("FilePage.latest_file_info.sha1")
    def getFileMd5Sum(self):
        """Return image file's MD5 checksum."""
        # TODO: check whether this needs a User-Agent header added
        req = http.fetch(self.fileUrl())
        h = hashlib.md5()
        h.update(req.raw)
        md5Checksum = h.hexdigest()
        return md5Checksum

    @deprecated("FilePage.latest_file_info.sha1")
    def getFileSHA1Sum(self):
        """Return the file's SHA1 checksum."""
        return self.latest_file_info.sha1

    @deprecated("FilePage.oldest_file_info.user")
    def getFirstUploader(self):
        """Return a list with first uploader of the FilePage and timestamp.

        For compatibility with compat only.

        """
        return [self.oldest_file_info.user, self.oldest_file_info.timestamp]

    @deprecated("FilePage.latest_file_info.user")
    def getLatestUploader(self):
        """Return a list with latest uploader of the FilePage and timestamp.

        For compatibility with compat only.

        """
        return [self.latest_file_info.user, self.latest_file_info.timestamp]

    @deprecated('FilePage.get_file_history()')
    def getFileVersionHistory(self):
        """Return the file's version history.

        @return: A list of dictionaries with the following keys::

            [comment, sha1, url, timestamp, metadata,
             height, width, mime, user, descriptionurl, size]

        """
        return self.site.loadimageinfo(self, history=True)

    def getFileVersionHistoryTable(self):
        """Return the version history in the form of a wiki table."""
        lines = []
        for info in self.getFileVersionHistory():
            datetime = info['timestamp']
            username = info['user']
            resolution = '%dx%d' % (info['height'], info['width'])
            size = info['size']
            comment = info['comment']
            lines.append(u'| %s || %s || %s || %s || <nowiki>%s</nowiki>'
                         % (datetime, username, resolution, size, comment))
        return u'{| border="1"\n! date/time || username || resolution || size || edit summary\n|----\n' + \
               u'\n|----\n'.join(lines) + '\n|}'

    def usingPages(self, step=None, total=None, content=False):
        """Yield Pages on which the file is displayed.

        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in total
        @param content: if True, load the current content of each iterated page
            (default False)

        """
        return self.site.imageusage(
            self, step=step, total=total, content=content)


import pywikibot.tools
wrapper = pywikibot.tools.ModuleDeprecationWrapper(__name__)
wrapper._add_deprecated_attr('ImagePage', FilePage)


class Category(Page):

    """A page in the Category: namespace."""

    @deprecate_arg("insite", None)
    def __init__(self, source, title=u"", sortKey=None):
        """
        Constructor.

        All parameters are the same as for Page() constructor.

        """
        self.sortKey = sortKey
        Page.__init__(self, source, title, ns=14)
        if self.namespace() != 14:
            raise ValueError(u"'%s' is not in the category namespace!"
                             % title)

    @deprecated_args(forceInterwiki=None, textlink=None, noInterwiki=None)
    def aslink(self, sortKey=None):
        """Return a link to place a page in this Category.

        Use this only to generate a "true" category link, not for interwikis
        or text links to category pages.

        @param sortKey: The sort key for the article to be placed in this
            Category; if omitted, default sort key is used.
        @type sortKey: (optional) unicode

        """
        key = sortKey or self.sortKey
        if key is not None:
            titleWithSortKey = '%s|%s' % (self.title(withSection=False),
                                          key)
        else:
            titleWithSortKey = self.title(withSection=False)
        return '[[%s]]' % titleWithSortKey

    @deprecated_args(startFrom=None, cacheResults=None)
    def subcategories(self, recurse=False, step=None, total=None,
                      content=False):
        """Iterate all subcategories of the current category.

        @param recurse: if not False or 0, also iterate subcategories of
            subcategories. If an int, limit recursion to this number of
            levels. (Example: recurse=1 will iterate direct subcats and
            first-level sub-sub-cats, but no deeper.)
        @type recurse: int or bool
        @param step: limit each API call to this number of categories
        @param total: iterate no more than this number of
            subcategories in total (at all levels)
        @param content: if True, retrieve the content of the current version
            of each category description page (default False)

        """
        if not isinstance(recurse, bool) and recurse:
            recurse = recurse - 1
        if not hasattr(self, "_subcats"):
            self._subcats = []
            for member in self.site.categorymembers(
                    self, member_type='subcat', step=step,
                    total=total, content=content):
                subcat = Category(member)
                self._subcats.append(subcat)
                yield subcat
                if total is not None:
                    total -= 1
                    if total == 0:
                        return
                if recurse:
                    for item in subcat.subcategories(
                            recurse, step=step, total=total, content=content):
                        yield item
                        if total is not None:
                            total -= 1
                            if total == 0:
                                return
        else:
            for subcat in self._subcats:
                yield subcat
                if total is not None:
                    total -= 1
                    if total == 0:
                        return
                if recurse:
                    for item in subcat.subcategories(
                            recurse, step=step, total=total, content=content):
                        yield item
                        if total is not None:
                            total -= 1
                            if total == 0:
                                return

    @deprecate_arg("startFrom", "startsort")
    def articles(self, recurse=False, step=None, total=None,
                 content=False, namespaces=None, sortby=None,
                 starttime=None, endtime=None, startsort=None,
                 endsort=None):
        """
        Yield all articles in the current category.

        By default, yields all *pages* in the category that are not
        subcategories!

        @param recurse: if not False or 0, also iterate articles in
            subcategories. If an int, limit recursion to this number of
            levels. (Example: recurse=1 will iterate articles in first-level
            subcats, but no deeper.)
        @type recurse: int or bool
        @param step: limit each API call to this number of pages
        @param total: iterate no more than this number of pages in
            total (at all levels)
        @param namespaces: only yield pages in the specified namespaces
        @type namespaces: int or list of ints
        @param content: if True, retrieve the content of the current version
            of each page (default False)
        @param sortby: determines the order in which results are generated,
            valid values are "sortkey" (default, results ordered by category
            sort key) or "timestamp" (results ordered by time page was
            added to the category). This applies recursively.
        @type sortby: str
        @param starttime: if provided, only generate pages added after this
            time; not valid unless sortby="timestamp"
        @type starttime: pywikibot.Timestamp
        @param endtime: if provided, only generate pages added before this
            time; not valid unless sortby="timestamp"
        @type endtime: pywikibot.Timestamp
        @param startsort: if provided, only generate pages >= this title
            lexically; not valid if sortby="timestamp"
        @type startsort: str
        @param endsort: if provided, only generate pages <= this title
            lexically; not valid if sortby="timestamp"
        @type endsort: str

        """
        for member in self.site.categorymembers(self,
                                                namespaces=namespaces,
                                                step=step, total=total,
                                                content=content, sortby=sortby,
                                                starttime=starttime,
                                                endtime=endtime,
                                                startsort=startsort,
                                                endsort=endsort,
                                                member_type=['page', 'file']
                                                ):
            yield member
            if total is not None:
                total -= 1
                if total == 0:
                    return
        if recurse:
            if not isinstance(recurse, bool) and recurse:
                recurse = recurse - 1
            for subcat in self.subcategories(step=step):
                for article in subcat.articles(recurse, step=step, total=total,
                                               content=content,
                                               namespaces=namespaces,
                                               sortby=sortby,
                                               starttime=starttime,
                                               endtime=endtime,
                                               startsort=startsort,
                                               endsort=endsort
                                               ):
                    yield article
                    if total is not None:
                        total -= 1
                        if total == 0:
                            return

    def members(self, recurse=False, namespaces=None, step=None, total=None,
                content=False):
        """Yield all category contents (subcats, pages, and files)."""
        for member in self.site.categorymembers(
                self, namespaces, step=step, total=total, content=content):
            yield member
            if total is not None:
                total -= 1
                if total == 0:
                    return
        if recurse:
            if not isinstance(recurse, bool) and recurse:
                recurse = recurse - 1
            for subcat in self.subcategories(step=step):
                for article in subcat.members(
                        recurse, namespaces, step=step,
                        total=total, content=content):
                    yield article
                    if total is not None:
                        total -= 1
                        if total == 0:
                            return

    @pywikibot.site.need_version("1.13")
    def isEmptyCategory(self):
        """Return True if category has no members (including subcategories)."""
        ci = self.categoryinfo
        return sum(ci[k] for k in ['files', 'pages', 'subcats']) == 0

    @pywikibot.site.need_version("1.11")
    def isHiddenCategory(self):
        """Return True if the category is hidden."""
        return u'hiddencat' in self.properties()

    def copyTo(self, cat, message):
        """
        Copy text of category page to a new page.  Does not move contents.

        @param cat: New category title (without namespace) or Category object
        @type cat: unicode or Category
        @param message: message to use for category creation message
        If two %s are provided in message, will be replaced
        by (self.title, authorsList)
        @type message: unicode
        @return: True if copying was successful, False if target page
            already existed.

        """
        # This seems far too specialized to be in the top-level framework
        # move to category.py? (Although it doesn't seem to be used there,
        # either)
        if not isinstance(cat, Category):
            cat = self.site.category_namespace() + ':' + cat
            targetCat = Category(self.site, cat)
        else:
            targetCat = cat
        if targetCat.exists():
            pywikibot.output(u'Target page %s already exists!'
                             % targetCat.title(),
                             level=pywikibot.WARNING)
            return False
        else:
            pywikibot.output('Moving text from %s to %s.'
                             % (self.title(), targetCat.title()))
            authors = ', '.join(self.contributingUsers())
            try:
                creationSummary = message % (self.title(), authors)
            except TypeError:
                creationSummary = message
            targetCat.put(self.get(), creationSummary)
            return True

    def copyAndKeep(self, catname, cfdTemplates, message):
        """Copy partial category page text (not contents) to a new title.

        Like copyTo above, except this removes a list of templates (like
        deletion templates) that appear in the old category text.  It also
        removes all text between the two HTML comments BEGIN CFD TEMPLATE
        and END CFD TEMPLATE. (This is to deal with CFD templates that are
        substituted.)

        Returns true if copying was successful, false if target page already
        existed.

        @param catname: New category title (without namespace)
        @param cfdTemplates: A list (or iterator) of templates to be removed
            from the page text
        @return: True if copying was successful, False if target page
            already existed.

        """
        # I don't see why we need this as part of the framework either
        # move to scripts/category.py?
        catname = self.site.category_namespace() + ':' + catname
        targetCat = Category(self.site, catname)
        if targetCat.exists():
            pywikibot.warning(u'Target page %s already exists!'
                              % targetCat.title())
            return False
        else:
            pywikibot.output(
                'Moving text from %s to %s.'
                % (self.title(), targetCat.title()))
            authors = ', '.join(self.contributingUsers())
            creationSummary = message % (self.title(), authors)
            newtext = self.get()
        for regexName in cfdTemplates:
            matchcfd = re.compile(r"{{%s.*?}}" % regexName, re.IGNORECASE)
            newtext = matchcfd.sub('', newtext)
            matchcomment = re.compile(
                r"<!--BEGIN CFD TEMPLATE-->.*?<!--END CFD TEMPLATE-->",
                re.IGNORECASE | re.MULTILINE | re.DOTALL)
            newtext = matchcomment.sub('', newtext)
            pos = 0
            while (newtext[pos:pos + 1] == "\n"):
                pos = pos + 1
            newtext = newtext[pos:]
            targetCat.put(newtext, creationSummary)
            return True

    @property
    def categoryinfo(self):
        """Return a dict containing information about the category.

        The dict contains values for:

        Numbers of pages, subcategories, files, and total contents.

        @return: dict
        """
        return self.site.categoryinfo(self)

    def newest_pages(self, total=None):
        """
        Return pages in a category ordered by the creation date.

        If two or more pages are created at the same time, the pages are
        returned in the order they were added to the category. The most recently
        added page is returned first.

        It only allows to return the pages ordered from newest to oldest, as it
        is impossible to determine the oldest page in a category without
        checking all pages. But it is possible to check the category in order
        with the newly added first and it yields all pages which were created
        after the currently checked page was added (and thus there is no page
        created after any of the cached but added before the currently checked).

        @param total: The total number of pages queried.
        @type total: int
        @return: A page generator of all pages in a category ordered by the
            creation date. From newest to oldest. Note: It currently only
            returns Page instances and not a subclass of it if possible. This
            might change so don't expect to only get Page instances.
        @rtype: generator
        """
        def check_cache(latest):
            """Return the cached pages in order and not more than total."""
            cached = []
            for timestamp in sorted((ts for ts in cache if ts > latest),
                                    reverse=True):
                # The complete list can be removed, it'll either yield all of
                # them, or only a portion but will skip the rest anyway
                cached += cache.pop(timestamp)[:None if total is None else
                                                total - len(cached)]
                if total and len(cached) >= total:
                    break  # already got enough
            assert(total is None or len(cached) <= total)
            return cached

        # all pages which have been checked but where created before the
        # current page was added, at some point they will be created after
        # the current page was added. It saves all pages via the creation
        # timestamp. Be prepared for multiple pages.
        cache = defaultdict(list)
        # TODO: Make site.categorymembers is usable as it returns pages
        # There is no total defined, as it's not known how many pages need to be
        # checked before the total amount of new pages was found. In worst case
        # all pages of a category need to be checked.
        for member in pywikibot.data.api.QueryGenerator(
                site=self.site, list='categorymembers', cmsort='timestamp',
                cmdir='older', cmprop='timestamp|title',
                cmtitle=self.title()):
            # TODO: Upcast to suitable class
            page = pywikibot.Page(self.site, member['title'])
            assert(page.namespace() == member['ns'])
            cached = check_cache(pywikibot.Timestamp.fromISOformat(
                member['timestamp']))
            for cached_page in cached:
                yield cached_page
            if total is not None:
                total -= len(cached)
                if total <= 0:
                    break
            cache[page.oldest_revision.timestamp] += [page]
        else:
            # clear cache
            assert(total is None or total > 0)
            for cached_page in check_cache(pywikibot.Timestamp.min):
                yield cached_page

# ### DEPRECATED METHODS ####
    @deprecated("list(Category.subcategories(...))")
    def subcategoriesList(self, recurse=False):
        """DEPRECATED: Equivalent to list(self.subcategories(...))."""
        return sorted(list(set(self.subcategories(recurse))))

    @deprecated("list(Category.articles(...))")
    def articlesList(self, recurse=False):
        """DEPRECATED: equivalent to list(self.articles(...))."""
        return sorted(list(set(self.articles(recurse))))

    @deprecated("Category.categories()")
    def supercategories(self):
        """DEPRECATED: equivalent to self.categories()."""
        return self.categories()

    @deprecated("list(Category.categories(...))")
    def supercategoriesList(self):
        """DEPRECATED: equivalent to list(self.categories(...))."""
        return sorted(list(set(self.categories())))


class User(Page):

    """A class that represents a Wiki user.

    This class also represents the Wiki page User:<username>
    """

    @deprecated_args(site="source", name="title")
    def __init__(self, source, title=u''):
        """Initializer for a User object.

        All parameters are the same as for Page() constructor.
        """
        if len(title) > 1 and title[0] == u'#':
            self._isAutoblock = True
            title = title[1:]
        else:
            self._isAutoblock = False
        Page.__init__(self, source, title, ns=2)
        if self.namespace() != 2:
            raise ValueError(u"'%s' is not in the user namespace!"
                             % title)
        if self._isAutoblock:
            # This user is probably being queried for purpose of lifting
            # an autoblock.
            pywikibot.output(
                "This is an autoblock ID, you can only use to unblock it.")

    def name(self):
        """
        The username.

        @return: unicode
        """
        return self.username

    @property
    def username(self):
        """The username.

        Convenience method that returns the title of the page with
        namespace prefix omitted, which is the username.

        @return: unicode
        """
        if self._isAutoblock:
            return u'#' + self.title(withNamespace=False)
        else:
            return self.title(withNamespace=False)

    def isRegistered(self, force=False):
        """Determine if the user is registered on the site.

        It is possible to have a page named User:xyz and not have
        a corresponding user with username xyz.

        The page does not need to exist for this method to return
        True.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: bool
        """
        if self.isAnonymous():
            return False
        else:
            return self.getprops(force).get('missing') is None

    def isAnonymous(self):
        """Determine if the user is editing as an IP address.

        @return: bool
        """
        return is_IP(self.username)

    def getprops(self, force=False):
        """Return a properties about the user.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: dict
        """
        if force:
            del self._userprops
        if not hasattr(self, '_userprops'):
            self._userprops = list(self.site.users([self.username, ]))[0]
            if self.isAnonymous():
                r = list(self.site.blocks(users=self.username))
                if r:
                    self._userprops['blockedby'] = r[0]['by']
                    self._userprops['blockreason'] = r[0]['reason']
        return self._userprops

    @deprecated('User.registration()')
    def registrationTime(self, force=False):
        """DEPRECATED. Fetch registration date for this user.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: long (MediaWiki's internal timestamp format) or 0
        """
        if self.registration():
            return long(self.registration().strftime('%Y%m%d%H%M%S'))
        else:
            return 0

    def registration(self, force=False):
        """Fetch registration date for this user.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: pywikibot.Timestamp or None
        """
        reg = self.getprops(force).get('registration')
        if reg:
            return pywikibot.Timestamp.fromISOformat(reg)

    def editCount(self, force=False):
        """Return edit count for a registered user.

        Always returns 0 for 'anonymous' users.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: long
        """
        if 'editcount' in self.getprops(force):
            return self.getprops()['editcount']
        else:
            return 0

    def isBlocked(self, force=False):
        """Determine whether the user is currently blocked.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: bool
        """
        return 'blockedby' in self.getprops(force)

    def isEmailable(self, force=False):
        """Determine whether emails may be send to this user through MediaWiki.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: bool
        """
        return 'emailable' in self.getprops(force)

    def groups(self, force=False):
        """Return a list of groups to which this user belongs.

        The list of groups may be empty.

        @param force: if True, forces reloading the data from API
        @type force: bool

        @return: list
        """
        if 'groups' in self.getprops(force):
            return self.getprops()['groups']
        else:
            return []

    def getUserPage(self, subpage=u''):
        """Return a Page object relative to this user's main page.

        @param subpage: subpage part to be appended to the main
                            page title (optional)
        @type subpage: unicode
        """
        if self._isAutoblock:
            # This user is probably being queried for purpose of lifting
            # an autoblock, so has no user pages per se.
            raise AutoblockUser(
                "This is an autoblock ID, you can only use to unblock it.")
        if subpage:
            subpage = u'/' + subpage
        return Page(Link(self.title() + subpage, self.site))

    def getUserTalkPage(self, subpage=u''):
        """Return a Page object relative to this user's main talk page.

        @param subpage: subpage part to be appended to the main
                            talk page title (optional)
        @type subpage: unicode
        """
        if self._isAutoblock:
            # This user is probably being queried for purpose of lifting
            # an autoblock, so has no user talk pages per se.
            raise AutoblockUser(
                "This is an autoblock ID, you can only use to unblock it.")
        if subpage:
            subpage = u'/' + subpage
        return Page(Link(self.title(withNamespace=False) + subpage,
                         self.site, defaultNamespace=3))

    def send_email(self, subject, text, ccme=False):
        """Send an email to this user via MediaWiki's email interface.

        @param subject: the subject header of the mail
        @type subject: unicode
        @param text: mail body
        @type text: unicode
        @param ccme: if True, sends a copy of this email to the bot
        @type ccme: bool
        @raises NotEmailableError: the user of this User is not emailable
        @raises UserRightsError: logged in user does not have 'sendemail' right
        @return: operation successful indicator
        @rtype: bool
        """
        if not self.isEmailable():
            raise NotEmailableError('%s is not mailable' % self.username)

        if not self.site.has_right('sendemail'):
            raise UserRightsError('You don\'t have permission to send mail')

        params = {
            'action': 'emailuser',
            'target': self.username,
            'token': self.site.tokens['email'],
            'subject': subject,
            'text': text,
        }
        if ccme:
            params['ccme'] = 1
        mailrequest = pywikibot.data.api.Request(site=self.site, **params)
        maildata = mailrequest.submit()

        if 'emailuser' in maildata:
            if maildata['emailuser']['result'] == u'Success':
                return True
        return False

    @deprecated('send_email')
    def sendMail(self, subject, text, ccme=False):
        """Send an email to this user via MediaWiki's email interface.

        Outputs 'Email sent' if the email was sent.

        @param subject: the subject header of the mail
        @type subject: unicode
        @param text: mail body
        @type text: unicode
        @param ccme: if True, sends a copy of this email to the bot
        @type ccme: bool
        @raises _EmailUserError: logged in user does not have 'sendemail' right
            or the target has disabled receiving emails
        @return: operation successful indicator
        @rtype: bool
        """
        if not self.isEmailable():
            raise _EmailUserError('This user is not mailable')

        if not self.site.has_right('sendemail'):
            raise _EmailUserError('You don\'t have permission to send mail')

        if self.send_email(subject, text, ccme=ccme):
            pywikibot.output('Email sent.')
            return True
        else:
            return False

    def block(self, expiry, reason, anononly=True, nocreate=True,
              autoblock=True, noemail=False, reblock=False):
        """
        Block user.

        @param expiry: When the block should expire
        @type expiry: pywikibot.Timestamp|str
        @param reason: Block reason
        @type reason: basestring
        @param anononly: Whether block should only affect anonymous users
        @type anononly: bool
        @param nocreate: Whether to block account creation
        @type nocreate: bool
        @param autoblock: Whether to enable autoblock
        @type autoblock: bool
        @param noemail: Whether to disable email access
        @type noemail: bool
        @param reblock: Whether to reblock if a block already is set
        @type reblock: bool
        @return: None
        """
        try:
            self.site.blockuser(self, expiry, reason, anononly, nocreate,
                                autoblock, noemail, reblock)
        except pywikibot.data.api.APIError as err:
            if err.code == 'invalidrange':
                raise ValueError("%s is not a valid IP range." % self.username)
            else:
                raise err

    @deprecated("contributions")
    @deprecate_arg("limit", "total")  # To be consistent with rest of framework
    def editedPages(self, total=500):
        """DEPRECATED. Use contributions().

        Yields pywikibot.Page objects that this user has
        edited, with an upper bound of 'total'. Pages returned are not
        guaranteed to be unique.

        @param total: limit result to this number of pages.
        @type total: int.
        """
        for item in self.contributions(total=total):
            yield item[0]

    @deprecate_arg("limit", "total")  # To be consistent with rest of framework
    @deprecate_arg("namespace", "namespaces")
    def contributions(self, total=500, namespaces=[]):
        """Yield tuples describing this user edits.

        Each tuple is composed of a pywikibot.Page object,
        the revision id (int), the edit timestamp (as a pywikibot.Timestamp
        object), and the comment (unicode).
        Pages returned are not guaranteed to be unique.

        @param total: limit result to this number of pages
        @type total: int
        @param namespaces: only iterate links in these namespaces
        @type namespaces: list
        """
        for contrib in self.site.usercontribs(
                user=self.username, namespaces=namespaces, total=total):
            ts = pywikibot.Timestamp.fromISOformat(contrib['timestamp'])
            yield (Page(self.site, contrib['title'], contrib['ns']),
                   contrib['revid'],
                   ts,
                   contrib.get('comment', None)
                   )

    @deprecate_arg("number", "total")
    def uploadedImages(self, total=10):
        """Yield tuples describing files uploaded by this user.

        Each tuple is composed of a pywikibot.Page, the timestamp (str in
        ISO8601 format), comment (unicode) and a bool for pageid > 0.
        Pages returned are not guaranteed to be unique.

        @param total: limit result to this number of pages
        @type total: int
        """
        if not self.isRegistered():
            raise StopIteration
        for item in self.site.logevents(
                logtype='upload', user=self.username, total=total):
            yield (FilePage(self.site, item.title().title()),
                   unicode(item.timestamp()),
                   item.comment(),
                   item.pageid() > 0
                   )


class WikibasePage(BasePage):

    """
    The base page for the Wikibase extension.

    There should be no need to instantiate this directly.
    """

    def __init__(self, site, title=u"", **kwargs):
        """Constructor.

        If title is provided, either ns or entity_type must also be provided,
        and will be checked against the title parsed using the Page
        initialisation logic.

        @param site: Wikibase data site
        @type site: DataSite
        @param title: normalized title of the page
        @type title: unicode
        @kwarg ns: namespace
        @type ns: Namespace instance, or int
        @kwarg entity_type: Wikibase entity type
        @type entity_type: str ('item' or 'property')

        @raise TypeError: incorrect use of parameters
        @raise ValueError: incorrect namespace
        @raise pywikibot.Error: title parsing problems
        @raise NotImplementedError: the entity type is not supported
        """
        if not isinstance(site, pywikibot.site.DataSite):
            raise TypeError("site must be a pywikibot.site.DataSite object")
        if title and ('ns' not in kwargs and 'entity_type' not in kwargs):
            pywikibot.debug("%s.__init__: %s title %r specified without "
                            "ns or entity_type"
                            % (self.__class__.__name__, site, title),
                            layer='wikibase')

        self._namespace = None

        if 'ns' in kwargs:
            if isinstance(kwargs['ns'], Namespace):
                self._namespace = kwargs.pop('ns')
                kwargs['ns'] = self._namespace.id
            else:
                # numerical namespace given
                ns = int(kwargs['ns'])
                if site.item_namespace.id == ns:
                    self._namespace = site.item_namespace
                elif site.property_namespace.id == ns:
                    self._namespace = site.property_namespace
                else:
                    raise ValueError('%r: Namespace "%d" is not valid'
                                     % self.site)

        if 'entity_type' in kwargs:
            entity_type = kwargs.pop('entity_type')
            if entity_type == 'item':
                entity_type_ns = site.item_namespace
            elif entity_type == 'property':
                entity_type_ns = site.property_namespace
            else:
                raise ValueError('Wikibase entity type "%s" unknown'
                                 % entity_type)

            if self._namespace:
                if self._namespace != entity_type_ns:
                    raise ValueError('Namespace "%d" is not valid for Wikibase'
                                     ' entity type "%s"'
                                     % (kwargs['ns'], entity_type))
            else:
                self._namespace = entity_type_ns
                kwargs['ns'] = self._namespace.id

        super(WikibasePage, self).__init__(site, title, **kwargs)

        # If a title was not provided,
        # avoid checks which may cause an exception.
        if not title:
            self.repo = site
            return

        if self._namespace:
            if self._link.namespace != self._namespace.id:
                raise ValueError(u"'%s' is not in the namespace %d"
                                 % (title, self._namespace.id))
        else:
            # Neither ns or entity_type was provided.
            # Use the _link to determine entity type.
            ns = self._link.namespace
            if self.site.item_namespace.id == ns:
                self._namespace = self.site.item_namespace
            elif self.site.property_namespace.id == ns:
                self._namespace = self.site.property_namespace
            else:
                raise ValueError('%r: Namespace "%r" is not valid'
                                 % (self.site, ns))

        # .site forces a parse of the Link title to determine site
        self.repo = self.site

    def _defined_by(self, singular=False):
        """
        Internal function to provide the API parameters to identify the entity.

        The API parameters may be 'id' if the ItemPage has one,
        or 'site'&'title' if instantiated via ItemPage.fromPage with
        lazy_load enabled.

        Once an item's "p/q##" is looked up, that will be used for all future
        requests.

        An empty dict is returned if the ItemPage is instantiated without
        either ID (internally it has id = '-1') or site&title.

        @param singular: Whether the parameter names should use the singular
                         form
        @type singular: bool
        @return: API parameters
        @rtype: dict
        """
        params = {}
        if singular:
            id = 'id'
            site = 'site'
            title = 'title'
        else:
            id = 'ids'
            site = 'sites'
            title = 'titles'

        lazy_loading_id = not hasattr(self, 'id') and hasattr(self, '_site')

        # id overrides all
        if hasattr(self, 'id'):
            if self.id != '-1':
                params[id] = self.id
        elif lazy_loading_id:
            params[site] = self._site.dbName()
            params[title] = self._title
        else:
            # if none of the above applies, this item is in an invalid state
            # which needs to be raise as an exception, but also logged in case
            # an exception handler is catching the generic Error.
            pywikibot.error('%s is in invalid state' %
                                            self.__class__.__name__)
            raise pywikibot.Error('%s is in invalid state' %
                                            self.__class__.__name__)

        return params

    def namespace(self):
        """Return the number of the namespace of the entity.

        @return: Namespace id
        @rtype: int
        """
        return self._namespace.id

    def exists(self):
        """
        Determine if an entity exists in the data repository.

        @return: bool
        """
        if not hasattr(self, '_content'):
            try:
                self.get()
                return True
            except pywikibot.NoPage:
                return False
        return 'lastrevid' in self._content

    def botMayEdit(self):
        """
        Return whether bots may edit this page.

        Because there is currently no system to mark a page that it shouldn't
        be edited by bots on Wikibase pages it always returns True. The content
        of the page is not text but a dict, the original way (to search for a
        template) doesn't apply.

        @return: True
        @rtype: boolean
        """
        return True

    def get(self, force=False, *args, **kwargs):
        """
        Fetch all page data, and cache it.

        @param force: override caching
        @type force: bool
        @param args: may be used to specify custom props.
        """
        lazy_loading_id = not hasattr(self, 'id') and hasattr(self, '_site')
        if force or not hasattr(self, '_content'):
            identification = self._defined_by()
            if not identification:
                raise pywikibot.NoPage(self)

            data = self.repo.loadcontent(identification, *args)
            item_index = list(data.keys())[0]
            if lazy_loading_id or item_index != '-1':
                self.id = item_index

            self._content = data[item_index]
        if 'lastrevid' in self._content:
            self.lastrevid = self._content['lastrevid']
        else:
            if lazy_loading_id:
                p = Page(self._site, self._title)
                if not p.exists():
                    raise pywikibot.NoPage(p)
            raise pywikibot.NoPage(self)

        # aliases
        self.aliases = {}
        if 'aliases' in self._content:
            for lang in self._content['aliases']:
                self.aliases[lang] = list()
                for value in self._content['aliases'][lang]:
                    self.aliases[lang].append(value['value'])

        # labels
        self.labels = {}
        if 'labels' in self._content:
            for lang in self._content['labels']:
                if 'removed' not in self._content['labels'][lang]:  # Bug 54767
                    self.labels[lang] = self._content['labels'][lang]['value']

        # descriptions
        self.descriptions = {}
        if 'descriptions' in self._content:
            for lang in self._content['descriptions']:
                self.descriptions[lang] = self._content[
                    'descriptions'][lang]['value']

        return {'aliases': self.aliases,
                'labels': self.labels,
                'descriptions': self.descriptions,
                }

    def _diff_to(self, type_key, key_name, value_name, diffto, data):
        assert(type_key not in data)
        source = self._normalizeLanguages(getattr(self, type_key)).copy()
        diffto = {} if not diffto else diffto.get(type_key, {})
        new = set(source.keys())
        for key in diffto:
            if key in new:
                if source[key] == diffto[key][value_name]:
                    del source[key]
            else:
                source[key] = ''
        for key, value in source.items():
            source[key] = {key_name: key, value_name: value}
        if source:
            data[type_key] = source

    def toJSON(self, diffto=None):
        """
        Create JSON suitable for Wikibase API.

        When diffto is provided, JSON representing differences
        to the provided data is created.

        @param diffto: JSON containing claim data
        @type diffto: dict

        @return: dict
        """
        data = {}
        self._diff_to('labels', 'language', 'value', diffto, data)

        self._diff_to('descriptions', 'language', 'value', diffto, data)

        aliases = self._normalizeLanguages(self.aliases).copy()
        if diffto and 'aliases' in diffto:
            for lang in set(diffto['aliases'].keys()) - set(aliases.keys()):
                aliases[lang] = []
        for lang, strings in list(aliases.items()):
            if diffto and 'aliases' in diffto and lang in diffto['aliases']:
                empty = len(diffto['aliases'][lang]) - len(strings)
                if empty > 0:
                    strings += [''] * empty
                elif Counter(val['value'] for val
                             in diffto['aliases'][lang]) == Counter(strings):
                    del aliases[lang]
            if lang in aliases:
                aliases[lang] = [{'language': lang, 'value': i} for i in strings]

        if aliases:
            data['aliases'] = aliases
        return data

    def getID(self, numeric=False, force=False):
        """
        Get the entity identifier.

        @param numeric: Strip the first letter and return an int
        @type numeric: bool
        @param force: Force an update of new data
        @type force: bool
        """
        if not hasattr(self, 'id') or force:
            self.get(force=force)
        if numeric:
            return int(self.id[1:]) if self.id != '-1' else -1

        return self.id

    @property
    def latest_revision_id(self):
        """
        Get the revision identifier for the most recent revision of the entity.

        @return: long
        """
        if not hasattr(self, 'lastrevid'):
            self.get()
        return self.lastrevid

    @staticmethod
    def _normalizeLanguages(data):
        """
        Helper function to replace site objects with their language codes.

        @param data: The dict to normalize.
        @type data: dict

        @return: the altered dict from parameter data.
        @rtype: dict
        """
        for key in data:
            if isinstance(key, pywikibot.site.BaseSite):
                data[key.language()] = data[key]
                del data[key]
        return data

    @classmethod
    def _normalizeData(cls, data):
        """
        Helper function to expand data into the Wikibase API structure.

        @param data: The dict to normalize
        @type data: dict

        @return: the altered dict from parameter data.
        @rtype: dict
        """
        for prop in ('labels', 'descriptions'):
            if prop not in data:
                continue
            data[prop] = cls._normalizeLanguages(data[prop])
            for key, value in data[prop].items():
                if isinstance(value, basestring):
                    data[prop][key] = {'language': key, 'value': value}

        if 'aliases' in data:
            for key, values in data['aliases'].items():
                if (isinstance(values, list) and
                        isinstance(values[0], basestring)):
                    data['aliases'][key] = [{'language': key, 'value': value}
                                            for value in values]

        return data

    def getdbName(self, site):
        """
        Helper function to obtain a dbName for a Site.

        @param site: The site to look up.
        @type site: Site
        """
        if isinstance(site, pywikibot.site.BaseSite):
            return site.dbName()
        return site

    def editEntity(self, data=None, **kwargs):
        """
        Edit an entity using Wikibase wbeditentity API.

        This function is wrapped around by:
         - editLabels
         - editDescriptions
         - editAliases
         - ItemPage.setSitelinks

        @param data: Data to be saved
        @type data: dict, or None to save the current content of the entity.
        """
        if hasattr(self, 'lastrevid'):
            baserevid = self.lastrevid
        else:
            baserevid = None

        if data is None:
            data = self.toJSON(diffto=(self._content if hasattr(self, '_content') else None))
        else:
            data = WikibasePage._normalizeData(data)

        updates = self.repo.editEntity(self._defined_by(singular=True), data,
                                       baserevid=baserevid, **kwargs)
        self.lastrevid = updates['entity']['lastrevid']

        lazy_loading_id = not hasattr(self, 'id') and hasattr(self, '_site')
        if lazy_loading_id or self.id == '-1':
            self.__init__(self.site, title=updates['entity']['id'])

    def editLabels(self, labels, **kwargs):
        """
        Edit entity labels.

        Labels should be a dict, with the key
        as a language or a site object. The
        value should be the string to set it to.
        You can set it to '' to remove the label.
        """
        data = {'labels': labels}
        self.editEntity(data, **kwargs)

    def editDescriptions(self, descriptions, **kwargs):
        """
        Edit entity descriptions.

        Descriptions should be a dict, with the key
        as a language or a site object. The
        value should be the string to set it to.
        You can set it to '' to remove the description.
        """
        data = {'descriptions': descriptions}
        self.editEntity(data, **kwargs)

    def editAliases(self, aliases, **kwargs):
        """
        Edit entity aliases.

        Aliases should be a dict, with the key
        as a language or a site object. The
        value should be a list of strings.
        """
        data = {'aliases': aliases}
        self.editEntity(data, **kwargs)

    def set_redirect_target(self, target_page, create=False, force=False,
                            keep_section=False, save=True, **kwargs):
        """
        Set target of a redirect for a Wikibase page.

        Has not been implemented in the Wikibase API yet, except for ItemPage.
        """
        raise NotImplementedError


class ItemPage(WikibasePage):

    """Wikibase entity of type 'item'.

    A Wikibase item may be defined by either a 'Q' id (qid),
    or by a site & title.

    If an item is defined by site & title, once an item's qid has
    been looked up, the item is then defined by the qid.
    """

    def __init__(self, site, title=None, ns=None):
        """
        Constructor.

        @param site: data repository
        @type site: pywikibot.site.DataSite
        @param title: id number of item, "Q###",
                      -1 or None for an empty item.
        @type title: str
        @type ns: namespace
        @type ns: Namespace instance, or int, or None
            for default item_namespace
        """
        if ns is None:
            ns = site.item_namespace
        # Special case for empty item.
        if title is None or title == '-1':
            super(ItemPage, self).__init__(site, u'-1', ns=ns)
            self.id = u'-1'
            return

        super(ItemPage, self).__init__(site, title, ns=ns)

        # Link.__init__, called from Page.__init__, has cleaned the title
        # stripping whitespace and uppercasing the first letter according
        # to the namespace case=first-letter.

        # Validate the title is 'Q' and a positive integer.
        if not re.match(r'^Q[1-9]\d*$', self._link.title):
            raise pywikibot.InvalidTitle(
                u"'%s' is not a valid item page title"
                % self._link.title)

        self.id = self._link.title

    def title(self, **kwargs):
        """
        Return ID as title of the ItemPage.

        If the ItemPage was lazy-loaded via ItemPage.fromPage, this method
        will fetch the wikibase item ID for the page, potentially raising
        NoPage with the page on the linked wiki if it does not exist, or
        does not have a corresponding wikibase item ID.

        This method also refreshes the title if the id property was set.
        i.e. item.id = 'Q60'

        All optional keyword parameters are passed to the superclass.
        """
        # If instantiated via ItemPage.fromPage using site and title,
        # _site and _title exist, and id does not exist.
        lazy_loading_id = not hasattr(self, 'id') and hasattr(self, '_site')

        if lazy_loading_id or self._link._text != self.id:
            # If the item is lazy loaded or has been modified,
            # _link._text is stale.  Removing _link._title
            # forces Link to re-parse ._text into ._title.
            if hasattr(self._link, '_title'):
                del self._link._title
            self._link._text = self.getID()
            self._link.parse()
            # Remove the temporary values that are no longer needed after
            # the .getID() above has called .get(), which populated .id
            if hasattr(self, '_site'):
                del self._title
                del self._site

        return super(ItemPage, self).title(**kwargs)

    @classmethod
    def fromPage(cls, page, lazy_load=False):
        """
        Get the ItemPage for a Page that links to it.

        @param page: Page to look for corresponding data item
        @type  page: pywikibot.Page
        @param lazy_load: Do not raise NoPage if either page or corresponding
                          ItemPage does not exist.
        @type  lazy_load: bool
        @return: ItemPage

        @exception NoPage: There is no corresponding ItemPage for the page
        """
        if not page.site.has_transcluded_data:
            raise pywikibot.WikiBaseError(u'%s has no transcluded data'
                                          % page.site)
        if not lazy_load and not page.exists():
            raise pywikibot.NoPage(page)

        repo = page.site.data_repository()
        if hasattr(page,
                   '_pageprops') and page.properties().get('wikibase_item'):
            # If we have already fetched the pageprops for something else,
            # we already have the id, so use it
            return cls(repo, page.properties().get('wikibase_item'))
        i = cls(repo)
        # clear id, and temporarily store data needed to lazy loading the item
        del i.id
        i._site = page.site
        i._title = page.title(withSection=False)
        if not lazy_load and not i.exists():
            raise pywikibot.NoPage(i)
        return i

    def get(self, force=False, *args, **kwargs):
        """
        Fetch all item data, and cache it.

        @param force: override caching
        @type force: bool
        @param args: values of props
        """
        super(ItemPage, self).get(force=force, *args, **kwargs)

        # claims
        self.claims = {}
        if 'claims' in self._content:
            for pid in self._content['claims']:
                self.claims[pid] = list()
                for claim in self._content['claims'][pid]:
                    c = Claim.fromJSON(self.repo, claim)
                    c.on_item = self
                    self.claims[pid].append(c)

        # sitelinks
        self.sitelinks = {}
        if 'sitelinks' in self._content:
            for dbname in self._content['sitelinks']:
                self.sitelinks[dbname] = self._content[
                    'sitelinks'][dbname]['title']

        return {'aliases': self.aliases,
                'labels': self.labels,
                'descriptions': self.descriptions,
                'sitelinks': self.sitelinks,
                'claims': self.claims
                }

    def getRedirectTarget(self):
        """Return the redirect target for this page."""
        target = super(ItemPage, self).getRedirectTarget()
        cmodel = target.content_model
        if cmodel != 'wikibase-item':
            raise pywikibot.Error(u'%s has redirect target %s with content '
                                  u'model %s instead of wikibase-item' %
                                  (self, target, cmodel))
        return self.__class__(target.site, target.title(), target.namespace())

    def toJSON(self, diffto=None):
        """
        Create JSON suitable for Wikibase API.

        When diffto is provided, JSON representing differences
        to the provided data is created.

        @param diffto: JSON containing claim data
        @type diffto: dict

        @return: dict
        """
        data = super(ItemPage, self).toJSON(diffto=diffto)

        self._diff_to('sitelinks', 'site', 'title', diffto, data)

        claims = {}
        for prop in self.claims:
            if len(self.claims[prop]) > 0:
                claims[prop] = [claim.toJSON() for claim in self.claims[prop]]

        if diffto and 'claims' in diffto:
            temp = defaultdict(list)
            claim_ids = set()

            diffto_claims = diffto['claims']

            for prop in claims:
                for claim in claims[prop]:
                    if (prop not in diffto_claims or
                            claim not in diffto_claims[prop]):
                        temp[prop].append(claim)

                    claim_ids.add(claim['id'])

            for prop, prop_claims in diffto_claims.items():
                for claim in prop_claims:
                    if 'id' in claim and claim['id'] not in claim_ids:
                        temp[prop].append({'id': claim['id'], 'remove': ''})

            claims = temp

        if claims:
            data['claims'] = claims
        return data

    def iterlinks(self, family=None):
        """
        Iterate through all the sitelinks.

        @param family: string/Family object which represents what family of
                       links to iterate
        @type family: str|pywikibot.family.Family
        @return: iterator of pywikibot.Page objects
        """
        if not hasattr(self, 'sitelinks'):
            self.get()
        if family is not None and not isinstance(family, Family):
            family = Family.load(family)
        for dbname in self.sitelinks:
            pg = Page(pywikibot.site.APISite.fromDBName(dbname),
                      self.sitelinks[dbname])
            if family is None or family == pg.site.family:
                yield pg

    def getSitelink(self, site, force=False):
        """
        Return the title for the specific site.

        If the item doesn't have that language, raise NoPage.

        @param site: Site to find the linked page of.
        @type site: pywikibot.Site or database name
        @param force: override caching

        @return: unicode
        """
        if force or not hasattr(self, '_content'):
            self.get(force=force)
        dbname = self.getdbName(site)
        if dbname not in self.sitelinks:
            raise pywikibot.NoPage(self)
        else:
            return self.sitelinks[dbname]

    def setSitelink(self, sitelink, **kwargs):
        """
        Set sitelinks. Calls setSitelinks().

        A sitelink can either be a Page object,
        or a {'site':dbname,'title':title} dictionary.
        """
        self.setSitelinks([sitelink], **kwargs)

    def removeSitelink(self, site, **kwargs):
        """
        Remove a sitelink.

        A site can either be a Site object, or it can be a dbName.
        """
        self.removeSitelinks([site], **kwargs)

    def removeSitelinks(self, sites, **kwargs):
        """
        Remove sitelinks.

        Sites should be a list, with values either
        being Site objects, or dbNames.
        """
        data = list()
        for site in sites:
            site = self.getdbName(site)
            data.append({'site': site, 'title': ''})
        self.setSitelinks(data, **kwargs)

    def setSitelinks(self, sitelinks, **kwargs):
        """
        Set sitelinks.

        Sitelinks should be a list. Each item in the
        list can either be a Page object, or a dict
        with a value for 'site' and 'title'.
        """
        data = {}
        for obj in sitelinks:
            if isinstance(obj, Page):
                dbName = self.getdbName(obj.site)
                data[dbName] = {'site': dbName, 'title': obj.title()}
            else:
                # TODO: Do some verification here
                dbName = obj['site']
                data[dbName] = obj
        data = {'sitelinks': data}
        self.editEntity(data, **kwargs)

    def addClaim(self, claim, bot=True, **kwargs):
        """
        Add a claim to the item.

        @param claim: The claim to add
        @type claim: Claim
        @param bot: Whether to flag as bot (if possible)
        @type bot: bool
        """
        self.repo.addClaim(self, claim, bot=bot, **kwargs)
        claim.on_item = self

    def removeClaims(self, claims, **kwargs):
        """
        Remove the claims from the item.

        @type claims: list

        """
        # this check allows single claims to be removed by pushing them into a
        # list of length one.
        if isinstance(claims, pywikibot.Claim):
            claims = [claims]
        self.repo.removeClaims(claims, **kwargs)

    def mergeInto(self, item, **kwargs):
        """
        Merge the item into another item.

        @param item: The item to merge into
        @type item: pywikibot.ItemPage
        """
        self.repo.mergeItems(fromItem=self, toItem=item, **kwargs)

    def set_redirect_target(self, target_page, create=False, force=False,
                            keep_section=False, save=True, **kwargs):
        """
        Make the item redirect to another item.

        You need to define an extra argument to make this work, like save=True
        @param target_page: target of the redirect, this argument is required.
        @type target_page: pywikibot.Item or string
        @param force: if true, it sets the redirect target even the page
            is not redirect.
        @type force: bool
        """
        if isinstance(target_page, basestring):
            target_page = pywikibot.ItemPage(self.repo, target_page)
        elif self.repo != target_page.repo:
            raise pywikibot.InterwikiRedirectPage(self, target_page)
        if self.exists() and not self.isRedirectPage() and not force:
            raise pywikibot.IsNotRedirectPage(self)
        if not save or keep_section or create:
            raise NotImplementedError
        self.repo.set_redirect_target(
            from_item=self, to_item=target_page)


class Property():

    """
    A Wikibase property.

    While every Wikibase property has a Page on the data repository,
    this object is for when the property is used as part of another concept
    where the property is not _the_ Page of the property.

    For example, a claim on an ItemPage has many property attributes, and so
    it subclasses this Property class, but a claim does not have Page like
    behaviour and semantics.
    """

    types = {'wikibase-item': ItemPage,
             'string': basestring,
             'commonsMedia': FilePage,
             'globe-coordinate': pywikibot.Coordinate,
             'url': basestring,
             'time': pywikibot.WbTime,
             'quantity': pywikibot.WbQuantity,
             }

    value_types = {'wikibase-item': 'wikibase-entityid',
                   'commonsMedia': 'string',
                   'url': 'string',
                   'globe-coordinate': 'globecoordinate',
                   }

    def __init__(self, site, id=None, datatype=None):
        """
        Constructor.

        @param site: data repository
        @type site: pywikibot.site.DataSite
        @param datatype: datatype of the property;
            if not given, it will be queried via the API
        @type datatype: basestring
        """
        self.repo = site
        self.id = id.upper()
        if datatype:
            self._type = datatype

    @property
    def type(self):
        """
        Return the type of this property.

        @return: str
        """
        if not hasattr(self, '_type'):
            self._type = self.repo.getPropertyType(self)
        return self._type

    @deprecated("Property.type")
    def getType(self):
        """
        Return the type of this property.

        It returns 'globecoordinate' for type 'globe-coordinate'
        in order to be backwards compatible.  See
        https://gerrit.wikimedia.org/r/#/c/135405/ for background.
        """
        if self.type == 'globe-coordinate':
            return 'globecoordinate'
        else:
            return self._type

    def getID(self, numeric=False):
        """
        Get the identifier of this property.

        @param numeric: Strip the first letter and return an int
        @type numeric: bool
        """
        if numeric:
            return int(self.id[1:])
        else:
            return self.id


class PropertyPage(WikibasePage, Property):

    """
    A Wikibase entity in the property namespace.

    Should be created as::

        PropertyPage(DataSite, 'P21')
    """

    def __init__(self, source, title=u""):
        """
        Constructor.

        @param source: data repository property is on
        @type source: pywikibot.site.DataSite
        @param title: page name of property, like "P##"
        @type title: str
        """
        WikibasePage.__init__(self, source, title,
                              ns=source.property_namespace)
        Property.__init__(self, source, title)
        self.id = self.title(withNamespace=False).upper()
        if not self.id.startswith(u'P'):
            raise pywikibot.InvalidTitle(
                u"'%s' is not an property page title" % title)

    def get(self, force=False, *args):
        """
        Fetch the property entity, and cache it.

        @param force: override caching
        @param args: values of props
        """
        if force or not hasattr(self, '_content'):
            WikibasePage.get(self, force=force, *args)
        self._type = self._content['datatype']

    def newClaim(self, *args, **kwargs):
        """
        Helper function to create a new claim object for this property.

        @return: Claim
        """
        return Claim(self.site, self.getID(), datatype=self.type,
                      *args, **kwargs)


class Claim(Property):

    """
    A Claim on a Wikibase entity.

    Claims are standard claims as well as references.
    """

    TARGET_CONVERTER = {
        'wikibase-item': lambda value, site:
            ItemPage(site, 'Q' + str(value['numeric-id'])),
        'commonsMedia': lambda value, site:
            FilePage(pywikibot.Site('commons', 'commons'), value),
        'globe-coordinate': pywikibot.Coordinate.fromWikibase,
        'time': lambda value, site: pywikibot.WbTime.fromWikibase(value),
        'quantity': lambda value, site: pywikibot.WbQuantity.fromWikibase(value),
    }

    def __init__(self, site, pid, snak=None, hash=None, isReference=False,
                 isQualifier=False, **kwargs):
        """
        Constructor.

        Defined by the "snak" value, supplemented by site + pid

        @param site: repository the claim is on
        @type site: pywikibot.site.DataSite
        @param pid: property id, with "P" prefix
        @param snak: snak identifier for claim
        @param hash: hash identifier for references
        @param isReference: whether specified claim is a reference
        @param isQualifier: whether specified claim is a qualifier
        """
        Property.__init__(self, site, pid, **kwargs)
        self.snak = snak
        self.hash = hash
        self.isReference = isReference
        self.isQualifier = isQualifier
        if self.isQualifier and self.isReference:
            raise ValueError(u'Claim cannot be both a qualifier and reference.')
        self.sources = []
        self.qualifiers = OrderedDict()
        self.target = None
        self.snaktype = 'value'
        self.rank = 'normal'
        self.on_item = None  # The item it's on

    @classmethod
    def fromJSON(cls, site, data):
        """
        Create a claim object from JSON returned in the API call.

        @param data: JSON containing claim data
        @type data: dict

        @return: Claim
        """
        claim = cls(site, data['mainsnak']['property'],
                    datatype=data['mainsnak'].get('datatype', None))
        if 'id' in data:
            claim.snak = data['id']
        elif 'hash' in data:
            claim.isReference = True
            claim.hash = data['hash']
        else:
            claim.isQualifier = True
        claim.snaktype = data['mainsnak']['snaktype']
        if claim.getSnakType() == 'value':
            value = data['mainsnak']['datavalue']['value']
            # The default covers string, url types
            claim.target = Claim.TARGET_CONVERTER.get(
                claim.type, lambda value, site: value)(value, site)
        if 'rank' in data:  # References/Qualifiers don't have ranks
            claim.rank = data['rank']
        if 'references' in data:
            for source in data['references']:
                claim.sources.append(cls.referenceFromJSON(site, source))
        if 'qualifiers' in data:
            for prop in data['qualifiers-order']:
                claim.qualifiers[prop] = [cls.qualifierFromJSON(site, qualifier)
                                          for qualifier in data['qualifiers'][prop]]
        return claim

    @classmethod
    def referenceFromJSON(cls, site, data):
        """
        Create a dict of claims from reference JSON returned in the API call.

        Reference objects are represented a
        bit differently, and require some
        more handling.

        @return: dict
        """
        source = OrderedDict()

        # Before #84516 Wikibase did not implement snaks-order.
        # https://gerrit.wikimedia.org/r/#/c/84516/
        if 'snaks-order' in data:
            prop_list = data['snaks-order']
        else:
            prop_list = data['snaks'].keys()

        for prop in prop_list:
            for claimsnak in data['snaks'][prop]:
                claim = cls.fromJSON(site, {'mainsnak': claimsnak,
                                            'hash': data['hash']})
                if claim.getID() not in source:
                    source[claim.getID()] = []
                source[claim.getID()].append(claim)
        return source

    @classmethod
    def qualifierFromJSON(cls, site, data):
        """
        Create a Claim for a qualifier from JSON.

        Qualifier objects are represented a bit
        differently like references, but I'm not
        sure if this even requires it's own function.

        @return: Claim
        """
        return cls.fromJSON(site, {'mainsnak': data,
                                   'hash': data['hash']})

    def toJSON(self):
        data = {
            'mainsnak': {
                'snaktype': self.snaktype,
                'property': self.getID()
            },
            'type': 'statement'
        }
        if hasattr(self, 'snak') and self.snak is not None:
            data['id'] = self.snak
        if hasattr(self, 'rank') and self.rank is not None:
            data['rank'] = self.rank
        if self.getSnakType() == 'value':
            data['mainsnak']['datatype'] = self.type
            data['mainsnak']['datavalue'] = self._formatDataValue()
        if self.isQualifier or self.isReference:
            data = data['mainsnak']
            if hasattr(self, 'hash') and self.hash is not None:
                data['hash'] = self.hash
        else:
            if len(self.qualifiers) > 0:
                data['qualifiers'] = {}
                data['qualifiers-order'] = list(self.qualifiers.keys())
                for prop, qualifiers in self.qualifiers.items():
                    for qualifier in qualifiers:
                        qualifier.isQualifier = True
                    data['qualifiers'][prop] = [qualifier.toJSON() for qualifier in qualifiers]
            if len(self.sources) > 0:
                data['references'] = []
                for collection in self.sources:
                    reference = {'snaks': {}, 'snaks-order': list(collection.keys())}
                    for prop, val in collection.items():
                        reference['snaks'][prop] = []
                        for source in val:
                            source.isReference = True
                            src_data = source.toJSON()
                            if 'hash' in src_data:
                                if 'hash' not in reference:
                                    reference['hash'] = src_data['hash']
                                del src_data['hash']
                            reference['snaks'][prop].append(src_data)
                    data['references'].append(reference)
        return data

    def setTarget(self, value):
        """
        Set the target value in the local object.

        @param value: The new target value.
        @type value: object

        @exception ValueError: if value is not of the type
            required for the Claim type.
        """
        value_class = self.types[self.type]
        if not isinstance(value, value_class):
            raise ValueError("%s is not type %s."
                                 % (value, value_class))
        self.target = value

    def changeTarget(self, value=None, snaktype='value', **kwargs):
        """
        Set the target value in the data repository.

        @param value: The new target value.
        @type value: object
        @param snaktype: The new snak type.
        @type snaktype: str ('value', 'somevalue', or 'novalue')
        """
        if value:
            self.setTarget(value)

        data = self.repo.changeClaimTarget(self, snaktype=snaktype,
                                           **kwargs)
        # TODO: Re-create the entire item from JSON, not just id
        self.snak = data['claim']['id']

    def getTarget(self):
        """
        Return the target value of this Claim.

        None is returned if no target is set

        @return: object
        """
        return self.target

    def getSnakType(self):
        """
        Return the type of snak.

        @return: str ('value', 'somevalue' or 'novalue')
        """
        return self.snaktype

    def setSnakType(self, value):
        """Set the type of snak.

        @param value: Type of snak
        @type value: str ('value', 'somevalue', or 'novalue')
        """
        if value in ['value', 'somevalue', 'novalue']:
            self.snaktype = value
        else:
            raise ValueError(
                "snaktype must be 'value', 'somevalue', or 'novalue'.")

    def getRank(self):
        """Return the rank of the Claim."""
        return self.rank

    def setRank(self):
        """
        Set the rank of the Claim.

        Has not been implemented in the Wikibase API yet
        """
        raise NotImplementedError

    def changeSnakType(self, value=None, **kwargs):
        """
        Save the new snak value.

        TODO: Is this function really needed?
        """
        if value:
            self.setSnakType(value)
        self.changeTarget(snaktype=self.getSnakType(), **kwargs)

    def getSources(self):
        """
        Return a list of sources, each being a list of Claims.

        @return: list
        """
        return self.sources

    def addSource(self, claim, **kwargs):
        """
        Add the claim as a source.

        @param claim: the claim to add
        @type claim: pywikibot.Claim
        """
        self.addSources([claim], **kwargs)

    def addSources(self, claims, **kwargs):
        """
        Add the claims as one source.

        @param claims: the claims to add
        @type claims: list of pywikibot.Claim
        """
        data = self.repo.editSource(self, claims, new=True, **kwargs)
        source = defaultdict(list)
        for claim in claims:
            claim.hash = data['reference']['hash']
            self.on_item.lastrevid = data['pageinfo']['lastrevid']
            source[claim.getID()].append(claim)
        self.sources.append(source)

    def removeSource(self, source, **kwargs):
        """
        Remove the source.  Calls removeSources().

        @param source: the source to remove
        @type source: pywikibot.Claim
        """
        self.removeSources([source], **kwargs)

    def removeSources(self, sources, **kwargs):
        """
        Remove the sources.

        @param sources: the sources to remove
        @type sources: list of pywikibot.Claim
        """
        self.repo.removeSources(self, sources, **kwargs)
        for source in sources:
            source_dict = defaultdict(list)
            source_dict[source.getID()].append(source)
            self.sources.remove(source_dict)

    def addQualifier(self, qualifier, **kwargs):
        """Add the given qualifier.

        @param qualifier: the qualifier to add
        @type qualifier: Claim
        """
        data = self.repo.editQualifier(self, qualifier, **kwargs)
        qualifier.isQualifier = True
        self.on_item.lastrevid = data['pageinfo']['lastrevid']
        if qualifier.getID() in self.qualifiers:
            self.qualifiers[qualifier.getID()].append(qualifier)
        else:
            self.qualifiers[qualifier.getID()] = [qualifier]

    def target_equals(self, value):
        """
        Check whether the Claim's target is equal to specified value.

        The function checks for:
        - ItemPage ID equality
        - WbTime year equality
        - Coordinate equality, regarding precision
        - direct equality

        @param value: the value to compare with
        @return: true if the Claim's target is equal to the value provided,
            false otherwise
        @rtype: bool
        """
        if (isinstance(self.target, pywikibot.ItemPage) and
                isinstance(value, basestring) and
                self.target.id == value):
            return True

        if (isinstance(self.target, pywikibot.WbTime) and
                not isinstance(value, pywikibot.WbTime) and
                self.target.year == int(value)):
            return True

        if (isinstance(self.target, pywikibot.Coordinate) and
                isinstance(value, basestring)):
            coord_args = [float(x) for x in value.split(',')]
            if len(coord_args) >= 3:
                precision = coord_args[2]
            else:
                precision = 0.0001  # Default value (~10 m at equator)
            try:
                if self.target.precision is not None:
                    precision = max(precision, self.target.precision)
            except TypeError:
                pass

            if (abs(self.target.lat - coord_args[0]) <= precision and
                    abs(self.target.lon - coord_args[1]) <= precision):
                return True

        if self.target == value:
            return True

        return False

    def has_qualifier(self, qualifier_id, target):
        """
        Check whether Claim contains specified qualifier.

        @param qualifier_id: id of the qualifier
        @type qualifier_id: str
        @param target: qualifier target to check presence of
        @return: true if the qualifier was found, false otherwise
        @rtype: bool
        """
        if self.isQualifier or self.isReference:
            raise ValueError(u'Qualifiers and references cannot have '
                             u'qualifiers.')

        for qualifier in self.qualifiers.get(qualifier_id, []):
            if qualifier.target_equals(target):
                return True
        return False

    def _formatValue(self):
        """
        Format the target into the proper JSON value that Wikibase wants.

        @return: JSON value
        @rtype: dict
        """
        if self.type == 'wikibase-item':
            value = {'entity-type': 'item',
                     'numeric-id': self.getTarget().getID(numeric=True)}
        elif self.type in ('string', 'url'):
            value = self.getTarget()
        elif self.type == 'commonsMedia':
            value = self.getTarget().title(withNamespace=False)
        elif self.type in ('globe-coordinate', 'time', 'quantity'):
            value = self.getTarget().toWikibase()
        else:
            raise NotImplementedError('%s datatype is not supported yet.'
                                      % self.type)
        return value

    def _formatDataValue(self):
        """
        Format the target into the proper JSON datavalue that Wikibase wants.

        @return: Wikibase API representation with type and value.
        @rtype: dict
        """
        return {'value': self._formatValue(),
                'type': self.value_types.get(self.type, self.type)
                }


class Revision(DotReadableDict):

    """A structure holding information about a single revision of a Page."""

    HistEntry = namedtuple('HistEntry', ['revid',
                                         'timestamp',
                                         'user',
                                         'comment'])

    FullHistEntry = namedtuple('FullHistEntry', ['revid',
                                                 'timestamp',
                                                 'user',
                                                 'text',
                                                 'rollbacktoken'])

    def __init__(self, revid, timestamp, user, anon=False, comment=u"",
                 text=None, minor=False, rollbacktoken=None):
        """
        Constructor.

        All parameters correspond to object attributes (e.g., revid
        parameter is stored as self.revid)

        @param revid: Revision id number
        @type revid: int
        @param text: Revision wikitext.
        @type text: unicode, or None if text not yet retrieved
        @param timestamp: Revision time stamp
        @type timestamp: pywikibot.Timestamp
        @param user: user who edited this revision
        @type user: unicode
        @param anon: user is unregistered
        @type anon: bool
        @param comment: edit comment text
        @type comment: unicode
        @param minor: edit flagged as minor
        @type minor: bool

        """
        self.revid = revid
        self.text = text
        self.timestamp = timestamp
        self.user = user
        self.anon = anon
        self.comment = comment
        self.minor = minor
        self.rollbacktoken = rollbacktoken

    def hist_entry(self):
        """Return a namedtuple with a Page history record."""
        return Revision.HistEntry(self.revid, self.timestamp, self.user,
                                  self.comment)

    def full_hist_entry(self):
        """Return a namedtuple with a Page full history record."""
        return Revision.FullHistEntry(self.revid, self.timestamp, self.user,
                                      self.text, self.rollbacktoken)


class FileInfo(DotReadableDict):

    """A structure holding imageinfo of latest rev. of FilePage.

    All keys of API imageinfo dictionary are mapped to FileInfo attributes.
    Attributes can be retrieved both as self['key'] or self.key.

    Following attributes will be returned:
        - timestamp, user, comment, url, size, sha1, mime, metadata
        - archivename (not for latest revision)

    See Site.loadimageinfo() for details.

    Note: timestamp will be casted to pywikibot.Timestamp.
    """

    def __init__(self, file_revision):
        """
        Create class with the dictionary returned by site.loadimageinfo().

        @param page: FilePage containing the image.
        @type page: FilePage object
        """
        self.__dict__.update(file_revision)
        self.timestamp = pywikibot.Timestamp.fromISOformat(self.timestamp)

    def __eq__(self, other):
        """Test if two File_info objects are equal."""
        return self.__dict__ == other.__dict__


class Link(ComparableMixin):

    """A MediaWiki link (local or interwiki).

    Has the following attributes:

      - site:  The Site object for the wiki linked to
      - namespace: The namespace of the page linked to (int)
      - title: The title of the page linked to (unicode); does not include
        namespace or section
      - section: The section of the page linked to (unicode or None); this
        contains any text following a '#' character in the title
      - anchor: The anchor text (unicode or None); this contains any text
        following a '|' character inside the link

    """

    illegal_titles_pattern = re.compile(
        # Matching titles will be held as illegal.
        r'''[\x00-\x1f\x23\x3c\x3e\x5b\x5d\x7b\x7c\x7d\x7f]'''
        # URL percent encoding sequences interfere with the ability
        # to round-trip titles -- you can't link to them consistently.
        u'|%[0-9A-Fa-f]{2}'
        # XML/HTML character references produce similar issues.
        u'|&[A-Za-z0-9\x80-\xff]+;'
        u'|&#[0-9]+;'
        u'|&#x[0-9A-Fa-f]+;'
    )

    def __init__(self, text, source=None, defaultNamespace=0):
        """Constructor.

        @param text: the link text (everything appearing between [[ and ]]
            on a wiki page)
        @type text: unicode
        @param source: the Site on which the link was found (not necessarily
            the site to which the link refers)
        @type source: Site
        @param defaultNamespace: a namespace to use if the link does not
            contain one (defaults to 0)
        @type defaultNamespace: int

        """
        assert source is None or isinstance(source, pywikibot.site.BaseSite), \
            "source parameter should be a Site object"

        self._text = text
        self._source = source or pywikibot.Site()
        self._defaultns = defaultNamespace

        # preprocess text (these changes aren't site-dependent)
        # First remove anchor, which is stored unchanged, if there is one
        if u"|" in self._text:
            self._text, self._anchor = self._text.split(u"|", 1)
        else:
            self._anchor = None

        # Convert URL-encoded characters to unicode
        encodings = [self._source.encoding()] + list(self._source.encodings())

        self._text = url2unicode(self._text, encodings=encodings)

        # Clean up the name, it can come from anywhere.
        # Convert HTML entities to unicode
        t = html2unicode(self._text)

        # Normalize unicode string to a NFC (composed) format to allow
        # proper string comparisons. According to
        # https://svn.wikimedia.org/viewvc/mediawiki/branches/REL1_6/phase3/includes/normal/UtfNormal.php?view=markup
        # the MediaWiki code normalizes everything to NFC, not NFKC
        # (which might result in information loss).
        t = unicodedata.normalize('NFC', t)

        # This code was adapted from Title.php : secureAndSplit()
        #
        if u'\ufffd' in t:
            raise pywikibot.Error(
                "Title contains illegal char (\\uFFFD 'REPLACEMENT CHARACTER')")

        # Replace underscores by spaces
        t = t.replace(u"_", u" ")
        # replace multiple spaces with a single space
        while u"  " in t:
            t = t.replace(u"  ", u" ")
        # Strip spaces at both ends
        t = t.strip()
        # Remove left-to-right and right-to-left markers.
        t = t.replace(u"\u200e", u"").replace(u"\u200f", u"")
        self._text = t

    def __repr__(self):
        """Return a more complete string representation."""
        return "pywikibot.page.Link(%r, %r)" % (self.title, self.site)

    def parse_site(self):
        """
        Parse only enough text to determine which site the link points to.

        This method does not parse anything after the first ":"; links
        with multiple interwiki prefixes (such as "wikt:fr:Parlais") need
        to be re-parsed on the first linked wiki to get the actual site.

        @return: The family name and site code for the linked site. If the site
            is not supported by the configured families it returns None instead
            of a str.
        @rtype: str or None, str or None
        """
        t = self._text
        fam = self._source.family
        code = self._source.code
        while u":" in t:
            # Initial colon
            if t.startswith(u":"):
                # remove the colon but continue processing
                # remove any subsequent whitespace
                t = t.lstrip(u":").lstrip(u" ")
                continue
            prefix = t[:t.index(u":")].lower()  # part of text before :
            ns = self._source.ns_index(prefix)
            if ns:
                # The prefix is a namespace in the source wiki
                return (fam.name, code)
            if prefix in fam.langs:
                # prefix is a language code within the source wiki family
                return (fam.name, prefix)
            try:
                newsite = self._source.interwiki(prefix)
            except KeyError:
                break  # text before : doesn't match any known prefix
            except SiteDefinitionError:
                return (None, None)
            else:
                return (newsite.family.name, newsite.code)
        return (fam.name, code)  # text before : doesn't match any known prefix

    def parse(self):
        """Parse wikitext of the link.

        Called internally when accessing attributes.
        """
        self._site = self._source
        self._namespace = self._defaultns
        self._is_interwiki = False
        t = self._text
        ns_prefix = False

        # This code was adapted from Title.php : secureAndSplit()
        #
        first_other_site = None
        while u":" in t:
            # Initial colon indicates main namespace rather than default
            if t.startswith(u":"):
                self._namespace = 0
                # remove the colon but continue processing
                # remove any subsequent whitespace
                t = t.lstrip(u":").lstrip(u" ")
                continue

            prefix = t[:t.index(u":")].lower()
            ns = self._site.ns_index(prefix)
            if ns:
                # Ordinary namespace
                t = t[t.index(u":"):].lstrip(u":").lstrip(u" ")
                self._namespace = ns
                ns_prefix = True
                break
            try:
                newsite = self._site.interwiki(prefix)
            except KeyError:
                break  # text before : doesn't match any known prefix
            except SiteDefinitionError as e:
                raise SiteDefinitionError(
                    u'{0} is not a local page on {1}, and the interwiki prefix '
                    '{2} is not supported by PyWikiBot!:\n{3}'.format(
                    self._text, self._site, prefix, e))
            else:
                t = t[t.index(u":"):].lstrip(u":").lstrip(u" ")
                if first_other_site:
                    if not self._site.local_interwiki(prefix):
                        raise pywikibot.InvalidTitle(
                            u'{0} links to a non local site {1} via an '
                            'interwiki link to {2}.'.format(
                            self._text, newsite, first_other_site))
                elif newsite != self._source:
                    first_other_site = newsite
                self._site = newsite
                self._is_interwiki = True

        if u"#" in t:
            t, sec = t.split(u'#', 1)
            t, self._section = t.rstrip(), sec.lstrip()
        else:
            self._section = None

        if ns_prefix:
            # 'namespace:' is not a valid title
            if not t:
                raise pywikibot.InvalidTitle(
                    u"'{0}' has no title.".format(self._text))
            elif ':' in t and self._namespace >= 0:  # < 0 don't have talk
                other_ns = self._site.namespaces[self._namespace - 1
                                                 if self._namespace % 2 else
                                                 self._namespace + 1]
                if '' in other_ns:  # other namespace uses empty str as ns
                    next_ns = t[:t.index(':')]
                    if self._site.ns_index(next_ns):
                        raise pywikibot.InvalidTitle(
                            u"The (non-)talk page of '{0}' is a valid title "
                            "in another namespace.".format(self._text))

        # Reject illegal characters.
        m = Link.illegal_titles_pattern.search(t)
        if m:
            raise pywikibot.InvalidTitle(
                u"%s contains illegal char(s) %s" % (repr(t), repr(m.group(0))))

        # Pages with "/./" or "/../" appearing in the URLs will
        # often be unreachable due to the way web browsers deal
        # * with 'relative' URLs. Forbid them explicitly.

        if u'.' in t and (
                t == u'.' or t == u'..' or
                t.startswith(u'./') or
                t.startswith(u'../') or
                u'/./' in t or
                u'/../' in t or
                t.endswith(u'/.') or
                t.endswith(u'/..')
        ):
            raise pywikibot.InvalidTitle(
                u"(contains . / combinations): '%s'"
                % self._text)

        # Magic tilde sequences? Nu-uh!
        if u"~~~" in t:
            raise pywikibot.InvalidTitle(u"(contains ~~~): '%s'" % self._text)

        if self._namespace != -1 and len(t) > 255:
            raise pywikibot.InvalidTitle(u"(over 255 bytes): '%s'" % t)

        # "empty" local links can only be self-links
        # with a fragment identifier.
        if not t.strip() and not self._is_interwiki:
            raise pywikibot.InvalidTitle("The link does not contain a page "
                                         "title")

        if self._site.namespaces[self._namespace].case == 'first-letter':
            t = first_upper(t)

        self._title = t

    # define attributes, to be evaluated lazily

    @property
    def site(self):
        """Return the site of the link.

        @return: unicode
        """
        if not hasattr(self, "_site"):
            self.parse()
        return self._site

    @property
    def namespace(self):
        """Return the namespace of the link.

        @return: unicode
        """
        if not hasattr(self, "_namespace"):
            self.parse()
        return self._namespace

    @property
    def title(self):
        """Return the title of the link.

        @return: unicode
        """
        if not hasattr(self, "_title"):
            self.parse()
        return self._title

    @property
    def section(self):
        """Return the section of the link.

        @return: unicode
        """
        if not hasattr(self, "_section"):
            self.parse()
        return self._section

    @property
    def anchor(self):
        """Return the anchor of the link.

        @return: unicode
        """
        if not hasattr(self, "_anchor"):
            self.parse()
        return self._anchor

    def canonical_title(self):
        """Return full page title, including localized namespace."""
        if self.namespace:
            return "%s:%s" % (self.site.namespace(self.namespace),
                              self.title)
        else:
            return self.title

    def ns_title(self, onsite=None):
        """Return full page title, including namespace.

        @param onsite: site object
            if specified, present title using onsite local namespace,
            otherwise use self canonical namespace.

            if no corresponding namespace is found in onsite,
            pywikibot.Error is raised.

        """
        ns_id = self.namespace
        ns = self.site.namespaces[ns_id]

        if onsite is None:
            namespace = ns.canonical_name
        else:
            # look for corresponding ns in onsite by name comparison
            for alias in ns:
                namespace = Namespace.lookup_name(alias, onsite.namespaces)
                if namespace:
                    namespace = namespace.custom_name
                    break
            else:
                # not found
                raise pywikibot.Error(
                    u'No corresponding namespace found for namespace %s on %s.'
                    % (self.site.namespaces[ns_id], onsite))

        if namespace:
            return u'%s:%s' % (namespace, self.title)
        else:
            return self.title

    def astext(self, onsite=None):
        """Return a text representation of the link.

        @param onsite: if specified, present as a (possibly interwiki) link
            from the given site; otherwise, present as an internal link on
            the source site.

        """
        if onsite is None:
            onsite = self._source
        title = self.title
        if self.namespace:
            title = onsite.namespace(self.namespace) + ":" + title
        if self.section:
            title = title + "#" + self.section
        if onsite == self.site:
            return u'[[%s]]' % title
        if onsite.family == self.site.family:
            return u'[[%s:%s]]' % (self.site.code, title)
        if self.site.family.name == self.site.code:
            # use this form for sites like commons, where the
            # code is the same as the family name
            return u'[[%s:%s]]' % (self.site.code,
                                   title)
        return u'[[%s:%s:%s]]' % (self.site.family.name,
                                  self.site.code,
                                  title)

    if sys.version_info[0] > 2:
        def __str__(self):
            """Return a string representation."""
            return self.__unicode__()
    else:
        def __str__(self):
            """Return a string representation."""
            return self.astext().encode("ascii", "backslashreplace")

    def _cmpkey(self):
        """
        Key for comparison of Link objects.

        Link objects are "equal" if and only if they are on the same site
        and have the same normalized title, including section if any.

        Link objects are sortable by site, then namespace, then title.
        """
        return (self.site, self.namespace, self.title)

    def __unicode__(self):
        """Return a unicode string representation.

        @return: unicode
        """
        return self.astext()

    def __hash__(self):
        """A stable identifier to be used as a key in hash-tables."""
        return hash(u'%s:%s:%s' % (self.site.family.name,
                                   self.site.code,
                                   self.title))

    @classmethod
    def fromPage(cls, page, source=None):
        """
        Create a Link to a Page.

        @param page: target Page
        @type page: Page
        @param source: Link from site source
        @param source: Site

        @return: Link
        """
        link = cls.__new__(cls)
        link._site = page.site
        link._section = page.section()
        link._namespace = page.namespace()
        link._title = page.title(withNamespace=False,
                                 allowInterwiki=False,
                                 withSection=False)
        link._anchor = None
        link._source = source or pywikibot.Site()

        return link

    @classmethod
    def langlinkUnsafe(cls, lang, title, source):
        """
        Create a "lang:title" Link linked from source.

        Assumes that the lang & title come clean, no checks are made.

        @param lang: target site code (language)
        @type lang: str
        @param title: target Page
        @type title: unicode
        @param source: Link from site source
        @param source: Site

        @return: Link
        """
        link = cls.__new__(cls)
        if source.family.interwiki_forward:
            link._site = pywikibot.Site(lang, source.family.interwiki_forward)
        else:
            link._site = pywikibot.Site(lang, source.family.name)
        link._section = None
        link._source = source

        link._namespace = 0

        if ':' in title:
            ns, t = title.split(':', 1)
            ns = link._site.ns_index(ns.lower())
            if ns:
                link._namespace = ns
                title = t
        if u"#" in title:
            t, sec = title.split(u'#', 1)
            title, link._section = t.rstrip(), sec.lstrip()
        else:
            link._section = None
        link._title = title
        return link


# Utility functions for parsing page titles


def html2unicode(text, ignore=None):
    """Replace HTML entities with equivalent unicode.

    @param ignore: HTML entities to ignore
    @param ignore: list of int

    @return: unicode
    """
    if ignore is None:
        ignore = []
    # This regular expression will match any decimal and hexadecimal entity and
    # also entities that might be named entities.
    entityR = re.compile(
        r'&(?:amp;)?(#(?P<decimal>\d+)|#x(?P<hex>[0-9a-fA-F]+)|(?P<name>[A-Za-z]+));')
    # These characters are Html-illegal, but sadly you *can* find some of
    # these and converting them to chr(decimal) is unsuitable
    convertIllegalHtmlEntities = {
        128: 8364,  # €
        130: 8218,  # ‚
        131: 402,   # ƒ
        132: 8222,  # „
        133: 8230,  # …
        134: 8224,  # †
        135: 8225,  # ‡
        136: 710,   # ˆ
        137: 8240,  # ‰
        138: 352,   # Š
        139: 8249,  # ‹
        140: 338,   # Œ
        142: 381,   # Ž
        145: 8216,  # ‘
        146: 8217,  # ’
        147: 8220,  # “
        148: 8221,  # ”
        149: 8226,  # •
        150: 8211,  # –
        151: 8212,  # —
        152: 732,   # ˜
        153: 8482,  # ™
        154: 353,   # š
        155: 8250,  # ›
        156: 339,   # œ
        158: 382,   # ž
        159: 376    # Ÿ
    }
    # ensuring that illegal &#129; &#141; and &#157, which have no known values,
    # don't get converted to chr(129), chr(141) or chr(157)
    ignore = set(ignore) | set([129, 141, 157])

    def handle_entity(match):
        if match.group('decimal'):
            unicodeCodepoint = int(match.group('decimal'))
        elif match.group('hex'):
            unicodeCodepoint = int(match.group('hex'), 16)
        elif match.group('name'):
            name = match.group('name')
            if name in htmlentitydefs.name2codepoint:
                # We found a known HTML entity.
                unicodeCodepoint = htmlentitydefs.name2codepoint[name]
            else:
                unicodeCodepoint = False
        try:
            unicodeCodepoint = convertIllegalHtmlEntities[unicodeCodepoint]
        except KeyError:
            pass
        if unicodeCodepoint and unicodeCodepoint not in ignore:
            if unicodeCodepoint > sys.maxunicode:
                # solve narrow Python 2 build exception (UTF-16)
                return eval("'\\U{0:08x}'".format(unicodeCodepoint))
            else:
                return chr(unicodeCodepoint)
        else:
            # Leave the entity unchanged
            return match.group(0)
    return entityR.sub(handle_entity, text)


def UnicodeToAsciiHtml(s):
    """Convert unicode to a str using HTML entities."""
    html = []
    for c in s:
        cord = ord(c)
        if 31 < cord < 128:
            html.append(c)
        else:
            html.append('&#%d;' % cord)
    return ''.join(html)


def unicode2html(x, encoding):
    """
    Convert unicode string to requested HTML encoding.

    Attempt to encode the
    string into the desired format; if that doesn't work, encode the unicode
    into HTML &#; entities. If it does work, return it unchanged.

    @param x: String to update
    @type x: unicode
    @param encoding: Encoding to use
    @type encoding: str

    @return: str
    """
    try:
        x.encode(encoding)
    except UnicodeError:
        x = UnicodeToAsciiHtml(x)
    return x


@deprecated_args(site2=None, site='encodings')
def url2unicode(title, encodings='utf-8'):
    """
    Convert URL-encoded text to unicode using several encoding.

    Uses the first encoding that doesn't cause an error.

    @param title: URL-encoded character data to convert
    @type title: str
    @param encodings: Encodings to attempt to use during conversion.
    @type encodings: str, list or Site
    @return: unicode

    @exception UnicodeError: Could not convert using any encoding.
    """
    if isinstance(encodings, basestring):
        encodings = [encodings]
    elif isinstance(encodings, pywikibot.site.BaseSite):
        # create a list of all possible encodings for both hint sites
        site = encodings
        encodings = [site.encoding()] + list(site.encodings())

    firstException = None
    for enc in encodings:
        try:
            t = title.encode(enc)
            t = unquote_to_bytes(t)
            return t.decode(enc)
        except UnicodeError as ex:
            if not firstException:
                firstException = ex
            pass
    # Couldn't convert, raise the original exception
    raise firstException
