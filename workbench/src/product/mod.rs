//! Unified product host. Deterministic workers never own user sessions or model loops.
pub mod providers;
pub mod worker;
pub mod api;
pub mod tools;
pub mod file_listing;
pub mod controlled_sources;
pub mod agent;
pub mod domains;
pub mod engineering;
pub mod packing;
pub mod auth;
pub mod project_control;
pub mod excel;
