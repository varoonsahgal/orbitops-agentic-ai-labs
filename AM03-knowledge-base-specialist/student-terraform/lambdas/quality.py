"""OrbitOps QUALITY tool Lambda (AgentCore Gateway Lambda target)."""
import json

DELIMITER = "___"

INSPECTIONS = {
    "INS-501": {"shipment_id": "SHP-1003", "item": "Navigation Gyro Assembly", "status": "PASSED", "inspector": "QC Droid Theta-9", "findings": "Calibration drift within 0.02 deg tolerance", "inspected_at": "2026-10-17T20:15Z"},
    "INS-502": {"shipment_id": "SHP-1001", "item": "Ion Thruster Coil", "status": "PENDING", "inspector": "QC Droid Theta-9", "findings": "Awaiting arrival at Lunar Gateway Bay 3", "inspected_at": None},
    "INS-503": {"shipment_id": "SHP-1002", "item": "Cryo-Pod Seal Ring", "status": "FAILED", "inspector": "QC Droid Sigma-4", "findings": "Seal batch B-77 shows micro-fractures; quarantine and request replacement from Europa Cryogenics", "inspected_at": "2026-10-16T11:00Z"},
    "INS-504": {"shipment_id": "SHP-1004", "item": "Hydrazine Fuel Cell 55L", "status": "PENDING", "inspector": "QC Droid Sigma-4", "findings": "Pressure test scheduled on arrival", "inspected_at": None},
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
    if tool == "get_inspection_status":
        key = str(args.get("inspection_id") or args.get("shipment_id") or "").upper().strip()
        if key in INSPECTIONS:
            return {"service": "quality", "inspection_id": key, **INSPECTIONS[key]}
        rows = [{"inspection_id": k, **v} for k, v in INSPECTIONS.items() if v["shipment_id"] == key]
        if rows:
            return {"service": "quality", "shipment_id": key, "inspections": rows}
        return {"service": "quality", "error": f"no inspection found for '{key}'", "known_inspection_ids": list(INSPECTIONS)}
    if tool == "list_open_inspections":
        rows = [{"inspection_id": k, **v} for k, v in INSPECTIONS.items() if v["status"] != "PASSED"]
        return {"service": "quality", "open_inspections": rows, "count": len(rows)}
    return {"service": "quality", "warning": f"quality service received an unknown tool request '{tool}'", "inspections": [{"inspection_id": k, "status": v["status"], "shipment_id": v["shipment_id"]} for k, v in INSPECTIONS.items()]}
