/* Shared file-inventory validation. A partial list never proves a source was deleted. */
(function (root) {
  "use strict";
  const messages = {
    "本次文件列表可能不完整（最多 {count} 个文件、目录深度 {depth}）。如需更多资料，请为较小的工程目录启动实例；此前资料可手动保留或取消选择。": "This file list may be incomplete (up to {count} files and folder depth {depth}). To include more materials, start an instance for a smaller project folder; you can manually keep or deselect previous choices.",
    "已达到目录扫描上限；如需更多资料，请为较小的工程目录启动实例。": "The folder scan limit was reached. To include more materials, start an instance for a smaller project folder.",
    "已保留此前选择：{path}。本次部分列表尚未核对到它；执行或保存时服务器仍会校验来源。": "Previous selection retained: {path}. This partial list has not confirmed it; the server will still validate the source when you run or save.",
    "文件列表响应无法核对，已保留当前选择。请重试刷新。": "The file-list response could not be validated. Current selections were retained. Please refresh again.",
    "完整列表中已找不到这些来源，已取消选择：{paths}。": "These sources are absent from the complete list and were deselected: {paths}."
  };
  const plain = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const safePath = value => typeof value === "string" && value.length > 0 && value.length <= 1024 && !/[\\:\u0000-\u001f\u007f]/.test(value)
    && value.split("/").every(part => part && part !== "." && part !== ".." && !part.startsWith("."));
  function validate(data, workspace, purpose) {
    const bad = () => { throw new Error("文件列表响应无法核对，已保留当前选择。请重试刷新。"); };
    if (!plain(data) || data.schema_version !== 1 || data.workspace !== workspace || data.purpose !== purpose
      || !["agent", "project_control"].includes(purpose) || !Array.isArray(data.files) || data.files.length > 500
      || data.returned_count !== data.files.length || !plain(data.limits) || data.limits.max_files !== 500
      || data.limits.max_directory_depth !== 5 || data.limits.max_scanned_entries !== 10000) bad();
    const keys = ["file_limit_reached", "depth_limit_reached", "scan_limit_reached"];
    if (typeof data.complete !== "boolean" || keys.some(key => typeof data[key] !== "boolean")) bad();
    const expected = keys.filter(key => data[key]).map(key => key.replace("_reached", ""));
    if (data.complete !== (expected.length === 0) || !Array.isArray(data.incomplete_reasons)
      || data.incomplete_reasons.length !== expected.length || expected.some(key => !data.incomplete_reasons.includes(key))
      || new Set(data.incomplete_reasons).size !== data.incomplete_reasons.length
      || data.file_limit_reached !== (data.files.length === data.limits.max_files)) bad();
    const paths = new Set(), formats = purpose === "project_control" ? /\.(pdf|xlsx|docx|txt|md|csv|json|ifc|dxf)$/i : /\.(pdf|xlsx|docx|txt|md|csv|json)$/i;
    for (const file of data.files) {
      if (!plain(file) || !safePath(file.path) || !formats.test(file.path) || paths.has(file.path)
        || typeof file.name !== "string" || !file.name || file.name.length > 1024
        || !Number.isSafeInteger(file.size) || file.size < 0) bad();
      paths.add(file.path);
    }
    return data;
  }
  function notice(report, t) {
    if (!report || report.complete) return "";
    const text = t("本次文件列表可能不完整（最多 {count} 个文件、目录深度 {depth}）。如需更多资料，请为较小的工程目录启动实例；此前资料可手动保留或取消选择。", { count: report.limits.max_files, depth: report.limits.max_directory_depth });
    return text + (report.scan_limit_reached ? " " + t("已达到目录扫描上限；如需更多资料，请为较小的工程目录启动实例。") : "");
  }
  const retained = (path, t) => t("已保留此前选择：{path}。本次部分列表尚未核对到它；执行或保存时服务器仍会校验来源。", { path });
  const failure = t => t("文件列表响应无法核对，已保留当前选择。请重试刷新。");
  const api = Object.freeze({ validate, notice, retained, failure, messages, safePath });
  root.CBFileListing = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
