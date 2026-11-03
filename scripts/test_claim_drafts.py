#!/usr/bin/env python3
"""Claim-event ownership, literal contract clocks and unsigned real exports."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from packing_assistant import expert_turn
from packing_assistant.office_job import tables_from_md
from packing_assistant.post_drafts import build_draft as dedicated
from packing_assistant.post_drafts.claim import MISSING, TOOL, _owned, build_draft, parse
from packing_assistant.runtime.scheduler import Scheduler


def draft(text):
    return build_draft("claim", TOOL, text)


def table(md, prefix):
    return next(rows for title, rows in tables_from_md(md) if title.startswith(prefix))


class Claim(unittest.TestCase):
    def test_exclusive_ten_sections_no_legal_clock_country_or_payment_default(self):
        md = draft("")
        self.assertEqual(md, dedicated("claim", TOOL, ""))
        self.assertIsNone(build_draft("variation", TOOL, ""))
        self.assertIsNone(build_draft("claim", "variation__visa", ""))
        self.assertEqual(10, len([s for s in md.splitlines() if s.startswith("## ")]))
        for text in ("AI 草稿", "submit_blocked=true", "TBD", "未签认", "（空）", "variation", "interim"):
            self.assertIn(text, md)
        for text in ("## 用户原文", "28", "60", "PSSCOC", "GF-2017", "GB 50500", "SG：", "CN：", "已获批准", "应予支持"):
            self.assertNotIn(text, md)

    def test_private_kb_has_no_default_singapore_jurisdiction(self):
        text = (ROOT / "demo/kb/commercial/claim/web-knowledge.md").read_text(encoding="utf-8")
        self.assertNotIn("未给则新加坡工地默认 SG", text)
        self.assertIn("未提供则 UNSPECIFIED", text)

    def test_missing_contract_wording_has_explicit_gap_but_supplied_wording_is_not_called_missing(self):
        self.assertIn("条款原文待贴", draft("生成索赔意向草稿"))
        self.assertNotIn("条款原文待贴", draft("合同原文：用户约定通知文字。"))

    def test_short_command_inline_evidence_reaches_its_field(self):
        data = parse("写一份索赔意向 图纸迟到 证据：监理通知 NCR-1；停工令 8 月 1 日")
        self.assertEqual("图纸迟到", data.events[0]["event"])
        self.assertEqual("监理通知 NCR-1", data.evidence[0]["evidence"])
        self.assertEqual(MISSING, data.documents[0]["document_id"])
        self.assertIn("8 月 1 日", data.documents[0]["document_record"])

    def test_dates_and_quantities_are_not_document_ids(self):
        for value in ("2026-09-01", "8 月 1 日", "35吨", "12人", "3台"):
            with self.subTest(value=value):
                data = parse("函号："+value)
                self.assertEqual(MISSING, data.documents[0]["document_id"])

    def test_development_facts_are_in_semantic_fields_not_input_appendix(self):
        from scripts.eval_post_content import classify, load, spots, split_echo
        for case in load("cases"):
            if case["post"] != "claim":
                continue
            with self.subTest(case=case["id"]):
                md = draft(case["request"])
                body, echo = split_echo(md)
                for fact in case["facts"]:
                    self.assertEqual("placed", classify(fact, body, echo, spots(body)), fact)
                self.assertTrue(all(t not in md for t in case.get("forbidden", [])))

    def test_supply_delay_keeps_agreed_arrival_actual_arrival_people_machine_and_receipt_separate(self):
        text = ("帮我出一份索赔意向草稿，甲供HRB400钢筋合同约定2026-08-25到场，实际拖到2026-09-06才进场，"
                "5#楼地下室底板钢筋绑扎一直停着，钢筋班组38人窝工，QTZ80塔吊闲置，我们发过催货函XM-HAN-031，监理周海波签收了")
        data = parse(text)
        self.assertEqual(1, len(data.events))
        row = data.events[0]
        self.assertEqual(("2026-08-25", "2026-09-06"), (row["agreed_date"], row["arrival"]))
        self.assertEqual("38人", data.resources[0]["labor_qty"])
        self.assertEqual("QTZ80", data.resources[1]["model"])
        self.assertNotIn("equipment_qty", data.resources[1])
        self.assertEqual(("催货函", "XM-HAN-031", "周海波", "监理"), tuple(data.documents[0][k] for k in ("document_kind", "document_id", "receiver", "receiver_role")))
        self.assertNotIn("user_duration", row)
        self.assertNotIn("user_amount", row)

    def test_two_narrative_events_do_not_share_letter_receiver_resource_or_owner(self):
        from scripts.eval_post_content import load
        text = next(c["request"] for c in load("cases") if c["id"] == "claim-03")
        data = parse(text)
        self.assertEqual(2, len(data.events))
        self.assertEqual(("31人", "14个人"), (data.resources[0]["labor_qty"], data.resources[1]["labor_qty"]))
        docs = _owned(data, data.documents)
        self.assertEqual("何志强", docs[0]["receiver"])
        self.assertEqual("ZJ-HAN-022", docs[0]["document_id"])
        self.assertNotIn("document_id", docs[1])
        self.assertNotIn("receiver", docs[1])
        self.assertIn("片区停电", docs[1]["event"])
        self.assertIn("片区停电", _owned(data, data.actions)[0]["event"])
        self.assertEqual("孙倩", data.actions[0]["owner"])
        self.assertNotIn("draw_date", data.events[1])

    def test_contract_original_preserves_commas_conditions_semicolons_and_punctuation(self):
        original = "如果事件持续，按每月提供记录；通知由指定代表接收。"
        data = parse("合同编号：C-A\n合同原文："+original+"\n合同原文来源：用户合同.pdf第7页\n事件：停电")
        self.assertEqual(original, data.contexts[0]["contract_original"])
        self.assertEqual(original, data.events[0]["contract_original"])
        self.assertEqual([], data.documents)
        self.assertNotIn("60", draft("合同原文："+original))

    def test_contract_wording_equal_to_label_prefix_is_not_lost_by_protection(self):
        self.assertEqual("合同", parse("合同原文：合同").contexts[0]["contract_original"])

    def test_cost_and_time_notice_clauses_stay_separate_and_no_last_date_is_computed(self):
        data = parse("合同原文：用户费用条款7.2规定60日。\n工期条款原文：用户工期条款4.1规定42日。\n事件：用户停电\n发生日期：2026-09-01")
        self.assertIn("60日", data.contexts[0]["contract_original"])
        self.assertIn("42日", data.contexts[0]["eot_original"])
        md = draft("通知要求原文：自知悉起12个工作日通知。\n事件：停电\n发生日期：2026-09-01")
        self.assertIn("自知悉起12个工作日通知。", md)
        self.assertIn("UNSPECIFIED · 未计算", md)
        self.assertNotIn("2026-09-13", md)

    def test_event_changes_clear_sources_notices_resource_counts_and_actions(self):
        data = parse("事件：停电\n发生日期：2026-09-01\n日期来源：D-A\n已发函：N-A\n签收人：张三\n窝工班组：甲班\n人数：12人\n同期记录来源：R-A\n补证负责人：李四\n事件：晚供\n发生日期：2026-09-03\n机械型号：PC220")
        self.assertEqual(2, len(data.events))
        self.assertNotIn("date_source", data.events[1])
        self.assertEqual([0], [r["_record"] for r in data.documents])
        self.assertEqual([0], [r["_record"] for r in data.actions])
        self.assertNotIn("resource_source", data.resources[-1])
        self.assertNotIn("labor_qty", data.resources[-1])

    def test_changed_workface_keeps_event_identity_not_previous_dates_documents_or_resources(self):
        data = parse("事件：停电\n部位：A区\n发生日期：2026-09-01\n日期来源：D-A\n已发函：N-A\n人数：12人\n部位：B区\n人数：8人")
        self.assertEqual(("停电", "B区"), (data.events[1]["event"], data.events[1]["location"]))
        self.assertNotIn("occurred", data.events[1])
        self.assertNotIn("date_source", data.events[1])
        self.assertEqual([0], [r["_record"] for r in data.documents])

    def test_contract_changes_drop_old_wording_sources_and_event_identity(self):
        data = parse("项目：A\n辖区：SG\n合同编号：C-A\n合同原文：用户约定10日。\n合同原文来源：CA.pdf\n事件：停电\n已发函：N-A\n合同编号：C-B\n事件：晚图")
        row = data.events[-1]
        self.assertEqual(("A", "SG", "C-B", "晚图"), tuple(row[k] for k in ("project", "jurisdiction", "contract", "event")))
        self.assertNotIn("contract_original", row)
        self.assertNotIn("contract_source", row)

    def test_project_and_jurisdiction_changes_do_not_keep_old_contract(self):
        for label in ("项目：B", "辖区：CN"):
            with self.subTest(change=label):
                data = parse("项目：A\n辖区：SG\n合同编号：C-A\n事件：停电\n"+label+"\n事件：晚图")
                self.assertNotIn("contract", data.events[-1])
                self.assertEqual("晚图", data.events[-1]["event"])

    def test_changed_contract_extract_does_not_reuse_previous_extract_source(self):
        data = parse("合同原文：用户文字A\n合同原文来源：CA.pdf\n合同原文：用户文字B\n事件：停电")
        self.assertEqual("用户文字B", data.events[-1]["contract_original"])
        self.assertNotIn("contract_source", data.events[-1])

    def test_file_boundaries_and_repeated_same_filename_do_not_join_events_or_contracts(self):
        data = parse("【用户上传：a.pdf】\n合同编号：C-A\n事件：停电\n已发函：N-A\n签收人：张三\n【用户上传：a.pdf】\n事件：晚供\n签收人：李四")
        self.assertEqual(2, len(data.events))
        self.assertNotEqual(data.events[0]["_file"], data.events[1]["_file"])
        self.assertNotIn("contract", data.events[1])
        self.assertNotIn("document_id", data.documents[-1])

    def test_blank_table_identity_is_not_last_event_and_following_text_is_unassigned(self):
        text = ("| 事件 | 部位 | 停工开始 | 日期来源 | 已发函 | 签收人 |\n| --- | --- | --- | --- | --- | --- |\n"
                "| 停电 | A区 | 2026-09-01 | D-A | N-A | 张三 |\n| | | 2026-09-03 | | N-B | |\n\n人数：8人")
        data = parse(text)
        self.assertEqual(MISSING, data.events[1]["event"])
        self.assertEqual(MISSING, data.events[1]["date_source"])
        self.assertNotIn("event", data.events[-1])
        self.assertEqual(MISSING, data.documents[1].get("receiver", MISSING))

    def test_upload_request_and_generic_missing_instruction_do_not_create_event_records(self):
        data = parse("【用户上传：a.csv】\n事件：停电\n【本轮用户要求】\n根据所选附件生成索赔意向草稿，缺项待核。")
        self.assertEqual(1, len(data.events))
        self.assertEqual("停电", data.events[0]["event"])

    def test_table_explicit_blank_contract_and_jurisdiction_override_header_scope(self):
        data = parse("合同编号：C-A\n辖区：SG\n| 事件 | 合同编号 | 辖区 |\n| --- | --- | --- |\n| 停电 | | |")
        self.assertEqual(MISSING, data.events[0]["contract"])
        self.assertEqual("UNSPECIFIED", data.events[0]["jurisdiction"])

    def test_repeated_event_name_is_a_new_record_not_duplicate_event_proof(self):
        data = parse("事件：停电\n发生日期：2026-09-01\n日期来源：D-A\n事件：停电\n发生日期：2026-09-03")
        self.assertEqual(2, len(data.events))
        self.assertNotIn("date_source", data.events[1])

    def test_repeated_date_has_its_own_source_and_does_not_reuse_letter(self):
        data = parse("事件：停电\n发生日期：2026-09-01\n日期来源：D-A\n已发函：N-A\n发生日期：2026-09-03")
        self.assertEqual(["2026-09-01", "2026-09-03"], [r["occurred"] for r in data.events])
        self.assertNotIn("date_source", data.events[1])
        self.assertEqual([0], [r["_record"] for r in data.documents])

    def test_letter_number_alone_is_not_a_sent_letter_or_receipt(self):
        data = parse("事件：停电\n函号：N-A\n拟收件人：张三\n函件来源：draft.pdf")
        self.assertNotIn("issued_record", data.documents[0])
        self.assertNotIn("receiver", data.documents[0])
        self.assertNotIn("receipt_source", data.documents[0])

    def test_planned_or_reported_send_and_receipt_are_only_pending_records(self):
        for source in ("预计明天发函N-A", "拟由监理张三签收", "据说监理张三签收了", "函还没发"):
            with self.subTest(source=source):
                data = parse("事件：停电\n"+source)
                self.assertTrue(all("issued_record" not in r and "receiver" not in r for r in data.documents))
        data = parse("已发函：预计明天发N-A\n签收人：据说张三")
        self.assertNotIn("issued_record", data.documents[0])
        self.assertEqual(MISSING, data.documents[-1]["receiver"])

    def test_changed_and_redeclared_letter_do_not_borrow_receipt_or_issued_source(self):
        for code in ("N-B", "N-A"):
            data = parse("事件：停电\n函号：N-A\n发出凭证：S-A\n签收人：张三\n送达回证：R-A\n函号："+code)
            self.assertEqual(2, len(data.documents))
            self.assertNotIn("receiver", data.documents[-1])
            self.assertNotIn("receipt_source", data.documents[-1])
            self.assertNotIn("issue_source", data.documents[-1])

    def test_denied_sending_and_only_draft_are_not_issued_records(self):
        for source in ("已发函：不曾发过N-A", "已发函：N-A（草稿）", "我们不曾发过函N-A", "已发函：N-A（待寄）"):
            with self.subTest(source=source):
                data = parse("事件：停电\n"+source)
                self.assertTrue(data.documents)
                self.assertTrue(all("issued_record" not in r for r in data.documents))
                self.assertTrue(any("document_record" in r for r in data.documents))

    def test_refused_or_denied_signature_does_not_become_a_receiver_name(self):
        for source in ("监理张三拒绝签收", "业主否认张三签收了"):
            with self.subTest(source=source):
                data = parse("事件：停电\n函号：N-A\n"+source)
                self.assertTrue(all("receiver" not in r for r in data.documents))
                self.assertTrue(any(source in r.get("receipt_record", "") for r in data.documents))

    def test_two_letter_ids_do_not_split_one_receiver_into_two_confirmations(self):
        data = parse("事件：停电\n已发函：N-A、N-B，业主代表张三签收\n签收凭证：R-A")
        named = [r for r in data.documents if r.get("document_id") in {"N-A", "N-B"}]
        self.assertEqual(2, len(named))
        self.assertTrue(all("receiver" not in r and "receipt_source" not in r for r in named))
        self.assertTrue(any(r.get("receiver") == "张三" and "document_id" not in r for r in data.documents))

    def test_resource_name_or_model_changes_do_not_carry_quantity_or_source(self):
        data = parse("事件：停电\n机械型号：PC220\n机械数量：3台\n同期记录来源：R-A\n机械型号：PC300")
        self.assertEqual(2, len(data.resources))
        self.assertNotIn("equipment_qty", data.resources[1])
        self.assertNotIn("resource_source", data.resources[1])
        data = parse("班组：甲班\n人数：12人\n同期记录来源：L-A\n班组：乙班")
        self.assertNotIn("labor_qty", data.resources[1])

    def test_person_and_machine_quantities_are_distinct_in_one_table_row(self):
        data = parse("| 事件 | 班组 | 人数 | 机械型号 | 机械数量 | 同期记录来源 |\n| --- | --- | --- | --- | --- | --- |\n| 停电 | 甲班 | 12人 | PC220 | 3台 | R-A |")
        self.assertEqual(3, len(data.resources))
        self.assertEqual("R-A", data.resources[0]["resource_source"])
        self.assertNotIn("labor", data.resources[0])
        self.assertEqual("12人", data.resources[1]["labor_qty"])
        self.assertNotIn("model", data.resources[1])
        self.assertEqual("3台", data.resources[2]["equipment_qty"])
        self.assertNotIn("resource_source", data.resources[2])

    def test_repeated_count_does_not_reuse_old_source(self):
        data = parse("事件：停电\n班组：甲班\n人数：12人\n同期记录来源：R-A\n人数：8人")
        self.assertEqual(2, len(data.resources))
        self.assertEqual("甲班", data.resources[1]["labor"])
        self.assertNotIn("resource_source", data.resources[1])

    def test_blank_other_resource_columns_do_not_unassign_only_known_crews_source(self):
        for row in ("| 甲班 | 12人 | | | R-A |", "| 甲班 | 12人 | | QTZ80 | R-A |"):
            # QTZ80 in a quantity cell is invalid, not a second machine.
            data = parse("| 班组 | 人数 | 机械型号 | 机械数量 | 同期记录来源 |\n| --- | --- | --- | --- | --- |\n"+row)
            self.assertEqual(1, len(data.resources))
            self.assertEqual(("甲班", "12人", "R-A"), tuple(data.resources[0][k] for k in ("labor", "labor_qty", "resource_source")))
            self.assertNotIn("model", data.resources[0])

    def test_blank_other_resource_columns_do_not_unassign_only_known_machines_source(self):
        data = parse("| 机械型号 | 机械数量 | 班组 | 人数 | 同期记录来源 |\n| --- | --- | --- | --- | --- |\n| PC220 | 3台 | | | R-A |")
        self.assertEqual(1, len(data.resources))
        self.assertEqual(("PC220", "3台", "R-A"), tuple(data.resources[0][k] for k in ("model", "equipment_qty", "resource_source")))
        self.assertNotIn("labor_qty", data.resources[0])

    def test_forecast_resource_counts_are_not_actual_idle_counts(self):
        for source in ("预计木工班组38人窝工", "据说PC220挖机3台闲置"):
            with self.subTest(source=source):
                data = parse("事件：停电\n"+source)
                self.assertTrue(data.resources)
                self.assertTrue(all(r.get("labor_qty", MISSING) == MISSING and r.get("equipment_qty", MISSING) == MISSING for r in data.resources))

    def test_models_dates_codes_and_floors_are_not_people_money_or_days(self):
        for value in ("QTZ80", "PC220", "N-2026-014", "2026-09-01", "5#楼", "35吨"):
            data = parse("人数："+value+"\n机械数量："+value+"\n用户主张金额："+value+"\n用户主张工期："+value)
            self.assertTrue(all(r.get("labor_qty", MISSING) == MISSING and r.get("equipment_qty", MISSING) == MISSING for r in data.resources))
            self.assertEqual(MISSING, data.events[0]["user_amount"])
            self.assertEqual(MISSING, data.events[0]["user_duration"])

    def test_user_requested_money_days_are_not_approved_or_calculated(self):
        data = parse("事件：停电\n用户主张金额：SGD12500\n金额来源：M-A\n用户主张工期：7天\n工期来源：T-A\n关键线路资料：network.pdf\n用户关键线路记录：用户声称被占")
        self.assertEqual(("SGD12500", "7天"), (data.events[0]["user_amount"], data.events[0]["user_duration"]))
        md = draft("用户主张金额：SGD12500\n用户主张工期：7天")
        self.assertIn("金额 TBD / 工期天数 TBD · 未核定", md)

    def test_restoration_unknown_keeps_relative_event_date_and_no_invented_end(self):
        data = parse("另一个是上月底片区停电\n恢复日期：哪天恢复我还得查")
        self.assertEqual("上月底", data.events[0]["occurred"])
        self.assertEqual(MISSING, data.events[0]["end"])
        self.assertNotIn("2026-09-30", draft("另一个是上月底片区停电"))

    def test_evidence_name_is_not_verified_content_or_original_page(self):
        data = parse("事件：停电\n证据：会议纪要.pdf\n证据位置：用户说第9页\n证据来源：archive-A")
        self.assertEqual("用户说第9页", data.evidence[0]["evidence_position"])
        self.assertIn("非原件页码", data.provenance[-1]["input_position"])

    def test_evidence_matrix_carries_own_event_date_contract_and_explicit_document_owner(self):
        source = "合同原文：用户条款A\n事件：停电\n日期来源：D-A\n证据：photo-A\n证据位置：用户目录第3项\n该证据补证责任：张三\n证据：photo-B\n补证负责人：李四"
        rows = table(draft(source), "5 证据")
        self.assertEqual(["D-A", "D-A"], [r[rows[0].index("用户事件日期来源")] for r in rows[1:]])
        self.assertEqual(["用户条款A", "用户条款A"], [r[rows[0].index("用户合同要求原文（未核）")] for r in rows[1:]])
        self.assertEqual(["张三", MISSING], [r[rows[0].index("该证据补证责任（明确归属才登记）")] for r in rows[1:]])
        self.assertEqual(MISSING, rows[2][rows[0].index("用户证据位置原文（未核）")])

    def test_evidence_for_different_events_does_not_borrow_date_clause_or_document_owner(self):
        data = parse("合同编号：C-A\n合同原文：用户条款A\n事件：停电\n日期来源：D-A\n证据：photo-A\n该证据补证责任：张三\n合同编号：C-B\n事件：晚供\n证据：photo-B")
        records = _owned(data, data.evidence)
        self.assertNotIn("date_source", records[1])
        self.assertNotIn("contract_original", records[1])
        self.assertNotIn("evidence_owner", records[1])

    def test_compensation_party_receiver_and_evidence_owner_are_not_interchangeable(self):
        data = parse("事件：停电\n主张方：甲承包人\n相对方：乙业主\n函号：N-A\n签收人：张三\n补证负责人：李四")
        self.assertEqual(("甲承包人", "乙业主"), (data.events[0]["claimant"], data.events[0]["respondent"]))
        self.assertEqual("张三", data.documents[0]["receiver"])
        self.assertEqual("李四", data.actions[0]["owner"])

    def test_generic_source_does_not_prove_date_contract_resource_or_receipt(self):
        data = parse("事件：停电\n来源：A.pdf\n发生日期：2026-09-01\n人数：12人\n函号：N-A\n签收人：张三")
        self.assertNotIn("date_source", data.events[0])
        self.assertNotIn("contract_source", data.events[0])
        self.assertNotIn("resource_source", data.resources[0])
        self.assertNotIn("receipt_source", data.documents[0])

    def test_late_event_name_does_not_retrofit_unassigned_resources_or_letters(self):
        data = parse("人数：12人\n函号：N-A\n事件：停电\n人数：8人")
        self.assertNotIn("event", _owned(data, data.documents)[0])
        self.assertNotIn("event", _owned(data, data.resources)[0])
        self.assertEqual("停电", data.events[-1]["event"])

    def test_late_contract_does_not_retrofit_existing_event_letter(self):
        data = parse("事件：停电\n函号：N-A\n合同编号：C-A\n事件：晚图")
        self.assertNotIn("contract", _owned(data, data.documents)[0])
        self.assertEqual("C-A", data.events[-1]["contract"])

    def test_examples_stay_masked_until_actual_record_reset(self):
        data = parse("例如事件：停电\n人数：12人\n合同原文：如果持续，7日更新。\n真实记录：事件：晚图\n人数：8人")
        self.assertEqual(1, len(data.events))
        self.assertEqual("晚图", data.events[0]["event"])
        self.assertEqual("8人", data.resources[0]["labor_qty"])
        self.assertNotIn("contract_original", data.contexts[0])

    def test_explicit_english_fields_keep_literal_roles(self):
        data = parse("Project: A\nJurisdiction: SG\nEvent: Power outage\nStart date: 2026-09-01\nLabor crew: Crew A\nLabor count: 12\nDocument ID: N-A\nReceiver: Jane Smith\nEvidence owner: John Lee")
        self.assertEqual("12", data.resources[0]["labor_qty"])
        self.assertEqual("Jane Smith", data.documents[0]["receiver"])
        self.assertEqual("John Lee", data.actions[0]["owner"])
        self.assertNotIn("receipt_source", data.documents[0])

    def test_adjustment_records_are_not_claim_or_budget_approval(self):
        data = parse("事件：用户地质变化\n调概事项：用户计划调整\n批准概算记录：用户声称A\n调概依据：用户办法B\n调概资料来源：budget.pdf")
        self.assertEqual("用户计划调整", data.adjustments[0]["adjustment"])
        self.assertIn("未执行 / 未批准", draft("调概事项：用户计划调整"))

    def test_physical_input_position_and_file_are_provenance_not_original_page(self):
        data = parse("【用户上传：a.pdf】\n事件：停电\n发生日期：2026-09-01")
        evidence = next(r for r in _owned(data, data.provenance) if r["field"] == "发生日期")
        self.assertEqual("停电", evidence["event"])
        self.assertEqual("a.pdf · 本轮输入第3行（非原件页码）", evidence["input_position"])

    def test_non_template_named_narrative_events_are_not_lost(self):
        data = parse("第一件是用户报告的仓储影响\n班组：甲班\n人数：12人\n另一个是用户报告的场地限制\n机械型号：PC220")
        self.assertEqual(["用户报告的仓储影响", "用户报告的场地限制"], [r["event"] for r in data.events])
        self.assertEqual([0, 1], [r["_record"] for r in data.resources])

    def test_negative_event_cause_is_literal_not_an_unknown_empty_identity(self):
        data = parse("事件：业主还没提供结构图导致停工\n人数：12人")
        self.assertEqual("业主还没提供结构图导致停工", data.events[0]["event"])
        self.assertIn("业主还没提供结构图", _owned(data, data.resources)[0]["event"])

    def test_reported_actual_arrival_does_not_become_observed_arrival(self):
        data = parse("事件：用户报告晚供\n据说实际拖到2026-09-06才进场")
        self.assertEqual(MISSING, data.events[0]["arrival"])
        self.assertIn("据说", data.events[0]["record"])

    def test_future_relative_receipt_or_arrival_is_not_actual_but_action_deadline_can_be_future(self):
        data = parse("事件：停电\n实际到场：明天\n签收日期：明天\n补证负责人：张三\n补证期限：明天")
        self.assertEqual(MISSING, data.events[0]["arrival"])
        self.assertEqual(MISSING, data.documents[0]["receipt_date"])
        self.assertEqual("明天", data.actions[0]["deadline"])

    def test_contract_provenance_stays_with_its_context_before_event_creation(self):
        data = parse("合同编号：C-A\n合同原文：用户文字A\n合同编号：C-B\n合同原文：用户文字B")
        evidence = [r for r in _owned(data, data.provenance) if r["field"] == "合同原文"]
        self.assertEqual([("C-A", "用户文字A"), ("C-B", "用户文字B")], [(r["contract"], r["value"]) for r in evidence])

    def test_original_contract_condition_is_not_parsed_as_a_receipt_or_resource(self):
        data = parse("合同原文：如果监理张三签收，仍需复核12人同期记录；不得据此认定已付10000元。\n事件：停电")
        self.assertEqual([], data.resources)
        self.assertEqual([], data.documents)
        self.assertNotIn("user_amount", data.events[0])


class ClaimRuntime(unittest.TestCase):
    def setUp(self):
        from packing_assistant.runtime import agent_loop, memory
        directory = tempfile.TemporaryDirectory(prefix="claim-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for module in (expert_turn, agent_loop, memory):
            context = patch.object(module, "_OUT", self.root)
            context.start()
            self.addCleanup(context.stop)
        self.scheduler = Scheduler()
        for context in (patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=self.scheduler), patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"})):
            context.start()
            self.addCleanup(context.stop)

    def test_real_office_tables_match_low_risk_draft_without_confirmation_or_formulas(self):
        import openpyxl
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        result = expert_turn.run_expert_turn("生成索赔意向草稿\n事件：=A | B & C\n函号：N-A\n签收人：张三\n送达回证：=1+1\n人数：12人", "claim", session_id="formats-claim", force_intent="run")
        self.assertTrue(result["wrote"] and result["submit_blocked"], result)
        self.assertFalse(result["hitl_pending"])
        self.assertEqual("done", self.scheduler.get(result["run_id"]).state)
        paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
        expected = table(paths[".md"].read_text(encoding="utf-8"), "4 意向")
        book = openpyxl.load_workbook(paths[".xlsx"])
        try:
            sheet = next(book[name] for name in book.sheetnames if name.startswith("4 意向"))
            self.assertEqual(expected, [["" if c.value is None else c.value for c in row] for row in sheet])
            self.assertTrue(all(c.data_type != "f" for sheet in book for row in sheet for c in row))
        finally:
            book.close()
        with ZipFile(paths[".docx"]) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)] for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
        self.assertIn(expected, tables)

    def test_chat_and_readonly_never_write_or_send_letters(self):
        chat = expert_turn.run_expert_turn("索赔意向和正式索赔报告怎么区分？", "claim", session_id="chat-claim")
        self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            denied = expert_turn.run_named_exclusive(TOOL, {"text": "生成索赔意向", "session_id": "ro-claim"})
        self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_hostile_inputs_leave_tables_intact_without_verdicts(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for name, source in INPUTS.items():
            with self.subTest(input=name):
                result = expert_turn.run_named_exclusive(TOOL, {"text": source, "session_id": "hostile-claim"})
                self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                path = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md"))
                md = path.read_text(encoding="utf-8")
                parsed = table_rows(md)
                for i in range(1, len(parsed)):
                    line, cells = parsed[i]
                    previous_line, previous = parsed[i-1]
                    if line != previous_line+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        continue
                    self.assertEqual(len(previous), len(cells))
                self.assertTrue(all(p.search(md) is None for p in VERDICTS))


if __name__ == "__main__":
    unittest.main()
