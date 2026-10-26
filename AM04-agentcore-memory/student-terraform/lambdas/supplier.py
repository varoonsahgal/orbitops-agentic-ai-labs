"""OrbitOps SUPPLIER tool Lambda (AgentCore Gateway Lambda target)."""
import json

DELIMITER = "___"

SUPPLIERS = {
    "SUP-CERES": {"name": "Ceres Forge Ltd", "category": "propulsion", "skus": ["ION-COIL-7"], "lead_time_days": 21, "rating": "A", "contact": "procurement@ceresforge.example", "contract_expires": "2027-03-31"},
    "SUP-TITAN": {"name": "Titan Dynamics", "category": "navigation", "skus": ["NAV-GYRO-X"], "lead_time_days": 45, "rating": "B", "contact": "orders@titandynamics.example", "contract_expires": "2026-12-31"},
    "SUP-EUROPA": {"name": "Europa Cryogenics", "category": "life-support", "skus": ["CRYO-SEAL-2"], "lead_time_days": 10, "rating": "A", "contact": "sales@europacryo.example", "contract_expires": "2027-06-30"},
    "SUP-PHOBOS": {"name": "Phobos Fuel Co-op", "category": "fuel", "skus": ["HYDRAZINE-55"], "lead_time_days": 30, "rating": "C", "contact": "dispatch@phobosfuel.example", "contract_expires": "2026-11-15"},
    "SUP-ARMOR": {"name": "Orbital Armor Works", "category": "structural", "skus": ["HULL-PATCH-K"], "lead_time_days": 14, "rating": "A", "contact": "support@orbitalarmor.example", "contract_expires": "2027-01-31"},
}


def _tool_name(event, context):
    try:
        full = context.client_context.custom["bedrockAgentCoreToolName"]
    except Exception:
        full = (event or {}).get("tool", "")
    return full.split(DELIMITER, 1)[1] if DELIMITER in full else full


def handler(event, context):
    tool = _tool_name(event, context)
    args = event if isinstance(event, dict) else {}
    if tool == "get_supplier":
        key = str(args.get("supplier_id") or args.get("sku") or "").upper().strip()
        if key in SUPPLIERS:
            return {"service": "supplier", "supplier_id": key, **SUPPLIERS[key]}
        for sid, s in SUPPLIERS.items():
            if key in s["skus"]:
                return {"service": "supplier", "supplier_id": sid, **s}
        return {"service": "supplier", "error": f"no supplier found for '{key}'", "known_supplier_ids": list(SUPPLIERS)}
    if tool == "list_suppliers":
        cat = str(args.get("category", "")).lower().strip()
        rows = [{"supplier_id": k, **v} for k, v in SUPPLIERS.items() if not cat or v["category"] == cat]
        return {"service": "supplier", "suppliers": rows, "count": len(rows)}
    return {"service": "supplier", "warning": f"supplier service received an unknown tool request '{tool}'", "suppliers": [{"supplier_id": k, "name": v["name"], "category": v["category"]} for k, v in SUPPLIERS.items()]}
