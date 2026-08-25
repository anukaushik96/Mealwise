"""Mealwise - grocery ordering over the Swiggy Instamart MCP server.

The package is arranged in three layers, and nothing in a lower layer
imports from a higher one:

    transport   swiggy_mcp, swiggy_auth   talk to Swiggy: JSON-RPC, OAuth
    engine      money, parse_order,       decide what to order and what it
                instamart, recipe         costs - no I/O of its own beyond
                                          the transport layer
    interface   web_app + web_ui, cli     two front ends over one engine

Read docs/instamart-notes.md before changing anything that touches the
Instamart API: it records where the published docs are wrong.
"""

__version__ = "0.1.0"
