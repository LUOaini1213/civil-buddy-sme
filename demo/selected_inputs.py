"""Turn-scoped copies of explicitly selected binary attachments.

Uploads retain .bin originals; deterministic file tools need the shown extension.
Never expose the whole upload directory or an unselected attachment as job input.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re

from packing_assistant.office_job import job_root_scope
from packing_assistant.runtime.civil_config import load_config
from packing_assistant.sandbox import assert_open, assert_write, guarded_write_bytes
from uploads import MAX_BYTES, MAX_FILES, UploadError, safe_filename, safe_session_id, upload_file


class SelectedInputError(ValueError):
    """A correctable input-selection error, not a model or tool failure."""

    def __init__(self, code: str, message: str, english: str):
        super().__init__(message)
        self.code, self.english = code, english

    def reply(self, locale: str) -> str:
        return (self.english + " No calculation was started and no deliverables were generated." if locale == "en"
                else str(self) + "；本轮未启动计算，未生成交付物。")


@contextmanager
def selected_job_inputs(root: Path, session: str, run: str, identifiers, *, check=lambda: None):
    """Supply selected originals under shown names in one unique run root.

    Call only on a write-authorised turn after the risk gate. Scratch copies are
    not output artifacts; the returned manifest records the exact input versions.
    """
    if not identifiers or not load_config().allow_write():
        yield []
        return
    sid = safe_session_id(session)
    if not re.fullmatch(r"[a-f0-9]{32}", run or ""):
        raise ValueError("输入副本的运行 id 无效")
    chosen = list(dict.fromkeys(identifiers))
    if len(chosen) > MAX_FILES:
        raise SelectedInputError("too_many_attachments", "本轮选中附件超过上限，请减少到 12 个以内",
                                 "Too many attachments are selected; choose at most 12.")
    sources = []
    names = set()
    for identifier in chosen:
        check()
        try:
            source, name = upload_file(sid, identifier)
        except UploadError as exc:
            raise SelectedInputError("attachment_unavailable", "选中附件已不可用，请重新选择当前会话的附件",
                                     "A selected attachment is unavailable; select it again in this session.") from exc
        if name != safe_filename(name) or name.startswith((".", "_", "~")):
            raise SelectedInputError("unsupported_name", "附件名称不适合文件工具，请改为普通文件名后上传",
                                     "An attachment name is unsuitable for file tools; rename it without a leading dot, underscore or tilde and upload it again.")
        if name.casefold() in names:
            raise SelectedInputError("duplicate_name", "选中附件有同名文件，请只选择本轮要使用的版本",
                                     "Selected attachments have duplicate filenames (case-insensitive); choose only the version to use this turn.")
        names.add(name.casefold())
        checked = assert_open(source)
        if checked.stat().st_size > MAX_BYTES:
            raise SelectedInputError("too_large", "选中附件超过读取上限，请拆分后重新上传",
                                     "A selected attachment exceeds the read limit; split it and upload it again.")
        sources.append((identifier, checked, name))
    base = Path(root).resolve()
    target = base / sid / "execution-inputs" / run
    if target.resolve() != target:
        raise PermissionError("输入副本目录不允许链接跳转")
    assert_write(target / "scope-check.txt")
    target.mkdir(parents=True, exist_ok=False)
    manifest = []
    for identifier, source, name in sources:
        check()
        with source.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise SelectedInputError("too_large", "选中附件超过读取上限，请拆分后重新上传",
                                     "A selected attachment exceeds the read limit; split it and upload it again.")
        guarded_write_bytes(target / name, data)
        manifest.append({"id": identifier, "name": name, "sha256": sha256(data).hexdigest(), "bytes": len(data)})
    check()
    with job_root_scope(target):
        yield manifest
