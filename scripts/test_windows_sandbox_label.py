"""Actual Windows Modify-only output ACL, label setup and confinement regressions."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packing_assistant.runtime import os_sandbox
from packing_assistant.runtime.os_sandbox import _windows as win


def security(path, information):
    adv, kernel = win._dlls()
    ptr = ctypes.c_void_p
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ptr, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes = [ptr]
    descriptor, output = ptr(), ptr()
    try:
        error = adv.GetNamedSecurityInfoW(str(path), 1, information, None, None, None, None, ctypes.byref(descriptor))
        if error:
            raise OSError(error, "GetNamedSecurityInfo")
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, information, ctypes.byref(output), None):
            raise win._fail("ConvertSecurityDescriptorToStringSecurityDescriptor")
        return ctypes.wstring_at(output)
    finally:
        if output:
            kernel.LocalFree(output)
        if descriptor:
            kernel.LocalFree(descriptor)


def set_test_dacl(path, sddl):
    adv, kernel = win._dlls()
    ptr = ctypes.c_void_p
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPWSTR,
        wintypes.DWORD, ctypes.POINTER(ptr), ctypes.POINTER(wintypes.DWORD)]
    adv.GetSecurityDescriptorDacl.argtypes = [ptr, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ptr), ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ptr, ptr, ptr, ptr]
    kernel.LocalFree.argtypes = [ptr]
    descriptor, acl = ptr(), ptr()
    try:
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise win._fail("ConvertStringSecurityDescriptorToSecurityDescriptor")
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
            raise win._fail("GetSecurityDescriptorDacl")
        error = adv.SetNamedSecurityInfoW(str(path), 1, 4 | 0x80000000, None, None, acl, None)
        if error:
            raise OSError(error, "SetNamedSecurityInfo test DACL")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)


@unittest.skipUnless(sys.platform == "win32", "Windows label/ACL integration requires Windows")
class WindowsLabelTests(unittest.TestCase):
    def setUp(self):
        # The original failure was hidden by C:\Temp's Full Control inheritance.
        # Fixtures live on the repository drive and explicitly lack WRITE_OWNER.
        (ROOT / "output").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="windows-label-test-", dir=ROOT / "output")
        self.addCleanup(self.directory.cleanup)
        self.job = Path(self.directory.name).resolve()
        self.out = self.job / ".civil-buddy" / "out"
        self.out.mkdir(parents=True)
        self.owner = security(self.out, 1).removeprefix("O:")
        self.original = f"D:P(A;;0x1301bf;;;{self.owner})"  # Modify, not Full Control or WRITE_OWNER.
        set_test_dacl(self.out, self.original)

    def test_modify_only_acl_is_repaired_without_full_control_or_inheritance(self):
        parent_before = security(self.out.parent, 4 | 16)
        source = self.job / "original.txt"
        source.write_text("untouched source", encoding="utf-8")
        source_before = security(source, 4 | 16)
        (self.out / ".tmp").mkdir()
        child_before = security(self.out / ".tmp", 4)
        roots = os_sandbox.prepare(self.job)
        dacl = security(self.out, 4)
        # SetEntriesInAcl merges with an existing explicit grant to the same SID.
        self.assertIn(f"(A;;0x1b01bf;;;{self.owner})", dacl)  # exactly Modify | WRITE_OWNER
        self.assertNotIn("FA;", dacl)
        self.assertIn("(ML;OICI;NW;;;LW)", security(self.out, 16))
        self.assertEqual(child_before, security(roots[1], 4), "The new grant must not propagate to a child")
        self.assertEqual(parent_before, security(self.out.parent, 4 | 16))
        self.assertEqual(source_before, security(source, 4 | 16))
        self.assertEqual("untouched source", source.read_text(encoding="utf-8"))
        os_sandbox.prepare(self.job)
        self.assertEqual(dacl, security(self.out, 4), "Repeated startup must not duplicate grants")

    def test_worker_really_confines_writes_and_children_on_modify_only_drive(self):
        with os_sandbox.Worker(self.job) as worker:
            report = worker.confined
            self.assertEqual("allowed", report["selftest"]["write_inside_state"])
            for name in ("write_job_folder", "write_home", "write_system_temp", "spawn_process", "inet_socket"):
                self.assertEqual("denied", report["selftest"][name])
            self.assertTrue(report["enforces"]["write"])
            self.assertTrue(report["enforces"]["spawn"])
            self.assertFalse(report["enforces"]["network"], "Windows network confinement is application-only")

    def test_existing_inheritable_owner_grant_does_not_inherit_new_label_right(self):
        set_test_dacl(self.out, f"D:P(A;OICI;0x1301bf;;;{self.owner})")
        child = self.out / "existing-child"
        child.mkdir()
        before = security(child, 4)
        os_sandbox.prepare(self.job)
        self.assertEqual(before, security(child, 4))
        self.assertIn(f"(A;;WO;;;{self.owner})", security(self.out, 4))
        self.assertIn(f"(A;OICI;0x1301bf;;;{self.owner})", security(self.out, 4))

    def test_explicit_and_inherited_label_denies_are_never_overridden(self):
        for flags in ("", "ID"):
            with self.subTest(flags=flags):
                set_test_dacl(self.out, f"D:P(D;{flags};WO;;;{self.owner})(A;;0x1301bf;;;{self.owner})")
                before = security(self.out, 4 | 16)
                with self.assertRaises(PermissionError):
                    win.label_low(str(self.out))
                self.assertEqual(before, security(self.out, 4 | 16))

    def test_other_owner_refusal_does_not_change_dacl_or_label(self):
        before = security(self.out, 4 | 16)
        adv, kernel = win._dlls()
        with patch.object(adv, "EqualSid", return_value=0), patch.object(win, "_dlls", return_value=(adv, kernel)):
            with self.assertRaises(PermissionError):
                win._grant_output_owner_label_access(str(self.out))
        self.assertEqual(before, security(self.out, 4 | 16))

    def test_permission_repair_rejects_broad_relative_and_file_targets(self):
        file = self.out / "not-a-directory"
        file.write_text("unchanged", encoding="utf-8")
        before = security(self.job, 4 | 16)
        for target in (self.job, self.job.parent, file, Path(".civil-buddy/out")):
            for setup in (win.label_low, win._grant_output_owner_label_access):
                with self.subTest(target=target, setup=setup.__name__), self.assertRaises(PermissionError):
                    setup(str(target))
        self.assertEqual(before, security(self.job, 4 | 16))
        self.assertEqual("unchanged", file.read_text(encoding="utf-8"))

    def test_permission_repair_rejects_reparse_points_before_reading_any_acl(self):
        metadata = SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400)
        for setup in (win.label_low, win._grant_output_owner_label_access):
            with patch.object(Path, "lstat", return_value=metadata), patch.object(win, "_dlls") as dll:
                with self.assertRaises(PermissionError):
                    setup(str(self.out))
                dll.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
