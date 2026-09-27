//! Drive shipped MCP JSON-RPC (resources + prompts + tools discovery).

use civil_workbench::config::Paths;
use civil_workbench::mcp::{self, McpFilter};
use serde_json::{json, Value};

struct Temp(std::path::PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("civil-mcp-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn path(&self) -> &std::path::Path {
        &self.0
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn paths() -> Paths {
    Paths::detect()
}

fn rpc(filter: McpFilter, method: &str, params: Value) -> Value {
    let msg = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    mcp::handle_rpc(&paths(), &filter, msg).expect("response")
}

#[test]
fn initialize_advertises_three_primitives() {
    let r = rpc(
        McpFilter {
            pack: None,
            expert: None,
        },
        "initialize",
        json!({}),
    );
    let caps = &r["result"]["capabilities"];
    assert!(caps.get("tools").is_some(), "{caps}");
    assert!(caps.get("resources").is_some(), "{caps}");
    assert!(caps.get("prompts").is_some(), "{caps}");
}

#[test]
fn bid_parse_resources_are_scoped_and_readable() {
    let filter = McpFilter {
        pack: None,
        expert: Some("bid-parse".into()),
    };
    let listed = rpc(filter.clone(), "resources/list", json!({}));
    let res = listed["result"]["resources"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    assert!(
        res.iter()
            .any(|r| r["uri"].as_str() == Some("kb://bid/bid-parse/web-knowledge.md")),
        "{res:?}"
    );
    // sibling private lib must not appear
    assert!(
        !res.iter()
            .any(|r| r["uri"].as_str().unwrap_or("").contains("bid-tech/")),
        "{res:?}"
    );

    let read = rpc(
        filter.clone(),
        "resources/read",
        json!({"uri": "kb://bid/bid-parse/web-knowledge.md"}),
    );
    let text = read["result"]["contents"][0]["text"].as_str().unwrap_or("");
    assert!(text.contains("招标解析"), "{text}");
    assert!(
        !text.contains("可以投标") || text.contains("不报"),
        "{text}"
    );

    let deny = rpc(
        filter.clone(),
        "resources/read",
        json!({"uri": "kb://bid/bid-tech/outline.md"}),
    );
    let denied = deny["result"]["contents"][0]["text"].as_str().unwrap_or("");
    assert!(denied.starts_with("拒绝"), "{denied}");

    let cross = rpc(
        filter.clone(),
        "resources/read",
        json!({"uri": "kb://construction/method-hazard/outline.md"}),
    );
    let cross_txt = cross["result"]["contents"][0]["text"]
        .as_str()
        .unwrap_or("");
    assert!(cross_txt.starts_with("拒绝"), "{cross_txt}");
    assert!(
        !res.iter()
            .any(|r| r["uri"].as_str().unwrap_or("").contains("method-hazard/")),
        "{res:?}"
    );
}

#[test]
fn prompts_filtered_and_do_not_invent_bid() {
    let filter = McpFilter {
        pack: None,
        expert: Some("bid-parse".into()),
    };
    let listed = rpc(filter.clone(), "prompts/list", json!({}));
    let names: Vec<&str> = listed["result"]["prompts"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|p| p["name"].as_str())
        .collect();
    assert_eq!(names, vec!["civil.bid.parse"]);

    let got = rpc(
        filter,
        "prompts/get",
        json!({
            "name": "civil.bid.parse",
            "arguments": {"tender_text": "工期 90 个日历天", "jurisdiction": "SG"}
        }),
    );
    let text = got["result"]["messages"][0]["content"]["text"]
        .as_str()
        .unwrap_or("");
    assert!(text.contains("90"), "{text}");
    assert!(text.contains("不要判定可投标"), "{text}");
}

#[test]
fn construction_pack_hides_tender_and_hazard_prompt() {
    let filter = McpFilter {
        pack: Some("construction".into()),
        expert: None,
    };
    let listed = rpc(filter.clone(), "tools/list", json!({}));
    let names: Vec<&str> = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|t| t["name"].as_str())
        .collect();
    assert!(names.contains(&"construction__scheme_draft"), "{names:?}");
    assert!(names.contains(&"construction__scan_forbidden"), "{names:?}");
    assert!(
        !names.contains(&"tender.parse") && !names.iter().any(|n| *n == "bid-parse__extract"),
        "{names:?}"
    );
    assert!(!names.contains(&"pack-ship__plan"), "{names:?}");
    assert!(!names.contains(&"method-hazard__judge_hazard"), "{names:?}");

    let prompts = rpc(filter.clone(), "prompts/list", json!({}));
    let pnames: Vec<&str> = prompts["result"]["prompts"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|p| p["name"].as_str())
        .collect();
    assert_eq!(pnames, vec!["civil.construction.scheme"]);
    assert!(!pnames
        .iter()
        .any(|n| n.contains("judge") || n.contains("hazard")));

    let got = rpc(
        filter,
        "prompts/get",
        json!({"name": "civil.method-hazard.judge", "arguments": {}}),
    );
    let desc = got["result"]["description"].as_str().unwrap_or("");
    assert!(desc.contains("拒绝"), "{got}");
}

#[test]
fn tool_arguments_cannot_approve_or_escape_the_launch_scope() {
    let temp = Temp::new();
    let mut isolated = paths();
    isolated.out_root = temp.path().join("out");
    let filter = McpFilter {
        pack: None,
        expert: Some("construction".into()),
    };
    let listed =
        mcp::handle_rpc(&isolated, &filter, json!({"id":1,"method":"tools/list"})).unwrap();
    for tool in listed["result"]["tools"].as_array().unwrap() {
        assert!(tool.pointer("/inputSchema/properties/confirm_ok").is_none());
        assert!(tool
            .pointer("/inputSchema/properties/confirm_text")
            .is_none());
    }
    for args in [
        json!({"brief":"synthetic draft", "confirm_ok":true}),
        json!({"brief":"synthetic draft", "confirm_text":"我明白，将由持证人员签认"}),
        json!({"brief":"synthetic draft", "p0_confirmed":true}),
        json!({"brief":"synthetic draft"}),
        json!({"brief":"synthetic draft", "expert_id":"finance-tax"}),
    ] {
        let result = mcp::handle_rpc(
            &isolated,
            &filter,
            json!({"id":2,"method":"tools/call",
            "params":{"name":"construction__scheme_draft", "arguments":args}}),
        )
        .unwrap();
        assert_eq!(result["result"]["isError"], true, "{result}");
        assert!(
            !isolated.out_root.exists(),
            "refused calls must create no output"
        );
    }
    let cross = mcp::handle_rpc(
        &isolated,
        &filter,
        json!({"id":3,"method":"tools/call",
        "params":{"name":"bid-parse__extract","arguments":{"expert_id":"bid-parse"}}}),
    )
    .unwrap();
    assert_eq!(cross["result"]["isError"], true, "{cross}");
    assert!(!isolated.out_root.exists());
}

#[test]
fn low_risk_tools_cannot_create_a_session_outside_the_output_root() {
    let temp = Temp::new();
    let mut isolated = paths();
    isolated.out_root = temp.path().join("out");
    let filter = McpFilter {
        pack: None,
        expert: Some("bid-parse".into()),
    };
    for session in ["../escaped", "..\\escaped", "C:/escaped", "/escaped", ""] {
        let result = mcp::handle_rpc(
            &isolated,
            &filter,
            json!({"id":1,"method":"tools/call",
            "params":{"name":"bid-parse__extract", "arguments":{"session_id":session}}}),
        )
        .unwrap();
        assert_eq!(result["result"]["isError"], true, "{result}");
        assert!(!isolated.out_root.exists());
    }
}

#[test]
fn tool_reads_match_resource_scope_and_do_not_import_arbitrary_paths() {
    let temp = Temp::new();
    let mut isolated = paths();
    isolated.out_root = temp.path().join("out");
    let filter = McpFilter {
        pack: None,
        expert: Some("bid-parse".into()),
    };
    for (tool, args) in [
        ("read_kb", json!({"path":"bid/bid-tech/outline.md"})),
        (
            "import_local",
            json!({"path":temp.path().join("private.txt")}),
        ),
        (
            "firm__bid_pack",
            json!({"path":temp.path().join("private.txt")}),
        ),
    ] {
        let result = mcp::handle_rpc(
            &isolated,
            &filter,
            json!({"id":1,"method":"tools/call",
            "params":{"name":tool,"arguments":args}}),
        )
        .unwrap();
        assert_eq!(result["result"]["isError"], true, "{result}");
        assert!(!isolated.out_root.exists());
    }
    let foreign = temp.path().join("private.xlsx");
    std::fs::write(&foreign, b"synthetic private bytes").unwrap();
    let result = mcp::handle_rpc(
        &isolated,
        &McpFilter {
            pack: None,
            expert: Some("pack-ship".into()),
        },
        json!({"id":2,"method":"tools/call","params":{"name":"pack-ship__plan",
            "arguments":{"materials":foreign.to_string_lossy()}}}),
    )
    .unwrap();
    assert_eq!(result["result"]["isError"], true, "{result}");
    assert_eq!(std::fs::read(&foreign).unwrap(), b"synthetic private bytes");
    assert!(!isolated.out_root.exists());
}

#[cfg(unix)]
#[test]
fn output_directory_and_file_links_cannot_escape() {
    use std::os::unix::fs::symlink;
    let temp = Temp::new();
    let mut isolated = paths();
    isolated.out_root = temp.path().join("out");
    std::fs::create_dir_all(&isolated.out_root).unwrap();
    let external = temp.path().join("external");
    std::fs::create_dir(&external).unwrap();
    let filter = McpFilter {
        pack: None,
        expert: Some("bid-parse".into()),
    };
    let call = || {
        mcp::handle_rpc(
            &isolated,
            &filter,
            json!({"id":1,"method":"tools/call",
        "params":{"name":"write_deliverable","arguments":{"session_id":"link-test",
        "filename":"draft.md","markdown":"must not write"}}}),
        )
        .unwrap()
    };
    let session = isolated.out_root.join("link-test");
    symlink(&external, &session).unwrap();
    assert_eq!(call()["result"]["isError"], true);
    assert_eq!(std::fs::read_dir(&external).unwrap().count(), 0);
    std::fs::remove_file(&session).unwrap();
    std::fs::create_dir_all(session.join("bid-parse")).unwrap();
    let protected = external.join("protected.txt");
    std::fs::write(&protected, "unchanged").unwrap();
    symlink(&protected, session.join("bid-parse/draft.md")).unwrap();
    assert_eq!(call()["result"]["isError"], true);
    assert_eq!(std::fs::read_to_string(&protected).unwrap(), "unchanged");
}
