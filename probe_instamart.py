"""Probe the Swiggy Instamart MCP server and record exactly what each tool returns.

Read-only by default. Nothing here mutates a cart or places an order:
update_cart / clear_cart / checkout are deliberately NOT called.

  python3 probe_instamart.py                 # schemas + read-only calls
  python3 probe_instamart.py --query "milk"  # use a different search term
  python3 probe_instamart.py --schemas-only  # tools/list only, no tool calls

Raw JSON for every call lands in probe_out/ so we can diff against the docs.
"""

import argparse
import json
import os
import re
import sys

import swiggy_auth
from swiggy_mcp import McpError, SwiggyMcp

# Dumps contain addresses, phone numbers and order history, so they must NOT
# land in the project folder - this repo is shared. swiggy_auth owns the
# per-user location under the OS user's home directory.
OUT_DIR = swiggy_auth.probe_dir()

# Tools that only read. Anything mutating stays off this list on purpose.
READ_ONLY = {
    "get_addresses", "search_products", "your_go_to_items", "get_cart",
    "get_payment_options", "get_orders", "get_order_details",
    "track_order", "get_delivery_status", "list_coupons",
}
MUTATING = {"update_cart", "clear_cart", "checkout", "confirm_order",
            "create_address", "delete_address", "apply_coupon", "report_error"}


def save(name, payload):
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    path = os.path.join(OUT_DIR, "%s.json" % name)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def show(label, payload, depth=None):
    print("\n" + "=" * 72)
    print(label)
    print("=" * 72)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    lines = text.splitlines()
    limit = depth if depth is not None else 60
    print("\n".join(lines[:limit]))
    if len(lines) > limit:
        print("... [%d more lines - full JSON in probe_out/]" % (len(lines) - limit))


def parse_addresses(payload):
    """Extract (label, id) pairs from get_addresses.

    get_addresses returns NO JSON - only prose lines like
    "1. [Home 2] Anu: 818 H ... (ID: d0n03p494bshq3uh2hj0)".
    So we parse the prose. If a future version adds a JSON body, prefer it.
    """
    addresses = (payload.get("data") or {}).get("addresses") if isinstance(payload, dict) else None
    if addresses:
        out = []
        for entry in addresses:
            if isinstance(entry, dict):
                aid = entry.get("id") or entry.get("addressId")
                if aid:
                    out.append((entry.get("annotation") or entry.get("name") or "?", str(aid)))
        if out:
            return out, "json"

    text = (payload or {}).get("_text", "") if isinstance(payload, dict) else ""
    pairs = re.findall(r"\[([^\]]+)\][^\n]*?\(ID:\s*([A-Za-z0-9]+)\)", text)
    return [(label.strip(), aid) for label, aid in pairs], "prose"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="milk", help="search_products query")
    parser.add_argument("--schemas-only", action="store_true")
    parser.add_argument("--force-login", action="store_true")
    args = parser.parse_args()

    token = swiggy_auth.login(force=args.force_login)
    client = SwiggyMcp(token, server="instamart")

    info = client.initialize()
    show("initialize -> serverInfo / capabilities", info)
    save("00_initialize", info)
    print("\nMcp-Session-Id: %s" % client.session_id)

    tools = client.list_tools()
    save("01_tools_list", tools)
    print("\n%d tools advertised by mcp.swiggy.com/im:\n" % len(tools))
    for tool in sorted(tools, key=lambda t: t.get("name", "")):
        schema = tool.get("inputSchema", {}) or {}
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        sig = ", ".join(
            "%s%s:%s" % (n, "" if n in required else "?", (p or {}).get("type", "any"))
            for n, p in props.items()
        )
        flag = "MUT " if tool.get("name") in MUTATING else "read"
        print("  [%s] %-22s (%s)" % (flag, tool.get("name"), sig or "no args"))

    if args.schemas_only:
        print("\nSchemas written to %s/01_tools_list.json" % OUT_DIR)
        return 0

    advertised = {t.get("name") for t in tools}

    def probe(name, arguments, label=None, depth=None):
        if name not in advertised:
            print("\n[skip] %s is not advertised by the server" % name)
            return None
        if name not in READ_ONLY:
            print("\n[skip] %s is mutating - not called by this probe" % name)
            return None
        try:
            result = client.call_tool(name, arguments)
        except McpError as exc:
            print("\n[error] %s -> %s" % (name, exc))
            save("err_%s" % name, {"error": str(exc), "payload": exc.payload})
            return None
        show(label or ("%s(%s)" % (name, json.dumps(arguments))), result, depth)
        save(name, result)
        return result

    addresses_res = probe("get_addresses", {}, depth=25)
    address_id = None
    if isinstance(addresses_res, dict):
        found, source = parse_addresses(addresses_res)
        print("\n>> parsed %d addresses from the %s body" % (len(found), source))
        for label, aid in found[:5]:
            print("     %-16s %s" % (label, aid))
        if found:
            address_id = found[0][1]
            print(">> using addressId=%s (%s)" % (address_id, found[0][0]))

    if address_id:
        probe("search_products", {"addressId": address_id, "query": args.query}, depth=90)
        probe("your_go_to_items", {"addressId": address_id}, depth=40)
    else:
        print("\n[skip] search_products / your_go_to_items need an addressId")

    probe("get_cart", {})
    probe("get_payment_options", {})
    probe("get_orders", {})

    print("\nDone. Raw JSON for every call is in %s/" % OUT_DIR)
    print("(kept out of the project folder on purpose - it holds your addresses"
          " and order history)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (swiggy_auth.AuthError, McpError) as exc:
        print("\nFAILED: %s" % exc)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
