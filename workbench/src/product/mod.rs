//! Unified product host. Deterministic workers never own user sessions or model loops.
pub mod providers;
pub mod worker;
pub mod api;
pub mod tools;
pub mod agent;
pub mod domains;
pub mod engineering;
pub mod packing;
pub mod auth;
pub mod project_control;
