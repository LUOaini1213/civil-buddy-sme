"""Offline interchange tests; all schedule data below is explicitly synthetic."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from packing_assistant.engineering import planning_exchange as ex
from packing_assistant.engineering.planning import calculate


def synthetic():
    return {"start_date": "2026-09-18", "calendar": {"weekdays": [0, 1, 2, 3, 4], "holidays": ["2026-09-21"]},
            "resources": [{"id": "crew", "name": "=Synthetic crew", "capacity": 2}], "tasks": [
                {"id": "WBS", "name": "Synthetic WBS", "duration": 0},
                {"id": "A", "name": "=1+1", "duration": 2, "parent_id": "WBS", "progress": 25,
                 "resources": {"crew": 1}, "actual_start": "2026-09-18"},
                {"id": "B", "name": "Synthetic B", "duration": 3, "parent_id": "WBS",
                 "dependencies": [{"task_id": "A", "type": "FS", "lag": 1}], "resources": {"crew": 2}},
                {"id": "C", "name": "Synthetic SS", "duration": 2, "dependencies": [{"task_id": "B", "type": "SS", "lag": -1}]},
                {"id": "D", "name": "Synthetic FF", "duration": 1, "dependencies": [{"task_id": "C", "type": "FF", "lag": 0}]},
                {"id": "M", "name": "Synthetic milestone", "duration": 0, "dependencies": [{"task_id": "D", "type": "SF", "lag": 0}]}]}


def external_one_crew():
    """Independently authored MSPDI: full-time resource is 1.0 (100%).

    Do not build this with the production exporter: paired scaling bugs can
    cancel in a round trip. These literal values express external semantics.
    """
    return b'''<?xml version="1.0" encoding="utf-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <Name>Synthetic external units fixture</Name>
  <StartDate>2026-09-21T08:00:00</StartDate><MinutesPerDay>480</MinutesPerDay><CalendarUID>1</CalendarUID>
  <Calendars><Calendar><UID>1</UID><BaseCalendarUID>-1</BaseCalendarUID><WeekDays>
    <WeekDay><DayType>1</DayType><DayWorking>0</DayWorking></WeekDay>
    <WeekDay><DayType>2</DayType><DayWorking>1</DayWorking></WeekDay>
    <WeekDay><DayType>3</DayType><DayWorking>1</DayWorking></WeekDay>
    <WeekDay><DayType>4</DayType><DayWorking>1</DayWorking></WeekDay>
    <WeekDay><DayType>5</DayType><DayWorking>1</DayWorking></WeekDay>
    <WeekDay><DayType>6</DayType><DayWorking>1</DayWorking></WeekDay>
    <WeekDay><DayType>7</DayType><DayWorking>0</DayWorking></WeekDay>
  </WeekDays></Calendar></Calendars>
  <Tasks><Task><UID>10</UID><Name>Synthetic work</Name><OutlineLevel>1</OutlineLevel>
    <Duration>PT8H0M0S</Duration><DurationFormat>7</DurationFormat>
    <Start>2026-09-21T08:00:00</Start><Finish>2026-09-21T17:00:00</Finish>
  </Task></Tasks>
  <Resources><Resource><UID>20</UID><Name>Synthetic single crew</Name>
    <Type>1</Type><MaxUnits>1.0</MaxUnits><CalendarUID>1</CalendarUID>
  </Resource></Resources>
  <Assignments><Assignment><UID>30</UID><TaskUID>10</TaskUID><ResourceUID>20</ResourceUID><Units>1.0</Units></Assignment></Assignments>
</Project>'''


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        computed = calculate(synthetic())
        self.plan, self.result = computed["plan"], computed["result"]

    def exported(self, format):
        return ex.export_plan(self.plan, self.result, format)["data"]

    def test_all_formats_roundtrip_dates_calendar_relationships_hierarchy_milestone(self):
        dates = {row["id"]: {"start": row["start"], "end": row["end"]} for row in self.result["tasks"]}
        self.assertEqual(dates["A"], {"start": "2026-09-18", "end": "2026-09-22"})
        for format in ("json", "csv", "xlsx", "xml"):
            with self.subTest(format=format):
                source = self.exported(format)
                imported = ex.import_plan(source, "synthetic." + format)
                self.assertEqual(imported["plan"], self.plan)
                self.assertEqual(imported["original_dates"], dates)
                self.assertTrue(imported["requires_confirmation"])
                self.assertEqual(calculate(imported["plan"])["result"]["tasks"], self.result["tasks"])

    def test_spreadsheet_formula_strings_are_literals_and_roundtrip(self):
        from openpyxl import load_workbook
        csv = self.exported("csv").decode("utf-8-sig")
        self.assertIn("'=1+1", csv)
        book = load_workbook(io.BytesIO(self.exported("xlsx")), data_only=False)
        try:
            cell = book["Tasks"]["B3"]
            self.assertEqual(cell.value, "=1+1")
            self.assertEqual(cell.data_type, "s")
            cell.data_type = "f"
            stream = io.BytesIO(); book.save(stream)
            with self.assertRaisesRegex(ValueError, "公式"):
                ex.import_plan(stream.getvalue(), "synthetic.xlsx")
        finally:
            book.close()

    def test_stale_or_missing_calculation_cannot_export(self):
        with self.assertRaisesRegex(ValueError, "不一致"):
            ex.export_plan(self.plan, {}, "json")
        changed = deepcopy(self.plan); changed["tasks"][1]["duration"] += 1
        with self.assertRaisesRegex(ValueError, "不一致"):
            ex.export_plan(changed, self.result, "xml")
        with self.assertRaises(ValueError):
            ex.export_plan(self.plan, self.result, "mpp")

    def test_import_keeps_original_dates_without_recalculating(self):
        raw = json.loads(self.exported("json"))
        raw["original_dates"]["A"] = {"start": "2026-10-01", "end": "2026-10-02"}
        with patch("packing_assistant.engineering.planning.calculate", side_effect=AssertionError("must not calculate")):
            imported = ex.import_plan(json.dumps(raw).encode(), "synthetic.json")
        self.assertEqual(imported["original_dates"]["A"]["start"], "2026-10-01")
        self.assertEqual(imported["plan"]["start_date"], "2026-09-18")

    def test_invalid_source_and_unbounded_inputs_are_rejected(self):
        for data, filename in ((b"", "a.json"), (b"{", "a.json"), (b"NaN", "a.json"), (b"x", "a.xlsx"),
                               (b"x", "a.exe"), (b"a,b\n1,2", "a.csv"), (b"<Project>", "a.xml")):
            with self.subTest(filename=filename, data=data), self.assertRaises(ValueError):
                ex.import_plan(data, filename)
        with patch.object(ex, "MAX_BYTES", 10), self.assertRaises(ValueError):
            ex.import_plan(b" " * 11, "a.json")
        with self.assertRaisesRegex(ValueError, "DTD"):
            ex.import_plan(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///C:/secret">]><Project>&e;</Project>', "a.xml")
        with self.assertRaises(ValueError):
            ex.import_plan(("<a>" * 34 + "</a>" * 34).encode(), "a.xml")
        expanded = io.BytesIO()
        with zipfile.ZipFile(expanded, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("large.xml", b" " * 1001)
        with patch.object(ex, "MAX_EXPANDED", 1000), self.assertRaisesRegex(ValueError, "解压"):
            ex.import_plan(expanded.getvalue(), "a.xlsx")

    def test_external_mspdi_uids_are_stable_and_missing_durations_not_invented(self):
        root = ex._xml(self.exported("xml"))
        # Simulate externally authored XML without our ID extension metadata.
        root.remove(ex._find(root, "ExtendedAttributes"))
        converted = ex.import_plan(ex.ET.tostring(root), "external-synthetic.xml")
        self.assertEqual([row["id"] for row in converted["plan"]["tasks"]], ["T1", "T2", "T3", "T4", "T5", "T6"])
        self.assertEqual(converted["plan"]["resources"][0]["id"], "R1")
        item = ex._all(root, "Tasks/Task")[1]
        item.remove(ex._find(item, "Duration"))
        with self.assertRaisesRegex(ValueError, "缺少明确工期"):
            ex.import_plan(ex.ET.tostring(root), "external-synthetic.xml")

    def test_xml_reports_unmapped_constraints_and_rejects_partial_days(self):
        root = ex._xml(self.exported("xml")); item = ex._all(root, "Tasks/Task")[1]
        ex._tag(item, "ConstraintType", 4); ex._tag(item, "Manual", 1)
        imported = ex.import_plan(ex.ET.tostring(root), "synthetic.xml")
        self.assertIn("unsupported_ConstraintType", {row["code"] for row in imported["report"]})
        ex._find(item, "Duration").text = "PT1H0M0S"
        with self.assertRaisesRegex(ValueError, "不会舍入"):
            ex.import_plan(ex.ET.tostring(root), "synthetic.xml")

    def test_duration_and_lag_units_do_not_confuse_hours_with_elapsed_time(self):
        root = ex._xml(self.exported("xml")); item = ex._all(root, "Tasks/Task")[1]
        ex._find(item, "DurationFormat").text = "5"  # Working hours, per Microsoft MSPDI enum.
        self.assertEqual(ex.import_plan(ex.ET.tostring(root), "synthetic.xml")["plan"], self.plan)
        for value in ("6", "8", "40"):
            ex._find(item, "DurationFormat").text = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "日历时间"):
                ex.import_plan(ex.ET.tostring(root), "synthetic.xml")
        ex._find(item, "DurationFormat").text = "7"
        link = ex._find(ex._all(root, "Tasks/Task")[2], "PredecessorLink")
        ex._find(link, "LagFormat").text = "8"
        with self.assertRaisesRegex(ValueError, "日历时距"):
            ex.import_plan(ex.ET.tostring(root), "synthetic.xml")
        ex._find(link, "LagFormat").text = "7"
        ex._find(link, "LinkLag").text = "not-a-number"
        with self.assertRaisesRegex(ValueError, "有限数值"):
            ex.import_plan(ex.ET.tostring(root), "synthetic.xml")

    def test_duplicate_unknown_ids_and_lag_fraction_are_rejected(self):
        root = ex._xml(self.exported("xml")); tasks = ex._all(root, "Tasks/Task")
        ex._find(tasks[1], "UID").text = "1"
        with self.assertRaisesRegex(ValueError, "重复"):
            ex.import_plan(ex.ET.tostring(root), "synthetic.xml")
        root = ex._xml(self.exported("xml")); link = ex._all(root, "Tasks/Task")[2]
        ex._find(link, "PredecessorLink/LinkLag").text = "1"
        with self.assertRaisesRegex(ValueError, "时距"):
            ex.import_plan(ex.ET.tostring(root), "synthetic.xml")

    def test_export_one_whole_crew_is_100_percent_in_external_mspdi(self):
        source = {"start_date": "2026-09-21", "calendar": {"weekdays": list(range(5)), "holidays": []},
                  "resources": [{"id": "crew", "name": "Synthetic one crew", "capacity": 1}],
                  "tasks": [{"id": "A", "name": "Synthetic work", "duration": 1, "resources": {"crew": 1}}]}
        computed = calculate(source)
        output = ex.export_plan(computed["plan"], computed["result"], "xml")["data"]
        root = ex.ET.fromstring(output)
        # Assert the external XML numeric meaning without calling the importer.
        ns = {"p": "http://schemas.microsoft.com/project"}
        self.assertEqual(root.findtext("p:Resources/p:Resource/p:MaxUnits", namespaces=ns), "1.0")
        self.assertEqual(root.findtext("p:Assignments/p:Assignment/p:Units", namespaces=ns), "1.0")

    def test_external_mspdi_full_units_import_without_percentage_scaling(self):
        imported = ex.import_plan(external_one_crew(), "external-whole-resource.xml")
        self.assertEqual(imported["plan"]["resources"], [{"id": "R20", "name": "Synthetic single crew", "capacity": 1}])
        self.assertEqual(imported["plan"]["tasks"][0]["resources"], {"R20": 1})
        # Multiple whole resources remain a count, with no x100 scaling.
        root = ex.ET.fromstring(external_one_crew())
        ex._find(root, "Resources/Resource/MaxUnits").text = "2.0"
        ex._find(root, "Assignments/Assignment/Units").text = "2.0"
        imported = ex.import_plan(ex.ET.tostring(root), "external-two-resources.xml")
        self.assertEqual(imported["plan"]["resources"][0]["capacity"], 2)
        self.assertEqual(imported["plan"]["tasks"][0]["resources"], {"R20": 2})

    def test_partial_resource_units_are_rejected_without_rounding_or_rescaling(self):
        for path in ("Resources/Resource/MaxUnits", "Assignments/Assignment/Units"):
            for value in ("0.5", "1.5", "0", "10001"):
                root = ex.ET.fromstring(external_one_crew())
                ex._find(root, path).text = value
                with self.subTest(path=path, value=value), self.assertRaisesRegex(ValueError, "整数完整资源"):
                    ex.import_plan(ex.ET.tostring(root), "external-partial-resource.xml")
            for value in ("NaN", "Infinity", "invalid"):
                root = ex.ET.fromstring(external_one_crew())
                ex._find(root, path).text = value
                with self.subTest(path=path, value=value), self.assertRaisesRegex(ValueError, "有限数值"):
                    ex.import_plan(ex.ET.tostring(root), "external-invalid-resource.xml")

    def test_unsupported_resource_types_and_independent_calendars_are_rejected(self):
        for value in ("0", "2", "invalid"):
            root = ex.ET.fromstring(external_one_crew())
            ex._find(root, "Resources/Resource/Type").text = value
            with self.subTest(type=value), self.assertRaisesRegex(ValueError, "不是工作资源"):
                ex.import_plan(ex.ET.tostring(root), "external-resource-type.xml")
        root = ex.ET.fromstring(external_one_crew())
        ex._find(root, "Resources/Resource/CalendarUID").text = "2"
        with self.assertRaisesRegex(ValueError, "独立资源日历"):
            ex.import_plan(ex.ET.tostring(root), "external-resource-calendar.xml")
        # -1 explicitly inherits the project calendar and remains supported.
        ex._find(root, "Resources/Resource/CalendarUID").text = "-1"
        self.assertEqual(ex.import_plan(ex.ET.tostring(root), "external-inherited-calendar.xml")["plan"]["resources"][0]["capacity"], 1)

    def test_missing_mpxj_runtime_is_explicit_and_no_subprocess_starts(self):
        with patch.object(ex, "capabilities", return_value={"mpxj": {"available": False, "reason": "缺少 JVM"}}), patch.object(ex.subprocess, "Popen") as process:
            for suffix in ("mpp", "xer", "pmxml"):
                with self.subTest(suffix=suffix), self.assertRaisesRegex(ImportError, "JVM"):
                    ex.import_plan(b"<P6/>" if suffix == "pmxml" else b"synthetic", "a." + suffix)
            process.assert_not_called()

    def test_capabilities_without_jpype_preserve_native_formats_without_probing_java(self):
        with patch.object(ex.importlib.util, "find_spec", side_effect=lambda name: None if name == "jpype" else object()), \
             patch.object(ex, "_jvm_path") as finder, patch.object(ex.subprocess, "Popen") as process:
            report = ex.capabilities()
        self.assertEqual(report["imports"], ["json", "csv", "xlsx", "xml"])
        self.assertEqual(report["exports"], ["json", "csv", "xlsx", "xml"])
        self.assertFalse(report["mpxj"]["available"])
        self.assertFalse(report["mpxj"]["packages_available"])
        self.assertFalse(report["mpxj"]["jvm_available"])
        self.assertIn("JPype1", report["mpxj"]["reason"])
        finder.assert_not_called()
        process.assert_not_called()

    def test_capabilities_http_without_jvm_report_optional_unavailable_not_500(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from demo.planning_api import router

        # Match JPype's public exception hierarchy without requiring this
        # optional package or a JVM in the regression test environment.
        class JVMNotFoundException(ValueError):
            pass

        class JVMNotSupportedException(ValueError):
            pass

        app = FastAPI()
        app.include_router(router)
        for failure in (JVMNotFoundException("no runtime"), JVMNotSupportedException("wrong runtime")):
            finder = Mock(side_effect=failure)
            with self.subTest(error=type(failure).__name__), \
                 patch.dict(os.environ), \
                 patch.dict(sys.modules, {"jpype": SimpleNamespace(getDefaultJVMPath=finder)}), \
                 patch.object(ex.importlib.util, "find_spec", return_value=object()), \
                 patch.object(ex.Path, "glob", return_value=iter(())), \
                 patch.object(ex.subprocess, "Popen") as process, \
                 TestClient(app) as client:
                os.environ.pop("CIVIL_JAVA_HOME", None)
                response = client.get("/api/engineering/planning/capabilities")
                self.assertEqual(response.status_code, 200, response.text)
                report = response.json()
                self.assertTrue(report["cpm"])
                self.assertEqual(report["formats"]["imports"], ["json", "csv", "xlsx", "xml"])
                self.assertEqual(report["formats"]["exports"], ["json", "csv", "xlsx", "xml"])
                mpxj = report["formats"]["mpxj"]
                self.assertTrue(mpxj["packages_available"])
                self.assertFalse(mpxj["jvm_available"])
                self.assertFalse(mpxj["available"])
                self.assertIn("JVM", mpxj["reason"])
                self.assertFalse(mpxj["native_mpp_export"])
                finder.assert_called_once_with()
                process.assert_not_called()

    def test_capabilities_with_unloadable_jpype_extension_remain_optional(self):
        with patch.object(ex.importlib.util, "find_spec", return_value=object()), \
             patch.object(ex, "_jvm_path", side_effect=ImportError("optional native extension missing")):
            report = ex.capabilities()
        self.assertFalse(report["mpxj"]["available"])
        self.assertFalse(report["mpxj"]["jvm_available"])
        self.assertEqual(report["exports"], ["json", "csv", "xlsx", "xml"])

    def test_resource_schedule_exports_actual_dates_and_rejects_capacity_violation(self):
        from packing_assistant.engineering.planning_optimize import optimize
        plan = {"start_date": "2026-09-18", "calendar": {"weekdays": [0, 1, 2, 3, 4], "holidays": []},
                "resources": [{"id": "crew", "name": "Synthetic", "capacity": 1}],
                "tasks": [{"id": "A", "name": "Synthetic A", "duration": 2, "resources": {"crew": 1}},
                          {"id": "B", "name": "Synthetic B", "duration": 2, "resources": {"crew": 1}}]}
        optimized = optimize(plan)
        dates = {row["id"]: {"start": row["start"], "end": row["end"]} for row in optimized["result"]["tasks"]}
        for format in ("json", "csv", "xlsx", "xml"):
            output = ex.export_plan(optimized["plan"], optimized["result"], format)
            self.assertTrue(output["report"])
            imported = ex.import_plan(output["data"], output["filename"])
            self.assertEqual(imported["original_dates"], dates)
        forged = deepcopy(optimized["result"])
        a, b = forged["tasks"]
        for field in ("start", "end", "start_offset", "finish_offset"):
            b[field] = a[field]
        with self.assertRaisesRegex(ValueError, "资源容量"):
            ex.export_plan(optimized["plan"], forged, "json")

    def test_resource_wbs_finishing_milestone_keeps_summary_end_in_all_formats(self):
        from packing_assistant.engineering.planning_optimize import optimize
        source = {"start_date": "2026-09-21", "calendar": {"weekdays": [0, 1, 2, 3, 4], "holidays": []},
                  "resources": [], "tasks": [
                      {"id": "W", "name": "Synthetic WBS", "duration": 0},
                      {"id": "A", "name": "Synthetic work", "duration": 2, "parent_id": "W"},
                      {"id": "M", "name": "Synthetic finish", "duration": 0, "parent_id": "W",
                       "dependencies": [{"task_id": "A", "type": "FS", "lag": 0}]}]}
        computed = optimize(source)
        rows = {row["id"]: row for row in computed["result"]["tasks"]}
        self.assertEqual((rows["W"]["duration"], rows["W"]["end"], rows["M"]["end"]),
                         (2, "2026-09-23", "2026-09-23"))
        for format in ("json", "csv", "xlsx", "xml"):
            with self.subTest(format=format):
                output = ex.export_plan(computed["plan"], computed["result"], format)
                restored = ex.import_plan(output["data"], output["filename"])
                self.assertEqual(restored["original_dates"]["W"]["end"], "2026-09-23")
                self.assertEqual(restored["original_dates"]["M"]["end"], "2026-09-23")
        forged = deepcopy(computed["result"])
        forged["tasks"][0]["end"] = "2026-09-22"
        with self.assertRaisesRegex(ValueError, "汇总日期"):
            ex.export_plan(computed["plan"], forged, "json")

    def test_last_supported_day_exports_but_later_source_dates_fail(self):
        from packing_assistant.engineering.planning_optimize import optimize
        source = {"start_date": "2100-12-31", "calendar": {"weekdays": list(range(7)), "holidays": []},
                  "resources": [], "tasks": [{"id": "A", "name": "Synthetic final day", "duration": 1}]}
        computed = optimize(source)
        output = ex.export_plan(computed["plan"], computed["result"], "json")
        self.assertEqual(ex.import_plan(output["data"], output["filename"])["original_dates"]["A"]["end"], "2100-12-31")
        raw = json.loads(self.exported("json")); raw["original_dates"]["A"]["end"] = "2101-01-01"
        with self.assertRaisesRegex(ValueError, "日期范围"):
            ex.import_plan(json.dumps(raw).encode(), "synthetic.json")

    @unittest.skipUnless(ex.capabilities()["mpxj"]["available"], "Optional MPXJ/JVM not available")
    def test_mpxj_actual_p6_formats_from_explicit_synthetic_plan(self):
        # Official installed library creates fixtures from our stated inputs;
        # no downloaded application code is executed and no network is used.
        script = """import sys, jpype, jpype.imports, mpxj
from packing_assistant.engineering.planning_exchange import _jvm_path
jpype.startJVM(_jvm_path(), '-Xmx384m')
from org.mpxj.reader import UniversalProjectReader
from org.mpxj.primavera import PrimaveraXERFileWriter, PrimaveraPMFileWriter
from org.mpxj import ActivityStatus
p = UniversalProjectReader().read(sys.argv[1])
for task in p.getTasks():
    task.setPlannedStart(task.getStart())
    task.setPlannedFinish(task.getFinish())
    task.setPlannedDuration(task.getDuration())
    task.setRemainingDuration(task.getDuration())
    task.setActivityStatus(ActivityStatus.NOT_STARTED)
PrimaveraXERFileWriter().write(p, sys.argv[2])
PrimaveraPMFileWriter().write(p, sys.argv[3])
jpype.shutdownJVM()
"""
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / ("synthetic." + suffix) for suffix in ("xml", "xer", "pmxml")]
            source = deepcopy(self.plan)
            for task in source["tasks"]:
                task.update(progress=0, actual_start=None, actual_finish=None)
            computed = calculate(source)
            paths[0].write_bytes(ex.export_plan(computed["plan"], computed["result"], "xml")["data"])
            process = subprocess.run([sys.executable, "-c", script, *map(str, paths)], cwd=ROOT,
                                     capture_output=True, text=True, timeout=30,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(process.returncode, 0, process.stderr)
            for path in paths[1:]:
                imported = ex.import_plan(path.read_bytes(), path.name)
                self.assertEqual(len(imported["plan"]["tasks"]), len(self.plan["tasks"]))
                self.assertEqual(len(imported["original_dates"]), len(self.plan["tasks"]))
                self.assertIn("mpxj_conversion", {row["code"] for row in imported["report"]})
                expected = {row["name"]: row["duration"] for row in computed["plan"]["tasks"]}
                self.assertEqual({row["name"]: row["duration"] for row in imported["plan"]["tasks"]}, expected)

    @unittest.skipUnless(ex.capabilities()["mpxj"]["available"], "Optional MPXJ/JVM not available")
    def test_optional_verified_upstream_binary_mpp_fixture(self):
        import hashlib
        # Local fixture is intentionally excluded from source release. SHA pins
        # MPXJ's synthetic test file; it is not a real construction acceptance.
        tools_root = next((parent for parent in Path(ex._jvm_path()).parents if parent.name == ".tools"), ROOT / ".tools")
        source = Path(os.environ.get("CIVIL_MPXJ_TEST_MPP", str(tools_root / "planning-fixtures/task-links-project2000-mpp9.mpp")))
        if not source.is_file():
            self.skipTest("Pinned upstream MPP fixture not present; no automatic download")
        data = source.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "15673f1358c4f869244e62226322f730dba9f6b097f2e50108f2afae9991b758")
        result = ex.import_plan(data, source.name)
        self.assertEqual(len(result["plan"]["tasks"]), 16)
        self.assertEqual(len(result["original_dates"]), 16)
        self.assertEqual(result["source"]["format"], "mpp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
