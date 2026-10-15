"""The shared look and the three rendered pages: placeholders filled, values escaped, and the
accessibility basics a production screen needs (landmarks, labelled controls, no pop-ups)."""
import re
import unittest

from garment_cv.backend.api.dashboard import render_dashboard
from garment_cv.backend.api.pages import render_status, render_zones
from garment_cv.backend.api.ui import BASE_CSS, esc, fill, js

HOSTILE = '<img src=x onerror=alert(1)> "Cam" & </script>'


class Helpers(unittest.TestCase):
    def test_fill_is_single_pass_so_a_value_is_never_re_expanded(self):
        self.assertEqual(fill("A __X__ B __Y__", x="__Y__", y="done"), "A __Y__ B done")

    def test_fill_leaves_unknown_tokens_alone(self):
        self.assertEqual(fill("__KNOWN__ __OTHER__", known="1"), "1 __OTHER__")

    def test_js_literal_cannot_close_a_script_element(self):
        out = js("a</script><script>alert(1)</script>")
        self.assertNotIn("</", out)
        self.assertNotIn("<", out)

    def test_esc_escapes_quotes_and_angle_brackets(self):
        self.assertEqual(esc('<a href="x">&'), "&lt;a href=&quot;x&quot;&gt;&amp;")


class Pages(unittest.TestCase):
    def pages(self):
        return {
            "dashboard": render_dashboard(HOSTILE, "cam-1"),
            "zones": render_zones(HOSTILE, "cam-1"),
            "status": render_status(HOSTILE, [("ok", HOSTILE, HOSTILE)], "/live.mjpg", HOSTILE, [("k", HOSTILE, HOSTILE)], True),
        }

    def test_no_placeholder_is_left_unfilled(self):
        for name, page in self.pages().items():
            self.assertNotRegex(page, r"__[A-Z][A-Z0-9_]*__", name)

    def test_hostile_text_is_escaped_everywhere(self):
        for name, page in self.pages().items():
            self.assertNotIn("<img src=x", page, name)
            self.assertNotIn("onerror=alert(1)>", page, name)

    def test_every_page_has_landmarks_a_skip_link_and_a_language(self):
        for name, page in self.pages().items():
            self.assertIn('<html lang="en">', page, name)
            self.assertIn("<main", page, name)
            self.assertIn('class="skip-link"', page, name)
            self.assertIn('name="viewport"', page, name)

    def test_no_browser_pop_ups(self):
        normal = {"dashboard": render_dashboard("Cam", "c"), "zones": render_zones("Cam", "c"),
                  "status": render_status("Cam", [("ok", "Camera", "fine")], "/live.mjpg", "note", [], True)}
        for name, page in normal.items():
            self.assertNotRegex(page, r"\balert\(", name)
            self.assertNotRegex(page, r"\bconfirm\(", name)

    def test_dashboard_tabs_follow_the_aria_tab_pattern(self):
        page = render_dashboard("Cam", "c")
        self.assertEqual(page.count('role="tab"'), 5)
        self.assertEqual(page.count('role="tabpanel"'), 5)
        self.assertEqual(len(re.findall(r'role="tab"[^>]*aria-selected="true"', page)), 1)   # exactly one selected tab

    def test_dashboard_honours_reduced_motion_and_uses_no_web_fonts(self):
        page = render_dashboard("Cam", "c")
        self.assertIn("prefers-reduced-motion: reduce", page)
        self.assertNotIn("fonts.googleapis", page)
        self.assertNotRegex(page, r'(src|href)="https?://')                 # nothing loaded from the internet

    def test_palette_keeps_secondary_text_readable(self):
        # WCAG AA needs 4.5:1; measured 5.0:1 on the canvas for this value
        self.assertIn("--muted: #6B6A66;", BASE_CSS)

    def test_status_page_lists_each_check_with_its_state(self):
        page = render_status("Cam", [("ok", "Camera", "fine"), ("bad", "Odoo", "down")], "/live.mjpg", "note", [], False)
        self.assertIn('class="dot ok"', page)
        self.assertIn('class="dot bad"', page)
        self.assertNotIn('href="/dashboard"', page)                          # no dashboard link when there is none


if __name__ == "__main__":
    unittest.main()
