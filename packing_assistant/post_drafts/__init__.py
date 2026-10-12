"""Dedicated post renderers, loaded only for the requested expert.

Renderers return Markdown and perform no I/O. The expert runtime owns approval,
content checks, sandboxed persistence and Office export for every post.
"""

from __future__ import annotations

from importlib import import_module

_MODULES = {
    "env": "site_records",
    "supervision": "site_records",
    "survey": "site_records",
    "cost": "business_observations",
    "variation": "business_observations",
    "finance-book": "business_observations",
    "finance-tax": "business_observations",
    "construction": "site_briefs",
    "emergency": "site_briefs",
    "worker-brief": "site_briefs",
    "method-hazard": "hazard",
    "claim": "claim",
    "subcontract": "subcontract",
    "safety-brief": "safety_brief",
    "dispatch": "dispatch",
    "interim": "interim",
    "material-site": "site_materials",
    "quality": "quality",
    "finance-fund": "funding",
    "equip": "equipment",
    "plan-master": "planning",
    "plan-lookahead": "planning",
    "plan-resource": "planning",
    "lab-sample": "lab",
    "lab-record": "lab",
    "lab-mix": "lab",
    "proc-plan": "procurement",
    "proc-compare": "procurement",
    "proc-vendor": "procurement",
    "hr-recruit": "hr_observations",
    "hr-labor": "hr",
    "hr-train": "hr",
    "admin-doc": "admin",
    "admin-office": "admin",
    "it-ops": "it",
    "it-data": "it",
    "it-app": "it",
    "bim-coord": "bim",
    "bim-qto": "bim",
    "bim-deliver": "bim",
    "architecture": "design_basic",
    "structure": "design_basic",
    "geotech": "design_basic",
    "facade": "design_basic",
    "plumbing": "design_services",
    "hvac": "design_services",
    "electrical": "design_services",
    "fire-protect": "design_services",
    "steel": "design_services",
    "landscape": "design_specialties",
    "interior": "design_specialties",
    "intel-weak": "design_specialties",
    "civil-defense": "design_specialties",
    "hydraulic": "design_specialties",
    "port": "design_infrastructure",
    "municipal": "design_infrastructure",
    "bridge": "design_infrastructure",
    "tunnel": "design_infrastructure",
    "traffic": "design_infrastructure",
    "design-coord": "design_infrastructure",
}


def dedicated_posts() -> frozenset[str]:
    return frozenset(_MODULES)


def build_draft(expert_id: str, tool_name: str, text: str) -> str | None:
    module_name = _MODULES.get(expert_id)
    if module_name is None:
        return None
    module = import_module(f"{__name__}.{module_name}")
    markdown = module.build_draft(expert_id, tool_name, text)
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError(f"No dedicated draft for {expert_id}/{tool_name}")
    return markdown
