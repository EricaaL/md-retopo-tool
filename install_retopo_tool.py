"""Install the ReTopo tool: drag this file into a Maya viewport.

It adds a "ReTopo" button to the shelf you are on (replacing an earlier one),
saves the shelves, and opens the window. The button points at this folder, so
the tool runs from wherever this file sits: keep the folder where it is, and
edits to the code are picked up the next time Maya starts. Drag the file in
again after moving the folder.
"""
import os
import sys

LABEL = "ReTopo"


def _button_command(root):
    """Shelf command: always run the code as it is on disk now. The open window
    (and its selection callback) is shut down, the tool's modules are dropped
    from Python's cache, and the window opens fresh."""
    return (
        "import sys\n"
        f"_root = {root!r}\n"
        "if _root not in sys.path:\n"
        "    sys.path.insert(0, _root)\n"
        "_old = sys.modules.get('retopo_tool.ui.window')\n"
        "if _old is not None and getattr(_old, '_window', None) is not None:\n"
        "    try:\n"
        "        _old._window.shutdown()\n"
        "    except Exception:\n"
        "        pass\n"
        "for _m in [m for m in sys.modules if m == 'retopo_tool' or m.startswith('retopo_tool.')]:\n"
        "    del sys.modules[_m]\n"
        "import retopo_tool.ui.window as _w\n"
        "_w.show()\n"
    )


def install(open_window=True):
    import maya.cmds as cmds
    import maya.mel as mel

    root = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(root, "retopo_tool")):
        raise RuntimeError(f"install_retopo_tool.py must sit next to the retopo_tool folder ({root}).")
    if root not in sys.path:
        sys.path.insert(0, root)

    shelf = None
    if not cmds.about(batch=True):
        top = mel.eval("$_retopo_tmp = $gShelfTopLevel")
        shelf = cmds.tabLayout(top, query=True, selectTab=True)
        for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
            if cmds.objectTypeUI(child) == "shelfButton" and \
                    cmds.shelfButton(child, query=True, label=True) == LABEL:
                cmds.deleteUI(child)
        cmds.shelfButton(parent=shelf, label=LABEL, imageOverlayLabel=LABEL, image="pythonFamily.png",
                         annotation="ReTopo: quad retopology of Marvelous Designer garments",
                         sourceType="python", command=_button_command(root))
        mel.eval("saveAllShelves $gShelfTopLevel")

    print(f"ReTopo installed from {root}" + (f" (button on shelf '{shelf}')." if shelf else "."))
    if open_window:
        import retopo_tool.ui.window as window
        return window.show()


def onMayaDroppedPythonFile(*args):
    """Maya calls this when the file is dropped into a viewport."""
    install()


if __name__ == "__main__":
    install()
