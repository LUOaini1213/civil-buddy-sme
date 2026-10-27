#!/usr/bin/env python3
"""Literal procurement ownership, evidence and real offline exports."""
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
from packing_assistant.post_drafts import build_draft as dispatch
from packing_assistant.post_drafts.procurement import MISSING, TOOLS, _parse, build_draft
from packing_assistant.runtime.scheduler import Scheduler
from packing_assistant.runtime.tool_engine import default_engine


def draft(post, text):
    return build_draft(post, TOOLS[post], text)


def table(md, title):
    return next(rows for name, rows in tables_from_md(md) if name.startswith(title))


def rows(md, title):
    result = table(md, title)
    return [dict(zip(result[0], row)) for row in result[1:]]


class ProcurementDrafts(unittest.TestCase):
    def test_only_own_named_tool_builds_each_post(self):
        for post, tool in TOOLS.items():
            self.assertEqual(draft(post, ""), dispatch(post, tool, ""))
            self.assertIsNone(build_draft(post, "architecture__memo", ""))
            md = draft(post, "")
            self.assertIn("AI 草稿", md)
            self.assertIn("submit_blocked=true", md)
            self.assertEqual(10, len([line for line in md.splitlines() if line.startswith("## ")]))

    def test_short_compare_company_list_means_one_row_per_supplier(self):
        quoted = rows(draft("proc-compare", "写一份比价表 钢筋 甲商 乙商"), "5 比价表")
        self.assertEqual(["甲商", "乙商"], [r["供方名称"] for r in quoted])
        self.assertTrue(all(r["单价 / 原报价"] == "TBD" for r in quoted))

    def test_short_compare_price_is_not_appended_to_the_supplier_name(self):
        quoted = rows(draft("proc-compare", "写一份比价表 钢筋 甲商 报价3861"), "5 比价表")
        self.assertEqual(1, len(quoted))
        self.assertEqual("甲商", quoted[0]["供方名称"])
        self.assertEqual("3861", quoted[0]["单价 / 原报价"])

    def test_unlabelled_number_in_short_command_is_not_a_supplier_or_price(self):
        quoted = rows(draft("proc-compare", "写一份比价表 钢筋 甲商 3861"), "5 比价表")
        self.assertEqual(1, len(quoted))
        self.assertEqual("甲商", quoted[0]["供方名称"])
        self.assertEqual("TBD", quoted[0]["单价 / 原报价"])

    def test_plan_two_materials_keep_kind_quantity_date_and_source_separate(self):
        md = draft("proc-plan", "3#楼主体的HRB400E钢筋要87吨，是甲指的宝丰钢铁供货，10月16日前必须进场；"
                   "地下室自采的M15砌筑砂浆要121方，需用单编号XQ-2026-042，工程部李强提的。")
        steel, mortar = rows(md, "甲指")[0], rows(md, "自采")[0]
        self.assertEqual(("钢筋", "HRB400E", "87吨", "宝丰钢铁", "10月16日前"),
                         tuple(steel[k] for k in ("物资", "规格", "用户数量", "指定供方", "需要日期 / 到货节点")))
        self.assertEqual(("砌筑砂浆", "121方", "XQ-2026-042", "工程部李强", MISSING),
                         tuple(mortar[k] for k in ("物资", "用户数量", "数量来源 / 需用单", "责任方 / 提需人", "需要日期 / 到货节点")))
        self.assertEqual("UNSPECIFIED", steel["用户提前期来源"])

    def test_plan_unknown_supply_choice_and_oral_quantity_source_are_not_defaults(self):
        md = draft("proc-plan", "塔吊基础的C40混凝土要84方，10月9号就得来；还有个地泵管，DN125的，"
                   "要81根，甲供还是自采领导没定，数量是老王在群里报的，没有需用单，到货时间也没说。")
        data = _parse("proc-plan", "塔吊基础的C40混凝土要84方，10月9号就得来；还有个地泵管，DN125的，"
                      "要81根，甲供还是自采领导没定，数量是老王在群里报的，没有需用单，到货时间也没说。")
        self.assertEqual(2, len(data.items))
        self.assertEqual(["84方", "81根"], [row["quantity"] for row in data.items])
        self.assertNotIn("kind", data.items[1])
        self.assertEqual("老王在群里报的", data.items[1]["source"])
        self.assertEqual("待划", rows(md, "待划")[1]["供应方式"])

    def test_plan_given_lead_time_is_literal_and_does_not_create_order_dates(self):
        md = draft("proc-plan", "物资：真石漆\n规格：灰色，配套柔性腻子\n供应方式：甲限，业主只认三个品牌\n"
                   "需要日期：2026-11-06\n提前期：厂家说备货加运输19天")
        row = rows(md, "甲限")[0]
        self.assertEqual("甲限", row["供应方式"])
        self.assertEqual("灰色，配套柔性腻子", row["规格"])
        self.assertEqual("厂家说备货加运输19天", row["用户提前期来源"])
        dates = rows(md, "6 提前期倒排")[0]
        self.assertEqual("UNSPECIFIED", dates["最迟下单日"])
        self.assertEqual("UNSPECIFIED", dates["最迟提需日"])

    def test_plan_material_in_a_numbered_building_is_not_quantity_from_its_id(self):
        data = _parse("proc-plan", "钢筋要3#楼使用，没给数量。")
        self.assertTrue(all(row.get("quantity", MISSING) == MISSING for row in data.items))

    def test_plan_adjacent_literal_quantity_and_arrival_date_are_preserved(self):
        for literal in ("20 吨", "19.75吨", "1,200 kg"):
            with self.subTest(literal=literal):
                data = _parse("proc-plan", f"出一份采购计划：钢筋 {literal}，5月20日前到场")
                self.assertEqual(1, len(data.items))
                self.assertEqual(literal, data.items[0]["quantity"])
                self.assertEqual("5月20日前", data.items[0]["delivery"])

    def test_plan_adjacent_shortcut_never_uses_prices_codes_examples_or_ambiguous_items(self):
        for text in ("钢筋 3861元/吨", "钢筋 HRB400E", "钢筋 XQ-20吨", "钢筋 20吨/批",
                     "钢筋 20", "钢筋工20人", "比如钢筋20吨", "不要钢筋20吨", "钢筋20吨？",
                     "钢筋 水泥20吨", "钢筋20吨不是本轮需求"):
            with self.subTest(text=text):
                data = _parse("proc-plan", text)
                self.assertTrue(all(row.get("quantity", MISSING) == MISSING for row in data.items), data)
        quote = _parse("proc-compare", "钢筋20吨")
        self.assertTrue(all(row.get("quantity", MISSING) == MISSING for row in quote.items), quote)

    def test_compare_prices_and_self_pickup_do_not_become_supplier_names(self):
        md = draft("proc-compare", "标的是HRB400E螺纹钢，一共127吨，10月13日前运到项目部，"
                   "德龙钢贸报3861一吨含税到场，冀东物资报3796一吨但要我们自提，"
                   "两家付款都写月结61天。")
        quotes = rows(md, "5 比价表")
        self.assertEqual(["德龙钢贸", "冀东物资"], [row["供方名称"] for row in quotes])
        self.assertEqual(["3861", "3796"], [row["单价 / 原报价"] for row in quotes])
        self.assertEqual(["到场", "自提"], [row["运费装卸"] for row in quotes])
        self.assertEqual(["月结61天", "月结61天"], [row["付款"] for row in quotes])
        self.assertTrue(all(row["币种"] == MISSING and row["合价"] == "TBD" for row in quotes))

    def test_quote_header_form_keeps_common_requirement_not_supplier_response(self):
        md = draft("proc-compare", "标的：混凝土 C30 P8 泵送\n数量：2401 方\n交货地点：南三环3#墩\n"
                   "报价一：长青建材 469 含税含泵送\n报价二：恒基商砼 456 泵送费另计\n"
                   "付款：收货后31天承兑\n报价有效期：到本月底")
        request = rows(md, "3 询价文件必备栏")[0]
        self.assertEqual("收货后31天承兑", request["询价付款要求（非供方响应）"])
        quotes = rows(md, "5 比价表")
        self.assertEqual(["长青建材", "恒基商砼"], [row["供方名称"] for row in quotes])
        self.assertTrue(all(row["付款"] == MISSING for row in quotes))

    def test_two_materials_and_partial_quote_restore_the_correct_material(self):
        md = draft("proc-compare", "8#楼外墙保温岩棉板100厚要1801平，鑫和报39一平；地下室那个聚氨酯板要601平，"
                   "多厚他没说，还是这家给53，瑞丰只回了岩棉36，另一样说过两天再报，运费两家都没提。")
        quotes = rows(md, "5 比价表")
        self.assertEqual(["岩棉板", "聚氨酯板", "岩棉板"], [row["物资 / 标的"] for row in quotes])
        self.assertEqual(["鑫和", "鑫和", "瑞丰"], [row["供方名称"] for row in quotes])
        self.assertEqual(["39", "53", "36"], [row["单价 / 原报价"] for row in quotes])
        self.assertTrue(all(row["运费装卸"] == MISSING for row in quotes))

    def test_multiple_item_names_without_supplier_are_not_a_new_quote(self):
        md = draft("proc-compare", "标的：钢筋\n数量：12吨\n供应商：甲商\n原报价：3861元/吨\n"
                   "标的：水泥\n数量：24吨\n原报价：421元/吨（没有写供应商）")
        quotes = rows(md, "5 比价表")
        self.assertEqual(2, len(quotes))
        self.assertEqual(["甲商", MISSING], [row["供方名称"] for row in quotes])

    def test_currency_and_pricing_unit_remain_literal_not_inferred_or_converted(self):
        md = draft("proc-compare", "标的：铝板\n供应商：甲商\n原报价：SGD 38.25/m2\n"
                   "供应商：乙商\n原报价：USD 29.75/m2")
        quotes = rows(md, "5 比价表")
        self.assertEqual(["SGD", "USD"], [row["币种"] for row in quotes])
        self.assertEqual(["/m2", "/m2"], [row["计价单位"] for row in quotes])
        self.assertTrue(all(row["合价"] == "TBD" for row in quotes))

    def test_followup_with_same_supplier_on_two_materials_does_not_choose_last_item(self):
        md = draft("proc-compare", "物资：钢筋；供应商：甲商；原报价：3861元/吨\n"
                   "物资：水泥；供应商：甲商；原报价：421元/吨\n供应商：甲商；付款：月结61天")
        quotes = rows(md, "5 比价表")
        self.assertEqual(3, len(quotes))
        self.assertEqual([MISSING, MISSING, "月结61天"], [row["付款"] for row in quotes])
        self.assertEqual(MISSING, quotes[2]["物资 / 标的"])

    def test_supplier_delivery_response_is_not_the_buyers_required_date(self):
        md = draft("proc-compare", "物资：铝板；需要日期：2026-10-23\n供应商：甲商\n"
                   "原报价：38元/m2\n交期：2026-10-18\n数量：125张\n规格：3mm\n"
                   "供应商：乙商\n原报价：40元/m2\n交期：2026-10-20\n询价数量：160张")
        request = rows(md, "3 询价文件必备栏")[0]
        self.assertEqual("2026-10-23", request["需要日期"])
        self.assertEqual("160张", request["询价数量"])
        quoted = rows(md, "5 比价表")
        self.assertEqual(["2026-10-18", "2026-10-20"], [r["到货期"] for r in quoted])
        self.assertEqual(["125张", MISSING], [r["供方报价数量"] for r in quoted])
        self.assertEqual(["3mm", MISSING], [r["规格响应"] for r in quoted])

    def test_collective_terms_only_belong_to_two_suppliers_for_the_current_item(self):
        md = draft("proc-compare", "物资：钢筋；供应商：甲商；原报价：3861元/吨\n"
                   "物资：水泥；供应商：乙商；原报价：421元/吨\n供应商：丙商；原报价：410元/吨\n两家付款都月结61天")
        quoted = rows(md, "5 比价表")
        self.assertEqual([MISSING, "月结61天", "月结61天"], [r["付款"] for r in quoted])

    def test_ambiguous_collective_terms_cannot_choose_the_last_supplier(self):
        md = draft("proc-compare", "物资：钢筋\n供应商：甲商；原报价：3861元/吨\n"
                   "供应商：乙商；原报价：3800元/吨\n供应商：丙商；原报价：3790元/吨\n两家付款都月结61天")
        quoted = rows(md, "5 比价表")
        self.assertEqual([MISSING]*3+["月结61天"], [r["付款"] for r in quoted])
        self.assertEqual(MISSING, quoted[-1]["供方名称"])
        self.assertIn("供方范围未明确", quoted[-1]["归属提示"])

    def test_distinct_quote_sources_and_revisions_are_separate_without_inherited_terms(self):
        for second in ("报价来源：BJ-B\n原报价：40元/m2", "原报价：40元/m2\n报价来源：BJ-B"):
            with self.subTest(second=second):
                md = draft("proc-compare", "物资：铝板\n供应商：甲商\n报价来源：BJ-A\n"
                           "原报价：38元/m2\n付款：货到付款\n供应商：甲商\n"+second+"\n付款：月结61天")
                quoted = rows(md, "5 比价表")
                self.assertEqual(["BJ-A", "BJ-B"], [r["报价来源"] for r in quoted])
                self.assertEqual(["38元/m2", "40元/m2"], [r["单价 / 原报价"] for r in quoted])
                self.assertEqual(["货到付款", "月结61天"], [r["付款"] for r in quoted])

    def test_quote_with_a_new_batch_does_not_merge_into_the_old_batch(self):
        md = draft("proc-compare", "物资：钢筋\n供应商：甲商\n批次：A\n原报价：3861元/吨\n"
                   "供应商：甲商\n批次：B\n原报价：3850元/吨")
        quoted = rows(md, "5 比价表")
        self.assertEqual(["A", "B"], [r["供货批次"] for r in quoted])
        self.assertEqual(["3861元/吨", "3850元/吨"], [r["单价 / 原报价"] for r in quoted])

    def test_version_numbers_and_dates_alone_are_not_prices(self):
        for value in ("2026版报价单尚未载单价", "2026A", "2026/10/18", "第3版报价单", "BJ-2026-018"):
            with self.subTest(value=value):
                data = _parse("proc-compare", "物资：钢筋\n供应商：甲商\n原报价："+value)
                self.assertEqual(MISSING, data.quotes[0]["price"])

    def test_dates_and_version_numbers_are_not_quantities(self):
        for value in ("2026年10月18日", "第3版", "3#楼", "BJ-2026-018", "2026A"):
            with self.subTest(value=value):
                data = _parse("proc-plan", "物资：钢筋\n数量："+value)
                self.assertEqual(MISSING, data.items[0]["quantity"])

    def test_literal_currency_suffixes_and_original_money_text_are_retained(self):
        for value in ("1988USD", "€1988", "口头报1988", "1988元/吨", "SGD 1,988.25/m2"):
            with self.subTest(value=value):
                data = _parse("proc-compare", "物资：钢筋\n供应商：甲商\n原报价："+value)
                self.assertEqual(value, data.quotes[0]["price"])

    def test_quote_table_price_cell_can_supply_literal_currency_and_unit(self):
        md = draft("proc-compare", "|供应商|物资|原报价|\n|---|---|---|\n"
                   "|甲商|铝板|SGD 38.25/m2|\n|乙商|铝板|USD 29.75/m2|")
        quoted = rows(md, "5 比价表")
        self.assertEqual(["SGD", "USD"], [r["币种"] for r in quoted])
        self.assertEqual(["/m2", "/m2"], [r["计价单位"] for r in quoted])
        self.assertTrue(all(r["合价"] == "TBD" for r in quoted))

    def test_table_rows_and_an_unscoped_followup_cannot_reuse_last_supplier(self):
        md = draft("proc-compare", "|单价|供应商|物资|币种|\n|---|---|---|---|\n"
                   "|3861|甲商|钢筋|CNY|\n|421|乙商|水泥|CNY|\n付款 月结61天")
        quotes = rows(md, "5 比价表")
        self.assertEqual([MISSING, MISSING], [row["付款"] for row in quotes])

    def test_missing_identity_in_table_is_its_own_row_with_no_borrowed_name(self):
        md = draft("proc-compare", "|供应商|物资|原报价|币种|\n|---|---|---|---|\n"
                   "|甲商|钢筋|3861元/吨|CNY|\n||水泥|421元/吨||")
        quotes = rows(md, "5 比价表")
        self.assertEqual(["甲商", MISSING], [row["供方名称"] for row in quotes])
        self.assertEqual(["CNY", MISSING], [row["币种"] for row in quotes])

    def test_examples_instructions_and_reference_codes_are_not_quote_or_quantity(self):
        for field, value in (("原报价", "比如3861元/吨只是示例"), ("原报价", "请按市场价3861元填"),
                             ("原报价", "只给报价单BJ-2026-018"), ("数量", "需用单XQ-2026-042未提供数量")):
            with self.subTest(field=field, value=value):
                data = _parse("proc-compare", "标的：钢筋\n供应商：甲商\n"+field+"："+value)
                key = "price" if field == "原报价" else "quantity"
                self.assertTrue(all(row.get(key, MISSING) == MISSING for row in data.items+data.quotes))

    def test_compact_markdown_cells_with_unknown_and_example_prices_stay_unknown(self):
        data = _parse("proc-compare", "|供应商|物资|原报价|\n|---|---|---|\n|甲商|钢筋|没报价3861元|\n"
                      "|乙商|水泥|比如421元只是示例|\n|丙商|钢管|156元|\n")
        self.assertEqual(3, len(data.quotes))
        self.assertEqual([MISSING, MISSING, "156元"], [row.get("price", MISSING) for row in data.quotes])

    def test_two_vendors_do_not_inherit_first_performance_licence_or_visit(self):
        md = draft("proc-vendor", "一个是天成砂石，供机制砂和碎石，执照给了，业绩说做过京唐港堆场，厂子没去看过；"
                   "另一个是宏利防水，卷材那家，上个月张工去他们唐山厂看了产线，业绩和资料都还没交，售后电话倒是留了。")
        entry = rows(md, "3 准入")
        self.assertEqual(["天成砂石", "宏利防水"], [row["供方"] for row in entry])
        self.assertEqual(["京唐港堆场", MISSING], [row["用户类似业绩"] for row in entry])
        visits = rows(md, "5 考察")
        self.assertEqual([MISSING, "张工"], [row["考察人"] for row in visits])
        self.assertEqual([MISSING, "唐山厂"], [row["厂址与库容"] for row in visits])

    def test_reported_vendor_documents_and_visit_have_source_positions_not_approval(self):
        md = draft("proc-vendor", "供应商：河北鑫达管道有限公司\n供货范围：球墨铸铁管\n"
                   "生产许可：已交，有效期到2027-03-19\n考察日期：2026-09-12\n考察人：采购部刘建军\n"
                   "质量资料：近三批出厂检测报告已收\n售后：邢台有服务网点")
        entry = rows(md, "3 准入")[0]
        self.assertEqual("2027-03-19", entry["有效期原文"])
        self.assertEqual("待核", entry["初审"])
        evidence = rows(md, "用户证据位置")
        quality = next(row for row in evidence if "出厂检测报告" in row["用户证据原文"])
        self.assertEqual("本轮输入第6行", quality["输入位置（非原件页码）"])
        score = rows(md, "7 评价表头")[0]
        self.assertEqual(("待核", "待核"), (score["分数"], score["结论"]))

    def test_no_dates_or_inspectors_are_invented_for_a_visit_not_made(self):
        md = draft("proc-vendor", "供应商：甲商\n厂子还没去看过，打算下周张工去。")
        visit = rows(md, "5 考察")[0]
        self.assertEqual(MISSING, visit["考察人"])
        self.assertEqual(MISSING, visit["考察日期"])

    def test_vendor_actual_status_is_not_overwritten_by_a_new_approval(self):
        md = draft("proc-vendor", "供应商：甲商\n用户状态：用户称已纳入名录\n企业制度：用户制度文件2026版")
        row = rows(md, "6 短名单")[0]
        self.assertEqual("用户称已纳入名录", row["用户名单状态（未核）"])
        self.assertEqual("待核", row["拟纳入 / 观察待决"])
        self.assertEqual("（空栏）", row["审批签认"])

    def test_vendor_evaluation_does_not_expose_quote_amounts_or_score_prices(self):
        md = draft("proc-vendor", "供应商：甲商\n供货范围：钢筋\n原报价：3861元/吨\n质量资料：报告A")
        self.assertNotIn("3861", md)
        self.assertIn("报告A", md)
        self.assertEqual("待核", rows(md, "7 评价表头")[0]["分数"])

    def test_vendor_table_evidence_position_is_the_actual_row_not_the_header(self):
        md = draft("proc-vendor", "|供方|供货范围|质量资料|\n|---|---|---|\n|甲商|机制砂|报告A|\n|乙商|卷材|报告B|")
        proof = rows(md, "用户证据位置")
        a = next(row for row in proof if row["用户证据原文"] == "报告A")
        b = next(row for row in proof if row["用户证据原文"] == "报告B")
        self.assertEqual("本轮输入第3行", a["输入位置（非原件页码）"])
        self.assertEqual("本轮输入第4行", b["输入位置（非原件页码）"])

    def test_key_value_vendor_table_uses_each_evidence_row_position(self):
        md = draft("proc-vendor", "|供应商|甲商|\n|质量资料|报告A|\n|售后服务|服务点B|")
        proof = rows(md, "用户证据位置")
        self.assertEqual(["质量资料", "售后服务"], [r["准入 / 评价项"] for r in proof])
        self.assertEqual(["本轮输入第2行", "本轮输入第3行"], [r["输入位置（非原件页码）"] for r in proof])

    def test_local_regions_remain_local_and_explicit_unknown_overrides_global(self):
        for post in TOOLS:
            identity = "供应商" if post == "proc-vendor" else "物资"
            text = f"{identity}：甲；辖区：CN\n{identity}：乙\n{identity}：丙；辖区：SG"
            data = _parse(post, text)
            actual = data.vendors if post == "proc-vendor" else data.items
            self.assertEqual(["CN", "UNSPECIFIED", "SG"], [r["jurisdiction"] for r in actual], post)
            data = _parse(post, f"辖区：CN\n{identity}：甲\n{identity}：乙；辖区：未知")
            actual = data.vendors if post == "proc-vendor" else data.items
            self.assertEqual(["CN", "UNSPECIFIED"], [r["jurisdiction"] for r in actual], post)

    def test_quote_region_inherits_only_its_identified_material_not_previous_item(self):
        md = draft("proc-compare", "物资：钢筋；辖区：CN\n供应商：甲商；原报价：3861元/吨\n"
                   "物资：铝板\n供应商：乙商；原报价：38元/m2\n物资：玻璃；辖区：SG\n"
                   "供应商：丙商；原报价：41SGD/m2")
        self.assertEqual(["CN", "UNSPECIFIED", "SG"], [r["对象辖区"] for r in rows(md, "5 比价表")])

    def test_escaped_pipe_in_input_table_cannot_shift_supplier_material_or_price(self):
        md = draft("proc-compare", "|供应商|物资|原报价|\n|---|---|---|\n"
                   r"|甲\|乙商|铝板|SGD 38.25/m2|"+"\n|丙商|玻璃|29.75元/m2|")
        quoted = rows(md, "5 比价表")
        self.assertEqual(["甲|乙商", "丙商"], [r["供方名称"] for r in quoted])
        self.assertEqual(["铝板", "玻璃"], [r["物资 / 标的"] for r in quoted])
        self.assertEqual(["SGD 38.25/m2", "29.75元/m2"], [r["单价 / 原报价"] for r in quoted])

    def test_file_boundaries_do_not_copy_previous_supplier_material_or_response(self):
        for marker, name in (("【用户上传：第二报价.txt】offset=0 本段25字 剩余约0字", "第二报价.txt"),
                             ("### 第二报价.txt", "第二报价.txt"), ("### 第二报价，复核;A.txt", "第二报价，复核;A.txt")):
            with self.subTest(marker=marker):
                md = draft("proc-compare", "### 第一报价.txt\n物资：铝板\n供应商：甲商\n"
                           "原报价：SGD 38.25/m2\n付款：货到付款\n"+marker+"\n原报价：40元/m2")
                quoted = rows(md, "5 比价表")
                self.assertEqual(["甲商", MISSING], [r["供方名称"] for r in quoted])
                self.assertEqual(["铝板", MISSING], [r["物资 / 标的"] for r in quoted])
                self.assertEqual(["货到付款", MISSING], [r["付款"] for r in quoted])
                self.assertEqual(["第一报价.txt", name], [r["输入资料名 / 分组"] for r in quoted])

    def test_same_material_in_distinct_files_keeps_independent_request_quantity(self):
        data = _parse("proc-plan", "### 需求A.txt\n物资：钢筋\n数量：87吨\n"
                      "### 需求B.txt\n物资：钢筋\n数量：92吨")
        self.assertEqual(["87吨", "92吨"], [r["quantity"] for r in data.items])

    def test_example_in_one_file_cannot_mask_actual_records_in_the_next_file(self):
        data = _parse("proc-compare", "### 示意.txt\n例如供应商甲报3861元/吨\n"
                      "### 实际报价.txt\n物资：钢筋\n供应商：乙商\n原报价：3800元/吨")
        self.assertEqual(1, len(data.quotes))
        self.assertEqual("3800元/吨", data.quotes[0]["price"])

    def test_vendor_file_evidence_positions_include_actual_filename(self):
        md = draft("proc-vendor", "### 供方A.txt\n供应商：甲商\n质量资料：报告A\n"
                   "### 供方B.txt\n质量资料：报告B")
        proof = rows(md, "用户证据位置")
        self.assertEqual(["甲商", MISSING], [r["供方"] for r in proof])
        self.assertEqual(["供方A.txt · 本轮输入第3行", "供方B.txt · 本轮输入第5行"],
                         [r["输入位置（非原件页码）"] for r in proof])

    def test_explicit_symbol_names_and_values_are_escaped_only_at_render(self):
        md = draft("proc-vendor", "供方：=2+3 A|B < 10\n质量资料：字面&lt;；报告A|B")
        self.assertIn("=2+3 A&#124;B &lt; 10", md)
        self.assertIn("字面&amp;lt;", md)


class ProcurementRuntime(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="procurement-regression-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for context in (
            patch.object(expert_turn, "_OUT", self.root),
            patch("packing_assistant.runtime.memory._OUT", self.root),
            patch("packing_assistant.runtime.scheduler.get_scheduler", return_value=Scheduler()),
            patch.dict(os.environ, {"CIVIL_JOB_ROOT": "", "CIVIL_SANDBOX_ROOTS": str(self.root), "CIVIL_SANDBOX": "workspace-write", "CIVIL_APPROVAL": "on-request"}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_all_three_tools_export_real_matching_markdown_word_and_excel(self):
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile
        import openpyxl
        for post, text, target in (
            ("proc-plan", "物资：=钢筋|A\n规格：HRB400E\n数量：87吨\n供应方式：甲指\n需要日期：10月16日前", "甲指"),
            ("proc-compare", "标的：铝板\n供应商：=甲商|A\n原报价：SGD 38.25/m2\n币种：SGD\n付款：月结61天", "5 比价表"),
            ("proc-vendor", "供应商：=甲商|A\n质量资料：出厂报告A\n售后：服务网点\n用户状态：用户称已纳入名录", "7 评价表头"),
        ):
            with self.subTest(post=post):
                result = default_engine().execute(TOOLS[post], {"text": text, "session_id": "export-"+post}, expert_id=post, intent="run")
                self.assertTrue(result["ok"] and result["wrote"], result)
                self.assertTrue(result["submit_blocked"])
                paths = {Path(f["path"]).suffix: Path(f["path"]) for f in result["files"]}
                self.assertTrue({".md", ".docx", ".xlsx"}.issubset(paths), paths)
                expected = table(paths[".md"].read_text(encoding="utf-8"), target)
                workbook = openpyxl.load_workbook(paths[".xlsx"])
                try:
                    sheet = next(workbook[name] for name in workbook.sheetnames if name.startswith(target))
                    actual = [["" if cell.value is None else cell.value for cell in row] for row in sheet]
                    self.assertEqual(expected, actual)
                    self.assertTrue(all(cell.data_type != "f" for sheet in workbook for row in sheet for cell in row))
                finally:
                    workbook.close()
                with ZipFile(paths[".docx"]) as archive:
                    document = ET.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                word_tables = [[["".join(t.text or "" for t in c.findall(".//w:t", ns)) for c in r.findall("w:tc", ns)]
                                for r in tbl.findall("w:tr", ns)] for tbl in document.findall(".//w:tbl", ns)]
                self.assertIn(expected, word_tables)

    def test_questions_and_readonly_mode_do_not_write_any_procurement_post(self):
        engine = default_engine()
        for post, tool in TOOLS.items():
            denied = engine.execute(tool, {"text": "写内部表", "session_id": "no-write"}, expert_id=post, intent="chat")
            self.assertFalse(denied["ok"])
            chat = expert_turn.run_expert_turn("这个岗位怎么用？", post, session_id="chat-"+post)
            self.assertFalse(chat["wrote"])
        with patch.dict(os.environ, {"CIVIL_SANDBOX": "read-only"}):
            for post, tool in TOOLS.items():
                denied = expert_turn.run_named_exclusive(tool, {"text": "写内部表", "session_id": "ro-"+post})
                self.assertFalse(denied["wrote"])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_each_procurement_post_survives_all_hostile_inputs_without_ragged_tables(self):
        from scripts.test_post_robustness import INPUTS, VERDICTS, table_rows
        import re
        for post, tool in TOOLS.items():
            for name, source in INPUTS.items():
                with self.subTest(post=post, input=name):
                    result = expert_turn.run_named_exclusive(tool, {"text": source, "session_id": "hostile-"+post})
                    self.assertTrue(result["wrote"] and result["submit_blocked"], result)
                    md = next(Path(f["path"]) for f in result["files"] if f["path"].endswith(".md")).read_text(encoding="utf-8")
                    parsed = table_rows(md)
                    for index in range(1, len(parsed)):
                        number, cells = parsed[index]
                        last_number, previous = parsed[index-1]
                        if number != last_number+1 or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                            continue
                        self.assertEqual(len(previous), len(cells))
                    self.assertTrue(all(pattern.search(md) is None for pattern in VERDICTS))


if __name__ == "__main__":
    unittest.main()
