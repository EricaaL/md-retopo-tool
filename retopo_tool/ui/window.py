"""The ReTopo window (dockable in Maya). Open it with:

    import retopo_tool.ui.window as w; w.show()

All garment logic is in session.GarmentSession; all Maya calls go through
maya_adapter.host. This file only lays out widgets and wires them up.

The panel reads top to bottom: 1 Source, 2 Settings, 3 Retopo (Auto or
Manual), 4 Finish (thickness). Under it: progress with Cancel, then the piece
and segment tables and the log.
"""
import functools
import os
import traceback

from ..core.mesh import piece_label
from ..maya_adapter import host
from .session import GarmentSession, SessionError, TooDense, clean_prefix

QtCore, QtGui, QtWidgets = host.qt()

OBJECT_NAME = "retopoToolWindow"
_window = None


def show():
    """Open the window (closing an earlier one first) and return it."""
    global _window
    host.close_dock(OBJECT_NAME)
    if _window is not None:
        try:
            _window.shutdown()
            _window.close()
            _window.deleteLater()
        except RuntimeError:   # its Qt object is already gone
            pass
    _window = RetopoWindow()
    if host.interactive():
        _window.show(dockable=True)
    else:
        _window.show()
    return _window


def _guarded(method):
    """Run an action: one at a time, other buttons off and Cancel on while it runs;
    any failure goes to the log and a message box, never a half state."""
    @functools.wraps(method)
    def run(self, *args):
        if self._busy:
            return None
        self._set_busy(True)
        try:
            return method(self)
        except SessionError as e:
            self._flush_log()
            self._error(str(e))
        except Exception as e:   # noqa: BLE001 - a UI must not die on an unexpected bug
            self._flush_log()
            self._log("error", traceback.format_exc())
            self._error(f"Unexpected error: {e}\nSee the Log tab for details.")
        finally:
            self._set_busy(False)
    return run


class _SliderSpin:
    """A slider and a number box showing one float value, kept in step."""

    STEPS = 1000

    def __init__(self, lo, hi, value, decimals=2, suffix=""):
        self.lo, self.hi = lo, hi
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, self.STEPS)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(10 ** -decimals * 5 if decimals > 1 else 0.1)
        self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(90)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.spin.setValue(value)
        self._from_spin(value)
        self.row = QtWidgets.QHBoxLayout()
        self.row.addWidget(self.slider, 1)
        self.row.addWidget(self.spin)

    def _from_slider(self, k):
        v = self.lo + (self.hi - self.lo) * k / self.STEPS
        if abs(v - self.spin.value()) > 10 ** -self.spin.decimals() / 2:
            self.spin.setValue(v)

    def _from_spin(self, v):
        k = int(round((v - self.lo) / (self.hi - self.lo) * self.STEPS))
        if k != self.slider.value():
            self.slider.blockSignals(True)
            self.slider.setValue(k)
            self.slider.blockSignals(False)

    def value(self):
        return self.spin.value()

    def setValue(self, v):
        self.spin.setValue(v)

    def setEnabled(self, on):
        self.slider.setEnabled(on)
        self.spin.setEnabled(on)

    def setToolTip(self, tip):
        self.slider.setToolTip(tip)
        self.spin.setToolTip(tip)


def _group(title):
    box = QtWidgets.QGroupBox(title)
    box.setStyleSheet("QGroupBox { font-weight: bold; } QGroupBox * { font-weight: normal; }")
    return box


class RetopoWindow(*host.dockable_bases(QtWidgets.QWidget)):
    PIECE_COLUMNS = ["Piece", "Layout", "Quads", "Rings", "Poles", "Twin", "Attach sets", "Note"]
    SEG_COLUMNS = ["Segment", "Kind", "Length (cm)", "Sewn to", "Count", "Yours"]

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(OBJECT_NAME)
        self.setWindowTitle("ReTopo")
        self.resize(560, 940)
        self.session = GarmentSession(progress=self._on_progress)
        self.source = None           # ("file", path) or ("scene", node)
        self.set_names = {}          # face set the tool means -> Maya set name
        self.flat_data = None        # last session.flatten() result
        self._thick_key = None       # (output version, support) while a thickness is shown
        self._busy = False
        self._filling = False        # tables are being refilled (ignore spin box signals)
        self._selecting = False      # a viewport click is driving the tables (don't select back)
        self._piece = None
        self._unread = 0
        self._timers = {}
        for name, slot in (("settings", self._live_update), ("thickness", self._thicken),
                           ("counts", self._live_counts)):
            t = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.setInterval(self.session.cfg["ui"]["live_delay_ms"])
            t.timeout.connect(slot)
            self._timers[name] = t
        self._build_ui()
        self._job = host.watch_selection(self._viewport_selection) if host.interactive() else None

    # ---- layout ---------------------------------------------------------
    def _build_ui(self):
        cfg = self.session.cfg
        ui = cfg["ui"]
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        outer.addWidget(split)

        controls = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(controls)
        root.setContentsMargins(2, 2, 2, 2)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(controls)
        split.addWidget(scroll)
        self._actions = []

        # 1 Source --------------------------------------------------------
        src = _group("1  Source")
        sl = QtWidgets.QGridLayout(src)
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("MD OBJ export with UVs")
        browse = QtWidgets.QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        use_sel = QtWidgets.QPushButton("Use selected mesh")
        use_sel.setToolTip("Remesh the mesh selected in the scene (it needs its MD pattern UVs).")
        use_sel.clicked.connect(self._use_selected)
        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.addItem("Detect", "auto")
        self.units_combo.addItem("cm (real size)", "cm")
        self.units_combo.addItem("mm (MD OBJ imported as is)", "mm")
        self.units_combo.setToolTip("Scene meshes only. An MD OBJ imported into Maya as is comes in 10x too big; "
                                    "Detect spots that from its size.")
        self.original_check = QtWidgets.QCheckBox("Bring in the MD mesh too (hidden)")
        self.original_check.setChecked(True)
        self.original_check.setToolTip("OBJ files only: the original MD mesh, hidden, to compare against.")
        self.hide_source_check = QtWidgets.QCheckBox("Hide the scene mesh after building")
        self.hide_source_check.setChecked(True)
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setToolTip("Name prefix for everything the tool makes, e.g. teddy_bear_piece_01.")
        rename = QtWidgets.QPushButton("Rename")
        rename.setToolTip("Give the nodes and sets already in the scene this prefix.")
        rename.clicked.connect(self._rename)
        sl.addWidget(QtWidgets.QLabel("OBJ file"), 0, 0)
        sl.addWidget(self.path_edit, 0, 1, 1, 2)
        sl.addWidget(browse, 0, 3)
        sl.addWidget(QtWidgets.QLabel("or"), 1, 0)
        sl.addWidget(use_sel, 1, 1)
        sl.addWidget(QtWidgets.QLabel("Scene units"), 1, 2, QtCore.Qt.AlignRight)
        sl.addWidget(self.units_combo, 1, 3)
        sl.addWidget(self.original_check, 2, 1)
        sl.addWidget(self.hide_source_check, 2, 2, 1, 2)
        sl.addWidget(QtWidgets.QLabel("Name prefix"), 3, 0)
        sl.addWidget(self.prefix_edit, 3, 1, 1, 2)
        sl.addWidget(rename, 3, 3)
        sl.setColumnStretch(1, 1)
        self._actions += [browse, use_sel, rename]
        root.addWidget(src)

        # 2 Settings ------------------------------------------------------
        sets = _group("2  Settings")
        form = QtWidgets.QFormLayout(sets)
        self.target = _SliderSpin(*ui["density_range"], cfg["solver"]["target_edge_length"], 2, " cm")
        self.target.setToolTip("Edge density: the starting quad edge length, in real cm. Lower is denser.")
        form.addRow("Edge length", self.target.row)
        self.width = _SliderSpin(*ui["ring_width_range"], cfg["ring"]["depth_ratio"], 2, " x edge")
        self.width.setToolTip("How wide the band of quads along each piece's outline is.")
        form.addRow("Outline ring width", self.width.row)
        self.rings_spin = QtWidgets.QSpinBox()
        self.rings_spin.setRange(0, cfg["ring"]["max_rings"])
        self.rings_spin.setValue(cfg["ring"]["auto_rings"])
        self.rings_spin.setToolTip("Rings of quads that follow each piece's outline.")
        form.addRow("Outline rings", self.rings_spin)
        self.tol = _SliderSpin(0.0, 0.3, cfg["symmetry"]["uv_tolerance"], 3)
        self.tol.setToolTip("How alike two pieces must look in the UV editor to share one topology. "
                            "0 = identical only.")
        form.addRow("Similar pieces", self.tol.row)
        self.inside_combo = QtWidgets.QComboBox()
        self.inside_combo.addItem("Keep them in a face set", "keep")
        self.inside_combo.addItem("Delete them (open the hole)", "delete")
        self.inside_combo.setCurrentIndex(0 if cfg["attach"]["inside"] == "keep" else 1)
        self.inside_combo.setToolTip("Faces on a base piece under an attached opening (arm, neck, leg).")
        form.addRow("Faces under attaches", self.inside_combo)
        for w in (self.target.spin, self.width.spin, self.tol.spin):
            w.valueChanged.connect(self._settings_changed)
        self.rings_spin.valueChanged.connect(self._settings_changed)
        self.inside_combo.currentIndexChanged.connect(self._settings_changed)
        self._settings_widgets = [self.target, self.width, self.tol, self.rings_spin, self.inside_combo]
        root.addWidget(sets)

        # 3 Retopo --------------------------------------------------------
        mode = _group("3  Retopo")
        mv = QtWidgets.QVBoxLayout(mode)
        self.tabs = QtWidgets.QTabWidget()
        mv.addWidget(self.tabs)
        auto = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(auto)
        al.addWidget(QtWidgets.QLabel("Build the whole garment on the drape in one go."))
        row = QtWidgets.QHBoxLayout()
        analyze = QtWidgets.QPushButton("Analyze")
        analyze.setToolTip("Find pieces, seams, similar pieces and attaches; solve counts. No mesh yet.")
        analyze.clicked.connect(self._analyze)
        build = QtWidgets.QPushButton("Build mesh")
        build.setToolTip("Build <prefix>_retopo on the drape (replaces the previous one).")
        build.clicked.connect(self._build)
        row.addWidget(analyze)
        row.addWidget(build, 1)
        al.addLayout(row)
        al.addStretch(1)
        self._actions += [analyze, build]
        self.tabs.addTab(auto, "Auto")

        manual = QtWidgets.QWidget()
        ml = QtWidgets.QGridLayout(manual)
        self.flatten_btn = QtWidgets.QPushButton("1  Flatten")
        self.flatten_btn.setToolTip("Lay every piece out flat beside the garment, by its UVs.")
        self.flatten_btn.clicked.connect(self._flatten)
        self.live_check = QtWidgets.QCheckBox("Live: settings and counts update the flat stage")
        self.live_check.setChecked(True)
        self.counts_check = QtWidgets.QCheckBox("Show counts")
        self.counts_check.setChecked(True)
        self.counts_check.toggled.connect(lambda on: host.set_visible(self._node("flat_labels"), on))
        self.colours_check = QtWidgets.QCheckBox("Show seam colours")
        self.colours_check.setChecked(True)
        self.colours_check.setToolTip("Sides sewn together share a colour; free sides each get their own.")
        self.colours_check.toggled.connect(lambda on: host.set_visible(self._node("flat_curves"), on))
        self.lock_check = QtWidgets.QCheckBox("3  Lock the flat mesh to edit it by hand")
        self.lock_check.setToolTip("Stops regenerating the flat mesh so your own edits on it are kept.")
        self.lock_check.toggled.connect(self._lock)
        self.to3d_btn = QtWidgets.QPushButton("4  To 3D")
        self.to3d_btn.setToolTip("Project the flat mesh (with your edits) back onto the drape.")
        self.to3d_btn.clicked.connect(self._to_3d)
        self.show_flat_check = QtWidgets.QCheckBox("Show flat stage")
        self.show_flat_check.setChecked(True)
        self.show_flat_check.toggled.connect(lambda on: host.set_visible(self._node("flat_stage"), on))
        step2 = QtWidgets.QLabel("2  Adjust: drag the settings, or click a border edge of the flat mesh "
                                 "and change its count in Pieces & counts below.")
        step2.setWordWrap(True)
        ml.addWidget(self.flatten_btn, 0, 0)
        ml.addWidget(self.live_check, 0, 1, 1, 2)
        ml.addWidget(step2, 1, 0, 1, 3)
        ml.addWidget(self.counts_check, 2, 1)
        ml.addWidget(self.colours_check, 2, 2)
        ml.addWidget(self.lock_check, 3, 0, 1, 3)
        ml.addWidget(self.to3d_btn, 4, 0)
        ml.addWidget(self.show_flat_check, 4, 1)
        self._actions += [self.flatten_btn, self.to3d_btn]
        self.tabs.addTab(manual, "Manual")
        root.addWidget(mode)

        # 4 Finish --------------------------------------------------------
        fin = _group("4  Finish: thickness")
        tl = QtWidgets.QGridLayout(fin)
        self.thickness = _SliderSpin(*ui["thickness_range"], cfg["thickness"]["default"], 2, " cm")
        self.thickness.setToolTip("Real cm. The shell grows inward; the outer surface stays on the drape.")
        self.support_check = QtWidgets.QCheckBox("Support loops")
        self.support_check.setChecked(True)
        self.support_check.setToolTip("Two extra loops in the rim keep its edges crisp when subdivided.")
        add = QtWidgets.QPushButton("Add / update")
        add.clicked.connect(self._thicken)
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(self._remove_thickness)
        tl.addLayout(self.thickness.row, 0, 0, 1, 3)
        tl.addWidget(self.support_check, 1, 0)
        tl.addWidget(add, 1, 1)
        tl.addWidget(remove, 1, 2)
        self.thickness.spin.valueChanged.connect(self._thickness_changed)
        self.support_check.toggled.connect(self._thickness_changed)
        self._actions += [add, remove]
        root.addWidget(fin)
        root.addStretch(1)

        # Progress, tables, log -------------------------------------------
        bottom = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        prow = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("Ready")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.cancel_btn)
        bl.addLayout(prow)

        self.info_tabs = QtWidgets.QTabWidget()
        self.info_tabs.currentChanged.connect(self._tab_changed)
        tables = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.piece_table = self._table(self.PIECE_COLUMNS)
        self.piece_table.itemSelectionChanged.connect(self._piece_selected)
        self.piece_table.cellDoubleClicked.connect(self._piece_double_clicked)
        tables.addWidget(self._titled("Click a piece to select its faces; double-click Attach sets to select "
                                      "the faces under the attach.", self.piece_table))
        seg_box = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(seg_box)
        sv.setContentsMargins(0, 0, 0, 0)
        self.seg_label = QtWidgets.QLabel("Segments: pick a piece above")
        sv.addWidget(self.seg_label)
        self.seg_table = self._table(self.SEG_COLUMNS)
        sv.addWidget(self.seg_table)
        seg_buttons = QtWidgets.QHBoxLayout()
        self.reset_btn = QtWidgets.QPushButton("Reset selected to auto")
        self.reset_btn.clicked.connect(self._reset_counts)
        self.apply_btn = QtWidgets.QPushButton("Apply counts")
        self.apply_btn.setToolTip("Re-solve with your counts and rebuild only the pieces that change.")
        self.apply_btn.clicked.connect(self._apply_counts)
        seg_buttons.addWidget(self.reset_btn)
        seg_buttons.addStretch(1)
        seg_buttons.addWidget(self.apply_btn)
        sv.addLayout(seg_buttons)
        tables.addWidget(seg_box)
        self.info_tabs.addTab(tables, "Pieces && counts")
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.info_tabs.addTab(self.log_view, "Log")
        bl.addWidget(self.info_tabs, 1)
        self._actions += [self.reset_btn, self.apply_btn]
        split.addWidget(bottom)
        split.setSizes([520, 420])

    def _table(self, columns):
        t = QtWidgets.QTableWidget(0, len(columns))
        t.setHorizontalHeaderLabels(columns)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _titled(self, title, widget):
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel(title)
        label.setWordWrap(True)
        lay.addWidget(label)
        lay.addWidget(widget)
        return box

    def _node(self, key):
        return host.names(self.session.prefix)[key]

    # ---- busy state -----------------------------------------------------
    def _set_busy(self, on):
        self._busy = on
        locked = self.session.locked
        for w in self._actions:
            w.setEnabled(not on)
        if not on:
            self._set_locked_ui(locked)
        self.cancel_btn.setEnabled(on)
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.progress.setFormat("Ready")

    def _cancel(self):
        self.session.cancel_requested = True
        self.progress.setFormat("Cancelling...")

    # ---- source ---------------------------------------------------------
    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "MD OBJ export", self.path_edit.text(),
                                                        "OBJ files (*.obj)")
        if path:
            self.path_edit.setText(path)
            self.source = None

    @_guarded
    def _use_selected(self):
        node = host.selected_mesh()
        if node is None:
            raise SessionError("Select a mesh in the scene first.")
        self.source = ("scene", node)
        self.path_edit.setText(node)
        self._load()
        self._fill_pieces()

    def _wanted_source(self):
        text = self.path_edit.text().strip().strip('"')
        if not text:
            raise SessionError("Pick an MD OBJ export (Browse...) or select a mesh and press Use selected mesh.")
        if self.source and self.source[0] == "scene" and text == self.source[1]:
            return self.source
        path = os.path.normpath(text)
        if not os.path.isfile(path):
            raise SessionError(f"No file at {path}.")
        return ("file", path)

    def _load(self):
        kind, where = self.source
        self.session.cancel_requested = False
        if kind == "file":
            self.session.load(where)
            if self.original_check.isChecked():
                host.import_original(self.session.source, self.session.prefix)
        else:
            self.session.load_mesh_data(host.read_scene_mesh(where), where.split("|")[-1],
                                        self.units_combo.currentData())
        self.prefix_edit.setText(self.session.prefix)
        self.flat_data, self._thick_key, self.set_names, self._piece = None, None, {}, None
        self.lock_check.blockSignals(True)
        self.lock_check.setChecked(False)
        self.lock_check.blockSignals(False)
        units = "" if self.session.scene_scale == 1 else " (millimetre scene: worked in real cm)"
        self._log("info", f"Loaded {where}{units}: {len(self.session.topo.pieces)} pieces, "
                          f"{len(self.session.model.regions)} with attach lines, about "
                          f"{self.session.estimate():,} quads at {self.session.target:g} cm.")
        self._flush_log()

    def _prepare(self):
        """Push the settings into the session and (re)load the source if it changed."""
        self.session.log = []
        self.session.cancel_requested = False
        wanted = self._wanted_source()
        if wanted != self.source or self.session.mesh is None:
            self.source = wanted
            self._load()
        if not self.session.locked:
            self._push_settings()

    def _push_settings(self):
        return self.session.configure(target=self.target.value(), similar_tol=self.tol.value(),
                                      rings=self.rings_spin.value(), ring_width=self.width.value(),
                                      attach_inside=self.inside_combo.currentData())

    def _dense(self, run):
        """run(force) once; if it would be very dense, ask: lighter edge length, anyway, or stop."""
        try:
            return run(False)
        except TooDense as e:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("ReTopo")
            box.setIcon(QtWidgets.QMessageBox.Question)
            box.setText(str(e))
            lighter = box.addButton(f"Use {e.suggested:g} cm", QtWidgets.QMessageBox.AcceptRole)
            anyway = box.addButton("Build anyway", QtWidgets.QMessageBox.DestructiveRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            box.exec_() if hasattr(box, "exec_") else box.exec()
            if box.clickedButton() is lighter:
                self.target.setValue(e.suggested)
                self._push_settings()
                return run(False)
            if box.clickedButton() is anyway:
                return run(True)
            raise SessionError("Stopped before building. Raise the edge length, then try again.")

    @_guarded
    def _rename(self):
        new = clean_prefix(self.prefix_edit.text())
        old = self.session.prefix
        if new == old:
            return
        done = host.rename_prefix(old, new)
        self.set_names = {new + k[len(old):]: done.get(v, v) for k, v in self.set_names.items()}
        self.session.prefix = new
        self.prefix_edit.setText(new)
        self._log("info", f"Renamed {len(done)} node(s) and set(s) from {old}_ to {new}_.")

    # ---- auto -----------------------------------------------------------
    @_guarded
    def _analyze(self):
        self._prepare()
        self.session.analyze()
        s = self.session
        pairs = ", ".join(f"{piece_label(m.a)}/{piece_label(m.b)}" for m in s.similar) or "none"
        bases = ", ".join(piece_label(p) for p in sorted(s.model.regions)) or "none"
        self._log("info", f"Similar pairs: {pairs}; pieces with attach lines: {bases}.")
        self._flush_log()
        self._fill_pieces()

    @_guarded
    def _build(self):
        self._prepare()
        out, face_piece = self._dense(lambda force: self.session.build(force=force))
        self._place(out, face_piece)

    def _place(self, out, face_piece, thick=False):
        _, self.set_names = host.replace_mesh(out, face_piece, self.session.prefix)
        if not thick:
            self._thick_key = None
        if self.source and self.source[0] == "scene" and self.hide_source_check.isChecked():
            host.set_visible(self.source[1], False)
        poles = sum(len(p.poles) for p in self.session.patches.values())
        bad = [piece_label(pid) for pid, p in self.session.patches.items() if not p.ok]
        self._log("info", f"Built {self._node('retopo')}: {len(out.faces):,} faces, {poles} poles"
                          + (f"; pieces {', '.join(bad)} failed (see their notes)." if bad else "."))
        self._flush_log()
        self._fill_pieces()

    # ---- manual (flat stage) -------------------------------------------
    @_guarded
    def _flatten(self):
        self._prepare()
        self._show_flat(self._dense(lambda force: self.session.flatten(force=force)))

    def _show_flat(self, data):
        self.flat_data = data
        host.show_flat(self.session.prefix, data["mesh"], data["curves"], data["colours"], data["labels"],
                       data["same_topology"], self.colours_check.isChecked(), self.counts_check.isChecked())
        self.show_flat_check.blockSignals(True)
        self.show_flat_check.setChecked(True)
        self.show_flat_check.blockSignals(False)
        self._flush_log()
        self._fill_pieces()

    def _settings_changed(self, *args):
        if self.flat_data is not None and self.live_check.isChecked() and not self.session.locked \
                and self.tabs.currentIndex() == 1:
            self._timers["settings"].start()

    @_guarded
    def _live_update(self):
        self.session.log = []
        try:
            if self._push_settings():
                self._show_flat(self.session.flatten())
        except TooDense as e:   # don't pop dialogs while a slider is dragged
            self._log("warning", f"Not updated: {e} Press Flatten to build it anyway.")

    def _lock(self, on):
        try:
            self.session.lock(on)
        except SessionError as e:
            self.lock_check.blockSignals(True)
            self.lock_check.setChecked(False)
            self.lock_check.blockSignals(False)
            self._error(str(e))
            return
        self._set_locked_ui(on)
        self._log("info", "Flat mesh locked: edit it by hand, then press To 3D." if on else
                  "Unlocked: the next change regenerates the flat mesh and replaces your hand edits.")

    def _set_locked_ui(self, on):
        for w in self._settings_widgets + [self.flatten_btn, self.apply_btn, self.reset_btn]:
            w.setEnabled(not on)
        self._fill_segments()

    @_guarded
    def _to_3d(self):
        out, face_piece = self.session.to_3d(host.read_flat(self.session.prefix))
        self._place(out, face_piece)
        self.show_flat_check.setChecked(False)

    # ---- counts ---------------------------------------------------------
    @_guarded
    def _apply_counts(self):
        if self.session.res is None:
            raise SessionError("Analyze, build or flatten first.")
        manual = self.flat_data is not None and self.tabs.currentIndex() == 1
        changed = self.session.apply_counts(output=not manual)
        if manual:
            self._show_flat(self.session.flatten())
            self._log("info", f"Updated the flat stage ({len(changed)} piece(s) changed).")
        elif self.session.output is not None and changed:
            out, face_piece = self.session.build()
            self._place(out, face_piece)
            self._log("info", f"Rebuilt pieces {', '.join(piece_label(p) for p in sorted(changed))}.")
        else:
            self._log("info", "Counts solved." + ("" if self.session.output is not None
                                                  else " Press Build mesh or Flatten to see them."))
            self._flush_log()
            self._fill_pieces()

    def _reset_counts(self):
        rows = {i.row() for i in self.seg_table.selectedIndexes()}
        try:
            for r in rows:
                self.session.clear_count(self.seg_table.item(r, 0).data(QtCore.Qt.UserRole))
        except SessionError as e:
            self._error(str(e))
            return
        if rows:
            self._fill_segments()
            self._log("info", "Reset to auto. Press Apply counts.")

    # ---- thickness ------------------------------------------------------
    def _thickness_changed(self, *args):
        if self._thick_key is not None:
            self._timers["thickness"].start()

    @_guarded
    def _thicken(self):
        support = self.support_check.isChecked()
        out, face_piece = self.session.thickened(self.thickness.value(), support)
        key = (self.session.output_version, support)
        if key == self._thick_key and host.move_points(self._node("retopo"), out.positions):
            return   # only the thickness changed: moved in place
        self._place(out, face_piece, thick=True)
        self._thick_key = key

    @_guarded
    def _remove_thickness(self):
        if self._thick_key is None:
            return
        out, face_piece = self.session.scene_output()
        self._place(out, face_piece)

    # ---- tables ---------------------------------------------------------
    def _fill_pieces(self):
        if self.session.topo is None:
            return
        rows = self.session.piece_rows()
        keep = self._piece
        self._filling = True
        t = self.piece_table
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [row["label"], row["layout"], row["quads"], row["rings"], row["poles"], row["twin"],
                    ", ".join(row["attach_sets"]), row["note"]]
            for c, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem("" if v is None else str(v))
                item.setData(QtCore.Qt.UserRole, row["id"])
                if row["ok"] is False:
                    item.setForeground(QtGui.QBrush(QtGui.QColor(220, 80, 80)))
                if c == 7:
                    item.setToolTip(str(v))
                t.setItem(r, c, item)
        t.resizeColumnsToContents()
        self._filling = False
        if keep is not None and keep < len(rows):
            self._selecting = True
            t.selectRow(keep)
            self._selecting = False
        self._fill_segments()

    def _piece_selected(self):
        if self._filling:
            return
        rows = sorted({i.row() for i in self.piece_table.selectedIndexes()})
        if not rows:
            return
        pieces = [self.piece_table.item(r, 0).data(QtCore.Qt.UserRole) for r in rows]
        self._piece = pieces[0]
        if not self._selecting:
            self._select_pieces(pieces)
        self._fill_segments()

    def _select_pieces(self, pieces):
        flat_shown = self.flat_data is not None and self.show_flat_check.isChecked() and not self.session.locked
        if flat_shown:
            host.select_faces(self._node("flat"), self.session.flat_faces_of(pieces))
        else:
            names = [f"{self.session.prefix}_piece_{piece_label(p)}" for p in pieces]
            host.select_sets([self.set_names.get(n) for n in names])

    def _piece_double_clicked(self, row, col):
        if col == 6:
            names = [n for n in self.piece_table.item(row, 6).text().split(", ") if n]
            found = host.select_sets([self.set_names.get(f"{self.session.prefix}_{n}") for n in names])
            if found:
                self._log("info", f"Selected {', '.join(names)}. Press Delete to open the hole(s).")

    def _fill_segments(self, focus=None):
        t = self.seg_table
        if self._piece is None or self.session.model is None:
            t.setRowCount(0)
            return
        rows = self.session.segment_rows(self._piece)
        self.seg_label.setText(f"Segments of piece {piece_label(self._piece)}: set a count, then Apply counts "
                               "(live in Manual mode).")
        editable = not self.session.locked
        self._filling = True
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [row["id"], row["kind"], f"{row['length']:.1f}", row["partners"], None,
                    "yes" if row["yours"] else ""]
            for c, v in enumerate(vals):
                if c != 4:
                    item = QtWidgets.QTableWidgetItem("" if v is None else str(v))
                    item.setData(QtCore.Qt.UserRole, row["id"])
                    t.setItem(r, c, item)
            spin = QtWidgets.QSpinBox()
            spin.setRange(1, 999)
            spin.setEnabled(row["count"] is not None and editable)
            if row["count"] is not None:
                spin.setValue(row["count"])
            spin.valueChanged.connect(functools.partial(self._count_edited, row["id"], r))
            t.setCellWidget(r, 4, spin)
            if row["id"] == focus:
                t.selectRow(r)
                spin.setFocus()
        t.resizeColumnsToContents()
        self._filling = False

    def _count_edited(self, seg_id, row, value):
        if self._filling:
            return
        try:
            self.session.set_count(seg_id, int(value))
        except SessionError as e:
            self._error(str(e))
            return
        self.seg_table.setItem(row, 5, QtWidgets.QTableWidgetItem("yes"))
        if self.flat_data is not None and self.live_check.isChecked() and self.tabs.currentIndex() == 1:
            self._timers["counts"].start()

    @_guarded
    def _live_counts(self):
        self.session.apply_counts(output=False)
        self._show_flat(self.session.flatten())

    # ---- viewport -------------------------------------------------------
    def _viewport_selection(self):
        """A border edge clicked on the flat mesh picks its piece and segment."""
        if self._busy or self._selecting or self.flat_data is None or self.session.locked:
            return
        try:
            edges = host.selected_flat_edges(self.session.prefix)
            sid = next((s for s in (self.session.segment_of_edge(*e) for e in edges) if s is not None), None)
            if sid is not None:
                self.pick_segment(sid)
        except Exception:   # noqa: BLE001 - never let a selection callback raise inside Maya
            self._log("error", traceback.format_exc())

    def pick_segment(self, sid):
        pid = self.session.model.segments[sid].piece
        self._selecting = True
        try:
            self.info_tabs.setCurrentIndex(0)
            for r in range(self.piece_table.rowCount()):
                if self.piece_table.item(r, 0).data(QtCore.Qt.UserRole) == pid:
                    self.piece_table.selectRow(r)
                    break
            self._piece = pid
            self._fill_segments(focus=sid)
        finally:
            self._selecting = False

    def shutdown(self):
        host.kill_job(self._job)
        self._job = None

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def dockCloseEventTriggered(self):
        self.shutdown()

    # ---- feedback -------------------------------------------------------
    def _on_progress(self, step, frac):
        self.progress.setValue(int(frac * 100))
        self.progress.setFormat(f"{step}  %p%")
        QtWidgets.QApplication.processEvents()   # keeps Cancel clickable; other actions are off while busy

    def _log(self, level, message):
        self.log_view.appendPlainText(f"[{level}] {message}")
        if level in ("warning", "error") and self.info_tabs.currentIndex() != 1:
            self._unread += 1
            self.info_tabs.setTabText(1, f"Log ({self._unread})")

    def _tab_changed(self, index):
        if index == 1:
            self._unread = 0
            self.info_tabs.setTabText(1, "Log")

    def _flush_log(self):
        for level, message in self.session.log:
            self._log(level, message)
        self.session.log = []

    def _error(self, message):
        self._log("error", message)
        QtWidgets.QMessageBox.warning(self, "ReTopo", message)
