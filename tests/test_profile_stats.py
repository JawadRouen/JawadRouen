import importlib.util
import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "profile_stats.py"
SPEC = importlib.util.spec_from_file_location("profile_stats", SCRIPT)
profile_stats = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(profile_stats)


class LanguageClassificationTests(unittest.TestCase):
    def test_programming_languages(self):
        cases = {
            "src/service.py": "Python",
            "app/page.tsx": "TypeScript",
            "queries/model.sql": "SQL",
            "analysis/model.R": "R",
            "infra/main.tf": "HCL",
            "Dockerfile": "Dockerfile",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(profile_stats.language_for_path(path), expected)

    def test_generated_and_non_source_files_are_excluded(self):
        for path in (
            "node_modules/pkg/index.ts",
            "frontend/package-lock.json",
            "dist/bundle.js",
            "notebooks/model.ipynb",
            "README.md",
            "data/fixture.json",
        ):
            with self.subTest(path=path):
                self.assertIsNone(profile_stats.language_for_path(path))


class SvgTests(unittest.TestCase):
    def test_activity_svg_is_valid_and_contains_exact_totals(self):
        data = {
            "totals": {
                "commits": 3572,
                "issues": 73,
                "pull_requests": 542,
                "reviews": 1370,
                "private_recent": 2131,
            },
            "by_year": [
                {"year": 2021, "commits": 100},
                {"year": 2022, "commits": 200},
                {"year": 2023, "commits": 300},
                {"year": 2024, "commits": 400},
                {"year": 2025, "commits": 500},
                {"year": 2026, "commits": 600},
            ],
            "window_commits": 2100,
            "updated_at": "2026-08-07T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "activity.svg"
            profile_stats.render_activity(data, output)
            mobile = pathlib.Path(directory) / "activity-mobile.svg"
            profile_stats.render_activity_mobile(data, mobile)
            ET.parse(output)
            ET.parse(mobile)
            content = output.read_text(encoding="utf-8")
            for value in ("3,572", "73", "542", "1,370", "2,131"):
                self.assertIn(value, content)
            self.assertNotIn("private/repository", content)

    def test_depth_svg_is_valid(self):
        data = {
            "window_start": "2021-08-07",
            "window_end": "2026-08-07",
            "search_authored_commits": 3000,
            "repositories_with_authored_commits": 20,
            "active_coding_days": 500,
            "source_line_changes": 150000,
            "private_source_line_changes": 120000,
            "public_source_line_changes": 30000,
            "code_file_changes": 9000,
            "source_lines_added": 100000,
            "source_lines_deleted": 50000,
            "non_merge_commits_analyzed": 2800,
            "private_non_merge_commits": 2400,
            "public_non_merge_commits": 400,
            "private_repositories_analyzed": 18,
            "public_repositories_analyzed": 2,
            "languages": [
                {"language": "Python", "changes": 100000, "percentage": 66.666},
                {"language": "TypeScript", "changes": 50000, "percentage": 33.333},
            ],
            "languages_by_year": [
                {
                    "year": 2021,
                    "changes": 1000,
                    "languages": [
                        {"language": "Python", "changes": 800, "percentage": 80.0},
                        {"language": "TypeScript", "changes": 200, "percentage": 20.0},
                    ],
                },
                {
                    "year": 2026,
                    "changes": 1000,
                    "languages": [
                        {"language": "TypeScript", "changes": 700, "percentage": 70.0},
                        {"language": "Python", "changes": 300, "percentage": 30.0},
                    ],
                },
            ],
            "updated_at": "2026-08-07T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "depth.svg"
            profile_stats.render_depth(data, output)
            mobile = pathlib.Path(directory) / "depth-mobile.svg"
            profile_stats.render_depth_mobile(data, mobile)
            ET.parse(output)
            ET.parse(mobile)
            content = output.read_text(encoding="utf-8")
            self.assertIn("Python", content)
            self.assertIn("66.7%", content)
            self.assertIn('viewBox="0 0 960 900"', content)
            self.assertIn("LANGUAGE EVOLUTION", content)


if __name__ == "__main__":
    unittest.main()
