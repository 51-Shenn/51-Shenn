"""Regression checks for the statistics shown on the profile."""

from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from generate_profile_preview import PALETTES, contributions
from profile_stats import CalendarParser, authenticated_data, fetch, public_data, summarize


def data(counts, first="2026-01-01"):
    start = date.fromisoformat(first)
    return {"login": "51-Shenn", "languages": [],
            "days": [{"date": (start + timedelta(days=i)).isoformat(), "count": count}
                     for i, count in enumerate(counts)]}


class StatsTests(unittest.TestCase):
    def test_supplied_svg_uses_live_numbers_and_generated_sparkline(self):
        stats = summarize(data([1, 2, 0, 4, 8]))
        for palette in PALETTES.values():
            graphic = contributions(stats, palette)
            root = ET.fromstring(graphic)
            ns = {'s': 'http://www.w3.org/2000/svg'}
            self.assertEqual(root.get('viewBox'), '0 0 620 148')
            texts = [node.text for node in root.findall('.//s:text', ns)]
            self.assertIn('15', texts)
            self.assertIn('4', texts)
            self.assertIn('JBMono', graphic)
            self.assertIn('data:font/woff2;base64,', graphic)
            self.assertNotIn('__TOTAL__', graphic)
            self.assertNotIn('__PALETTE__', graphic)
            self.assertEqual(root.findall('.//s:path', ns)[1].get('d'), 'M0.0 126.0L620.0 90.0')
            self.assertEqual(root.find('s:circle', ns).get('cy'), '90.0')

    def test_today_without_activity_preserves_yesterdays_streak(self):
        stats = summarize(data([1, 1, 0, 2, 3, 0]))
        self.assertEqual(stats["total"], 7)
        self.assertEqual(stats["active"], 4)
        self.assertEqual(stats["current"], {"length": 2, "start": "2026-01-04", "end": "2026-01-05"})
        self.assertEqual(stats["longest"], {"length": 2, "start": "2026-01-01", "end": "2026-01-02"})

    def test_two_inactive_days_end_current_streak(self):
        stats = summarize(data([1, 2, 1, 0, 0]))
        self.assertEqual(stats["current"]["length"], 0)
        self.assertEqual(stats["longest"]["length"], 3)

    def test_today_counts_and_weeks_align_on_sunday_across_new_year(self):
        stats = summarize(data([1, 0, 2, 3, 4], "2025-12-31"))
        self.assertEqual(stats["current"]["length"], 3)
        self.assertEqual(stats["weekly"], [6, 4])
        self.assertEqual([week["start"] for week in stats["weeks"]], ["2025-12-28", "2026-01-04"])

    def test_empty_calendar_and_zero_calendar_do_not_invent_activity(self):
        for counts in ([], [0, 0, 0]):
            stats = summarize(data(counts))
            self.assertEqual(stats["total"], 0)
            self.assertEqual(stats["current"]["length"], 0)
            self.assertEqual(stats["longest"]["length"], 0)

    def test_tooltips_supply_exact_counts_including_singular_and_commas(self):
        parser = CalendarParser()
        parser.feed('''<td id="a" data-date="2026-01-01"></td>
            <tool-tip for="a">No contributions on January 1st.</tool-tip>
            <td id="b" data-date="2026-01-02"></td>
            <tool-tip for="b">1 contribution on January 2nd.</tool-tip>
            <td id="c" data-date="2026-01-03"></td>
            <tool-tip for="c">1,234 contributions on January 3rd.</tool-tip>''')
        self.assertEqual(parser.counts, {"a": 0, "b": 1, "c": 1234})

    @patch("profile_stats.request")
    def test_public_calendar_fetches_both_years_with_reused_cell_ids(self, request):
        request.side_effect = [
            '<td id="a" data-date="2025-12-31"></td><tool-tip for="a">3 contributions on December 31st.</tool-tip>',
            '<td id="a" data-date="2026-01-01"></td><tool-tip for="a">1 contribution on January 1st.</tool-tip>',
            '[]',
        ]
        days, _, _ = public_data("51-Shenn", date(2025, 12, 31), date(2026, 1, 1))
        self.assertEqual(days, [{"date": "2025-12-31", "count": 3}, {"date": "2026-01-01", "count": 1}])
        self.assertIn('from=2025-01-01', request.call_args_list[0].args[0])
        self.assertIn('from=2026-01-01', request.call_args_list[1].args[0])

    @patch("profile_stats.request")
    def test_graphql_paginates_repos_without_double_counting_days(self, request):
        def response(has_next, sizes):
            return json.dumps({"data": {"user": {
                "contributionsCollection": {"contributionCalendar": {"weeks": [
                    {"contributionDays": [{"date": "2026-01-01", "contributionCount": 4}]}]}},
                "repositories": {"pageInfo": {"hasNextPage": has_next, "endCursor": "next"},
                    "nodes": [{"languages": {"pageInfo": {"hasNextPage": False}, "edges": [
                        {"size": size, "node": {"name": name}} for name, size in sizes.items()]}}]}
            }}})
        request.side_effect = [response(True, {"Python": 80, "Java": 20}), response(False, {"Java": 100})]
        days, sizes, repos = authenticated_data("51-Shenn", "test-token", date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(days, [{"date": "2026-01-01", "count": 4}])
        self.assertEqual(sizes, {"Python": 80, "Java": 120})
        self.assertEqual(repos, {"Python": 1, "Java": 1})
        self.assertEqual(request.call_args_list[1].args[2]['variables']['cursor'], 'next')

    @patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
    @patch("profile_stats.public_data", return_value=([], {}, {}))
    def test_incomplete_calendar_fails_instead_of_publishing_false_zeroes(self, _fetch):
        with self.assertRaisesRegex(ValueError, "complete 365-day calendar"):
            fetch("51-Shenn")


if __name__ == "__main__":
    unittest.main()
