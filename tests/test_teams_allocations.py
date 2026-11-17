"""
Unit tests for Teams allocation extraction and matching. Run with: python -m unittest discover tests
"""

import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from services.allocation_extraction import (
    OpenAIAllocationExtractor, RawAllocation, build_user_prompt, parse_extraction, TARGET_BATCH_SIZE,
)
from services.allocation_matching import NameMatcher, resolve_allocations, person_day_totals, working_days_for
from services.teams_chat_service import ChatMessage, html_to_text

# 12-11-2026 is a Thursday; 10-11 and 11-11 are company holidays (Diwali Padawa, Bhaubhej)
THU, FRI, MON = date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)
NO_FLOATER = lambda d, uid: False

PEOPLE = {1: "Mayur Malode", 2: "Rushikesh Sonawane", 3: "Poonam Sulakhe", 4: "Mohd Taher Sayyad",
          5: "saurabh chaugule", 6: "Saurabh  More"}
PROJECTS = {10: "Protecta", 11: "Minimalist", 12: "LST NetSuite Data Archival Solution",
            13: "Order Pilot - Sales Order", 14: "Leave", 15: "PointsBet"}


def msg(mid, sent, text="", sender="Rutuja Lohokare"):
    return ChatMessage(id=mid, sent_at=sent, sender=sender, text=text)


def raw(mid, person, project, start, end=None, hours=8.0, replaces=False, confidence="high",
        person_text="", project_text=None):
    return RawAllocation(mid, person, project, start, end or start, hours, replaces, confidence, "evidence",
                         person_text=person_text, project_text=project_text)


def resolve(rows, messages, aliases=None):
    return resolve_allocations(rows, messages, NameMatcher(PEOPLE, aliases), NameMatcher(PROJECTS),
                               floater=NO_FLOATER)


M1 = msg("1", datetime(2026, 11, 12, 9, 0))
M2 = msg("2", datetime(2026, 11, 12, 12, 0))


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.people = NameMatcher(PEOPLE, {"Rushi": "Rushikesh Sonawane"})
        self.projects = NameMatcher(PROJECTS)

    def test_exact_ignores_case_and_spacing(self):
        m = self.people.match("SAURABH more")
        self.assertEqual((m.id, m.method), (6, "exact"))

    def test_alias(self):
        self.assertEqual(self.people.match("rushi").id, 2)

    def test_unique_first_name(self):
        m = self.people.match("Mayur")
        self.assertEqual((m.id, m.method), (1, "partial"))

    def test_shared_first_name_is_ambiguous(self):
        m = self.people.match("Saurabh")
        self.assertIsNone(m.id)
        self.assertEqual(m.method, "ambiguous")
        self.assertEqual(len(m.candidates), 2)

    def test_partial_project_name(self):
        self.assertEqual(self.projects.match("order pilot").id, 13)
        self.assertEqual(self.projects.match("archival").id, 12)

    def test_misspelling(self):
        m = self.projects.match("Protekta")
        self.assertEqual((m.id, m.method), (10, "fuzzy"))

    def test_name_written_without_spaces(self):
        m = self.projects.match("OrderPilot")
        self.assertEqual((m.id, m.method), (13, "partial"))

    def test_spaced_and_joined_names_are_pooled(self):
        matcher = NameMatcher({1: "Order Pilot - Sales Order", 2: "OrderPilot - Accora Enhancement", 3: "Order pilot-Quote"})
        m = matcher.match("OrderPilot")
        self.assertEqual((m.method, len(m.candidates)), ("ambiguous", 3))

    def test_unknown_name(self):
        self.assertIsNone(self.people.match("Amit").id)

    def test_alias_ignores_spacing(self):
        matcher = NameMatcher({1: "LST Activities", 2: "Protecta"}, {"SOC 2 Documentation": "LST Activities"})
        self.assertEqual(matcher.match("SOC2 documentation").id, 1)

    def test_preferred_record_wins_when_several_fit(self):
        matcher = NameMatcher({1: "OBC Associates", 2: "OBC Old"}, preferred={1})
        self.assertEqual(matcher.match("OBC").id, 1)
        self.assertEqual(NameMatcher({1: "OBC Associates"}, preferred={2}).match("OBC").id, 1)  # only inactive fits

    def test_alias_beats_ai_choice(self):
        r = resolve_allocations([raw("1", "Mayur", "Minimalist", THU, project_text="archival")], [M1],
                                NameMatcher(PEOPLE), NameMatcher(PROJECTS, {"Archival": "LST NetSuite Data Archival Solution"}),
                                floater=NO_FLOATER)
        self.assertEqual(r.planned[0].project_id, 12)

    def test_alias_to_unknown_record_is_ignored(self):
        matcher = NameMatcher(PEOPLE, {"Ghost": "Nobody Here"})
        self.assertIsNone(matcher.match("Ghost").id)


class ResolveTests(unittest.TestCase):
    def test_single_day(self):
        r = resolve([raw("1", "Mayur Malode", "Protecta", THU)], [M1])
        self.assertEqual([(p.user_id, p.project_id, p.date, p.seconds) for p in r.planned], [(1, 10, THU, 8 * 3600)])
        self.assertEqual(r.review, [])

    def test_range_skips_weekend(self):
        r = resolve([raw("1", "Mayur Malode", "Protecta", THU, MON)], [M1])
        self.assertEqual([p.date for p in r.planned], [THU, FRI, MON])

    def test_range_skips_company_holiday(self):
        self.assertEqual(working_days_for(date(2026, 11, 9), THU, 1, NO_FLOATER), [date(2026, 11, 9), THU])

    def test_range_skips_floater(self):
        days = working_days_for(THU, FRI, 1, floater=lambda d, uid: d == FRI and uid == 1)
        self.assertEqual(days, [THU])

    def test_holiday_only_goes_to_review(self):
        r = resolve([raw("1", "Mayur Malode", "Protecta", date(2026, 11, 10))], [M1])
        self.assertEqual(r.planned, [])
        self.assertIn("No working days", r.review[0].reason)

    def test_unknown_person_goes_to_review(self):
        r = resolve([raw("1", "Amit", "Protecta", THU)], [M1])
        self.assertEqual(r.planned, [])
        self.assertIn("Person 'Amit' not found", r.review[0].reason)

    def test_ambiguous_person_goes_to_review(self):
        r = resolve([raw("1", "Saurabh", "Protecta", THU)], [M1])
        self.assertIn("could be", r.review[0].reason)

    def test_unknown_project_goes_to_review(self):
        r = resolve([raw("1", "Mayur", "Zephyr", THU)], [M1])
        self.assertIn("Project 'Zephyr' not found", r.review[0].reason)

    def test_long_range_goes_to_review(self):
        r = resolve([raw("1", "Mayur", "Protecta", THU, date(2027, 1, 30))], [M1])
        self.assertEqual(r.planned, [])
        self.assertIn("longer than", r.review[0].reason)

    def test_split_day_keeps_both_projects(self):
        r = resolve([raw("1", "Poonam", "Minimalist", THU, hours=4), raw("1", "Poonam", "archival", THU, hours=4)], [M1])
        self.assertEqual(sorted(p.project_id for p in r.planned), [11, 12])
        self.assertEqual(person_day_totals(r.planned)[(3, THU)], 8 * 3600)

    def test_change_replaces_earlier_plan(self):
        rows = [raw("1", "Poonam", "Minimalist", THU, hours=4), raw("1", "Poonam", "archival", THU, hours=4),
                raw("2", "Poonam", "Minimalist", THU, hours=8, replaces=True)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([(p.project_id, p.seconds) for p in r.planned], [(11, 8 * 3600)])

    def test_change_is_applied_in_sent_order_not_list_order(self):
        rows = [raw("2", "Poonam", "Minimalist", THU, hours=8, replaces=True), raw("1", "Poonam", "archival", THU, hours=4)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([p.project_id for p in r.planned], [11])

    def test_rows_of_one_message_for_same_project_add_up(self):
        rows = [raw("1", "Rushikesh", "Protecta", THU, hours=7),
                raw("1", "Rushikesh", "Protecta", THU, hours=1, confidence="medium")]
        r = resolve(rows, [M1])
        self.assertEqual([(p.project_id, p.seconds, p.confidence) for p in r.planned], [(10, 8 * 3600, "medium")])

    def test_tasks_of_one_project_add_up_and_stay_visible(self):
        rows = [RawAllocation("1", "Mayur", "Protecta", THU, THU, 0.5, False, "high", "e", task="Client Call"),
                RawAllocation("1", "Mayur", "Protecta", THU, THU, 1.0, False, "high", "e", task="Development")]
        r = resolve(rows, [M1])
        self.assertEqual([(p.seconds, p.task) for p in r.planned],
                         [(int(1.5 * 3600), "Client Call (0.5h); Development (1h)")])

    def test_newer_row_for_same_project_overrides_without_summing(self):
        rows = [raw("1", "Mayur", "Protecta", THU, hours=8), raw("2", "Mayur", "Protecta", THU, hours=6)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([p.seconds for p in r.planned], [6 * 3600])

    def test_addition_without_replace_keeps_earlier_project(self):
        rows = [raw("1", "Mayur", "Protecta", THU, hours=6), raw("2", "Mayur", "PointsBet", THU, hours=2)]
        r = resolve(rows, [M1, M2])
        self.assertEqual(sorted(p.project_id for p in r.planned), [10, 15])

    def test_cancel_one_project(self):
        rows = [raw("1", "Mayur", "Protecta", THU, hours=6), raw("1", "Mayur", "PointsBet", THU, hours=2),
                raw("2", "Mayur", "PointsBet", THU, hours=0, replaces=False)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([p.project_id for p in r.planned], [10])

    def test_cancel_one_project_marked_as_replacing_keeps_other_work(self):
        rows = [raw("1", "Poonam", "Minimalist", THU, hours=4), raw("1", "Poonam", "archival", THU, hours=4),
                raw("2", "Poonam", "archival", THU, hours=0, replaces=True)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([(p.project_id, p.seconds) for p in r.planned], [(11, 4 * 3600)])

    def test_only_x_with_unstated_hours_is_zero(self):
        rows = [raw("1", "Poonam", "Minimalist", THU, hours=4), raw("1", "Poonam", "archival", THU, hours=4),
                raw("2", "Poonam", "Minimalist", THU, hours=None, replaces=True),
                raw("2", "Poonam", "archival", THU, hours=0)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([(p.project_id, p.seconds, p.hours_stated) for p in r.planned], [(11, 0, False)])

    def test_cancel_whole_day(self):
        rows = [raw("1", "Mayur", "Protecta", THU, FRI), raw("2", "Mayur", None, FRI, hours=None, replaces=True)]
        r = resolve(rows, [M1, M2])
        self.assertEqual([p.date for p in r.planned], [THU])

    def test_unstated_hours_are_zero(self):
        rows = [raw("1", "Mohd Taher", "Order Pilot", THU, hours=2.5), raw("1", "Mohd Taher", "archival", THU, hours=None)]
        r = resolve(rows, [M1])
        by_project = {p.project_id: p for p in r.planned}
        self.assertEqual((by_project[13].seconds, by_project[12].seconds), (int(2.5 * 3600), 0))
        self.assertFalse(by_project[12].hours_stated)
        self.assertIn("hours not stated", by_project[12].match_note)

    def test_all_hours_unstated_are_zero(self):
        rows = [raw("1", "Mayur", "Protecta", THU, hours=None), raw("1", "Mayur", "PointsBet", THU, hours=None)]
        r = resolve(rows, [M1])
        self.assertEqual([p.seconds for p in r.planned], [0, 0])

    def test_unstated_hours_on_full_day_are_kept_as_zero(self):
        rows = [raw("1", "Mayur", "Protecta", THU, hours=8), raw("1", "Mayur", "PointsBet", THU, hours=None)]
        r = resolve(rows, [M1])
        self.assertEqual(sorted((p.project_id, p.seconds) for p in r.planned), [(10, 8 * 3600), (15, 0)])
        self.assertEqual(r.review, [])

    def test_leave_is_a_project(self):
        r = resolve([raw("1", "Mohd Taher", "Leave", FRI)], [M1])
        self.assertEqual([p.project_name for p in r.planned], ["Leave"])

    def test_unique_first_name_needs_no_note(self):
        r = resolve([raw("1", "Mayur", "Protecta", THU)], [M1])
        self.assertEqual(r.planned[0].match_note, "")

    def test_close_spelling_noted(self):
        r = resolve([raw("1", "Mayur", "Protekta", THU)], [M1])
        self.assertIn("'Protekta' read as Protecta (close spelling)", r.planned[0].match_note)

    def test_ai_pick_between_two_people_goes_to_review(self):
        r = resolve([raw("1", "Saurabh  More", "Order Pilot", FRI, person_text="Saurabh")], [M1])
        self.assertEqual(r.planned, [])
        self.assertIn("'Saurabh' could be", r.review[0].reason)

    def test_ai_pick_for_nickname_is_accepted_and_noted(self):
        r = resolve([raw("1", "Rushikesh Sonawane", "Protecta", THU, person_text="Rushi bhau")], [M1])
        self.assertEqual(r.planned[0].user_id, 2)
        self.assertIn("'Rushi bhau' read as Rushikesh Sonawane by AI", r.planned[0].match_note)
        self.assertEqual(r.planned[0].confidence, "medium")   # AI-only pick is never shown as high

    def test_ai_pick_conflicting_with_written_name_goes_to_review(self):
        r = resolve([raw("1", "Mayur Malode", "Protecta", THU, person_text="Poonam")], [M1])
        self.assertIn("read by AI as Mayur Malode, but matches Poonam Sulakhe", r.review[0].reason)

    def test_written_name_used_when_ai_name_unknown(self):
        r = resolve([raw("1", "Mayur M.", "Protecta", THU, person_text="Mayur")], [M1])
        self.assertEqual(r.planned[0].user_id, 1)

    def test_project_written_text_agreeing_with_ai(self):
        r = resolve([raw("1", "Mayur", "Order Pilot - Sales Order", THU, project_text="pilot")], [M1])
        self.assertEqual(r.planned[0].project_id, 13)

    def test_project_written_text_conflicting_with_ai(self):
        r = resolve([raw("1", "Mayur", "Order Pilot - Sales Order", THU, project_text="archival")], [M1])
        self.assertEqual(r.planned, [])
        self.assertIn("Project 'archival' was read by AI as Order Pilot - Sales Order", r.review[0].reason)

    def test_unknown_person_reported_once_for_a_range(self):
        r = resolve([raw("1", "Neha Unknown", "Protecta", THU, FRI)], [M1])
        self.assertEqual(len(r.review), 1)


class ExtractionTests(unittest.TestCase):
    def test_parse_maps_refs_and_validates(self):
        data = {"allocations": [
            {"message_ref": "m1", "person_text": "mayur", "person": "Mayur", "project_text": "protecta",
             "project": "Protecta", "start_date": "2026-11-12",
             "end_date": "2026-11-12", "hours_per_day": 8, "replaces_earlier": False, "confidence": "high", "evidence": "x"},
            {"message_ref": "m9", "person": "Ghost", "project": "X", "start_date": "2026-11-12",
             "end_date": "2026-11-12", "hours_per_day": 8, "replaces_earlier": False, "confidence": "high", "evidence": ""},
            {"message_ref": "m1", "person": "Poonam", "project": "X", "start_date": "12/11/2026",
             "end_date": "2026-11-12", "hours_per_day": 8, "replaces_earlier": False, "confidence": "high", "evidence": ""},
            {"message_ref": "m1", "person": "Poonam", "project": "X", "start_date": "2026-11-12",
             "end_date": "2026-11-12", "hours_per_day": 30, "replaces_earlier": False, "confidence": "high", "evidence": ""},
            {"message_ref": "m1", "person": "Rushi", "project": None, "start_date": "2026-11-16",
             "end_date": "2026-11-13", "hours_per_day": None, "replaces_earlier": True, "confidence": "odd", "evidence": ""},
        ]}
        rows = parse_extraction(data, {"m1": "graph-id-1"})
        self.assertEqual([r.person for r in rows], ["Mayur", "Rushi"])
        self.assertEqual(rows[0].message_id, "graph-id-1")
        self.assertEqual((rows[0].person_text, rows[0].project_text), ("mayur", "protecta"))
        self.assertEqual(rows[1].person_text, "Rushi")   # falls back to person when missing
        self.assertEqual((rows[1].start_date, rows[1].end_date), (FRI, MON))   # swapped
        self.assertIsNone(rows[1].project)
        self.assertEqual(rows[1].confidence, "low")

    def test_prompt_labels_targets_and_context(self):
        prompt = build_user_prompt([M2], [M1], ["Mayur Malode"], ["Protecta"])
        self.assertIn("[c1] Thu 2026-11-12 09:00", prompt)
        self.assertIn("[m1] Thu 2026-11-12 12:00", prompt)
        self.assertLess(prompt.index("CONTEXT"), prompt.index("TARGET"))

    def test_extractor_batches_and_uses_strict_schema(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            content = json.dumps({"allocations": [
                {"message_ref": "m1", "person": "Mayur", "project": "Protecta", "start_date": "2026-11-12",
                 "end_date": "2026-11-12", "hours_per_day": 8, "replaces_earlier": False,
                 "confidence": "high", "evidence": "Mayur Protecta"}]})
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=content, refusal=None))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        messages = [msg(str(i), datetime(2026, 11, 12, 9, 0)) for i in range(TARGET_BATCH_SIZE + 1)]
        rows = OpenAIAllocationExtractor(api_key="", model="test-model", client=client).extract(messages, ["Mayur Malode"], ["Protecta"])

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(calls[0]["model"], "test-model")
        self.assertEqual([r.message_id for r in rows], ["0", str(TARGET_BATCH_SIZE)])
        self.assertIn("CONTEXT", calls[1]["messages"][1]["content"])

    def test_refusal_raises(self):
        def create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=None, refusal="no"))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(RuntimeError):
            OpenAIAllocationExtractor(api_key="", model="m", client=client).extract([M1], [], [])


class SenderTests(unittest.TestCase):
    def test_company_suffix_removed(self):
        from services.teams_chat_service import clean_sender
        self.assertEqual(clean_sender("Prakash Bagul | LST Consultancy"), "Prakash Bagul")
        self.assertEqual(clean_sender(""), "Unknown")


class HtmlTests(unittest.TestCase):
    def test_teams_html_to_text(self):
        content = '<p>Today:</p><p><at id="0">Mayur Malode</at> &amp; Rushi - Protecta&nbsp;8h<br>Poonam - 4h</p>'
        self.assertEqual(html_to_text(content), "Today:\nMayur Malode & Rushi - Protecta 8h\nPoonam - 4h")


if __name__ == "__main__":
    unittest.main()
