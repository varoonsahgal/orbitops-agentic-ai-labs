"""OrbitOps LOGISTICS tool Lambda (AgentCore Gateway Lambda target)."""
import json

DELIMITER = "___"

SHIPMENTS = {
    "SHP-1001": {"contents": "Ion Thruster Coils x24 (ION-COIL-7)", "origin": "Ceres Forge Orbital Yard", "destination": "Lunar Gateway Bay 3", "status": "IN_TRANSIT", "current_position": "Trans-lunar injection corridor, 0.62 AU from origin", "eta": "2026-10-23T14:00Z", "carrier": "Hermes Cargo Line", "last_checkpoint": "Mars Relay Station (departed 2026-10-19T03:10Z)"},
    "SHP-1002": {"contents": "Cryo-Pod Seal Rings x500 (CRYO-SEAL-2)", "origin": "Europa Cryogenics Dock", "destination": "Earth Orbit Dock A", "status": "DELAYED", "current_position": "Holding at Jovian transfer point (solar storm advisory)", "eta": "2026-10-28T09:30Z", "carrier": "Hermes Cargo Line", "last_checkpoint": "Jovian transfer point (2026-10-18T22:40Z)"},
    "SHP-1003": {"contents": "Navigation Gyro Assemblies x6 (NAV-GYRO-X)", "origin": "Titan Dynamics Fabrication", "destination": "Lunar Gateway Bay 1", "status": "DELIVERED", "current_position": "Lunar Gateway Bay 1 (unloaded)", "eta": "2026-10-17T18:05Z", "carrier": "Kuiper Express", "last_checkpoint": "Delivered and signed by dock supervisor Rao"},
    "SHP-1004": {"contents": "Hydrazine Fuel Cells x40 (HYDRAZINE-55)", "origin": "Phobos Fuel Co-op", "destination": "Mars Relay Depot", "status": "IN_TRANSIT", "current_position": "Phobos departure lane, 2,400 km out", "eta": "2026-10-20T06:45Z", "carrier": "Kuiper Express", "last_checkpoint": "Phobos Fuel Co-op launch pad (2026-10-19T19:00Z)"},
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
    if tool == "track_shipment":
        sid = str(args.get("shipment_id", "")).upper().strip()
        s = SHIPMENTS.get(sid)
        if not s:
            return {"service": "logistics", "shipment_id": sid, "error": "unknown shipment id", "known_shipments": list(SHIPMENTS)}
        return {"service": "logistics", "shipment_id": sid, **s}
    if tool == "list_shipments":
        status = str(args.get("status", "")).upper().strip()
        rows = [{"shipment_id": k, **v} for k, v in SHIPMENTS.items() if not status or v["status"] == status]
        return {"service": "logistics", "shipments": rows, "count": len(rows)}
    return {"service": "logistics", "warning": f"logistics service received an unknown tool request '{tool}'", "shipments": [{"shipment_id": k, "status": v["status"], "destination": v["destination"]} for k, v in SHIPMENTS.items()]}
