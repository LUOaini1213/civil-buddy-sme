"""Independent English request forms and their non-authorising look-alikes."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.runtime.task_router import route_task


class EnglishRequestForms(unittest.TestCase):
    def test_colloquial_drafting_commands_and_need_requests(self):
        for request, post in (
            ("Please do up a site diary for Block Z, 11 installers.", "pm-daily"),
            ("Do the toolbox briefing for the glazing crew.", "safety-brief"),
            ("Need a safety briefing about hand tools.", "safety-brief"),
            ("Help me go through this tender and extract its dates.", "bid-parse"),
            ("Site diary 2026-11-04: cloudy, 11 installers, crane stopped.", "pm-daily"),
            ("Toolbox meeting notes for 4 November: panel handling.", "safety-brief"),
            ("Prepare the MOS for gondola installation.", "construction"),
        ):
            with self.subTest(request=request):
                result = route_task(request)
                self.assertEqual("run", result["intent"])
                self.assertEqual([post], result["expert_ids"])

    def test_narration_advice_schedule_and_quotes_do_not_authorise_drafts(self):
        for request in (
            "Do the site diary fields include weather?",
            "Do the toolbox briefing records need signatures?",
            "Do we need a safety briefing for hand tools?",
            "We need a site diary from the subcontractor.",
            "I will do up a site diary myself later.",
            "I went through this tender yesterday.",
            "How do I go through this tender?",
            "Site diary 2026-11-04: was corrected yesterday.",
            "Site diary 2026-11-04:    was corrected yesterday.",
            "Site diary 2026-11-04:   ",
            "Toolbox meeting on Monday at 7am, workers must attend.",
            'Explain the phrase "do up a site diary".',
            "Please do not do the toolbox briefing.",
            "Need a safety briefing? No, just explain its fields.",
            "Explain how to do up a site diary, then tell me its purpose.",
            "Please do up a site diary, but do not write any files.",
            "Generate a site diary; read-only please.",
            "Write the site diary, do not run anything.",
        ):
            with self.subTest(request=request):
                self.assertEqual("chat", route_task(request)["intent"])

    def test_physical_loading_commands_choose_packing_not_context_tender(self):
        for request in (
            "Load glazing_batch.xlsx into 40HQ.",
            "Crate and load the panels in glazing_batch.xlsx.",
            "Plan the shipment of glazing_batch.xlsx in a 40HQ container.",
            "Work out a packing plan for glazing_batch.xlsx; I only have the panel list, not the ITT.",
            "How many 20GP containers are needed for glazing_panels.csv?",
        ):
            with self.subTest(request=request):
                result = route_task(request)
                self.assertEqual("run", result["intent"])
                self.assertEqual(["pack-ship"], result["expert_ids"])

    def test_loading_lookalikes_do_not_authorise_packing(self):
        for request in (
            "Load balancing is not working.",
            "How should I load glazing_batch.xlsx into 40HQ?",
            "Do not load glazing_batch.xlsx into 40HQ.",
            "I loaded glazing_batch.xlsx yesterday.",
            "Explain how many containers glazing_panels.csv might need; do not calculate anything.",
            "If I load glazing_batch.xlsx, what changes?",
            'The instruction reads "Load glazing_batch.xlsx into 40HQ"; explain it.',
            "Should I crate the panels in glazing_batch.xlsx?",
            "Remind me later to load glazing_batch.xlsx into 40HQ.",
            "How many containers are needed for glazing_panels.csv? Do not write any files.",
        ):
            with self.subTest(request=request):
                self.assertEqual("chat", route_task(request)["intent"])

    def test_named_tender_file_operations_choose_a_bid_post(self):
        for request, post in (
            ("List the logistics clauses in north_itt.md.", "bid-parse"),
            ("Review north_itt.md and give me the missing-form checklist.", "bid-compliance"),
            ("Extract the closing date from north_tender.pdf.", "bid-parse"),
        ):
            with self.subTest(request=request):
                result = route_task(request)
                self.assertEqual("run", result["intent"])
                self.assertEqual([post], result["expert_ids"])

    def test_closed_compliance_checks_respect_limits_are_not_generic_advice(self):
        for request in (
            "Does the plan for glazing_panels.csv respect the weight limits in north_itt.md?",
            "Will glazing_panels.csv fit the container limits in north_itt.md?",
            "Here are north_itt.md and glazing_panels.csv - does the plan respect the tender weight limits?",
        ):
            with self.subTest(request=request):
                result = route_task(request)
                self.assertEqual("run", result["intent"])
                self.assertEqual(["bid-parse"], result["expert_ids"])
        for request in (
            "Should I check whether glazing_panels.csv fits north_itt.md?",
            "Explain whether glazing_panels.csv respects the limits in north_itt.md.",
            "I checked glazing_panels.csv against north_itt.md yesterday.",
            "Does glazing_panels.csv respect north_itt.md? Do not write any files.",
            "Check glazing_panels.csv against north_itt.md; read-only please.",
        ):
            self.assertEqual("chat", route_task(request)["intent"])

    def test_explicit_post_selection_still_wins_and_does_not_add_a_team(self):
        for request in ("Load glazing_batch.xlsx into 40HQ.", "Review north_itt.md and extract its clauses."):
            result = route_task(request, ["pm-daily"])
            self.assertEqual(["pm-daily"], result["expert_ids"])
            self.assertEqual("", result["workflow"])

    def test_unknown_acronym_or_ordinary_filename_does_not_guess_a_post(self):
        for request in ("MOS is a transistor term.", "Prepare the MOS transistor table.", "Load the page.", "Review accounting.md.", "How many invoices are in ledger.xlsx?"):
            self.assertEqual([], route_task(request)["expert_ids"])

    def test_quoted_no_files_phrase_is_source_material_not_an_execution_veto(self):
        self.assertEqual("run", route_task('Write the site diary and quote the label "read-only" as source text.')["intent"])
        self.assertEqual("run", route_task("Generate a read-only access policy.")["intent"])
        self.assertEqual("run", route_task("Draft a site diary. No files were provided, leave missing fields blank.")["intent"])

    def test_named_chinese_count_request_is_preserved_without_the_old_filename_bypass(self):
        for request in ("glazing_panels.csv 要几个柜", "请算一下柜数 glazing_panels.csv"):
            result = route_task(request)
            self.assertEqual("run", result["intent"])
            self.assertEqual(["pack-ship"], result["expert_ids"])
        for request in ("解释 glazing_panels.csv 要几个柜的计算方法", "怎么计算 glazing_panels.csv 要几个柜？"):
            self.assertEqual("chat", route_task(request)["intent"])

    def test_convenience_prefix_authorises_a_current_imperative_not_a_schedule(self):
        for request, post in (
            ("When you get a chance, check glazing_panels.xlsx against north_itt.md.", "bid-parse"),
            ("When you have a moment, please draft a site diary for Block Z.", "pm-daily"),
            ("At your convenience, could you prepare the lifting plan for panel installation?", "construction"),
            ("Please, when you have a chance, extract the logistics clauses from north_itt.md.", "bid-parse"),
        ):
            with self.subTest(request=request):
                result = route_task(request)
                self.assertEqual("run", result["intent"])
                self.assertEqual([post], result["expert_ids"])
        for request in (
            "When you get a chance, should I check glazing_panels.xlsx against north_itt.md?",
            "When you get a chance, explain how to check glazing_panels.xlsx against north_itt.md.",
            "When you get a chance tomorrow, check glazing_panels.xlsx against north_itt.md.",
            "Remind me when you get a chance to check glazing_panels.xlsx against north_itt.md.",
            "When you get a chance, remind me to check glazing_panels.xlsx against north_itt.md.",
            "When you get a chance, check glazing_panels.xlsx against north_itt.md; no files.",
            "When you get a chance, do not check glazing_panels.xlsx against north_itt.md.",
            'Explain the request "When you get a chance, check glazing_panels.xlsx against north_itt.md".',
            "When will you get a chance to check glazing_panels.xlsx against north_itt.md?",
            "The PM said that when you get a chance, check glazing_panels.xlsx against north_itt.md.",
            "My colleague asked me to check glazing_panels.xlsx against north_itt.md.",
            "The manager wrote: Please prepare the lifting plan for panel installation.",
            "The PM said to draft a site diary; then check the tender.",
        ):
            with self.subTest(request=request):
                self.assertEqual("chat", route_task(request)["intent"])
        current = route_task("The PM said to draft a site diary yesterday. Now draft a site diary for Block Z.")
        self.assertEqual("run", current["intent"])
        self.assertEqual(["pm-daily"], current["expert_ids"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
