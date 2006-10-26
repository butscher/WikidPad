import sys, traceback
from time import strftime
import re

from os.path import exists, isdir, isfile

from wxPython.wx import *
from wxPython.html import *
import wxPython.xrc as xrc

from wxHelper import *

from StringOps import uniToGui, guiToUni, mbcsEnc, mbcsDec, \
        wikiWordToLabel, escapeForIni, unescapeForIni, escapeHtml
import WikiFormatting
from WikiExceptions import *
import Exporters, Importers

from WikidPadStarter import VERSION_STRING

from SearchAndReplaceDialogs import WikiPageListConstructionDialog
from SearchAndReplace import ListWikiPagesOperation


class OpenWikiWordDialog(wxDialog):
    def __init__(self, pWiki, ID, title="Open Wiki Word",
                 pos=wxDefaultPosition, size=wxDefaultSize,
                 style=wxNO_3D):

        d = wxPreDialog()
        self.PostCreate(d)
        
        self.pWiki = pWiki
        self.value = None     
        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, self.pWiki, "OpenWikiWordDialog")

        self.SetTitle(title)

        self.ctrls = XrcControls(self)

        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)

        EVT_BUTTON(self, wxID_OK, self.OnOk)
        # EVT_TEXT(self, XRCID("text"), self.OnText) 

        EVT_TEXT(self, ID, self.OnText)
        EVT_CHAR(self.ctrls.text, self.OnCharText)
        EVT_CHAR(self.ctrls.lb, self.OnCharListBox)
        EVT_LISTBOX(self, ID, self.OnListBox)
        EVT_LISTBOX_DCLICK(self, XRCID("lb"), self.OnOk)
        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_BUTTON(self, XRCID("btnCreate"), self.OnCreate)
        
    def OnOk(self, evt):
        if not self.pWiki.getWikiData().isDefinedWikiWord(self.value):
#             words = self.pWiki.getWikiData().getWikiWordsWith(self.value.lower(),
#                     True)
            words = self.pWiki.getWikiData().getWikiWordsWith(self.value,
                    True)
            if len(words) > 0:
                self.value = words[0]
            else:
                wikiWord = self.value
                nakedWord = wikiWordToLabel(wikiWord)

                if not self.pWiki.getFormatting().isNakedWikiWord(nakedWord):
                    # Entered text is not a valid wiki word
                    self.ctrls.text.SetFocus()
                    return

                # wikiWord is valid but nonexisting, so maybe create it?
                result = wxMessageBox(
                        uniToGui(u"'%s' is not an existing wikiword. Create?" %
                        wikiWord), uniToGui(u"Create"),
                        wxYES_NO | wxYES_DEFAULT | wxICON_QUESTION, self)

                if result == wxNO:
                    self.ctrls.text.SetFocus()
                    return
                
                self.value = wikiWord
                                
        self.EndModal(wxID_OK)
        
                
    def GetValue(self):
        return self.value

    def OnText(self, evt):
        self.value = guiToUni(evt.GetString())
        self.ctrls.lb.Clear()
        if len(self.value) > 0:
#             words = self.pWiki.getWikiData().getWikiWordsWith(self.value.lower(),
#                     True)
            words = self.pWiki.getWikiData().getWikiWordsWith(self.value,
                    True)
            for word in words:
                self.ctrls.lb.Append(word)

    def OnListBox(self, evt):
        self.value = guiToUni(evt.GetString())

    def OnCharText(self, evt):
        if (evt.GetKeyCode() == WXK_DOWN) and not self.ctrls.lb.IsEmpty():
            self.ctrls.lb.SetFocus()
            self.ctrls.lb.SetSelection(0)
        elif (evt.GetKeyCode() == WXK_UP):
            pass
        else:
            evt.Skip()
            

    def OnCharListBox(self, evt):
        if (evt.GetKeyCode() == WXK_UP) and (self.ctrls.lb.GetSelection() == 0):
            self.ctrls.text.SetFocus()
            self.ctrls.lb.Deselect(0)
        else:
            evt.Skip()
            
            
    def OnCreate(self, evt):
        """
        Create new WikiWord
        """
        nakedWord = wikiWordToLabel(self.value)
        if not self.pWiki.getFormatting().isNakedWikiWord(nakedWord):
            self.pWiki.displayErrorMessage(u"'%s' is an invalid WikiWord" % nakedWord)
            self.ctrls.text.SetFocus()
            return
        
        if self.pWiki.getWikiData().isDefinedWikiWord(nakedWord):
            self.pWiki.displayErrorMessage(u"'%s' exists already" % nakedWord)
            self.ctrls.text.SetFocus()
            return
            
        self.value = nakedWord
        self.EndModal(wxID_OK)
 
 

class IconSelectDialog(wxDialog):
    def __init__(self, parent, ID, iconCache, title="Select Icon",
                 pos=wxDefaultPosition, size=wxDefaultSize,
                 style=wxNO_3D|wxDEFAULT_DIALOG_STYLE|wxRESIZE_BORDER):
        wxDialog.__init__(self, parent, ID, title, pos, size, style)

        self.iconCache = iconCache
        self.iconImageList = self.iconCache.iconImageList
        
        self.iconNames = [n for n in self.iconCache.iconLookupCache.keys()
                if not n.startswith("tb_")]
#         filter(lambda n: not n.startswith("tb_"),
#                 self.iconCache.iconLookupCache.keys())
        self.iconNames.sort()
        
        # Now continue with the normal construction of the dialog
        # contents
        sizer = wxBoxSizer(wxVERTICAL)

        label = wxStaticText(self, -1, "Select Icon")
        sizer.Add(label, 0, wxALIGN_CENTRE|wxALL, 5)

        box = wxBoxSizer(wxVERTICAL)

        self.lc = wxListCtrl(self, -1, wxDefaultPosition, wxSize(145, 200), 
                style = wxLC_REPORT | wxLC_NO_HEADER)    ## | wxBORDER_NONE
                
        self.lc.SetImageList(self.iconImageList, wxIMAGE_LIST_SMALL)
        self.lc.InsertColumn(0, "Icon")

        for icn in self.iconNames:
            self.lc.InsertImageStringItem(sys.maxint, icn,
                    self.iconCache.lookupIconIndex(icn))
        self.lc.SetColumnWidth(0, wxLIST_AUTOSIZE)
        
        
        box.Add(self.lc, 1, wxALIGN_CENTRE|wxALL|wxEXPAND, 5)

        sizer.Add(box, 1, wxGROW|wxALIGN_CENTER_VERTICAL|wxALL, 5)

        line = wxStaticLine(self, -1, size=(20,-1), style=wxLI_HORIZONTAL)
        sizer.Add(line, 0, wxGROW|wxALIGN_CENTER_VERTICAL|wxRIGHT|wxTOP, 5)

        box = wxBoxSizer(wxHORIZONTAL)

        btn = wxButton(self, wxID_OK, " OK ")
        btn.SetDefault()
        box.Add(btn, 0, wxALIGN_CENTRE|wxALL, 5)

        btn = wxButton(self, wxID_CANCEL, " Cancel ")
        box.Add(btn, 0, wxALIGN_CENTRE|wxALL, 5)

        sizer.Add(box, 0, wxALIGN_CENTER_VERTICAL|wxALL, 5)

        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        sizer.Fit(self)

        self.value = None

        EVT_BUTTON(self, wxID_OK, self.OnOkPressed)
        EVT_LIST_ITEM_ACTIVATED(self, self.lc.GetId(), self.OnOkPressed)

    def GetValue(self):
        """
        Return name of selected icon or None
        """
        return self.value    


    def OnOkPressed(self, evt):
        no = self.lc.GetNextItem(-1, state = wxLIST_STATE_SELECTED)
        if no > -1:
            self.value = self.iconNames[no]
        else:
            self.value = None
            
        self.EndModal(wxID_OK)



class SavedVersionsDialog(wxDialog):
    def __init__(self, pWiki, ID, title="Saved Versions",
                 pos=wxDefaultPosition, size=wxDefaultSize,
                 style=wxNO_3D):
        wxDialog.__init__(self, pWiki, ID, title, pos, size, style)
        self.pWiki = pWiki
        self.value = None        
        
        # Now continue with the normal construction of the dialog
        # contents
        sizer = wxBoxSizer(wxVERTICAL)

        label = wxStaticText(self, -1, "Saved Versions")
        sizer.Add(label, 0, wxALIGN_CENTRE|wxALL, 5)

        box = wxBoxSizer(wxVERTICAL)

        self.lb = wxListBox(self, -1, wxDefaultPosition, wxSize(165, 200), [], wxLB_SINGLE)

        # fill in the listbox
        self.versions = self.pWiki.getWikiData().getStoredVersions()
            
        for version in self.versions:
            self.lb.Append(version[1])

        box.Add(self.lb, 1, wxALIGN_CENTRE|wxALL, 5)

        sizer.AddSizer(box, 0, wxGROW|wxALIGN_CENTER_VERTICAL|wxALL, 5)

        line = wxStaticLine(self, -1, size=(20,-1), style=wxLI_HORIZONTAL)
        sizer.Add(line, 0, wxGROW|wxALIGN_CENTER_VERTICAL|wxRIGHT|wxTOP, 5)

        box = wxBoxSizer(wxHORIZONTAL)

        btn = wxButton(self, wxID_OK, " Retrieve ")
        btn.SetDefault()
        box.Add(btn, 0, wxALIGN_CENTRE|wxALL, 5)

        btn = wxButton(self, wxID_CANCEL, " Cancel ")
        box.Add(btn, 0, wxALIGN_CENTRE|wxALL, 5)

        sizer.AddSizer(box, 0, wxALIGN_CENTER_VERTICAL|wxALL, 5)

        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        sizer.Fit(self)

        ## EVT_BUTTON(self, wxID_OK, self.OnRetrieve)
        EVT_LISTBOX(self, ID, self.OnListBox)
        EVT_LISTBOX_DCLICK(self, ID, lambda evt: self.EndModal(wxID_OK))
        
##    def OnRetrieve(self, evt):
##        if self.value:
##            self.pWiki.getWikiData().deleteSavedSearch(self.value)
##            self.EndModal(wxID_CANCEL)
        
    def GetValue(self):
        """ Returns None or tuple (<id>, <description>, <creation date>)
        """
        return self.value

    def OnListBox(self, evt):
        self.value = self.versions[evt.GetSelection()]



class DateformatDialog(wxDialog):

    # HTML explanation for strftime:
    FORMATHELP = """<html>
<body bgcolor="#FFFFFF">

<table border="1" align="center" style="border-collapse: collapse">
    <tr><td align="center" valign="baseline"><b>Directive</b></td>
        <td align="left"><b>Meaning</b></td></tr>
    <tr><td align="center" valign="baseline"><code>%a</code></td>
        <td align="left">Locale's abbreviated weekday name.</td></tr>
    <tr><td align="center" valign="baseline"><code>%A</code></td>
        <td align="left">Locale's full weekday name.</td></tr>
    <tr><td align="center" valign="baseline"><code>%b</code></td>
        <td align="left">Locale's abbreviated month name.</td></tr>
    <tr><td align="center" valign="baseline"><code>%B</code></td>
        <td align="left">Locale's full month name.</td></tr>
    <tr><td align="center" valign="baseline"><code>%c</code></td>
        <td align="left">Locale's appropriate date and time representation.</td></tr>
    <tr><td align="center" valign="baseline"><code>%d</code></td>
        <td align="left">Day of the month as a decimal number [01,31].</td></tr>
    <tr><td align="center" valign="baseline"><code>%H</code></td>
        <td align="left">Hour (24-hour clock) as a decimal number [00,23].</td></tr>
    <tr><td align="center" valign="baseline"><code>%I</code></td>
        <td align="left">Hour (12-hour clock) as a decimal number [01,12].</td></tr>
    <tr><td align="center" valign="baseline"><code>%j</code></td>
        <td align="left">Day of the year as a decimal number [001,366].</td></tr>
    <tr><td align="center" valign="baseline"><code>%m</code></td>
        <td align="left">Month as a decimal number [01,12].</td></tr>
    <tr><td align="center" valign="baseline"><code>%M</code></td>
        <td align="left">Minute as a decimal number [00,59].</td></tr>
    <tr><td align="center" valign="baseline"><code>%p</code></td>
        <td align="left">Locale's equivalent of either AM or PM.</td></tr>
    <tr><td align="center" valign="baseline"><code>%S</code></td>
        <td align="left">Second as a decimal number [00,61].</td></tr>
    <tr><td align="center" valign="baseline"><code>%U</code></td>
        <td align="left">Week number of the year (Sunday as the first day of the
                week) as a decimal number [00,53].  All days in a new year
                preceding the first Sunday are considered to be in week 0.</td></tr>
    <tr><td align="center" valign="baseline"><code>%w</code></td>
        <td align="left">Weekday as a decimal number [0(Sunday),6].</td></tr>
    <tr><td align="center" valign="baseline"><code>%W</code></td>
        <td align="left">Week number of the year (Monday as the first day of the
                week) as a decimal number [00,53].  All days in a new year
                preceding the first Monday are considered to be in week 0.</td></tr>
    <tr><td align="center" valign="baseline"><code>%x</code></td>
        <td align="left">Locale's appropriate date representation.</td></tr>
    <tr><td align="center" valign="baseline"><code>%X</code></td>
        <td align="left">Locale's appropriate time representation.</td></tr>
    <tr><td align="center" valign="baseline"><code>%y</code></td>
        <td align="left">Year without century as a decimal number [00,99].</td></tr>
    <tr><td align="center" valign="baseline"><code>%Y</code></td>
        <td align="left">Year with century as a decimal number.</td></tr>
    <tr><td align="center" valign="baseline"><code>%Z</code></td>
        <td align="left">Time zone name (no characters if no time zone exists).</td></tr>
    <tr><td align="center" valign="baseline"><code>%%</code></td>
        <td align="left">A literal "<tt class="character">%</tt>" character.</td></tr>
    </tbody>
</table>
</body>
</html>
"""

    def __init__(self, parent, ID, mainControl, title="Choose Date Format",
                 pos=wxDefaultPosition, size=wxDefaultSize,
                 style=wxNO_3D, deffmt=u""):
        """
        deffmt -- Initial value for format string
        """
        d = wxPreDialog()
        self.PostCreate(d)
        
        self.mainControl = mainControl
        self.value = None     
        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, parent, "DateformatDialog")
        self.SetTitle(title)
        
        # Create HTML explanation
        html = wxHtmlWindow(self, -1)
        html.SetPage(self.FORMATHELP)
        res.AttachUnknownControl("htmlExplain", html, self)
        
        self.ctrls = XrcControls(self)
        
        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)
        
        # Set dropdown list of recent time formats
        tfs = self.mainControl.getConfig().get("main", "recent_time_formats")
        self.recentFormats = [unescapeForIni(s) for s in tfs.split(u";")]
        for f in self.recentFormats:
            self.ctrls.fieldFormat.Append(f)

        self.ctrls.fieldFormat.SetValue(deffmt)
        self.OnText(None)
        
        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_TEXT(self, XRCID("fieldFormat"), self.OnText) 

        
    def OnText(self, evt):
        preview = "<invalid>"
        text = guiToUni(self.ctrls.fieldFormat.GetValue())
        try:
            # strftime can't handle unicode correctly, so conversion is needed
            mstr = mbcsEnc(text, "replace")[0]
            preview = mbcsDec(strftime(mstr), "replace")[0]
            self.value = text
        except:
            pass

        self.ctrls.fieldPreview.SetLabel(preview)
        
        
    def GetValue(self):
        return self.value
        
    
    def OnOk(self, evt):
        if self.value != u"":
            # Update recent time formats list
            
            try:
                self.recentFormats.remove(self.value)
            except ValueError:
                pass
                
            self.recentFormats.insert(0, self.value)
            if len(self.recentFormats) > 10:
                self.recentFormats = self.recentFormats[:10]

            # Escape to store it in configuration
            tfs = u";".join([escapeForIni(f, u";") for f in self.recentFormats])
            self.mainControl.getConfig().set("main", "recent_time_formats", tfs)

        self.EndModal(wxID_OK)



class FontFaceDialog(wxDialog):
    """
    Presents a list of available fonts (its face names) and renders a sample
    string with currently selected face.
    """
    def __init__(self, parent, ID, value="",
                 pos=wxDefaultPosition, size=wxDefaultSize,
                 style=wxNO_3D):
        """
        value -- Current value of a text field containing a face name (used to
                 choose default item in the shown list box)
        """
        d = wxPreDialog()
        self.PostCreate(d)
        
        self.parent = parent
        self.value = value

        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, self.parent, "FontFaceDialog")
        
        self.ctrls = XrcControls(self)
        
        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)
        
        # Fill font listbox
        fenum = wxFontEnumerator()
        fenum.EnumerateFacenames()
        facelist = fenum.GetFacenames()
        self.parent.getCollator().sort(facelist)

        for f in facelist:
            self.ctrls.lbFacenames.Append(f)
            
        if len(facelist) > 0:
            try:
                # In wxPython, this can throw an exception if self.value
                # does not match an item
                if not self.ctrls.lbFacenames.SetStringSelection(self.value):
                    self.ctrls.lbFacenames.SetSelection(0)
            except:
                self.ctrls.lbFacenames.SetSelection(0)

            self.OnFaceSelected(None)
            
        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_LISTBOX(self, GUI_ID.lbFacenames, self.OnFaceSelected)
        EVT_LISTBOX_DCLICK(self, GUI_ID.lbFacenames, self.OnOk)


    def OnOk(self, evt):
        self.value = self.ctrls.lbFacenames.GetStringSelection()
        evt.Skip()

        
    def OnFaceSelected(self, evt):
        face = self.ctrls.lbFacenames.GetStringSelection()
        font = wx.Font(12, wx.DEFAULT, wx.NORMAL, wx.NORMAL, False, face)
        self.ctrls.stFacePreview.SetLabel(face)
        self.ctrls.stFacePreview.SetFont(font)

    def GetValue(self):
        return self.value



class ExportDialog(wxDialog):
    def __init__(self, pWiki, ID, title="Export",
                 pos=wxDefaultPosition, size=wxDefaultSize):
        d = wxPreDialog()
        self.PostCreate(d)
        
        self.pWiki = pWiki
        
        self.listPagesOperation = ListWikiPagesOperation()
        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, self.pWiki, "ExportDialog")
        
        self.ctrls = XrcControls(self)
        
        self.emptyPanel = None
        
        exporterList = [] # List of tuples (<exporter object>, <export tag>,
                          # <readable description>, <additional options panel>)
        
        for ob in Exporters.describeExporters(self.pWiki):   # TODO search plugins
            for tp in ob.getExportTypes(self.ctrls.additOptions):
                panel = tp[2]
                if panel is None:
                    if self.emptyPanel is None:
                        # Necessary to avoid a crash        
                        self.emptyPanel = wxPanel(self.ctrls.additOptions)
                        self.emptyPanel.Fit()
                    panel = self.emptyPanel
                else:
                    panel.Fit()

                # Add Tuple (Exporter object, export type tag,
                #     export type description, additional options panel)
                exporterList.append((ob, tp[0], tp[1], panel)) 

        self.ctrls.additOptions.Fit()
        mins = self.ctrls.additOptions.GetMinSize()

        self.ctrls.additOptions.SetMinSize(wxSize(mins.width+10, mins.height+10))
        self.Fit()

        self.exporterList = exporterList

        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)

        self.ctrls.tfDestination.SetValue(self.pWiki.getLastActiveDir())

        for e in self.exporterList:
            e[3].Show(False)
            e[3].Enable(False)
            self.ctrls.chExportTo.Append(e[2])
            
#         # Enable first addit. options panel
#         self.exporterList[0][3].Enable(True)
#         self.exporterList[0][3].Show(True)

        self.ctrls.chExportTo.SetSelection(0)  
        self._refreshForEtype()
        
        EVT_CHOICE(self, GUI_ID.chExportTo, self.OnExportTo)
        EVT_CHOICE(self, GUI_ID.chSelectedSet, self.OnChSelectedSet)

        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_BUTTON(self, GUI_ID.btnSelectDestination, self.OnSelectDest)


    def _refreshForEtype(self):
        for e in self.exporterList:
            e[3].Show(False)
            e[3].Enable(False)
            
        ob, etype, desc, panel = \
                self.exporterList[self.ctrls.chExportTo.GetSelection()][:4]

        # Enable appropriate addit. options panel
        panel.Enable(True)
        panel.Show(True)

        expDestWildcards = ob.getExportDestinationWildcards(etype)

        if expDestWildcards is None:
            # Directory destination
            self.ctrls.stDestination.SetLabel(u"Destination directory:")
        else:
            # File destination
            self.ctrls.stDestination.SetLabel(u"Destination file:")


    def OnExportTo(self, evt):
        self._refreshForEtype()
        evt.Skip()


    def OnChSelectedSet(self, evt):
        selset = self.ctrls.chSelectedSet.GetSelection()
        if selset == 3:  # Custom
            dlg = WikiPageListConstructionDialog(self, self.pWiki, -1, 
                    value=self.listPagesOperation)
            if dlg.ShowModal() == wxID_OK:
                self.listPagesOperation = dlg.getValue()
            dlg.Destroy()

    def OnOk(self, evt):
        import SearchAndReplace as Sar

        # Run exporter
        ob, etype, desc, panel = \
                self.exporterList[self.ctrls.chExportTo.GetSelection()][:4]
                
        # If this returns None, export goes to a directory
        expDestWildcards = ob.getExportDestinationWildcards(etype)
        if expDestWildcards is None:
            # Export to a directory
            if not exists(guiToUni(self.ctrls.tfDestination.GetValue())):
                self.pWiki.displayErrorMessage(
                        u"Destination directory does not exist")
                return
            
            if not isdir(guiToUni(self.ctrls.tfDestination.GetValue())):
                self.pWiki.displayErrorMessage(
                        u"Destination must be a directory")
                return
        else:
            if exists(guiToUni(self.ctrls.tfDestination.GetValue())) and \
                    not isfile(guiToUni(self.ctrls.tfDestination.GetValue())):
                self.pWiki.displayErrorMessage(
                        u"Destination must be a file")
                return


        # Create wordList (what to export)
        selset = self.ctrls.chSelectedSet.GetSelection()
        root = self.pWiki.getCurrentWikiWord()
        
        if root is None and selset in (0, 1):
            self.pWiki.displayErrorMessage(u"No real wiki word selected as root")
            return
            
        lpOp = Sar.ListWikiPagesOperation()

        if selset == 0:
            # single page
            item = Sar.ListItemWithSubtreeWikiPagesNode(lpOp, [root], 0)
            lpOp.setSearchOpTree(item)
            lpOp.ordering = "asroottree"  # Slow, but more intuitive
        elif selset == 1:
            # subtree
            item = Sar.ListItemWithSubtreeWikiPagesNode(lpOp, [root], -1)
            lpOp.setSearchOpTree(item)
            lpOp.ordering = "asroottree"  # Slow, but more intuitive
#             wordList = self.pWiki.getWikiData().getAllSubWords([root])
        elif selset == 2:
            # whole wiki
            item = Sar.AllWikiPagesNode(lpOp)
            lpOp.setSearchOpTree(item)
            lpOp.ordering = "asroottree"  # Slow, but more intuitive
#             wordList = self.pWiki.getWikiData().getAllDefinedWikiPageNames()
        else:
            # custom list
            lpOp = self.listPagesOperation

        wordList = self.pWiki.getWikiDocument().searchWiki(lpOp, True)

#         self.pWiki.getConfig().set("main", "html_export_pics_as_links",
#                 self.ctrls.cbHtmlExportPicsAsLinks.GetValue())


        if panel is self.emptyPanel:
            panel = None
            
        try:
            ob.export(self.pWiki.getWikiDataManager(), wordList, etype, 
                    guiToUni(self.ctrls.tfDestination.GetValue()), 
                    self.ctrls.compatFilenames.GetValue(), ob.getAddOpt(panel))
        except ExportException, e:
            self.pWiki.displayErrorMessage("Error while exporting", unicode(e))

        self.EndModal(wxID_OK)

        
    def OnSelectDest(self, evt):
        ob, etype, desc, panel = \
                self.exporterList[self.ctrls.chExportTo.GetSelection()][:4]

        expDestWildcards = ob.getExportDestinationWildcards(etype)

        if expDestWildcards is None:
            # Only transfer between GUI elements, so no unicode conversion
            seldir = wxDirSelector(u"Select Export Directory",
                    self.ctrls.tfDestination.GetValue(),
                    style=wxDD_DEFAULT_STYLE|wxDD_NEW_DIR_BUTTON, parent=self)
                
            if seldir:
                self.ctrls.tfDestination.SetValue(seldir)

        else:
            # Build wildcard string
            wcs = []
            for wd, wp in expDestWildcards:
                wcs.append(wd)
                wcs.append(wp)
                
            wcs.append(u"All files (*.*)")
            wcs.append(u"*")
            
            wcs = u"|".join(wcs)
            
            selfile = wxFileSelector(u"Select Export File",
                    self.ctrls.tfDestination.GetValue(),
                    default_filename = "", default_extension = "",
                    wildcard = wcs, flags=wxSAVE | wxOVERWRITE_PROMPT,
                    parent=self)

            if selfile:
                self.ctrls.tfDestination.SetValue(selfile)


class ImportDialog(wxDialog):
    def __init__(self, parent, ID, mainControl, title="Import",
                 pos=wxDefaultPosition, size=wxDefaultSize):
        d = wxPreDialog()
        self.PostCreate(d)
        
        self.parent = parent
        self.mainControl = mainControl
        
        self.listPagesOperation = ListWikiPagesOperation()
        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, self.parent, "ImportDialog")

        self.ctrls = XrcControls(self)

        self.emptyPanel = None
        
        importerList = [] # List of tuples (<importer object>, <import tag=type>,
                          # <readable description>, <additional options panel>)
        
        for ob in Importers.describeImporters(self.mainControl):   # TODO search plugins
            for tp in ob.getImportTypes(self.ctrls.additOptions):
                panel = tp[2]
                if panel is None:
                    if self.emptyPanel is None:
                        # Necessary to avoid a crash        
                        self.emptyPanel = wxPanel(self.ctrls.additOptions)
                        self.emptyPanel.Fit()
                    panel = self.emptyPanel
                else:
                    panel.Fit()

                # Add Tuple (Importer object, import type tag,
                #     import type description, additional options panel)
                importerList.append((ob, tp[0], tp[1], panel)) 

        self.ctrls.additOptions.Fit()
        mins = self.ctrls.additOptions.GetMinSize()
        
        self.ctrls.additOptions.SetMinSize(wxSize(mins.width+10, mins.height+10))
        self.Fit()
        
        self.importerList = importerList

        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)
        
        self.ctrls.tfSource.SetValue(self.mainControl.getLastActiveDir())
        
        for e in self.importerList:
            e[3].Show(False)
            e[3].Enable(False)
            self.ctrls.chImportFormat.Append(e[2])
            
#         # Enable first addit. options panel
#         self.importerList[0][3].Enable(True)
#         self.importerList[0][3].Show(True)
        self.ctrls.chImportFormat.SetSelection(0)
        self._refreshForItype()

        EVT_CHOICE(self, GUI_ID.chImportFormat, self.OnImportFormat)

        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_BUTTON(self, GUI_ID.btnSelectSource, self.OnSelectSrc)


    def _refreshForItype(self):
        """
        Refresh GUI depending on chosen import type
        """
        for e in self.importerList:
            e[3].Show(False)
            e[3].Enable(False)

        ob, itype, desc, panel = \
                self.importerList[self.ctrls.chImportFormat.GetSelection()][:4]

        # Enable appropriate addit. options panel
        panel.Enable(True)
        panel.Show(True)

        impSrcWildcards = ob.getImportSourceWildcards(itype)

        if impSrcWildcards is None:
            # Directory source
            self.ctrls.stSource.SetLabel(u"Source directory:")
        else:
            # File source
            self.ctrls.stSource.SetLabel(u"Source file:")


    def OnImportFormat(self, evt):
        self._refreshForItype()
        evt.Skip()



    def OnOk(self, evt):
        # Run importer
        ob, itype, desc, panel = \
                self.importerList[self.ctrls.chImportFormat.GetSelection()][:4]
                
        if not exists(guiToUni(self.ctrls.tfSource.GetValue())):
            self.mainControl.displayErrorMessage(
                    u"Source does not exist")
            return

        # If this returns None, import goes to a directory
        impSrcWildcards = ob.getImportSourceWildcards(itype)
        if impSrcWildcards is None:
            # Import from a directory
            
            if not isdir(guiToUni(self.ctrls.tfSource.GetValue())):
                self.mainControl.displayErrorMessage(
                        u"Source must be a directory")
                return
        else:
            if not isfile(guiToUni(self.ctrls.tfSource.GetValue())):
                self.mainControl.displayErrorMessage(
                        u"Source must be a file")
                return

        if panel is self.emptyPanel:
            panel = None

        try:
            ob.doImport(self.mainControl.getWikiDataManager(), itype, 
                    guiToUni(self.ctrls.tfSource.GetValue()), 
                    False, ob.getAddOpt(panel))
        except ImportException, e:
            self.mainControl.displayErrorMessage("Error while importing",
                    unicode(e))

        self.EndModal(wxID_OK)

        
    def OnSelectSrc(self, evt):
        ob, itype, desc, panel = \
                self.importerList[self.ctrls.chImportFormat.GetSelection()][:4]

        impSrcWildcards = ob.getImportSourceWildcards(itype)

        if impSrcWildcards is None:
            # Only transfer between GUI elements, so no unicode conversion
            seldir = wxDirSelector(u"Select Import Directory",
                    self.ctrls.tfSource.GetValue(),
                    style=wxDD_DEFAULT_STYLE, parent=self)

            if seldir:
                self.ctrls.tfSource.SetValue(seldir)

        else:
            # Build wildcard string
            wcs = []
            for wd, wp in impSrcWildcards:
                wcs.append(wd)
                wcs.append(wp)
                
            wcs.append(u"All files (*.*)")
            wcs.append(u"*")
            
            wcs = u"|".join(wcs)
            
            selfile = wxFileSelector(u"Select Import File",
                    self.ctrls.tfSource.GetValue(),
                    default_filename = "", default_extension = "",
                    wildcard = wcs, flags=wxOPEN | wxFILE_MUST_EXIST,
                    parent=self)

            if selfile:
                self.ctrls.tfSource.SetValue(selfile)



class ChooseWikiWordDialog(wxDialog):
    def __init__(self, pWiki, ID, words, motionType, title="Choose Wiki Word",
                 pos=wxDefaultPosition, size=wxDefaultSize):
        d = wxPreDialog()
        self.PostCreate(d)
        
        self.pWiki = pWiki
        res = xrc.wxXmlResource.Get()
        res.LoadOnDialog(self, self.pWiki, "ChooseWikiWordDialog")
        
        self.ctrls = XrcControls(self)
        
        self.SetTitle(title)
        self.ctrls.staTitle.SetLabel(title)
        
        self.motionType = motionType
        self.words = words
        wordsgui = map(uniToGui, words)
        
        self.ctrls.lb.Set(wordsgui)

        self.ctrls.btnOk.SetId(wxID_OK)
        self.ctrls.btnCancel.SetId(wxID_CANCEL)

        EVT_BUTTON(self, GUI_ID.btnDelete, self.OnDelete)
        EVT_BUTTON(self, wxID_OK, self.OnOk)
        EVT_LISTBOX_DCLICK(self, GUI_ID.lb, self.OnOk)


    def OnOk(self, evt):
        sels = self.ctrls.lb.GetSelections()
        if len(sels) != 1:
            return # We can only go to exactly one wiki word
            
        wikiWord = self.words[sels[0]]
        self.pWiki.openWikiPage(wikiWord, forceTreeSyncFromRoot=True,
                motionType=self.motionType)

        self.EndModal(GUI_ID.btnDelete)


    def OnDelete(self, evt):
        sellen = len(self.ctrls.lb.GetSelections())
        if sellen > 0:
            answer = wxMessageBox(u"Do you want to delete %i wiki page(s)?" % sellen,
                    u"Delete Wiki Page(s)",
                    wxYES_NO | wxNO_DEFAULT | wxICON_QUESTION, self)

            if answer != wxYES:
                return

            self.pWiki.saveAllDocPages()
            for s in self.ctrls.lb.GetSelections():
                delword = self.words[s]
                # Un-alias word
                delword = self.pWiki.getWikiData().getAliasesWikiWord(delword)
                
                if self.pWiki.getWikiData().isDefinedWikiWord(delword):
                    self.pWiki.getWikiData().deleteWord(delword)
        
                    # trigger hooks
                    self.pWiki.hooks.deletedWikiWord(self.pWiki, delword)
                    
                    p2 = {}
                    p2["deleted page"] = True
                    p2["deleted wiki page"] = True
                    p2["wikiWord"] = delword
                    self.pWiki.fireMiscEventProps(p2)
            
            self.pWiki.pageHistory.goAfterDeletion()

            self.EndModal(wxID_OK)


def _children(win, indent=0):
    print " " * indent + repr(win), win.GetId()
    for c in win.GetChildren():
        _children(c, indent=indent+2)


class AboutDialog(wxDialog):
    """ An about box that uses an HTML window """

    textTemplate = '''
<html>
<body bgcolor="#FFFFFF">
    <center>
        <table bgcolor="#CCCCCC" width="100%%" cellspacing="0" cellpadding="0" border="1">
            <tr>
                <td align="center"><h2>%s</h2></td>
            </tr>
        </table>

        <p>
wikidPad is a Wiki-like notebook for storing your thoughts, ideas, todo lists, contacts, or anything else you can think of to write down.
What makes wikidPad different from other notepad applications is the ease with which you can cross-link your information.        </p>        
        <br><br>

        <table border=0 cellpadding=1 cellspacing=0>
            <tr><td width="30%%" align="right"><font size="3"><b>Author:</b></font></td><td nowrap><font size="3">Michael Butscher</font></td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>Email:</b></font></td><td nowrap><font size="3">mbutscher@gmx.de</font></td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>URL:</b></font></td><td nowrap><font size="3">http://www.mbutscher.de/software.html</font></td></tr>
            <tr><td width="30%%" align="right">&nbsp;</td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>Author:</b></font></td><td nowrap><font size="3">Jason Horman</font></td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>Email:</b></font></td><td nowrap><font size="3">wikidpad@jhorman.org</font></td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>URL:</b></font></td><td nowrap><font size="3">http://www.jhorman.org/wikidPad/</font></td></tr>
            <tr><td width="30%%" align="right">&nbsp;</td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>Author:</b></font></td><td nowrap><font size="3">Gerhard Reitmayr</font></td></tr>
            <tr><td width="30%%" align="right"><font size="3"><b>Email:</b></font></td><td nowrap><font size="3">gerhard.reitmayr@gmail.com</font></td></tr>
        </table>
    </center>
    
    <hr />
    
    <p />Your configuration directory is: %s
</body>
</html>
'''

    def __init__(self, pWiki):
        wxDialog.__init__(self, pWiki, -1, 'About WikidPad',
                          size=(470, 330) )
        text = self.textTemplate % (VERSION_STRING,
                escapeHtml(pWiki.globalConfigDir))

        html = wxHtmlWindow(self, -1)
        html.SetPage(text)
        button = wxButton(self, wxID_OK, "Okay")

        # constraints for the html window
        lc = wxLayoutConstraints()
        lc.top.SameAs(self, wxTop, 5)
        lc.left.SameAs(self, wxLeft, 5)
        lc.bottom.SameAs(button, wxTop, 5)
        lc.right.SameAs(self, wxRight, 5)
        html.SetConstraints(lc)

        # constraints for the button
        lc = wxLayoutConstraints()
        lc.bottom.SameAs(self, wxBottom, 5)
        lc.centreX.SameAs(self, wxCentreX)
        lc.width.AsIs()
        lc.height.AsIs()
        button.SetConstraints(lc)

        self.SetAutoLayout(True)
        self.Layout()
        self.CentreOnParent(wxBOTH)

