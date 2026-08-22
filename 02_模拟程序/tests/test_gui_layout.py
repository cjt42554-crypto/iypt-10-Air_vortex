"""Geometry gates for controls that must survive a large Matplotlib request."""

from __future__ import annotations

import tkinter as tk
import unittest

from airvortex.gui.plots import ProcessPlots


class TimelineLayoutTests(unittest.TestCase):
    def test_timeline_and_time_label_keep_their_full_height(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        try:
            try:
                root.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            root.geometry("1045x712+0+0")
            plots = ProcessPlots(
                root,
                on_seek=lambda _value: None,
                on_toggle_play=lambda: None,
            )
            plots.pack(fill=tk.BOTH, expand=True)

            # Reproduce the failure mode: a rendered Matplotlib canvas may ask
            # for substantially more height than the notebook can provide.
            canvas_widget = plots.canvas.get_tk_widget()
            canvas_widget.configure(height=1200)
            plots.set_timeline(10.0, 4.98)
            root.update()

            self.assertEqual(canvas_widget.winfo_manager(), "grid")
            self.assertEqual(plots.toolbar.winfo_manager(), "grid")
            self.assertEqual(plots.timeline_controls.winfo_manager(), "grid")
            self.assertTrue(plots.timeline_controls.winfo_ismapped())
            self.assertTrue(plots.time_label.winfo_ismapped())
            self.assertGreaterEqual(
                plots.time_label.winfo_height(),
                plots.time_label.winfo_reqheight(),
            )
            self.assertLessEqual(
                plots.timeline_controls.winfo_y()
                + plots.timeline_controls.winfo_height(),
                plots.winfo_height(),
            )
            self.assertLessEqual(
                plots.time_label.winfo_x() + plots.time_label.winfo_width(),
                plots.timeline_controls.winfo_width(),
            )
            self.assertEqual(plots.time_label.cget("text"), "t = 4.98 s")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
