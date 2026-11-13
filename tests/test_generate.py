import pathlib
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import generate as g

SVG = "{http://www.w3.org/2000/svg}"


def data(label="Alpha", langs=None, note="", repo="me/alpha", pushed="2026-11-13T01:02:37Z"):
    langs = {"Python": 600, "Rust": 400} if langs is None else langs
    return g.ProjectData(g.Project(repo, label, note), langs, pushed)


def overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    eps = 1e-6
    return ax + aw > bx + eps and bx + bw > ax + eps and ay + ah > by + eps and by + bh > ay + eps


class SquarifyTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(g.squarify([], 0, 0, 100, 50), [])

    def test_single_fills_the_box(self):
        self.assertEqual(g.squarify([7], 10, 20, 100, 50), [(10, 20, 100, 50)])

    def test_areas_are_proportional_and_in_input_order(self):
        weights = [1, 6, 2, 3, 4, 2, 6]
        rects = g.squarify(weights, 0, 0, 600, 400)
        self.assertEqual(len(rects), len(weights))
        unit = 600 * 400 / sum(weights)
        for weight, (_, _, w, h) in zip(weights, rects):
            self.assertAlmostEqual(w * h, weight * unit, places=5)

    def test_rects_stay_inside_and_do_not_overlap(self):
        rects = g.squarify([5, 3, 3, 2, 1, 1, 0.5], 10, 20, 300, 200)
        for x, y, w, h in rects:
            self.assertGreaterEqual(x, 10 - 1e-6)
            self.assertGreaterEqual(y, 20 - 1e-6)
            self.assertLessEqual(x + w, 310 + 1e-6)
            self.assertLessEqual(y + h, 220 + 1e-6)
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(overlap(a, b), (a, b))

    def test_prefers_squarish_cells(self):
        rects = g.squarify([1, 1, 1, 1], 0, 0, 200, 200)
        for _, _, w, h in rects:
            self.assertAlmostEqual(w, 100)
            self.assertAlmostEqual(h, 100)


class FoldSmallTest(unittest.TestCase):
    def test_merges_languages_under_the_share(self):
        langs = {"Rust": 560, "TypeScript": 260, "Svelte": 170, "CSS": 8, "HTML": 2}
        self.assertEqual(
            g.fold_small(langs, min_share=0.03),
            [("Rust", 560), ("TypeScript", 260), ("Svelte", 170), ("Other", 10)],
        )

    def test_sorted_by_size_then_name(self):
        self.assertEqual(
            g.fold_small({"B": 50, "A": 50, "C": 100}),
            [("C", 100), ("A", 50), ("B", 50)],
        )

    def test_nothing_to_merge_adds_no_other(self):
        self.assertEqual(g.fold_small({"Python": 10}), [("Python", 10)])

    def test_empty(self):
        self.assertEqual(g.fold_small({}), [])


class HumanSizeTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(g.human_size(0), "0 B")
        self.assertEqual(g.human_size(999), "999 B")
        self.assertEqual(g.human_size(1000), "1 KB")
        self.assertEqual(g.human_size(482605), "483 KB")
        self.assertEqual(g.human_size(999_999), "1.0 MB")
        self.assertEqual(g.human_size(1_260_000), "1.3 MB")


class ConfigTest(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "projects.toml"
            path.write_text(
                'title = "someone"\n\n'
                '[[project]]\nrepo = "a/b"\nlabel = "B"\nnote = "a note"\n\n'
                '[[project]]\nrepo = "c/d"\nlabel = "D"\n',
                encoding="utf-8",
            )
            config = g.load_config(path)
        self.assertEqual(config.title, "someone")
        self.assertEqual(
            config.projects,
            [g.Project("a/b", "B", "a note"), g.Project("c/d", "D", "")],
        )


class RenderTest(unittest.TestCase):
    def render(self, projects, theme="dark"):
        return g.render("someone", projects, theme)

    def texts(self, svg):
        root = ET.fromstring(svg)
        return ["".join(node.itertext()) for node in root.iter(SVG + "text")]

    def test_is_well_formed_and_names_everything(self):
        for theme in ("dark", "light"):
            svg = self.render([data("Alpha"), data("Beta", {"Java": 900}, repo="me/beta")], theme)
            texts = " | ".join(self.texts(svg))
            for word in ("someone", "Alpha", "Beta", "Python", "Rust", "Java", "2 projects"):
                self.assertIn(word, texts, theme)

    def test_themes_differ(self):
        projects = [data()]
        self.assertNotEqual(self.render(projects, "dark"), self.render(projects, "light"))

    def test_unknown_theme_is_rejected(self):
        with self.assertRaises(ValueError):
            self.render([data()], "sepia")

    def test_is_deterministic(self):
        projects = [data("Alpha"), data("Beta", {"Java": 900}, repo="me/beta")]
        self.assertEqual(self.render(projects), self.render(projects))

    def test_updated_is_the_newest_push_not_today(self):
        svg = self.render([
            data("Alpha", pushed="2026-11-02T11:59:48Z"),
            data("Beta", {"Java": 900}, repo="me/beta", pushed="2026-11-13T01:02:37Z"),
        ])
        self.assertIn("updated 2026-11-13", " | ".join(self.texts(svg)))

    def test_escapes_markup_in_labels(self):
        svg = self.render([data('R&D <"x">', note="a & b")])
        self.assertIn('R&D <"x">', " | ".join(self.texts(svg)))

    def test_single_project_fills_the_map(self):
        svg = self.render([data("Solo")])
        self.assertIn("1 project ", " | ".join(self.texts(svg)) + " ")
        self.assertNotIn("1 projects", svg)

    def test_project_without_languages_is_skipped(self):
        svg = self.render([data("Alpha"), data("Ghost", {}, repo="me/ghost")])
        texts = " | ".join(self.texts(svg))
        self.assertIn("Alpha", texts)
        self.assertNotIn("Ghost", texts)
        self.assertIn("1 project", texts)

    def test_nothing_to_draw_is_an_error(self):
        with self.assertRaises(ValueError):
            self.render([data("Ghost", {})])

    def test_tiny_cell_gets_no_label(self):
        svg = self.render([data("Alpha", {"Python": 100000, "Zig": 3200})])
        texts = " | ".join(self.texts(svg))
        self.assertIn("Python", texts)
        self.assertNotIn("Zig", texts)

    def test_narrow_cell_falls_back_to_a_short_name(self):
        svg = self.render([data("Alpha", {"Python": 100000, "JavaScript": 9000})])
        texts = self.texts(svg)
        self.assertIn("JS", texts)
        self.assertNotIn("JavaScript", texts)

    def test_text_colour_is_the_one_with_more_contrast(self):
        self.assertEqual(g.ink("#dea584", "#111111", "#ffffff"), "#111111")
        self.assertEqual(g.ink("#f1e05a", "#111111", "#ffffff"), "#111111")
        self.assertEqual(g.ink("#3572a5", "#111111", "#ffffff"), "#ffffff")
        self.assertEqual(g.ink("#663399", "#111111", "#ffffff"), "#ffffff")

    def test_long_label_never_overflows_its_block(self):
        long = "An extraordinarily long project label that cannot possibly fit"
        svg = self.render([
            data("Big", {"Python": 100000}),
            data(long, {"Rust": 9000}, repo="me/long", note="and a long note as well"),
        ])
        texts = self.texts(svg)
        self.assertNotIn(long, texts)
        self.assertNotIn("and a long note as well", texts)

    def test_animation_respects_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", self.render([data()]))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.config = self.root / "projects.toml"
        self.config.write_text(
            'title = "someone"\n\n'
            '[[project]]\nrepo = "a/one"\nlabel = "One"\n\n'
            '[[project]]\nrepo = "a/two"\nlabel = "Two"\n',
            encoding="utf-8",
        )
        self.out = self.root / "assets"
        self.args = ["--config", str(self.config), "--out", str(self.out)]

    def test_writes_both_themes(self):
        def fetcher(repo, token):
            return {"Python": 1000}, "2026-11-13T01:02:37Z"

        self.assertEqual(g.main(self.args, fetcher=fetcher), 0)
        for name in ("map-dark.svg", "map-light.svg"):
            ET.fromstring((self.out / name).read_text(encoding="utf-8"))

    def test_failed_fetch_leaves_existing_files_untouched(self):
        self.out.mkdir()
        (self.out / "map-dark.svg").write_text("old dark", encoding="utf-8")
        (self.out / "map-light.svg").write_text("old light", encoding="utf-8")

        def fetcher(repo, token):
            if repo == "a/two":
                raise OSError("rate limit exceeded")
            return {"Python": 1000}, "2026-11-13T01:02:37Z"

        self.assertNotEqual(g.main(self.args, fetcher=fetcher), 0)
        self.assertEqual((self.out / "map-dark.svg").read_text(encoding="utf-8"), "old dark")
        self.assertEqual((self.out / "map-light.svg").read_text(encoding="utf-8"), "old light")

    def test_all_projects_empty_is_a_failure(self):
        def fetcher(repo, token):
            return {}, "2026-11-13T01:02:37Z"

        self.assertNotEqual(g.main(self.args, fetcher=fetcher), 0)
        self.assertFalse((self.out / "map-dark.svg").exists())


class FetchTest(unittest.TestCase):
    def test_reads_languages_and_push_date_and_sends_token(self):
        seen = []

        class Reply:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return self.body

        def opener(request, timeout=None):
            seen.append((request.full_url, request.get_header("Authorization")))
            if request.full_url.endswith("/languages"):
                return Reply(b'{"Python": 12, "Rust": 3}')
            return Reply(b'{"pushed_at": "2026-11-13T01:02:37Z"}')

        langs, pushed = g.fetch("a/one", "secret", opener=opener)
        self.assertEqual(langs, {"Python": 12, "Rust": 3})
        self.assertEqual(pushed, "2026-11-13T01:02:37Z")
        self.assertEqual(
            seen,
            [
                ("https://api.github.com/repos/a/one/languages", "Bearer secret"),
                ("https://api.github.com/repos/a/one", "Bearer secret"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
