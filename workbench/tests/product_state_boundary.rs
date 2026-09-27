use civil_workbench::product::auth::InstanceAuth;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, sync::Arc};

struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("civil-storage-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn dir(&self, name: &str) -> PathBuf {
        let root = self.0.join(name);
        std::fs::create_dir_all(&root).unwrap();
        root
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn identity(user: &str, root: PathBuf) -> Arc<InstanceAuth> {
    InstanceAuth::named(
        user,
        &format!("{:x}", Sha256::digest(b"synthetic-state-test-token")),
        vec![root],
        None,
    )
    .unwrap()
}

#[test]
fn each_identity_requires_nonoverlapping_state_and_source_storage() {
    let temp = Temp::new();
    let job = temp.dir("job");
    assert!(identity("alice", job.clone())
        .claim_state(&temp.dir("job/state"))
        .is_err());
    assert!(identity("alice", job).claim_state(&temp.0).is_err());
    assert!(
        !temp.0.join("identity.sqlite").exists(),
        "rejected ownership must not create a state binding"
    );
}

#[test]
fn state_may_not_be_created_inside_or_around_another_owned_workspace() {
    let temp = Temp::new();
    let alice = temp.dir("alice-job");
    identity("alice", alice.clone())
        .claim_state(&temp.dir("alice-state"))
        .unwrap();
    let bob = identity("bob", temp.dir("bob-job"));
    assert!(bob
        .claim_state(&temp.dir("alice-job/private-state"))
        .is_err());
    assert!(bob.claim_state(&alice).is_err());
    let enclosing = temp.dir("enclosing");
    identity("carol", temp.dir("enclosing/carol-job"))
        .claim_state(&temp.dir("carol-state"))
        .unwrap();
    assert!(bob.claim_state(&enclosing).is_err());
}

#[test]
fn previously_created_state_cannot_be_selected_as_or_nested_in_any_workspace() {
    let temp = Temp::new();
    let state = temp.dir("private-tree/alice-state");
    identity("alice", temp.dir("alice-job"))
        .claim_state(&state)
        .unwrap();
    for (i, workspace) in [
        state.clone(),
        temp.dir("private-tree/alice-state/data"),
        temp.dir("private-tree"),
    ]
    .into_iter()
    .enumerate()
    {
        assert!(identity("bob", workspace)
            .claim_state(&temp.dir(&format!("bob-state-{i}")))
            .is_err());
    }
    assert!(identity("bob", temp.dir("bob-job"))
        .claim_state(&temp.dir("private-tree/alice-state/other"))
        .is_err());
    // Sibling state directories remain independent and can share an unowned parent.
    identity("bob", temp.dir("bob-job"))
        .claim_state(&temp.dir("private-tree/bob-state"))
        .unwrap();
}
