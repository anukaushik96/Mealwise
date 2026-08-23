"""Mealwise — AI food & spending manager on Swiggy MCP.

Layout:

    mealwise.swiggy    the verified Swiggy MCP client (Food + Instamart)
    mealwise.cli       terminal ordering: login, order, basket, dump_schemas
    mealwise.web       the FastAPI app deployed on Vercel
    mealwise.ai        nutrition estimation and the recommendation engine
"""

from .common import (
    INSTAMART_MINIMUM_INR,
    ORDER_CEILING_INR,
    CartCeilingError,
    address_label,
    read_cart_coords,
    read_cart_total,
)

__all__ = [
    "ORDER_CEILING_INR",
    "INSTAMART_MINIMUM_INR",
    "CartCeilingError",
    "address_label",
    "read_cart_total",
    "read_cart_coords",
]
