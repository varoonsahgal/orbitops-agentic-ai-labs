"""OrbitOps INVENTORY tool Lambda (AgentCore Gateway Lambda target).

The gateway invokes this function with:
  event   = the tool's input arguments (a JSON object matching the tool schema)
  context.client_context.custom['bedrockAgentCoreToolName'] = '<target-name>___<tool-name>'
"""
import json

DELIMITER = "___"

STOCK = {
    "ION-COIL-7": {"name": "Ion Thruster Coil", "on_hand": 42, "reorder_point": 50, "bay": "Lunar Gateway Bay 3", "unit": "each"},
    "CRYO-SEAL-2": {"name": "Cryo-Pod Seal Ring", "on_hand": 310, "reorder_point": 120, "bay": "Earth Orbit Dock A", "unit": "each"},
    "HYDRAZINE-55": {"name": "Hydrazine Fuel Cell 55L", "on_hand": 8, "reorder_point": 20, "bay": "Mars Relay Depot", "unit": "cell"},
    "HULL-PATCH-K": {"name": "Hull Patch Kit", "on_hand": 97, "reorder_point": 40, "bay": "Earth Orbit Dock B", "unit": "kit"},
    "NAV-GYRO-X": {"name": "Navigation Gyro Assembly", "on_hand": 3, "reorder_point": 6, "bay": "Lunar Gateway Bay 1", "unit": "each"},
}


def _tool_name(event, context):
    try:
        full = context.client_context.custom["bedrockAgentCoreToolName"]
    except Exception:  # direct invoke (tests) -> allow event["tool"]
        full = (event or {}).get("tool", "")
    return full.split(DELIMITER, 1)[1] if DELIMITER in full else full


def _summary():
    return {
        "service": "inventory",
        "total_skus": len(STOCK),
        "stock_levels": [{"sku": k, **v} for k, v in STOCK.items()],
        "low_stock": [k for k, v in STOCK.items() if v["on_hand"] < v["reorder_point"]],
    }


def handler(event, context):
    tool = _tool_name(event, context)
    args = event if isinstance(event, dict) else {}
    if tool == "get_stock_level":
        sku = str(args.get("sku", "")).upper().strip()
        item = STOCK.get(sku)
        if not item:
            return {"service": "inventory", "sku": sku, "error": "unknown SKU", "known_skus": list(STOCK)}
        return {"service": "inventory", "sku": sku, **item, "below_reorder_point": item["on_hand"] < item["reorder_point"]}
    if tool == "list_low_stock":
        low = [{"sku": k, **v} for k, v in STOCK.items() if v["on_hand"] < v["reorder_point"]]
        return {"service": "inventory", "low_stock_items": low, "count": len(low)}
    if tool in ("get_stock_summary", "", None):
        return _summary()
    # Unknown tool for this domain: the inventory droid answers the only way it knows how.
    return {
        "service": "inventory",
        "warning": f"inventory service received an unknown tool request '{tool}' - returning the stock summary instead",
        "requested_arguments": args,
        **_summary(),
    }
