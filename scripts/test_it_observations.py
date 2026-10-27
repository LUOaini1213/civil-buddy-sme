#!/usr/bin/env python3
"""Scoped IT evidence, ordinary notes and negative/conditional requirements."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_it_drafts import draft, rows_for
from packing_assistant.post_drafts.it import _Facts, _MISSING


class ItObservationTests(unittest.TestCase):
    def records(self, post, text):
        return _Facts(text, post).records()

    def test_natural_permission_application_is_not_execution(self):
        text = "帮我起一份运维手册提纲，项目管理系统这块，现场资料员这个岗位只给查询和导出两个动作，申请人李文强，业务审批人周敏，2026-09-23 生效，2026-12-31 到期收回，工单号 YW-2026-0918。"
        md = draft("it-ops", text)
        self.assertEqual(["项目管理系统", "资料员", "查询和导出"], [rows_for(md, "系统与权限申请矩阵")[1][i] for i in (0, 2, 3)])
        details = dict(rows_for(md, "权限变更与留痕")[1:])
        self.assertEqual("李文强", details["申请人"])
        self.assertEqual("周敏", details["拟审批人"])
        self.assertEqual("2026-09-23", details["拟生效时间"])
        self.assertEqual("2026-12-31", details["到期/收回时间"])
        self.assertEqual("YW-2026-0918", details["工单号（用户提供）"])
        self.assertEqual("", details["实际生效记录"])
        self.assertEqual("", details["审批签认"])

    def test_natural_two_permissions_and_incident_stay_separate(self):
        text = "权限这边两件事：财务新来的出纳要 OA 的报销单查询，用到 2026-12-31 就收回；监控回放平台那头，项目经理王建军要能导出关键片段，谁审批还没定，工单也没走。另外截标那天 VPN 掉过一次线，按 P3 记。"
        facts = _Facts(text, "it-ops")
        rows = facts.records()
        cashier = next(r for r in rows if r.get("role") == "出纳")
        manager = next(r for r in rows if r.get("role") == "项目经理")
        self.assertEqual("OA", cashier["system"])
        self.assertEqual("2026-12-31", cashier["expiry"])
        self.assertEqual("监控回放平台", manager["system"])
        self.assertEqual("王建军", manager["requester"])
        self.assertNotIn("expiry", manager)
        self.assertNotIn("approver", manager)
        self.assertNotIn("severity", manager)
        incidents = facts.children("incidents")
        self.assertEqual(1, len(incidents))
        self.assertEqual("VPN", incidents[0]["system"])
        self.assertEqual("P3", incidents[0]["severity"])
        self.assertNotIn("role", incidents[0])

    def test_role_change_drops_requester_approver_ticket_and_window(self):
        a, b = self.records("it-ops", "系统：OA\n角色：资料员；权限：查询；申请人：李工；审批人：王工；工单号：REQ-A；变更窗口：周日\n角色：员工；权限：查看本人资料")
        self.assertEqual("李工", a["requester"])
        for key in ("requester", "approver", "ticket", "window"):
            self.assertNotIn(key, b)
        self.assertEqual("OA", b["system"])

    def test_ops_full_source_and_audit_columns(self):
        md = draft("it-ops", "系统：OA；角色：出纳；权限：报销单查询；权限依据：岗位清单A；申请人：李工；审批人：王工；申请来源：申请单A；审批依据：审批单A；回收条件：离岗；审计证据：操作记录A")
        rows = rows_for(md, "权限依据与申请审批")
        r = dict(zip(rows[0], rows[1]))
        for key, value in (("权限依据", "岗位清单A"), ("申请来源", "申请单A"), ("审批依据", "审批单A"), ("回收条件", "离岗"), ("审计证据", "操作记录A")):
            self.assertEqual(value, r[key])
        self.assertEqual("", r["审批签认"])

    def test_unknown_approver_is_pending_not_a_person(self):
        md = draft("it-ops", "系统：OA；角色：出纳；权限：查询；审批人：王工可以吗？")
        details = dict(rows_for(md, "权限变更与留痕")[1:])
        self.assertEqual(_MISSING, details["拟审批人"])
        self.assertIn("王工可以吗", md)

    def test_natural_backup_targets_keep_literal_limits(self):
        rows = self.records("it-data", "劳务实名考勤系统按核心级管，数据负责人是综合办王海涛，业务口径 RPO 不超过 15 分钟、RTO 不超过 240 分钟，备份介质用磁带库，保留 180 天，副本放异地机房，演练记录还没整理。")
        self.assertEqual(1, len(rows))
        r = rows[0]
        self.assertEqual("劳务实名考勤系统", r["system"])
        self.assertEqual("不超过 15 分钟", r["rpo"])
        self.assertEqual("不超过 240 分钟", r["rto"])
        self.assertEqual("综合办王海涛", r["owner"])
        self.assertEqual("180 天", r["retention"])
        self.assertNotIn("duration", r)

    def test_natural_two_backup_data_objects_keep_policies_separate(self):
        text = "备份这块想让你理一理。工地那套视频回放索引，量太大，一周全备一次就行，保留 90 天，先放项目部本地硬盘；还有农民工工资发放台账，那个真不能丢，最多容忍半小时，得留三年，存哪儿老李还没拍板，演练也一直没安排。"
        a, b = self.records("it-data", text)
        self.assertEqual("视频回放索引", a["data"])
        self.assertEqual("项目部本地硬盘", a["location"])
        self.assertEqual("农民工工资发放台账", b["data"])
        self.assertEqual("半小时", b["rpo"])
        self.assertEqual("三年", b["retention"])
        for key in ("location", "rto", "owner", "cycle"):
            self.assertNotIn(key, b)

    def test_same_system_distinct_data_never_share_target_sources(self):
        a, b = self.records("it-data", "系统：项目平台\n数据对象：工资台账；RPO：半小时；RPO来源：业务单A；RTO：2小时；RTO来源：业务单B\n数据对象：视频索引；保留期限：90天")
        self.assertEqual("业务单A", a["rpo_source"])
        for key in ("rpo", "rpo_source", "rto", "rto_source"):
            self.assertNotIn(key, b)

    def test_targets_do_not_supply_actual_drill_duration(self):
        md = draft("it-data", "系统：OA；RPO：15分钟；RTO：240分钟；演练对象：审批附件")
        self.assertEqual(_MISSING, rows_for(md, "恢复演练记录")[1][-1])
        self.assertEqual("240分钟", rows_for(md, "业务恢复目标")[1][2])

    def test_planned_duration_date_evidence_and_result_remain_pending(self):
        md = draft("it-data", "系统：OA；RTO：2小时；演练日期：计划2026-11-30；演练记录：预计生成记录A；实际恢复时长：预计35分钟；用户演练结果：预计恢复成功")
        rows = rows_for(md, "恢复演练记录")
        self.assertEqual(_MISSING, rows[1][2])
        self.assertEqual(_MISSING, rows[1][-1])
        self.assertEqual(_MISSING, rows_for(md, "恢复证据与核对")[1][3])
        self.assertIn("预计恢复成功", md)
        self.assertIn("本稿没有访问数据或执行备份", md)

    def test_actual_duration_without_evidence_is_not_a_recovery_result(self):
        md = draft("it-data", "系统：OA；实际恢复时长：35分钟")
        self.assertEqual(_MISSING, rows_for(md, "恢复演练记录")[1][-1])
        self.assertIn("用户口述实际恢复时长：35分钟；未附演练记录", md)

    def test_denied_or_unattached_evidence_cannot_validate_reported_duration(self):
        for evidence in ("无", "无记录", "无证据", "未附", "未提供原始记录", "还没整理"):
            md = draft("it-data", f"系统：OA；演练记录：{evidence}；实际恢复时长：35分钟")
            self.assertEqual(_MISSING, rows_for(md, "恢复演练记录")[1][-1], evidence)

    def test_column_incident_retains_its_own_system_not_the_permission_role(self):
        md = draft("it-ops", "系统：OA；角色：出纳；权限：查询\n| 系统 | 故障级别 | 故障现象 | 联系人 |\n| --- | --- | --- | --- |\n| 门禁B | P2 | 离线 | 李工 |\n| VPN | P3 | 中断 | |")
        rows = rows_for(md, "故障分级与升级路径")
        self.assertEqual(["门禁B", "VPN"], [r[0] for r in rows[1:]])
        self.assertEqual("李工", rows[1][3])
        self.assertEqual(_MISSING, rows[2][3])

    def test_actual_drill_sources_and_steps_are_kept_as_unverified(self):
        md = draft("it-data", "系统：OA；数据对象：附件；RPO：15分钟；RPO来源：业务目标A；RTO：240分钟；RTO来源：业务目标B；演练对象：附件；恢复步骤：隔离区读取副本；演练记录：记录A；演练来源：报告A；实际恢复时长：35分钟；恢复时长来源：计时表A；用户演练结果：用户报告恢复成功")
        r = rows_for(md, "恢复步骤与演练证据归属")
        values = dict(zip(r[0], r[1]))
        for key, value in (("RPO来源", "业务目标A"), ("RTO来源", "业务目标B"), ("恢复步骤", "隔离区读取副本"), ("验证证据", "记录A"), ("演练来源", "报告A"), ("恢复时长来源", "计时表A")):
            self.assertEqual(value, values[key])
        self.assertEqual("35分钟", rows_for(md, "恢复演练记录")[1][-1])
        self.assertEqual("", values["签认"])

    def test_two_drills_do_not_copy_actual_duration_or_evidence(self):
        md = draft("it-data", "系统：OA\n演练对象：附件A；演练记录：记录A；实际恢复时长：35分钟\n演练对象：附件B")
        a, b = rows_for(md, "恢复演练记录")[1:]
        self.assertEqual("35分钟", a[-1])
        self.assertEqual(_MISSING, b[-1])
        self.assertEqual(_MISSING, rows_for(md, "恢复证据与核对")[2][3])

    def test_different_drill_object_cannot_borrow_policy_data_or_rpo_sources(self):
        md = draft("it-data", "系统：OA；数据对象：附件A；RPO：1小时；RPO来源：目标单A；RTO：2小时；RTO来源：目标单B；演练对象：附件B；演练记录：记录B；实际恢复时长：35分钟")
        rows = rows_for(md, "恢复步骤与演练证据归属")
        self.assertEqual("OA", rows[1][0])
        self.assertEqual("附件B", rows[1][1])
        self.assertEqual([_MISSING, _MISSING], rows[1][2:4])
        self.assertEqual("35分钟", rows_for(md, "恢复演练记录")[1][-1])

    def test_matching_drill_object_can_keep_explicit_policy_sources(self):
        md = draft("it-data", "系统：OA；数据对象：附件A；RPO：1小时；RPO来源：目标单A；演练对象：附件A；演练记录：记录A")
        rows = rows_for(md, "恢复步骤与演练证据归属")
        self.assertEqual("OA / 附件A", rows[1][0])
        self.assertEqual("目标单A", rows[1][2])

    def test_project_and_nonfunctional_requirements_have_body_fields(self):
        md = draft("it-app", "项目：合成项目A；系统：OA；角色：员工；功能：查询；非功能要求：弱网时保留草稿，不声明具体性能阈值")
        rows = dict(rows_for(md, "非功能与数据来源")[1:])
        self.assertEqual("合成项目A", rows["项目"])
        self.assertEqual("弱网时保留草稿", rows["非功能要求（用户提出，待评估）"])

    def test_invalid_numeric_targets_not_dates_or_version_numbers(self):
        for key, value in (("RPO", "2026-10-01"), ("RTO", "v2.1"), ("RPO", "预计1小时"), ("实际恢复时长", "自行计算30分钟")):
            with self.subTest(key=key):
                md = draft("it-data", f"系统：OA；{key}：{value}")
                self.assertEqual(_MISSING, rows_for(md, "业务恢复目标")[1][1 if key == "RPO" else 2])
                self.assertEqual(_MISSING, rows_for(md, "恢复演练记录")[1][-1])

    def test_feature_actor_and_business_rule_are_different_fields(self):
        md = draft("it-app", "系统：物资台账\n用户角色：试验员、商务只读\n功能需求：材料员登记进场过磅记录，无磅单影像不能入库\n验收标准：能按月导出收发存明细")
        row = next(r for r in rows_for(md, "角色与功能需求")[1:] if r[1] == "材料员")
        self.assertEqual("登记进场过磅记录", row[3])
        self.assertEqual("无磅单影像不能入库", row[5])
        self.assertNotIn("材料员", row[3])

    def test_two_modules_resolve_only_unique_explicit_reference(self):
        text = "两个需求一块提：一个是工人考勤，门口闸机的数据想直接进项目部报表，劳务员每天不用再导表；另一个是机械台班登记，司机手机上填台班，机管员审，单价那块财务说先不做。考勤这个王总要国庆前看到，台班那个没说时间。"
        facts = _Facts(text, "it-app")
        rows = facts.records()
        self.assertTrue(any(r.get("module") == "工人考勤" and r.get("delivery") == "国庆前" for r in rows))
        self.assertTrue(any(r.get("module") == "机械台班登记" and r.get("role") == "司机" and r.get("feature") == "填台班" for r in rows))
        self.assertFalse(any(r.get("module") == "机械台班登记" and "delivery" in r for r in rows))
        interfaces = facts.children("interfaces")
        self.assertEqual("工人考勤", interfaces[0]["module"])
        self.assertEqual("门口闸机", interfaces[0]["peer"])

    def test_repeated_features_do_not_copy_acceptance_rule_or_source(self):
        a, b = self.records("it-app", "系统：OA；角色：员工；功能：提交申请；规则：日期必填；验收标准：缺日期时提示；需求来源：单A；功能：查看本人记录")
        self.assertEqual("员工", b["role"])
        for key in ("rule", "acceptance", "requirement_source"):
            self.assertNotIn(key, b)

    def test_interfaces_never_copy_other_peers_direction_or_retry(self):
        facts = _Facts("系统：OA；角色：员工；功能：查询\n对端系统：门禁A；接口方向：A至OA；主数据来源：人员清单A；失败重试：人工登记；接口资料来源：接口单A\n对端系统：门禁B", "it-app")
        a, b = facts.children("interfaces")
        self.assertEqual("接口单A", a["interface_source"])
        for key in ("direction", "source", "retry", "interface_source"):
            self.assertNotIn(key, b)

    def test_conditional_rules_are_requirements_not_example_live_facts(self):
        md = draft("it-app", "系统：物资台账；角色：材料员；功能：登记入库\n业务规则：如果无磅单则不能入库\n验收标准：如果缺磅单，显示提示并保留草稿")
        row = rows_for(md, "角色与功能需求")[1]
        self.assertEqual("如果无磅单则不能入库", row[5])
        self.assertIn("如果缺磅单", rows_for(md, "可观察验收标准")[1][2])

    def test_planned_implementation_is_not_a_live_function_record(self):
        md = draft("it-app", "系统：OA；角色：员工；功能：查询；用户实现记录：计划下周上线")
        rows = rows_for(md, "需求来源与系统模块归属")
        self.assertEqual(_MISSING, rows[1][rows[0].index("用户实现记录（未核）")])
        self.assertIn("计划下周上线", md)

    def test_declared_implementation_never_sets_acceptance_result(self):
        md = draft("it-app", "系统：OA；角色：员工；功能：查询；用户实现记录：用户称已上线；验收标准：只能查本人记录")
        self.assertEqual("", rows_for(md, "可观察验收标准")[1][-1])
        self.assertIn("用户称已上线", md)
        self.assertIn("未签认；submit_blocked=true", md)

    def test_file_boundaries_reset_same_named_systems_and_sources(self):
        for post in ("it-ops", "it-data", "it-app"):
            text = "【用户上传：first.md】\n系统：OA；角色：资料员；功能：查询；权限：查询；数据对象：附件；RPO：1小时；需求来源：单A\n【用户上传：second.md】\n系统：OA；角色：员工；功能：导出；权限：导出；数据对象：日志"
            with self.subTest(post=post):
                a, b = self.records(post, text)
                self.assertEqual("first.md", a["_origin"])
                self.assertEqual("second.md", b["_origin"])
                self.assertNotIn("rpo", b)
                self.assertNotIn("requirement_source", b)
                self.assertTrue(all("first.md" not in value for value in b["_sources"].values()))

    def test_duplicate_filename_markers_are_independent_occurrences(self):
        a, b = self.records("it-data", "【用户上传：same.md】\n系统：OA；RPO：1小时\n【用户上传：same.md】\n系统：OA；RTO：2小时")
        self.assertNotIn("rpo", b)
        self.assertNotIn("rto", a)

    def test_table_blank_cells_do_not_inherit_prior_roles_or_targets(self):
        facts = _Facts("系统：OA\n| 数据对象 | RPO | RTO | RPO来源 |\n| --- | --- | --- | --- |\n| 工资 | 半小时 | 2小时 | 单A |\n| 视频 | | | |", "it-data")
        a, b = facts.records()
        self.assertEqual("单A", a["rpo_source"])
        self.assertEqual(_MISSING, b["rpo_source"])
        self.assertEqual(_MISSING, b["rpo"])

    def test_table_system_does_not_borrow_prior_system_context(self):
        md = draft("it-app", "系统：系统A；建设方：部门A；现状：人工汇总\n| 系统 | 角色 | 功能 |\n| --- | --- | --- |\n| 系统B | 员工 | 查询 |")
        records = self.records("it-app", "系统：系统A；建设方：部门A\n| 系统 | 角色 | 功能 |\n| --- | --- | --- |\n| 系统B | 员工 | 查询 |")
        b = next(r for r in records if r.get("system") == "系统B")
        self.assertNotIn("builder", b)
        self.assertIn("部门A", md)

    def test_late_system_name_does_not_relabel_earlier_unowned_requirement(self):
        a, b = self.records("it-app", "角色：资料员；功能：查询；验收标准：本人资料\n系统：OA；角色：员工；功能：导出")
        self.assertNotIn("system", a)
        self.assertEqual("OA", b["system"])

    def test_scenarios_remain_excluded_until_actual_record_reset(self):
        for post in ("it-app", "it-ops", "it-data"):
            md = draft(post, "例如系统：EXAMPLE-NOT-FACT；RPO：99小时\n角色：EXAMPLE-NOT-ROLE\n实际情况：系统：OA；角色：员工；功能：查询；权限：查询；RPO：1小时")
            self.assertNotIn("EXAMPLE-NOT-FACT", md)
            self.assertNotIn("EXAMPLE-NOT-ROLE", md)
            self.assertIn("OA", md)

    def test_jurisdiction_is_explicit_and_per_object_not_filename_or_site_guess(self):
        md = draft("it-app", "【用户上传：SG-system.md】\n系统：新加坡办公室平台；使用现场：苏州项目；角色：员工；功能：查询")
        self.assertIn("- 辖区：UNSPECIFIED\n", md)
        dual = draft("it-app", "辖区：CN；系统：A；角色：员工；功能：查询\n辖区：SG；系统：B；角色：资料员；功能：导出")
        self.assertIn("- 辖区：DUAL\n", dual)
        records = self.records("it-app", "辖区：CN；系统：A；角色：员工；功能：查询\n辖区：SG；系统：B；角色：资料员；功能：导出")
        self.assertEqual(["CN", "SG"], [r["jurisdiction"] for r in records])

    def test_encoded_table_pipes_remain_one_cell_after_privacy_filter(self):
        for entity in ("&#124;", "&#x7c;", "&vert;"):
            md = draft("it-app", f"| 系统 | 角色 | 功能 |\n| --- | --- | --- |\n| OA | 员工 | 判断 a{entity}b |")
            rows = rows_for(md, "角色与功能需求")
            self.assertEqual(7, len(rows[1]))
            self.assertEqual("判断 a|b", rows[1][3])

    def test_encoded_credentials_remain_excluded_from_all_fields_and_provenance(self):
        for post in ("it-app", "it-ops", "it-data"):
            md = draft(post, '系统：OA\n功能：查询\nＰａｓｓｗｏｒｄ：encoded-secret-alpha\nAPI&#95;key：encoded-secret-bravo\n权限：https://user:encoded-secret-charlie@example.invalid/api')
            for secret in ("encoded-secret-alpha", "encoded-secret-bravo", "encoded-secret-charlie"):
                self.assertNotIn(secret, md)

    def test_bare_endpoint_host_and_ipv6_are_not_safe_peer_names(self):
        for post in ("it-app", "it-ops", "it-data"):
            md = draft(post, "【用户上传：policy.md】\n系统：OA\n对端系统：api.example.invalid\n故障现象：[2001:db8::1] 中断\n数据对象：2001:db8::2\n功能：读取 api.example.invalid\n权限：查询 [2001:db8::3]")
            self.assertNotIn("api.example.invalid", md)
            self.assertNotIn("2001:db8", md)
        rows = self.records("it-app", "【用户上传：policy.md】\n系统：OA；角色：员工；功能：查询")
        self.assertEqual("policy.md", rows[0]["_origin"])

    def test_clock_time_is_not_mistaken_for_ipv6(self):
        md = draft("it-ops", "系统：OA；变更窗口：12:30:00")
        self.assertIn("12:30:00", md)

    def test_ipv6_sentence_punctuation_and_embedded_ipv4_are_redacted(self):
        for address in ("2001:db8::1.", "2001:db8::", "::1", "2001:db8:0:0:0:ffff:192.0.2.1"):
            md = draft("it-app", f"系统：OA\n角色：员工；功能：查询 {address}")
            self.assertNotIn("2001:db8", md)
            self.assertNotIn("::1", md)
            self.assertNotIn("192.0.2.1", md)

    def test_requirement_source_in_professional_matrix_not_unowned_appendix(self):
        md = draft("it-app", "系统：OA；模块：报销；需求编号：REQ-1；角色：出纳；场景：月结；功能：查询；对象：报销单；验收标准：本人部门；需求来源：业务单A；优先级：高")
        rows = rows_for(md, "需求来源与系统模块归属")
        values = dict(zip(rows[0], rows[1]))
        for key, value in (("系统模块", "OA / 报销"), ("需求编号", "REQ-1"), ("角色", "出纳"), ("场景", "月结"), ("数据对象", "报销单"), ("需求来源", "业务单A")):
            self.assertEqual(value, values[key])
        self.assertEqual("", values["验收签认"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
