# MERGE_PLAN — adding the Ejector Test Panel to the sorter

`ejector_test_panel.py` contains one reusable widget, **`EjectorTestPanel`**. Today it runs alone
(`RUN_EJECTOR_TEST_PANEL.bat`). This plan adds it to `LAMTON_LINE_NEW_ESP32.py` **without opening
the COM port a second time**.

> **STATUS 16 Oct 2026: DONE (Option A).** Button `EJECTOR TEST PANEL` in the footer, method
> `MainDashboard.open_ejector_test_panel()`, switch `ejector_test_panel_button` = **ON by default**
> (the user's decision, an exception to "new features OFF"). Locked for OPERATOR. Tested in the real
> main window with the ejector on COM13.

## The idea in one picture

```
 MainDashboard ──► self.esp32  (ESP32Controller)
                       └── .ejector_link  (EjectorLink, COM24, heartbeat thread)   ◄── the ONLY port owner
                                ▲
 EjectorTestPanel(link_provider=lambda: self.esp32.ejector_link)
     • reads the link (state, counters, TEST_STEP events) every 40 ms
     • sends commands through it (fire, test, stop, remap)
     • never opens / closes the port, never replaces the sorter's callbacks
```

`link_provider` is a *function* rather than the link object. The sorter can disconnect and reconnect
(`connect_ejector()` creates a new `EjectorLink`), and the panel then picks up the new link automatically.

## Recommended: Option A — its own dialog behind a feature switch (about 25 lines, OFF by default)

**1. `features.py`, add a default** (rule 3: new features OFF):
```python
DEFAULTS = {
    ...
    "continuous_capture_button": False,
    "ejector_test_panel_button": False,      # NEW
}
```
and `"ejector_test_panel_button": false` in `features_config.json`.

**2. `LAMTON_LINE_NEW_ESP32.py`, footer buttons** (next to the existing switch for
`continuous_capture_button`, around line 13173):
```python
        if features.enabled("ejector_test_panel_button"):
            btn_ejtest = QPushButton("EJECTOR TEST PANEL")
            btn_ejtest.setStyleSheet("background-color: #333; color: #00e5ff; border: 1px solid #00e5ff; font-weight: bold;")
            btn_ejtest.clicked.connect(self.open_ejector_test_panel)
            btn_layout.addWidget(btn_ejtest)
```

**3. `MainDashboard`, new method** (next to `show_ejector_window`):
```python
    def open_ejector_test_panel(self):
        from ejector_test_panel import EjectorTestPanel      # import only the panel class (see note 1)
        dlg = QDialog(self)
        dlg.setWindowTitle("63-CH Ejector Test Panel")
        dlg.resize(1100, 980)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        panel = EjectorTestPanel(link_provider=lambda: self.esp32.ejector_link, parent=dlg)
        lay.addWidget(panel)
        # same flags as the existing ejector window: the sorter must NOT fire while we test
        self.esp32.test_mode = True
        self.esp32.ejector_test_active = True
        try:
            dlg.exec_()
        finally:
            panel.shutdown()                      # all valves OFF; the shared link stays open
            self.esp32.ejector_test_active = False
            self.esp32.test_mode = False
```
`exec_()` also returns when the user presses the window's X, so `finally` always runs.

**4. Role lock (optional):** like `btn_ejector`, disable the button for `OPERATOR` in `change_user_role()`.

## Option B — as a tab inside the existing "Ejector Diagnostics" dialog

`EjectorTestWindow(QDialog)` (line ~7504) has no tabs today. You would have to:
1. wrap its current content in a `QTabWidget` (tab "Classic"),
2. add `EjectorTestPanel(link_provider=lambda: self.arduino.ejector_link)` as tab "Test Panel",
3. call `panel.shutdown()` in its `close_cleanly()`,
4. enlarge the dialog from 1024 × 768 to at least **1024 × 1010** (the embedded panel needs 916 × 970, plus the tab bar).

This touches more of the old dialog, so Option A is safer to start with.

## Rules the host must keep

| Must | Why |
|---|---|
| Pass `link_provider`, **never** create a second `EjectorLink` for the same port | Windows allows only one program/object per COM port |
| Set `esp32.ejector_test_active = True` (and `test_mode`) while the panel is open | `ESP32Controller` (lines ~1194/1222) then skips sorting ejections, so the sorter and the panel never fire at the same time |
| Call `panel.shutdown()` when the dialog/tab closes | sends TEST stop + STOP ALL (all valves OFF) and stops the panel's timer/thread. It does **not** close or disarm the shared link |
| Do not change `link.on_event` / `on_text` for the panel | the panel only *reads* `link.counters`, `link.last_test_step`, `link.test_done_count`, `link.esp_state`, `link.text_lines`. The sorter's `_on_ejector_event` (watchdog → safety stop) stays in charge |

## Notes

1. **Name clash:** both files contain a class called `EjectorTestWindow` (the sorter's QDialog and the
   panel's standalone QMainWindow). Import only `EjectorTestPanel` as shown, never `*`.
2. **Sizes (measured):** embedded (no COM controls) minimum **916 × 970 px**; standalone 1220 × 970.
   Opening the remap section adds about 60 px of height.
3. **Style:** the panel uses the same grey palette as the sorter's ejector dialog (#1a1a1a / #242424 / cyan
   titles) and sets its own stylesheet, so the main window's style cannot break it.
4. **Esc:** inside the panel, Esc = STOP ALL (only while the focus is in the panel, so Esc elsewhere in the
   sorter keeps its old meaning).
5. **Counters:** `fired`, `crc_err`, `len_err`, `watchdog trips` are counted by the ESP32 since power-up.
   `no-ack` is the PC's own count, shared with the sorter's link.

## Test after merging

1. `features_config.json` switch OFF → the sorter looks exactly as before (no new button).
2. Switch ON → the button appears; open the panel while the sorter is connected → badge `v2.0 BINARY · ARMED`, no
   "port busy" error.
3. Click a channel / run AUTO 1–63 → valves fire; the sorter does not eject at the same time.
4. Close the panel → the sorter still shows the ejector connected, and START works.
5. `python tools\test_ejector_test_panel_sim.py` → ALL TESTS PASSED (includes the embedded mode).
