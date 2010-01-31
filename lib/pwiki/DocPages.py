from __future__ import with_statement

import os.path, re, struct, time, traceback, threading

from .rtlibRepl import minidom

import wx

from MiscEvent import MiscEventSourceMixin

import Consts
from WikiExceptions import *

from StringOps import strToBool, fileContentToUnicode, lineendToInternal, \
        loadEntireTxtFile, writeEntireFile

from Utilities import DUMBTHREADSTOP, FunctionThreadStop, TimeoutRLock, \
        callInMainThread, callInMainThreadAsync

from WikiPyparsing import buildSyntaxNode
import ParseUtilities

import Serialization
# from Serialization import SerializeStream



# Dummy
UNDEFINED = object()


class DocPage(object, MiscEventSourceMixin):
    """
    Abstract common base class for WikiPage and FunctionalPage
    """

    def __init__(self, wikiDocument):
        MiscEventSourceMixin.__init__(self)
        
        self.wikiDocument = wikiDocument
        self.txtEditors = []  # List of all editors (views) showing this page
        self.livePageAst = None   # Cached page AST of live text

        # lock while building live AST
        self.livePageAstBuildLock = TimeoutRLock(Consts.DEADBLOCKTIMEOUT)

        # lock while setting, getting or saving self.editorText and some other ops
        self.textOperationLock = TimeoutRLock(Consts.DEADBLOCKTIMEOUT)

        # lock while changing or reading self.txtEditors list
        self.txtEditorListLock = TimeoutRLock(Consts.DEADBLOCKTIMEOUT)
        self.editorText = None  # Contains editor text, 
                # if no text editor is registered, this cache is invalid
#         self.pageState = STATE_TEXTCACHE_MATCHES_EDITOR


    def invalidate(self):
        """
        Make page invalid to prevent yet running threads from changing
        database.
        """
        with self.textOperationLock:
            with self.txtEditorListLock:
                self.wikiDocument = None
                self.txtEditors = None
                self.livePageAst = None
                self.setEditorText(None)


    def isInvalid(self):
        return self.txtEditors is None

    def getTextOperationLock(self):
        return self.textOperationLock

    def getWikiDocument(self):
        return self.wikiDocument
        
    def setEditorText(self, text, dirty=True):
        """
        Just set editor text. Derived class overwrites this to set flags
        """
        with self.textOperationLock:
            self.editorText = text
            self.livePageAst = None


    def getEditorText(self):
        return self.editorText

    def addTxtEditor(self, txted):
        """
        Add txted to the list of editors (views) showing this page.
        """
        # TODO Set text in editor if first editor is created?
        with self.txtEditorListLock:
            if not txted in self.txtEditors:
                if len(self.txtEditors) == 0:
                    with self.textOperationLock:
                        # We are assuming that editor content came from
                        # database
                        self.setEditorText(txted.GetText(), dirty=False)

                self.txtEditors.append(txted)


    def removeTxtEditor(self, txted):
        """
        Remove txted from the list of editors (views) showing this page.
        If the last is removed, text is saved to database.
        """
        with self.txtEditorListLock:
            try:
                idx = self.txtEditors.index(txted)
                if len(self.txtEditors) == 1:
                    self.setEditorText(None)

                del self.txtEditors[idx]
    
            except ValueError:
                # txted not in list
                pass

    def getTxtEditor(self):
        """
        Returns an arbitrary text editor associated with the page
        or None if no editor is associated.
        """
        with self.txtEditorListLock:
            if len(self.txtEditors) > 0:
                return self.txtEditors[0]
            else:
                return None


    def getLiveText(self):
        """
        Return current text of page, either from a text editor or
        from the database
        """
        with self.textOperationLock:
            if self.getEditorText() is not None:
                return self.getEditorText()
            
            return self.getContent()



    def getLiveTextNoTemplate(self):
        """
        Return None if page isn't existing instead of creating an automatic
        live text (e.g. by template).
        """
        raise NotImplementedError   # abstract


    def appendLiveText(self, text, fireEvent=True):
        """
        Append some text to page which is either loaded in one or more
        editor(s) or only stored in the database (with automatic update).

        fireEvent -- Send event if database was directly modified
        """
        with self.textOperationLock:
            if self.isReadOnlyEffect():
                return

            self.setDirty(True)
            txtEditor = self.getTxtEditor()
            self.livePageAst = None
            if txtEditor is not None:
                # page is in text editor(s), so call AppendText on one of it
                # TODO Call self.SetReadOnly(False) first?
                txtEditor.AppendText(text)
                return

            # Modify database
            text = self.getContent() + text
            self.writeToDatabase(text, fireEvent=fireEvent)


    def replaceLiveText(self, text, fireEvent=True):
        with self.textOperationLock:
            if self.isReadOnlyEffect():
                return

            self.setDirty(True)
            txtEditor = self.getTxtEditor()
            self.livePageAst = None
            if txtEditor is not None:
                # page is in text editor(s), so call replace on one of it
                # TODO Call self.SetReadOnly(False) first?
                txtEditor.replaceText(text)
                return

            self.writeToDatabase(text, fireEvent=fireEvent)


    def informEditorTextChanged(self, changer):
        """
        Called by the txt editor control. Must be called in GUI(=main) thread
        """
        with self.textOperationLock:
            txtEditor = self.getTxtEditor()
            self.setEditorText(txtEditor.GetText())

        self.fireMiscEventProps({"changed editor text": True,
                "changed live text": True, "changer": changer})


    def getWikiLanguageName(self):
        """
        Returns the internal name of the wiki language of this page.
        """
        return self.wikiDocument.getWikiDefaultWikiLanguage()



    def createWikiLanguageHelper(self):
        return wx.GetApp().createWikiLanguageHelper(self.getWikiLanguageName())


    def getContent(self):
        """
        Returns page content. If page doesn't exist already some content
        is created automatically (may be empty string).
        """
        raise NotImplementedError #abstract

    def setDirty(self, dirt):
        raise NotImplementedError #abstract

    def getDirty(self):
        raise NotImplementedError #abstract


    def getTitle(self):
        """
        Return human readable title of the page.
        """
        raise NotImplementedError #abstract


    def getUnifiedPageName(self):
        """
        Return the name of the unified name of the page, which is
        "wikipage/" + the wiki word for wiki pages or the functional tag
        for functional pages.
        """
        raise NotImplementedError #abstract


    def isReadOnlyEffect(self):
        """
        Return true if page is effectively read-only, this means
        "for any reason", regardless if error or intention.
        """
        return self.wikiDocument.isReadOnlyEffect()


    def writeToDatabase(self, text=None, fireEvent=True):
        """
        Write current text to database and initiate update of meta-data.
        """
        with self.textOperationLock:
            s, u = self.getDirty()
            if s:
                if text is None:
                    text = self.getLiveText()
                self._save(text, fireEvent=fireEvent)
                self.initiateUpdate(fireEvent=fireEvent)
            elif u:
                self.initiateUpdate(fireEvent=fireEvent)
            else:
                if self.getWikiData().getMetaDataState(self.wikiWord) != 2:
                    self.updateDirtySince = time.time()
                    self.initiateUpdate(fireEvent=fireEvent)


    def _save(self, text, fireEvent=True):
        """
        Saves the content of current doc page.
        """
        raise NotImplementedError #abstract


    def initiateUpdate(self, fireEvent=True):
        """
        Initiate update of page meta-data. This function may call update
        directly if can be done fast
        """
        raise NotImplementedError #abstract


#     def _update(self, fireEvent=True):
#         """
#         Update additional cached informations of doc page
#         """
#         raise NotImplementedError #abstract



class AliasWikiPage(DocPage):
    """
    Fake page for an alias name of a wiki page. Most functions are delegated
    to underlying real page
    Fetched via the (WikiDocument=) WikiDataManager.getWikiPage method.
    """
    def __init__(self, wikiDocument, aliasWikiWord, realWikiPage):
        self.wikiDocument = wikiDocument
        self.aliasWikiWord = aliasWikiWord
        self.realWikiPage = realWikiPage

    def getWikiWord(self):
        return self.aliasWikiWord

    def getTitle(self):
        """
        Return human readable title of the page.
        """
        return self.aliasWikiWord
        
    def getUnifiedPageName(self):
        """
        Return the name of the unified name of the page, which is
        "wikipage/" + the wiki word for wiki pages or the functional tag
        for functional pages.
        """
        return u"wikipage/" + self.aliasWikiWord

    def getNonAliasPage(self):
        """
        If this page belongs to an alias of a wiki word, return a page for
        the real one, otherwise return self
        """
        return self.realWikiPage
#         word = self.wikiDocument.getWikiData().getUnAliasedWikiWord(self.wikiWord)
#         return self.wikiDocument.getWikiPageNoError(word)

    def getContent(self):
        """
        Returns page content. If page doesn't exist already some content
        is created automatically (may be empty string).
        """
        return self.realWikiPage.getContent()


    def setDirty(self, dirt):
        return self.realWikiPage.setDirty(dirt)

    def _save(self, text, fireEvent=True):
        """
        Saves the content of current doc page.
        """
        return self.realWikiPage._save(text, fireEvent)

    def initiateUpdate(self):
        return self.realWikiPage.initiateUpdate()
        
#     def update(self, fireEvent=True):
#         return self.realWikiPage.update(fireEvent)

    def getLivePageAst(self, fireEvent=True, dieOnChange=False, 
            threadstop=DUMBTHREADSTOP):
        return self.realWikiPage.getLivePageAst(fireEvent, dieOnChange,
                threadstop)


    # TODO A bit hackish, maybe remove
    def __getattr__(self, attr):
        return getattr(self.realWikiPage, attr)


class DataCarryingPage(DocPage):
    """
    A page that carries data for itself (mainly everything except an alias page)
    """
    def __init__(self, wikiDocument):
        DocPage.__init__(self, wikiDocument)
        
        # does this page need to be saved?
        
        # None, if not dirty or timestamp when it became dirty
        # Inside self.textOperationLock, it is ensured that it is None iff
        # the editorText is None or is in sync with the database.
        # This applies not only to editorText, but also to the text returned
        # by getLiveText().

        self.saveDirtySince = None
        self.updateDirtySince = None

        # To not store the content of the page here, a placeholder
        # object is stored instead. Each time, the live text may have changed,
        # a new object is created. Functions running in a separate thread can
        # keep a reference to the object at beginning and check for
        # identity at the end of their work.
        self.liveTextPlaceHold = object()


#         # To not store the full DB content of the page here, a placeholder
#         # object is stored instead. Each time, text is written to DB,
#         # a new object is created. Functions running in a separate task can
#         # keep a reference to the object at beginning and check for
#         # identity at the end of their work.
#         self.dbContentPlaceHold = object()
        

    def setDirty(self, dirt):
        if self.isReadOnlyEffect():
            return

        if dirt:
            if self.saveDirtySince is None:
                ti = time.time()
                self.saveDirtySince = ti
                self.updateDirtySince = ti
        else:
            self.saveDirtySince = None
            self.updateDirtySince = None

    def getDirty(self):
        return (self.saveDirtySince is not None,
                self.updateDirtySince is not None)

    def getDirtySince(self):
        return (self.saveDirtySince, self.updateDirtySince)


    def setEditorText(self, text, dirty=True):
        with self.textOperationLock:
            super(DataCarryingPage, self).setEditorText(text)
            if text is None:
                if self.saveDirtySince is not None:
                    """
                    Editor text was removed although it wasn't in sync with
                    database, so the self.liveTextPlaceHold must be updated,
                    but self.saveDirtySince is set to None because
                    self.editorText isn't valid anymore
                    """
                    self.saveDirtySince = None
                    self.liveTextPlaceHold = object()
            else:
                if dirty:
                    self.setDirty(True)
                    self.liveTextPlaceHold = object()

    def checkFileSignatureAndMarkDirty(self, fireEvent=True):
        return True
    
    
    def markTextChanged(self):
        """
        Mark text as changed and cached pageAst as invalid.
        Mainly called when an external file change is detected.
        """
        self.liveTextPlaceHold = object()


class AbstractWikiPage(DataCarryingPage):
    """
    Abstract base for WikiPage and Versioning.WikiPageSnapshot
    """

    def __init__(self, wikiDocument, wikiWord):
        DataCarryingPage.__init__(self, wikiDocument)

        self.livePageBasePlaceHold = None   # liveTextPlaceHold object on which
                # the livePageAst is based.
                # This is needed to check for changes when saving
        self.livePageBaseFormatDetails = None   # Cached format details on which the
                # page-ast bases

#         self.metaDataProcessLock = threading.RLock()  # lock while processing
#                 # meta-data

        self.wikiWord = wikiWord
        self.childRelations = None
        self.childRelationSet = set()
        self.todos = None
        self.props = None
        self.modified, self.created, self.visited = None, None, None
        self.suggNewPageTitle = None  # Title to use for page if it is
                # newly created

#         if self.getWikiData().getMetaDataState(self.wikiWord) != 1:
#             self.updateDirtySince = time.time()

    def getWikiWord(self):
        return self.wikiWord

    def getTitle(self):
        """
        Return human readable title of the page.
        """
        return self.getWikiWord()


    def getUnifiedPageName(self):
        """
        Return the name of the unified name of the page, which is
        "wikipage/" + the wiki word for wiki pages or the functional tag
        for functional pages.
        """
        return u"wikipage/" + self.wikiWord

    def getWikiDocument(self):
        return self.wikiDocument

    def getWikiData(self):
        return self.wikiDocument.getWikiData()

    def addTxtEditor(self, txted):
        """
        Add txted to the list of editors (views) showing this page.
        """
        with self.txtEditorListLock:
            if len(self.txtEditors) == 0:
                with self.textOperationLock:
                    if not self.checkFileSignatureAndMarkDirty():
                        self.initiateUpdate()

            super(AbstractWikiPage, self).addTxtEditor(txted)


        # TODO Set text in editor here if first editor is created?

#         with self.txtEditorListLock:
            if not txted in self.txtEditors:
                if len(self.txtEditors) == 0:
                    with self.textOperationLock:
                        # We are assuming that editor content came from
                        # database
                        self.setEditorText(txted.GetText(), dirty=False)

                self.txtEditors.append(txted)


    def getTimestamps(self):
        """
        Return tuple (<last mod. time>, <creation time>, <last visit time>)
        of this page.
        """
        if self.modified is None:
            self.modified, self.created, self.visited = \
                    self.getWikiData().getTimestamps(self.wikiWord)
                    
        if self.modified is None:
            ti = time.time()
            self.modified, self.created, self.visited = ti, ti, ti
        
        return self.modified, self.created, self.visited

    def setTimestamps(self, timestamps):
        if self.isReadOnlyEffect():
            return

        timestamps = timestamps[:3]
        self.modified, self.created, self.visited = timestamps
        
        self.getWikiData().setTimestamps(self.wikiWord, timestamps)


    def getSuggNewPageTitle(self):
        return self.suggNewPageTitle
        
    def setSuggNewPageTitle(self, suggNewPageTitle):
        self.suggNewPageTitle = suggNewPageTitle

    def getParentRelationships(self):
        return self.getWikiData().getParentRelationships(self.wikiWord)


    def getChildRelationships(self, existingonly=False, selfreference=True,
            withFields=(), excludeSet=frozenset(),
            includeSet=frozenset()):
        """
        get the child relations of this word
        existingonly -- List only existing wiki words
        selfreference -- List also wikiWord if it references itself
        withFields -- Seq. of names of fields which should be included in
            the output. If this is not empty, tuples are returned
            (relation, ...) with ... as further fields in the order mentioned
            in withfields.

            Possible field names:
                "firstcharpos": position of link in page (may be -1 to represent
                    unknown)
                "modified": Modification date
        excludeSet -- set of words which should be excluded from the list
        includeSet -- wikiWords to include in the result

        Does not support caching
        """
        with self.textOperationLock:
            wikiData = self.getWikiData()
            wikiDocument = self.getWikiDocument()
            
            if withFields is None:
                withFields = ()
    
            relations = wikiData.getChildRelationships(self.wikiWord,
                    existingonly, selfreference, withFields=withFields)
    
            if len(excludeSet) > 0:
                # Filter out members of excludeSet
                if len(withFields) > 0:
                    relations = [r for r in relations if not r[0] in excludeSet]
                else:
                    relations = [r for r in relations if not r in excludeSet]
    
            if len(includeSet) > 0:
                # First unalias wiki pages and remove non-existing ones
                clearedIncSet = set()
                for w in includeSet:
                    w = wikiDocument.getUnAliasedWikiWord(w)
                    if w is None:
                        continue

#                     if not wikiDocument.isDefinedWikiLink(w):
#                         continue
    
                    clearedIncSet.add(w)

                # Then remove items already present in relations
                if len(clearedIncSet) > 0:
                    if len(withFields) > 0:
                        for r in relations:
                            clearedIncSet.discard(r[0])
                    else:
                        for r in relations:
                            clearedIncSet.discard(r)
    
                # Now collect info
                if len(clearedIncSet) > 0:
                    relations += [wikiData.getExistingWikiWordInfo(r,
                            withFields=withFields) for r in clearedIncSet]
    
            return relations


    def getProperties(self):
        with self.textOperationLock:
            if self.props is not None:
                return self.props
            
            data = self.getWikiData().getPropertiesForWord(self.wikiWord)

#         with self.textOperationLock:
#             if self.props is not None:
#                 return self.props

            self.props = {}
            for (key, val) in data:
                self._addProperty(key, val)
            
            return self.props

    def getPropertyOrGlobal(self, propkey, default=None):
        """
        Tries to find a property on this page and returns the first value.
        If it can't be found for page, it is searched for a global
        property with this name. If this also can't be found,
        default (normally None) is returned.
        """
        with self.textOperationLock:
            props = self.getProperties()
            if props.has_key(propkey):
                return props[propkey][-1]
            else:
                globalProps = self.getWikiData().getGlobalProperties()     
                return globalProps.get(u"global."+propkey, default)


    def _addProperty(self, key, val):
        values = self.props.get(key)
        if not values:
            values = []
            self.props[key] = values
        values.append(val)


    def getTodos(self):
        with self.textOperationLock:
            if self.todos is None:
                self.todos = self.getWikiData().getTodosForWord(self.wikiWord)
                        
            return self.todos


    def getAnchors(self):
        """
        Return sequence of anchors in page
        """
        pageAst = self.getLivePageAst()
        return [node.anchorLink
                for node in pageAst.iterDeepByName("anchorDef")]


    def getLiveTextNoTemplate(self):
        """
        Return None if page isn't existing instead of creating an automatic
        live text (e.g. by template).
        """
        with self.textOperationLock:
            if self.getTxtEditor() is not None:
                return self.getLiveText()
            else:
                if self.isDefined():
                    return self.getContent()
                else:
                    return None


    def getFormatDetails(self):
        """
        According to currently stored settings, return a
        ParseUtilities.WikiPageFormatDetails object to describe
        formatting
        """
        with self.textOperationLock:
            withCamelCase = strToBool(self.getPropertyOrGlobal(
                    u"camelCaseWordsEnabled"), True)
    
#             footnotesAsWws = self.wikiDocument.getWikiConfig().getboolean(
#                     "main", "footnotes_as_wikiwords", False)
    
            autoLinkMode = self.getPropertyOrGlobal(u"auto_link", u"off").lower()

            paragraphMode = strToBool(self.getPropertyOrGlobal(
                    u"paragraph_mode"), False)
                    
            langHelper = wx.GetApp().createWikiLanguageHelper(
                    self.wikiDocument.getWikiDefaultWikiLanguage())

            wikiLanguageDetails = langHelper.createWikiLanguageDetails(
                    self.wikiDocument, self)

            return ParseUtilities.WikiPageFormatDetails(
                    withCamelCase=withCamelCase,
                    wikiDocument=self.wikiDocument,
                    basePage=self,
                    autoLinkMode=autoLinkMode,
                    paragraphMode=paragraphMode,
                    wikiLanguageDetails=wikiLanguageDetails)


    def isDefined(self):
        return self.getWikiDocument().isDefinedWikiPage(self.getWikiWord())


    @staticmethod
    def extractPropertyNodesFromPageAst(pageAst):
        """
        Return an iterator of property nodes in pageAst. This does not return
        properties inside of todo entries.
        """
        return pageAst.iterUnselectedDeepByName("property",
                frozenset(("todoEntry",)))

    def _save(self, text, fireEvent=True):
        """
        Saves the content of current doc page.
        """
        pass


    def setPresentation(self, data, startPos):
        """
        Set (a part of) the presentation tuple. This is silently ignored
        if the "write access failed" or "read access failed" flags are
        set in the wiki document.
        data -- tuple with new presentation data
        startPos -- start position in the presentation tuple which should be
                overwritten with data.
        """
        raise NotImplementedError   # abstract


    def initiateUpdate(self, fireEvent=True):
        """
        Initiate update of page meta-data. This function may call update
        directly if can be done fast
        """
        pass


    def getLivePageAstIfAvailable(self):
        """
        Return the current, up-to-data page AST if available, None otherwise
        """
        with self.textOperationLock:
            # Current state
            text = self.getLiveText()
            formatDetails = self.getFormatDetails()

            # AST state
            pageAst = self.livePageAst
            baseFormatDetails = self.livePageBaseFormatDetails

            if pageAst is not None and \
                    baseFormatDetails is not None and \
                    formatDetails.isEquivTo(baseFormatDetails) and \
                    self.liveTextPlaceHold is self.livePageBasePlaceHold:
                return pageAst

            return None



    def getLivePageAst(self, fireEvent=True, dieOnChange=False,
            threadstop=DUMBTHREADSTOP, allowMetaDataUpdate=False):
        """
        Return PageAst of live text. In rare cases the live text may have
        changed while method is running and the result is inaccurate.
        """
#         if self.livePageAstBuildLock.acquire(False):
#             self.livePageAstBuildLock.release()
#         else:
#             if wx.Thread_IsMain(): traceback.print_stack()

        with self.livePageAstBuildLock:   # TODO: Timeout?
            threadstop.testRunning()

            with self.textOperationLock:
                text = self.getLiveText()
                liveTextPlaceHold = self.liveTextPlaceHold
                formatDetails = self.getFormatDetails()

                pageAst = self.getLivePageAstIfAvailable()

            if pageAst is not None:
                return pageAst

            if dieOnChange:
                if threadstop is DUMBTHREADSTOP:
                    threadstop = FunctionThreadStop(
                            lambda: liveTextPlaceHold is self.liveTextPlaceHold)
                else:
                    origThreadstop = threadstop
                    threadstop = FunctionThreadStop(
                            lambda: origThreadstop.isRunning() and 
                            liveTextPlaceHold is self.liveTextPlaceHold)

            if len(text) == 0:
                pageAst = buildSyntaxNode([], 0)
            else:
                pageAst = self.parseTextInContext(text, formatDetails=formatDetails,
                        threadstop=threadstop)

            with self.textOperationLock:
                threadstop.testRunning()

                self.livePageAst = pageAst
                self.livePageBasePlaceHold = liveTextPlaceHold
                self.livePageBaseFormatDetails = formatDetails


        if self.isReadOnlyEffect():
            threadstop.testRunning()
            return pageAst

#         if False and allowMetaDataUpdate:   # TODO: Option
#             self._refreshMetaData(pageAst, formatDetails, fireEvent=fireEvent,
#                     threadstop=threadstop)

        with self.textOperationLock:
            threadstop.testRunning()
            return pageAst


    def parseTextInContext(self, text, formatDetails=None,
            threadstop=DUMBTHREADSTOP):
        """
        Return PageAst of text in the context of this page (wiki language and
        format details).
        
        text: unistring with text
        """
        parser = wx.GetApp().createWikiParser(self.getWikiLanguageName()) # TODO debug mode  , True

        if formatDetails is None:
            formatDetails = self.getFormatDetails()

        try:
            pageAst = parser.parse(self.getWikiLanguageName(), text,
                    formatDetails, threadstop=threadstop)
        finally:
            wx.GetApp().freeWikiParser(parser)

        threadstop.testRunning()

        return pageAst


    _DEFAULT_PRESENTATION = (0, 0, 0, 0, 0, None)

    def getPresentation(self):
        """
        Get the presentation tuple (<cursor pos>, <editor scroll pos x>,
            <e.s.p. y>, <preview s.p. x>, <p.s.p. y>, <folding list>)
        The folding list may be None or a list of UInt32 numbers
        containing fold level, header flag and expand flag for each line
        in editor.
        """
        wikiData = self.wikiDocument.getWikiData()

        if wikiData is None:
            return AbstractWikiPage._DEFAULT_PRESENTATION

        datablock = wikiData.getPresentationBlock(
                self.getWikiWord())

        if datablock is None or datablock == "":
            return AbstractWikiPage._DEFAULT_PRESENTATION

        try:
            if len(datablock) == struct.calcsize("iiiii"):
                # Version 0
                return struct.unpack("iiiii", datablock) + (None,)
            else:
                ss = Serialization.SerializeStream(stringBuf=datablock)
                rcVer = ss.serUint8(1)
                if rcVer > 1:
                    return AbstractWikiPage._DEFAULT_PRESENTATION

                # Compatible to version 1                
                ver = ss.serUint8(1)
                pt = [ss.serInt32(0), ss.serInt32(0), ss.serInt32(0),
                        ss.serInt32(0), ss.serInt32(0), None]

                # Fold list
                fl = ss.serArrUint32([])
                if len(fl) == 0:
                    fl = None

                pt[5] = fl

                return tuple(pt)
        except struct.error:
            return AbstractWikiPage._DEFAULT_PRESENTATION




class WikiPage(AbstractWikiPage):
    """
    holds the data for a real wikipage (no alias).

    Fetched via the WikiDataManager.getWikiPage method.
    """
    def __init__(self, wikiDocument, wikiWord):
        AbstractWikiPage.__init__(self, wikiDocument, wikiWord)

        self.versionOverview = UNDEFINED


    def getVersionOverview(self):
        """
        Return Versioning.VersionOverview object. If necessary create one.
        """
        with self.textOperationLock:
            if self.versionOverview is UNDEFINED or self.versionOverview is None:
                from .timeView.Versioning import VersionOverview
                
                versionOverview = VersionOverview(self.getWikiDocument(),
                        self)
                versionOverview.readOverview()
                self.versionOverview = versionOverview
    
            return self.versionOverview


    def getExistingVersionOverview(self):
        """
        Return Versioning.VersionOverview object.
        If not existing already return None.
        """
        with self.textOperationLock:
            if self.versionOverview is UNDEFINED:
                from .timeView.Versioning import VersionOverview

                versionOverview = VersionOverview(self.getWikiDocument(),
                        self)

                if versionOverview.isNotInDatabase():
                    self.versionOverview = None
                else:
                    versionOverview.readOverview()
                    self.versionOverview = versionOverview

            return self.versionOverview

    def releaseVersionOverview(self):
        """
        Should only be called by VersionOverview.invalidate()
        """
        self.versionOverview = UNDEFINED


    def getNonAliasPage(self):
        """
        If this page belongs to an alias of a wiki word, return a page for
        the real one, otherwise return self.
        This class always returns self
        """
        return self


    def setPresentation(self, data, startPos):
        """
        Set (a part of) the presentation tuple. This is silently ignored
        if the "write access failed" or "read access failed" flags are
        set in the wiki document.
        data -- tuple with new presentation data
        startPos -- start position in the presentation tuple which should be
                overwritten with data.
        """
        if self.isReadOnlyEffect():
            return

        if self.wikiDocument.getReadAccessFailed() or \
                self.wikiDocument.getWriteAccessFailed():
            return

        try:
            pt = self.getPresentation()
            pt = pt[:startPos] + data + pt[startPos+len(data):]
    
            wikiData = self.wikiDocument.getWikiData()
            if wikiData is None:
                return
                
            if pt[5] is None:
                # Write it in old version 0
                wikiData.setPresentationBlock(self.getWikiWord(),
                        struct.pack("iiiii", *pt[:5]))
            else:
                # Write it in new version 1
                ss = Serialization.SerializeStream(stringBuf=True, readMode=False)
                ss.serUint8(1)  # Read compatibility version
                ss.serUint8(1)  # Real version
                # First five numbers
                for n in pt[:5]:
                    ss.serInt32(n)
                # Folding tuple
                ft = pt[5]
                if ft is None:
                    ft = ()
                ss.serArrUint32(pt[5])

                wikiData.setPresentationBlock(self.getWikiWord(),
                        ss.getBytes())

        except AttributeError:
            traceback.print_exc()


    def _changeHeadingForTemplate(self, content):
        """
        Return modified or unmodified content
        """
        # Prefix is normally u"++"
        pageTitlePrefix = \
                self.getWikiDocument().getPageTitlePrefix() + u" "
                
        if self.suggNewPageTitle is None:
            wikiWordHead = self.getWikiDocument().getWikiPageTitle(
                    self.getWikiWord())
        else:
            wikiWordHead = self.suggNewPageTitle

        if wikiWordHead is None:
            return content

        wikiWordHead = pageTitlePrefix + wikiWordHead + u"\n"

        # Remove current heading, if present. removeFirst holds number of
        # characters to remove at beginning when prepending new title 

        removeFirst = 0
        if content.startswith(pageTitlePrefix):
            try:
                removeFirst = content.index(u"\n", len(pageTitlePrefix)) + 1
            except ValueError:
                pass

        return wikiWordHead + content[removeFirst:]


    def getContent(self):
        """
        Returns page content. If page doesn't exist already the template
        creation is done here. After calling this function, properties
        are also accessible for a non-existing page
        """
        content = None
        try:
            content = self.getWikiData().getContent(self.wikiWord)
        except WikiFileNotFoundException, e:
            # Create initial content of new page

            # Check if there is exactly one parent
            parents = self.getParentRelationships()
            if len(parents) == 1:
                # Check if there is a template page
                try:
                    parentPage = self.wikiDocument.getWikiPage(parents[0])
                    # TODO Error checking
                    templateWord = parentPage.getPropertyOrGlobal("template")
                    templatePage = self.wikiDocument.getWikiPage(templateWord)

                    # getLiveText() would be more logical, but this may
                    # mean that content is up to date, while attributes
                    # are not updated.
                    content = templatePage.getContent()
                    # Load properties from template page
                    self.props = templatePage._cloneDeepProperties()
                    
                    # Check if template title should be changed
                    tplHeading = parentPage.getPropertyOrGlobal(
                            u"template_head", u"auto")
                    if tplHeading in (u"auto", u"automatic"):
                        content = self._changeHeadingForTemplate(content)
                except (WikiWordNotFoundException, WikiFileNotFoundException):
                    pass

            if content is None:
                if self.suggNewPageTitle is None:
                    title = self.getWikiDocument().getWikiPageTitle(
                            self.getWikiWord())
                else:
                    title = self.suggNewPageTitle

                if title is not None:
                    content = u"%s %s\n\n" % \
                            (self.wikiDocument.getPageTitlePrefix(),
                            title)
                else:
                    content = u""

        return content


#     def isDefined(self):
#         return self.getWikiDocument().isDefinedWikiPage(self.getWikiWord())


    def deletePage(self):
        """
        Deletes the page from database
        """
        with self.textOperationLock:
            if self.isReadOnlyEffect():
                return
    
            if self.isDefined():
                self.getWikiData().deleteWord(self.getWikiWord())

            vo = self.getExistingVersionOverview()
            if vo is not None:
                vo.delete()
                self.versionOverview = UNDEFINED

            wx.CallAfter(self.fireMiscEventKeys,
                    ("deleted page", "deleted wiki page"))


    def renameVersionData(self, newWord):
        """
        This is called by WikiDocument(=WikiDataManager) during
        WikiDocument.renameWikiWord() and shouldn't be called elsewhere.
        """
        with self.textOperationLock:
            vo = self.getExistingVersionOverview()
            if vo is None:
                return
            
            vo.renameTo(u"wikipage/" + newWord)
            self.versionOverview = UNDEFINED


    def informRenamedWikiPage(self, newWord):
        """
        Informs object that the page was renamed to newWord.
        This page object itself does not change its name but becomes invalid!

        This function should be called by WikiDocument(=WikiDataManager) only,
        use WikiDocument.renameWikiWord() to rename a page.
        """

        p = {}
        p["renamed page"] = True
        p["renamed wiki page"] = True
        p["newWord"] = newWord

        callInMainThreadAsync(self.fireMiscEventProps, p)


    def _cloneDeepProperties(self):
        with self.textOperationLock:
            result = {}
            for key, value in self.getProperties().iteritems():
                result[key] = value[:]
                
            return result


    def checkFileSignatureAndMarkDirty(self, fireEvent=True):
        """
        First checks if file signature is valid, if not, the
        "metadataprocessed" field of the word is set to 0 to mark
        meta-data as not up-to-date. At last the signature is
        refreshed.
        
        This all is done inside the lock of the WikiData so it is
        somewhat atomically.
        """
        with self.textOperationLock:
            if self.wikiDocument.isReadOnlyEffect():
                return True  # TODO Error message?
    
            if not self.isDefined():
                return True  # TODO Error message?
    
            wikiData = self.getWikiData()
            word = self.wikiWord

            proxyAccessLock = getattr(wikiData, "proxyAccessLock", None)
            if proxyAccessLock is not None:
                proxyAccessLock.acquire()
            try:
                valid = wikiData.validateFileSignatureForWord(word)
                
                if valid:
                    return True
    
                wikiData.setMetaDataState(word,
                        Consts.WIKIWORDMETADATA_STATE_DIRTY)
                wikiData.refreshFileSignatureForWord(word)
                self.markTextChanged()
            finally:
                if proxyAccessLock is not None:
                    proxyAccessLock.release()

            editor = self.getTxtEditor()
        
        if editor is not None:
            # TODO Check for deadlocks
            callInMainThread(editor.handleInvalidFileSignature, self)

        if fireEvent:
            wx.CallAfter(self.fireMiscEventKeys,
                    ("checked file signature invalid",))

        return False


    def markMetaDataDirty(self):
        self.getWikiData().setMetaDataState(self.wikiWord,
                Consts.WIKIWORDMETADATA_STATE_DIRTY)


    def _refreshMetaData(self, pageAst, formatDetails, fireEvent=True,
            threadstop=DUMBTHREADSTOP):

        self.refreshPropertiesFromPageAst(pageAst, threadstop=threadstop)

        formatDetails2 = self.getFormatDetails()
        if not formatDetails.isEquivTo(formatDetails2):
            # Formatting details have changed -> stop and wait for
            # new round to update
            return False

        return self.refreshMainDbCacheFromPageAst(pageAst, fireEvent=fireEvent,
                threadstop=threadstop)


    def refreshSyncUpdateMatchTerms(self):
        """
        Refresh those match terms which must be refreshed synchronously
        """
        if self.isReadOnlyEffect():
            return

        WORD_TYPE = Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK | \
                Consts.WIKIWORDMATCHTERMS_TYPE_FROM_WORD | \
                Consts.WIKIWORDMATCHTERMS_TYPE_SYNCUPDATE

        matchTerms = [(self.wikiWord, WORD_TYPE, self.wikiWord, -1)]
        self.getWikiData().updateWikiWordMatchTerms(self.wikiWord, matchTerms,
                syncUpdate=True)


    def refreshPropertiesFromPageAst(self, pageAst, threadstop=DUMBTHREADSTOP):
        """
        Update properties (aka attributes) only.
        This is step one in update/rebuild process.
        """
        if self.isReadOnlyEffect():
            return True  # TODO Error?

        langHelper = wx.GetApp().createWikiLanguageHelper(
                self.getWikiLanguageName())

#         self.deleteProperties()

        props = {}

        def addProperty(key, value):
            threadstop.testRunning()
            values = props.get(key)
            if not values:
                values = []
                props[key] = values
            values.append(value)


        propNodes = self.extractPropertyNodesFromPageAst(pageAst)
        for node in propNodes:
            for propKey, propValue in node.props:
                addProperty(propKey, propValue)

        with self.textOperationLock:
            threadstop.testRunning()

            self.props = None

        try:
            self.getWikiData().updateProperties(self.wikiWord, props)
        except WikiWordNotFoundException:
            return False

        valid = False

        with self.textOperationLock:
#             print "--refreshPropertiesFromPageAst43", repr((self.wikiWord, self.saveDirtySince,
#                     self.livePageBasePlaceHold is self.liveTextPlaceHold,
#                     self.livePageBaseFormatDetails is not None,
# #                     self.getFormatDetails().isEquivTo(self.livePageBaseFormatDetails),
#                     pageAst is self.livePageAst))

            if self.saveDirtySince is None and \
                    self.livePageBasePlaceHold is self.liveTextPlaceHold and \
                    self.livePageBaseFormatDetails is not None and \
                    self.getFormatDetails().isEquivTo(self.livePageBaseFormatDetails) and \
                    pageAst is self.livePageAst:

                threadstop.testRunning()
                # clear the dirty flag

                self.getWikiData().setMetaDataState(self.wikiWord,
                        Consts.WIKIWORDMETADATA_STATE_PROPSPROCESSED)

                valid = True

        return valid



    def refreshMainDbCacheFromPageAst(self, pageAst, fireEvent=True,
            threadstop=DUMBTHREADSTOP):
        """
        Update everything else (todos, relations).
        This is step two in update/rebuild process.
        """
        if self.isReadOnlyEffect():
            return True   # return True or False?

        todos = []
        childRelations = []
        childRelationSet = set()

        def addTodo(todo):
            threadstop.testRunning()
            if todo not in todos:
                todos.append(todo)

        def addChildRelationship(toWord, pos):
            threadstop.testRunning()
            if toWord not in childRelationSet:
                childRelations.append((toWord, pos))
                childRelationSet.add(toWord)

        # Add todo entries
        todoTokens = pageAst.iterDeepByName("todoEntry")
        for t in todoTokens:
            addTodo(t.key + t.delimiter + t.valueNode.getString())
        
        threadstop.testRunning()

        # Add child relations
        wwTokens = pageAst.iterDeepByName("wikiWord")
        for t in wwTokens:
            addChildRelationship(t.wikiWord, t.pos)

        threadstop.testRunning()
        
        # Add aliases to match terms
        matchTerms = []

        ALIAS_TYPE = Consts.WIKIWORDMATCHTERMS_TYPE_EXPLICIT_ALIAS | \
                Consts.WIKIWORDMATCHTERMS_TYPE_ASLINK | \
                Consts.WIKIWORDMATCHTERMS_TYPE_FROM_PROPERTIES

        langHelper = wx.GetApp().createWikiLanguageHelper(
                self.getWikiLanguageName())

        for w, k, v in self.getWikiDocument().getPropertyTriples(
                self.wikiWord, u"alias", None):
            threadstop.testRunning()
            if not langHelper.checkForInvalidWikiWord(v,
                    self.getWikiDocument()):
                matchTerms.append((v, ALIAS_TYPE, self.wikiWord, -1))

        # Add headings to match terms if wanted
        depth = self.wikiDocument.getWikiConfig().getint(
                "main", "headingsAsAliases_depth")

        if depth > 0:
            HEADALIAS_TYPE = Consts.WIKIWORDMATCHTERMS_TYPE_FROM_CONTENT
            for node in pageAst.iterFlatByName("heading"):
                threadstop.testRunning()
                if node.level > depth:
                    continue

                title = node.getString()
                if title.endswith(u"\n"):
                    title = title[:-1]
                
                matchTerms.append((title, HEADALIAS_TYPE, self.wikiWord,
                        node.pos))

        with self.textOperationLock:
            threadstop.testRunning()

            self.todos = None
            self.childRelations = None
            self.childRelationSet = set()
        try:
            self.getWikiData().updateTodos(self.wikiWord, todos)
            threadstop.testRunning()
            self.getWikiData().updateChildRelations(self.wikiWord, childRelations)
            threadstop.testRunning()
            self.getWikiData().updateWikiWordMatchTerms(self.wikiWord, matchTerms)
            threadstop.testRunning()
        except WikiWordNotFoundException:
            return False
#             self.modified = None   # ?
#             self.created = None


            # Now we check the whole chain if flags can be set:
            # db content is identical to liveText
            # liveText is basis of current livePageAst
            # formatDetails are same as the ones used for livePageAst
            # and livePageAst is identical to pageAst processed in this method

        valid = False
        with self.textOperationLock:
#             print "--refreshMainDbCacheFromPageAst43", repr((self.wikiWord, self.saveDirtySince,
#                     self.livePageBasePlaceHold is self.liveTextPlaceHold,
#                     self.livePageBaseFormatDetails is not None,
# #                     self.getFormatDetails().isEquivTo(self.livePageBaseFormatDetails),
#                     pageAst is self.livePageAst))
            if self.saveDirtySince is None and \
                    self.livePageBasePlaceHold is self.liveTextPlaceHold and \
                    self.livePageBaseFormatDetails is not None and \
                    self.getFormatDetails().isEquivTo(self.livePageBaseFormatDetails) and \
                    pageAst is self.livePageAst:

                threadstop.testRunning()
                # clear the dirty flag
                self.updateDirtySince = None

                self.getWikiData().setMetaDataState(self.wikiWord,
                        Consts.WIKIWORDMETADATA_STATE_UPTODATE)
                valid = True

        if fireEvent:
            callInMainThreadAsync(self.fireMiscEventKeys,
                    ("updated wiki page", "updated page"))

        return valid


#     def update(self):
#         return self.runDatabaseUpdate(step=-2)

    def runDatabaseUpdate(self, step=-1, threadstop=DUMBTHREADSTOP):
        with self.textOperationLock:
            if not self.isDefined():
                return False
            if self.isReadOnlyEffect():
                return False

            liveTextPlaceHold = self.liveTextPlaceHold
            formatDetails = self.getFormatDetails()

        try:
            pageAst = self.getLivePageAst(dieOnChange=True,
                    threadstop=threadstop)

            # Check within lock if data is current yet
            with self.textOperationLock:
                if not liveTextPlaceHold is self.liveTextPlaceHold:
                    return False
    
    
            if step == -1:
                self._refreshMetaData(pageAst, formatDetails, threadstop=threadstop)
    
                with self.textOperationLock:
                    if not liveTextPlaceHold is self.liveTextPlaceHold:
                        return False
                    if not formatDetails.isEquivTo(self.getFormatDetails()):
                        self.initiateUpdate()
                        return False
#             elif step == -2:
#                 for i in range(15):   # while True  is too dangerous
#                     metaState = self.getWikiData().getMetaDataState(self.wikiWord)
# 
#                     if not liveTextPlaceHold is self.liveTextPlaceHold:
#                         return False
#                     if not formatDetails.isEquivTo(self.getFormatDetails()):
#                         self.initiateUpdate()
#                         return False
# 
#                     if metaState == Consts.WIKIWORDMETADATA_STATE_UPTODATE:
#                         return True
# 
#                     elif metaState == Consts.WIKIWORDMETADATA_STATE_PROPSPROCESSED:
#                         self.refreshMainDbCacheFromPageAst(pageAst,
#                                 threadstop=threadstop)
#                         continue
# 
#                     else: # step == Consts.WIKIWORDMETADATA_STATE_DIRTY
#                         self.refreshPropertiesFromPageAst(pageAst,
#                                 threadstop=threadstop)
#                         continue
            else:
                metaState = self.getWikiData().getMetaDataState(self.wikiWord)
    
                if metaState == Consts.WIKIWORDMETADATA_STATE_UPTODATE or \
                        metaState != step:
                    return False
    
                if step == Consts.WIKIWORDMETADATA_STATE_PROPSPROCESSED:
                    return self.refreshMainDbCacheFromPageAst(pageAst,
                            threadstop=threadstop)
    
                else: # step == Consts.WIKIWORDMETADATA_STATE_DIRTY
                    return self.refreshPropertiesFromPageAst(pageAst,
                            threadstop=threadstop)

        except NotCurrentThreadException:
            return False



    def initiateUpdate(self, fireEvent=True):
        """
        Initiate update of page meta-data. This function may call update
        directly if it can be done fast
        """
        with self.textOperationLock:
            self.wikiDocument.pushUpdatePage(self)


    def _save(self, text, fireEvent=True):
        """
        Saves the content of current wiki page.
        """
        if self.isReadOnlyEffect():
            return
        
        with self.textOperationLock:
            if not self.getWikiDocument().isDefinedWikiPage(self.wikiWord):
                # Pages isn't yet in database  -> fire event
                # The event may be needed to invalidate a cache
                self.fireMiscEventKeys(("saving new wiki page",))

            self.getWikiData().setContent(self.wikiWord, text)
            self.refreshSyncUpdateMatchTerms()
            self.saveDirtySince = None
#             self.dbContentPlaceHold = object()
            if self.getEditorText() is None:
                self.liveTextPlaceHold = object()


            # Clear timestamp cache
            self.modified = None




    # ----- Advanced functions -----

    def getChildRelationshipsTreeOrder(self, existingonly=False,
            excludeSet=frozenset(), includeSet=frozenset()):
        """
        Return a list of children wiki words of the page, ordered as they would
        appear in tree. Some children may be missing if they e.g.
        are set as hidden.
        existingonly -- true iff non-existing words should be hidden
        excludeSet -- set of words which should be excluded from the list
        includeSet -- wikiWords to include in the result
        """
        
        wikiDocument = self.wikiDocument
        
        # get the sort order for the children
        childSortOrder = self.getPropertyOrGlobal(u'child_sort_order',
                u"ascending")
            
        # Apply sort order
        if childSortOrder == u"natural":
            # TODO: Do it right 
            # Retrieve relations as list of tuples (child, firstcharpos)
            relations = self.getChildRelationships(existingonly,
                    selfreference=False, withFields=("firstcharpos",),
                    excludeSet=excludeSet, includeSet=includeSet)

            relations.sort(_cmpNumbersItem1)
            # Remove firstcharpos
            relations = [r[0] for r in relations]
        elif childSortOrder == u"mod_oldest":
            # Retrieve relations as list of tuples (child, modifTime)
            relations = self.getChildRelationships(existingonly,
                    selfreference=False, withFields=("modified",),
                    excludeSet=excludeSet, includeSet=includeSet)
            relations.sort(_cmpNumbersItem1)
            # Remove firstcharpos
            relations = [r[0] for r in relations]
        elif childSortOrder == u"mod_newest":
            # Retrieve relations as list of tuples (child, modifTime)
            relations = self.getChildRelationships(existingonly,
                    selfreference=False, withFields=("modified",),
                    excludeSet=excludeSet, includeSet=includeSet)
            relations.sort(_cmpNumbersItem1Rev)
            # Remove firstcharpos
            relations = [r[0] for r in relations]            
        else:
            # Retrieve relations as list of children words
            relations = self.getChildRelationships(existingonly, 
                    selfreference=False, withFields=(),
                    excludeSet=excludeSet, includeSet=includeSet)
            if childSortOrder.startswith(u"desc"):
                coll = wikiDocument.getCollator()

                def cmpLowerDesc(a, b):
                    return coll.strcoll(
                            b.lower(), a.lower())
                            
                # TODO Python 3.0 supports only key argument, no cmp. function
                relations.sort(cmpLowerDesc) # sort alphabetically
            elif childSortOrder.startswith(u"asc"):
                coll = wikiDocument.getCollator()

                def cmpLowerAsc(a, b):
                    return coll.strcoll(
                            a.lower(), b.lower())

                relations.sort(cmpLowerAsc)



        priorized = []
        positioned = []
        other = []

        # Put relations into their appropriate arrays
        for relation in relations:
            relationPage = wikiDocument.getWikiPageNoError(relation)
            props = relationPage.getProperties()
            try:
                if (props.has_key(u'tree_position')):
                    positioned.append((int(props[u'tree_position'][-1]) - 1, relation))
                elif (props.has_key(u'priority')):
                    priorized.append((int(props[u'priority'][-1]), relation))
                else:
                    other.append(relation)
            except:
                other.append(relation)
                
        # Sort special arrays
        priorized.sort(key=lambda t: t[0])
        positioned.sort(key=lambda t: t[0])


        result = []
        ipr = 0
        ipo = 0
        iot = 0

        for i in xrange(len(relations)):
            if ipo < len(positioned) and positioned[ipo][0] <= i:
                result.append(positioned[ipo][1])
                ipo += 1
                continue
            
            if ipr < len(priorized):
                result.append(priorized[ipr][1])
                ipr += 1
                continue
            
            if iot < len(other):
                result.append(other[iot])
                iot += 1
                continue
            
            # When reaching this, only positioned can have elements yet
            if ipo < len(positioned):
                result.append(positioned[ipo][1])
                ipo += 1
                continue
            
            raise InternalError("Empty relation sorting arrays")
        

        return result


        # TODO Remove aliases?
    def _flatTreeHelper(self, page, deepness, excludeSet, includeSet, result,
            unalias):
        """
        Recursive part of getFlatTree
        """
#         print "_flatTreeHelper1", repr((page.getWikiWord(), deepness, len(excludeSet)))

        word = page.getWikiWord()
        nonAliasWord = page.getNonAliasPage().getWikiWord()
        excludeSet.add(nonAliasWord)

        children = page.getChildRelationshipsTreeOrder(existingonly=True)

        for word in children:
            subpage = self.wikiDocument.getWikiPage(word)
            nonAliasWord = subpage.getNonAliasPage().getWikiWord()
            if nonAliasWord in excludeSet:
                continue
            if unalias:
                result.append((nonAliasWord, deepness + 1))
            else:
                result.append((word, deepness + 1))
            
            if includeSet is not None:
                includeSet.discard(word)
                includeSet.discard(nonAliasWord)
                if len(includeSet) == 0:
                    return
            
            self._flatTreeHelper(subpage, deepness + 1, excludeSet, includeSet,
                    result, unalias)


    def getFlatTree(self, unalias=False, includeSet=None):
        """
        Returns a sequence of tuples (word, deepness) where the current
        word is the first one with deepness 0.
        The words may contain aliases, but no word appears twice neither
        will both a word and its alias appear in the list.
        unalias -- replace all aliases by their real word
        TODO EXPLAIN FUNCTION !!!
        """
        word = self.getWikiWord()
        nonAliasWord = self.getNonAliasPage().getWikiWord()

        if unalias:
            result = [(nonAliasWord, 0)]
        else:
            result = [(word, 0)]

        if includeSet is not None:
            includeSet.discard(word)
            includeSet.discard(nonAliasWord)
            if len(includeSet) == 0:
                return result

        excludeSet = set()   # set((self.getWikiWord(),))

        self._flatTreeHelper(self, 0, excludeSet, includeSet, result, unalias)

#         print "getFlatTree", repr(result)

        return result


    def getDependentDataBlocks(self):
        vo = self.getExistingVersionOverview()
        
        if vo is None:
            return []
        
        return vo.getDependentDataBlocks()
        

    def serializeOverviewToXml(self, xmlNode, xmlDoc):
        """
        Create XML node to contain overview information (neither content nor
        version overview) about this object.
        """
#         Serialization.serToXmlUnicode(xmlNode, xmlDoc, u"unifiedName",
#                 self.getUnifiedPageName(), replace=True)

        timeStamps = self.getTimestamps()[:3]

        # Do not use StringOps.strftimeUB here as its output
        # relates to local time, but we need UTC here.
        timeStrings = [unicode(time.strftime(
                "%Y-%m-%d/%H:%M:%S", time.gmtime(ts)))
                for ts in timeStamps]
        
        tsNode = Serialization.findOrAppendXmlElementFlat(xmlNode, xmlDoc,
            u"timeStamps")

        tsNode.setAttribute(u"modificationTime", timeStrings[0])
        tsNode.setAttribute(u"creationTime", timeStrings[1])
        tsNode.setAttribute(u"visitTime", timeStrings[2])


    def serializeOverviewFromXml(self, xmlNode):
        """
        Set object state from data in xmlNode
        """
        tsNode = Serialization.findXmlElementFlat(xmlNode, 
            u"timeStamps", excOnFail=False)
        
        if tsNode is not None:
            timeStrings = [u""] * 3
            timeStrings[0] = tsNode.getAttribute(u"modificationTime")
            timeStrings[1] = tsNode.getAttribute(u"creationTime")
            timeStrings[2] = tsNode.getAttribute(u"visitTime")

        timeStamps = []
        for tstr in timeStrings:
            if tstr == u"":
                timeStamps.append(0.0)
            else:
                timeStamps.append(timegm(time.strptime(tstr,
                        "%Y-%m-%d/%H:%M:%S")))

        self.setTimestamps(timeStamps)

        self.versionNumber = serFromXmlInt(xmlNode, u"versionNumber")




# TODO Maybe split into single classes for each tag

class FunctionalPage(DataCarryingPage):
    """
    holds the data for a functional page. Such a page controls the behavior
    of the application or a special wiki
    """
    def __init__(self, wikiDocument, funcTag):
        DataCarryingPage.__init__(self, wikiDocument)
        
        if not isFuncTag(funcTag):
            raise BadFuncPageTagException(
                    _(u"Func. tag %s does not exist") % funcTag)

        self.funcTag = funcTag

        # does this page need to be saved?
        self.saveDirtySince = None  # None if not dirty or timestamp when it became dirty
        self.updateDirtySince = None


    def getWikiWord(self):
        return None

    def getTitle(self):
        """
        Return human readable title of the page.
        """
        return u"<" + getHrNameForFuncTag(self.funcTag) + u">"


    def getFuncTag(self):
        """
        Return the functional tag of the page (a kind of filepath
        for the page)
        """
        return self.funcTag

    def getUnifiedPageName(self):
        """
        Return the name of the unified name of the page, which is
        "wikipage/" + the wiki word for wiki pages or the functional tag
        for functional pages.
        """
        return self.funcTag


    def _loadGlobalPage(self, subtag):
        tbLoc = os.path.join(wx.GetApp().getGlobalConfigSubDir(),
                "[%s].wiki" % subtag)
        try:
            tbContent = loadEntireTxtFile(tbLoc)
            return fileContentToUnicode(lineendToInternal(tbContent))
        except:
            return u""


    def _loadDbSpecificPage(self, funcTag):
        content = self.wikiDocument.getWikiData().retrieveDataBlockAsText(funcTag)
        if content is None:
            return u""
        
        return content

#         if self.wikiDocument.isDefinedWikiWord(subtag):
#             return self.wikiDocument.getWikiData().getContent(subtag)
#         else:
#             return u""


    def getLiveTextNoTemplate(self):
        """
        Return None if page isn't existing instead of creating an automatic
        live text (e.g. by template).
        Functional pages by definition exist always 
        """
        return self.getLiveText()


    def getContent(self):
        if self.funcTag in (u"global/TextBlocks", u"global/PWL",
                u"global/CCBlacklist", u"global/FavoriteWikis"):
            return self._loadGlobalPage(self.funcTag[7:])
        elif self.funcTag in (u"wiki/TextBlocks", u"wiki/PWL",
                u"wiki/CCBlacklist"):
            return self._loadDbSpecificPage(self.funcTag)


    def getFormatDetails(self):
        """
        According to currently stored settings, return a
        ParseUtilities.WikiPageFormatDetails object to describe
        formatting.
        
        For functional pages this is normally no formatting
        """
        return ParseUtilities.WikiPageFormatDetails(noFormat=True)


    def getLivePageAstIfAvailable(self):
        return self.getLivePageAst()


    # TODO Checking with dieOnChange == True
    def getLivePageAst(self, fireEvent=True, dieOnChange=False,
            threadstop=DUMBTHREADSTOP):
        """
        The PageAst of a func. page is always a single "default" token
        containing the whole text.
        """
        with self.livePageAstBuildLock:
            threadstop.testRunning()
    
            pageAst = self.livePageAst
            
            if pageAst is not None:
                return pageAst

            with self.textOperationLock:
                pageAst = buildSyntaxNode([buildSyntaxNode(
                        self.getLiveText(), 0, "plainText")], 0, "text")

                threadstop.testRunning()

                self.livePageAst = pageAst

                return pageAst



    def _saveGlobalPage(self, text, subtag):
        tbLoc = os.path.join(wx.GetApp().getGlobalConfigSubDir(),
                "[%s].wiki" % subtag)

        writeEntireFile(tbLoc, text, True)


    def _saveDbSpecificPage(self, text, funcTag):
        if self.isReadOnlyEffect():
            return

        wikiData = self.wikiDocument.getWikiData()
        
        if text == u"":
            wikiData.deleteDataBlock(funcTag)
        else:
            wikiData.storeDataBlock(funcTag, text,
                    storeHint=Consts.DATABLOCK_STOREHINT_EXTERN)


#         if self.wikiDocument.isDefinedWikiWord(subtag) and text == u"":
#             # Delete content
#             wikiData.deleteContent(subtag)
#         else:
#             if text != u"":
#                 wikiData.setContent(subtag, text)


    def _save(self, text, fireEvent=True):
        """
        Saves the content of current wiki page.
        """
        if self.isReadOnlyEffect():
            return
        
        with self.textOperationLock:
            # text = self.getLiveText()
    
            if self.funcTag in (u"global/TextBlocks", u"global/PWL",
                    u"global/CCBlacklist", u"global/FavoriteWikis"):
                self._saveGlobalPage(text, self.funcTag[7:])
            elif self.funcTag in (u"wiki/TextBlocks", u"wiki/PWL",
                    u"wiki/CCBlacklist"):
                self._saveDbSpecificPage(text, self.funcTag)

            self.saveDirtySince = None



    def initiateUpdate(self, fireEvent=True):
        """
        Update additional cached informations (properties, todos, relations).
        Here it is done directly in initiateUpdate() because it doesn't need
        much work.
        """
        if self.isReadOnlyEffect():
            return

        with self.textOperationLock:
            # clear the dirty flag
            self.updateDirtySince = None
    
            if fireEvent:
                if self.funcTag.startswith(u"wiki/"):
                    evtSource = self
                else:
                    evtSource = wx.GetApp()
    
                if self.funcTag in (u"global/TextBlocks", u"wiki/TextBlocks"):
                    # The text blocks for the text blocks submenu was updated
                    evtSource.fireMiscEventKeys(("updated func page", "updated page",
                            "reread text blocks needed"))
                elif self.funcTag in (u"global/PWL", u"wiki/PWL"):
                    # The personal word list (words to ignore by spell checker)
                    # was updated
                    evtSource.fireMiscEventKeys(("updated func page", "updated page",
                            "reread personal word list needed"))
                elif self.funcTag in (u"global/CCBlacklist", u"wiki/CCBlacklist"):
                    # The blacklist of camelcase words not to mark as wiki links
                    # was updated
                    evtSource.fireMiscEventKeys(("updated func page", "updated page",
                            "reread cc blacklist needed"))
                elif self.funcTag == u"global/FavoriteWikis":
                    # The list of favorite wikis was updated (there is no
                    # wiki-bound version of favorite wikis
                    evtSource.fireMiscEventKeys(("updated func page", "updated page",
                            "reread favorite wikis needed"))

    def isReadOnlyEffect(self):
        """
        Return true if page is effectively read-only, this means
        "for any reason", regardless if error or intention.
        Global func. pages do not depend on the wiki state so they are writable.
        """
        if self.funcTag.startswith(u"global/"):
            # Global pages are not stored in the wiki and are always writable
            return False
        else:
            return DataCarryingPage.isReadOnlyEffect(self)


    def getPresentation(self):
        """Dummy"""
        return (0, 0, 0, 0, 0)

    def setPresentation(self, data, startPos):
        """Dummy"""
        pass
        



# Two search helpers for WikiPage.getChildRelationshipsTreeOrder

def _floatToCompInt(f):
    if f > 0:
        return 1
    elif f < 0:
        return -1
    else:
        return 0



# TODO: Remove for Python 3.0
def _cmpNumbersItem1(a, b):
    """
    Compare "natural", means using the char. positions or moddates of
    the links in page.
    """
    return _floatToCompInt(a[1] - b[1])


def _cmpNumbersItem1Rev(a, b):
    """
    Compare "natural", means using the char. positions or moddates of
    the links in page.
    """
    return _floatToCompInt(b[1] - a[1])



# TODO: Allow localization (then, this map must be created after localization is
#     set or changed.
_FUNCTAG_TO_HR_NAME_MAP = {
            u"global/TextBlocks": N_(u"Global text blocks"),
            u"wiki/TextBlocks": N_(u"Wiki text blocks"),
            u"global/PWL": N_(u"Global spell list"),
            u"wiki/PWL": N_(u"Wiki spell list"),
            u"global/CCBlacklist": N_(u"Global cc. blacklist"),
            u"wiki/CCBlacklist": N_(u"Wiki cc. blacklist"),
            u"global/FavoriteWikis": N_(u"Favorite wikis"),
        }


def getHrNameForFuncTag(funcTag):
    """
    Return the human readable name of functional page with tag funcTag.
    """
    return _(_FUNCTAG_TO_HR_NAME_MAP.get(funcTag, funcTag))
    

def getFuncTags():
    """
    Return all available func tags
    """
    return _FUNCTAG_TO_HR_NAME_MAP.keys()


def isFuncTag(funcTag):
    return _FUNCTAG_TO_HR_NAME_MAP.has_key(funcTag)

