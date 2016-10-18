#!/usr/bin/python3
# -*- coding: utf-8 -*-

import sys
import re
import os
import threading

from PyQt5.QtWidgets import QApplication, QMainWindow, QTreeWidget, \
    QTreeWidgetItem, QVBoxLayout, QPushButton, QWidget, QPlainTextEdit, \
    QSplitter, QTextEdit, QAction, QToolBar, QFileDialog, QLabel
from PyQt5.QtGui import QBrush, QColor, QIcon
from PyQt5.QtCore import QObject, pyqtSignal, QCoreApplication, QSettings

from model import SystemTreeNode
from scripts.dirsize import humanize_bytes


infoLabelText = "<strong>Filters list</strong><br/>Regex accepted<br/>"
matchRootLabelText = """<span style='color:red'><em>Matching also root
path</em></span>"""
filtersErrorText = """ERROR while compiling filters.
Make sure they are valid regular expression."""
filtersValidLabelText = """<span style='color:#666'><em>
Filters are not applyed</em>"""
waitStatus1 = "Please wait...reading file system. It may take a while."
waitStatus2 = "Please wait...populating the tree. It may take a while."
waitStatus3 = "Please wait...connecting the tree. It may take a while."
toggleMatchRootStatus = """Switched to {0}match root path.
 Views are NOT updated. Apply filters again."""


def matchNothing(ignored):
    return False


class SystemTreeWidgetNode(QTreeWidgetItem):

    percentTemplate = "{:.1%}"
    brushes = {
        SystemTreeNode.DIRECTLY_EXCLUDED: QBrush(QColor("orange")),
        SystemTreeNode.PARTIALLY_INCLUDED: QBrush(QColor("yellow")),
        SystemTreeNode.FULLY_INCLUDED: QBrush(QColor("white"))
    }

    def __init__(self, parent, data):
        super().__init__(parent)
        self.setText(0, data.name)
        self.uncutSize = data.subtreeTotalSize
        self.setText(1, humanize_bytes(self.uncutSize))
        self.setText(2, SystemTreeWidgetNode.percentTemplate.format(1))
        self.setText(3, humanize_bytes(self.uncutSize))
        data.visibilityChangedHandler = self._update_visibility

    def _colorBackground(self, brush):
        self.setBackground(0, brush)
        self.setBackground(1, brush)
        self.setBackground(2, brush)
        self.setBackground(3, brush)

    def _update_visibility(self, exclusionState, actualSize):
        ratio = float(actualSize)/max([self.uncutSize, 1.0])
        self._colorBackground(self.brushes[exclusionState])
        self.setText(1, humanize_bytes(actualSize))
        self.setText(2, SystemTreeWidgetNode.percentTemplate.format(ratio))
        if exclusionState == SystemTreeNode.DIRECTLY_EXCLUDED:
            # Update all the descendant children because the model won't
            # send any event/call any callback for these nodes.
            for i in range(0, self.childCount()):
                self.child(i)._update_visibility(exclusionState, 0)
        QCoreApplication.processEvents()

    @staticmethod
    def fromSystemTree(parent, data):
        root = SystemTreeWidgetNode(parent, data)
        for node in sorted(data.children):
            SystemTreeWidgetNode.fromSystemTree(root, data.getChild(node))
        QCoreApplication.processEvents()
        return root


class WorkerThread(threading.Thread):

    def __init__(self, mainThread, initialPath):
        super().__init__()
        self.mainThread = mainThread
        self.initialPath = initialPath

    def run(self):
        callback = self.mainThread._createSystemTreeAsyncEnd
        workerObject = WorkerObject(self.mainThread)
        workerObject.moveToThread(QApplication.instance().thread())
        workerObject.workFinished.connect(callback)
        workerObject.doWork(self.initialPath)


class WorkerObject(QObject):

    workFinished = pyqtSignal()

    def __init__(self, parent):
        super().__init__(None)
        self.mainThread = parent

    def doWork(self, initialPath):
        a, b, c = SystemTreeNode.createSystemTree(initialPath)
        self.mainThread.basePath = a
        self.mainThread.root = b
        self.mainThread.totalNodes = c
        self.workFinished.emit()


class BackupExcluderWindow(QMainWindow):

    startWork = pyqtSignal(str)

    def __init__(self, initialPath):
        super().__init__()
        self._customInit(os.path.abspath(initialPath))

    def _customInit(self, initialPath):
        super().__init__()

        QCoreApplication.setOrganizationName("Develer")
        QCoreApplication.setOrganizationDomain("develer.com")
        QCoreApplication.setApplicationName("Backup excluder")
        self.settings = QSettings()

        self.basePath = self.settings.value("config/basePath", initialPath)
        self.root = None
        self.totalNodes = 0
        self.matchRoot = self.settings.value("config/matchRoot",
                                             False, type=bool)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File System", "Size", "%", "Uncut Size"])
        self.tree.header().resizeSection(0, 250)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("No path matched")

        self.rootFolderDisplay = QLabel()

        self.infoLabel = QLabel(infoLabelText)
        self.infoLabel.setWordWrap(True)
        self.filtersValidLabel = QLabel(filtersValidLabelText)
        self.filtersValidLabel.setWordWrap(True)
        self.filtersValidLabel.setVisible(True)
        self.matchRootLabel = QLabel(matchRootLabelText)
        self.matchRootLabel.setWordWrap(True)
        self.matchRootLabel.setVisible(self.matchRoot)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("No filters")
        self.edit.setPlainText(self.settings.value("editor/filters"))

        self.confirm = QPushButton("apply filters")
        self.confirm.clicked.connect(self.applyFilters)

        v1 = QVBoxLayout()
        v1.addWidget(self.rootFolderDisplay)
        v1.addWidget(self.tree)
        v1.addWidget(self.output)
        leftPane = QWidget()
        leftPane.setLayout(v1)

        v2 = QVBoxLayout()
        v2.addWidget(self.infoLabel)
        v2.addWidget(self.filtersValidLabel)
        v2.addWidget(self.matchRootLabel)
        v2.addWidget(self.edit)
        v2.addWidget(self.confirm)
        v2.addStretch(1)
        rightPane = QWidget()
        rightPane.setLayout(v2)

        splitter = QSplitter()
        splitter.addWidget(leftPane)
        splitter.addWidget(rightPane)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        self._initToolBar()

        self.statusBar()
        self.setCentralWidget(splitter)
        self.setGeometry(10, 100, 800, 600)
        self.treeview.trigger()

        # initial visualization: no tree to avoid (possible) long wait,
        # display of initial path (with hint of refresh), disable filters
        moreInfo = " <span style='color:red'>(refresh to show tree)</span>"
        self._update_basePath(self.basePath + os.sep, moreInfo)
        self.confirm.setEnabled(False)

        self.show()

    def _initToolBar(self):

        openIcon = QIcon.fromTheme("folder-open")
        openAction = QAction(
            openIcon, "Select root folder", self)
        openAction.triggered.connect(self._selectRootFolder)

        saveIcon = QIcon.fromTheme("document-save")
        saveAction = QAction(
            saveIcon, "Save excluded paths", self)
        saveAction.triggered.connect(self._saveToFile)

        refreshIcon = QIcon.fromTheme("view-refresh")
        refreshAction = QAction(
            refreshIcon, "Refresh from file system", self)
        refreshAction.triggered.connect(self._refreshFileSystem)

        treeviewIcon = QIcon.fromTheme("format-indent-more")
        treeviewAction = QAction(
            treeviewIcon, "File system tree view", self, checkable=True)
        treeviewAction.triggered.connect(self._showTreeView)

        listviewIcon = QIcon.fromTheme("format-justify-fill")
        listviewAction = QAction(
            listviewIcon, "Excluded paths list view", self, checkable=True)
        listviewAction.triggered.connect(self._showListView)

        matchFromRootIcon = QIcon.fromTheme("tools-check-spelling")
        matchFromRootAction = QAction(
            matchFromRootIcon, "Match also root path", self, checkable=True)
        matchFromRootAction.setChecked(self.matchRoot)
        matchFromRootAction.triggered.connect(self._toggle_match_root)

        manageToolBar = QToolBar()
        manageToolBar.addAction(openAction)
        manageToolBar.addAction(saveAction)
        manageToolBar.addAction(refreshAction)
        manageToolBar.addAction(matchFromRootAction)
        viewToolBar = QToolBar()
        viewToolBar.addAction(treeviewAction)
        viewToolBar.addAction(listviewAction)

        self.addToolBar(manageToolBar)
        self.addToolBar(viewToolBar)
        self.insertToolBarBreak(manageToolBar)
        self.treeview = treeviewAction
        self.listview = listviewAction
        self.save = saveAction
        self.open = openAction
        self.refresh = refreshAction

    def _update_basePath(self, path, moreInfo=""):
        labelText = "root path: <strong>" + path + "</strong>" + moreInfo
        self.rootFolderDisplay.setText(labelText)

    def _notifyStatus(self, message):
        self.statusBar().showMessage(message)

    def _notifyBackupStatus(self, finalSize, usedNodes):
        self._notifyStatus("Backup size: {} (using {}/{} nodes)".format(
            humanize_bytes(finalSize), usedNodes, self.totalNodes))

    def _clear_widgets(self):
        self.tree.clear()
        self.output.clear()
        self.filtersValidLabel.setVisible(True)

    def _setOutputEnabled(self, enabled):
        self.tree.setEnabled(enabled)
        self.output.setEnabled(enabled)
        self.confirm.setEnabled(enabled)
        self.refresh.setEnabled(enabled)
        self.open.setEnabled(enabled)
        self.save.setEnabled(enabled)

    def _createSystemTree(self, initialPath):
        self._createSystemTreeAsyncStart(initialPath)

    def _createSystemTreeAsyncStart(self, initialPath):
        self._notifyStatus(waitStatus1)
        self._setOutputEnabled(False)
        self._clear_widgets()
        worker = WorkerThread(self, initialPath)
        worker.start()

    def _createSystemTreeAsyncEnd(self):
        self._notifyStatus(waitStatus2)
        SystemTreeWidgetNode.fromSystemTree(self.tree, self.root)
        self._notifyStatus(waitStatus3)
        self._listen_for_excluded_paths(self.root)
        self._update_basePath(self.basePath + os.sep)
        self._notifyBackupStatus(self.root.subtreeTotalSize, self.totalNodes)
        self._setOutputEnabled(True)

    def _selectRootFolder(self):
        newRootFolder = QFileDialog.getExistingDirectory(
            self, "Select Root Directory", None, QFileDialog.ShowDirsOnly)
        if newRootFolder:
            self.settings.setValue("config/basePath", newRootFolder)
            self._createSystemTree(newRootFolder)
            return True
        return False

    def _saveToFile(self):
        fileName, fileExtension = QFileDialog.getSaveFileName(
            self, "Save excluded paths list", "",
            "Paths excluded list (*.pel);;All Files (*)")
        if not fileName:
            return False
        with open(fileName[0], "w+") as f:
            f.write(self.output.document().toPlainText())

    def _refreshFileSystem(self):
        self._createSystemTree(self.basePath)

    def _showTreeView(self):
        self.tree.setVisible(True)
        self.output.setVisible(False)
        self.treeview.setChecked(True)
        self.listview.setChecked(False)

    def _showListView(self):
        self.tree.setVisible(False)
        self.output.setVisible(True)
        self.treeview.setChecked(False)
        self.listview.setChecked(True)

    def _toggle_match_root(self):
        self.matchRoot = not self.matchRoot
        self.settings.setValue("config/matchRoot", self.matchRoot)
        self.matchRootLabel.setVisible(self.matchRoot)
        matching = "" if self.matchRoot else "NOT "
        self._notifyStatus(toggleMatchRootStatus.format(matching))

    def _manageExcludedPath(self, newPath):
        self.output.append(newPath)

    def _listen_for_excluded_paths(self, root):
        root.excludedPathFoundHandler = self._manageExcludedPath
        for child in root.children.values():
            self._listen_for_excluded_paths(child)

    def applyFilters(self, sender):
        self._notifyStatus("Please wait...applying filters.")
        self.output.document().clear()
        text = self.edit.document().toPlainText()
        self.settings.setValue("editor/filters", text)
        filters = list(filter(str.strip, text.split("\n")))
        if len(filters) > 0:
            if self.matchRoot:
                base = ""
            else:
                base = self.basePath + os.sep
            filterExpr = base + '(' + '|'.join(filters) + ')'
            try:
                cutFunction = re.compile(filterExpr).match
            except Exception:
                self._notifyStatus(filtersErrorText)
                return
        else:
            cutFunction = matchNothing
        hiddenPath = os.path.dirname(self.basePath)
        self.confirm.setEnabled(False)
        finalSize, nodesCount = self.root.update(hiddenPath, cutFunction)
        self.filtersValidLabel.setVisible(False)
        self.confirm.setEnabled(True)
        self._notifyBackupStatus(finalSize, nodesCount)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='backup excluder')
    parser.add_argument('start', nargs='?', default='.')
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = BackupExcluderWindow(args.start)
    retVal = app.exec_()
    del window
    del app
    sys.exit(retVal)


if __name__ == '__main__':
    main()
