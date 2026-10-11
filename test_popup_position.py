"""Popup placement in a multi-monitor virtual desktop."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import app


class PopupTests(unittest.TestCase):
    def assert_inside(self, anchor, size, bounds):
        x, y = app.tooltip_position(anchor, size, bounds)
        left, top, right, bottom = bounds
        self.assertGreaterEqual(x, left + 8)
        self.assertGreaterEqual(y, top + 8)
        self.assertLessEqual(x + size[0], right - 8)
        self.assertLessEqual(y + size[1], bottom - 8)
        return x, y

    def test_monitor_right_of_primary(self):
        x, _ = self.assert_inside((2200, 150, 30), (440, 260), (1920, 0, 4480, 1400))
        self.assertGreater(x, 1920)

    def test_negative_monitor_origin(self):
        x, y = self.assert_inside((-1800, -900, 30), (440, 260), (-1920, -1080, 0, 0))
        self.assertLess(x, 0)
        self.assertLess(y, 0)

    def test_tooltip_moves_above_widget_at_bottom(self):
        _, y = self.assert_inside((2200, 1000, 30), (440, 260), (1920, 0, 3840, 1040))
        self.assertLess(y + 260, 1000)

    def test_all_monitor_edges(self):
        for bounds in ((0, 0, 1920, 1040), (-1920, 0, 0, 1040), (1920, -1080, 3840, 0)):
            left, top, right, bottom = bounds
            for anchor in ((left, top, 30), (right - 20, top, 30), (left, bottom - 20, 30)):
                with self.subTest(bounds=bounds, anchor=anchor):
                    self.assert_inside(anchor, (440, 260), bounds)

    def test_directory_dialogs_have_application_as_owner(self):
        owner = SimpleNamespace()
        with patch.object(app.filedialog, 'askdirectory', return_value='') as dialog:
            app.App.browse(owner)
            self.assertIs(dialog.call_args.kwargs['parent'], owner)
            app.App.browse_source(owner)
            self.assertIs(dialog.call_args.kwargs['parent'], owner)


if __name__ == '__main__':
    unittest.main()
