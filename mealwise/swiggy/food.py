"""Swiggy Food ordering -- POST mcp.swiggy.com/food

Journey::

    get_addresses -> search_restaurants -> get_restaurant_menu / search_menu
    -> update_food_cart -> get_food_cart -> get_payment_options
    -> place_food_order -> check_payment_status -> confirm_order -> track_food_order

All 14 tool names used here exist on the live server. The response *shapes* have not
been exercised -- only Instamart has.
"""

from __future__ import annotations

from typing import Any, Literal

from ..common import assert_within_ceiling, normalise_addresses
from .envelope import SwiggyIndeterminateOrderError, SwiggyToolError
from .instamart import CheckoutResult
from .payment import PaymentChoice, PaymentOutcome, is_pending_payment, payment_args, settle_payment
from .session import SwiggySession

__all__ = ["FoodOrdering", "FOOD_BINDING"]


class _FoodBinding:
    """Food echoes the geo on BOTH calls, and confirm takes no ``paasId``.

    Omit the geo and the server cannot reconcile the order, so it stays stuck pending.
    """

    def __init__(self, address_id: str, lat: float, lng: float) -> None:
        self._geo = {"addressId": address_id, "lat": lat, "lng": lng}

    def status_args(self, pending: dict[str, Any]) -> dict[str, Any]:
        return {"paasId": pending.get("paasId"), "orderId": pending.get("orderId"), **self._geo}

    def confirm_args(self, pending: dict[str, Any]) -> dict[str, Any]:
        return {"orderId": pending.get("orderId"), **self._geo}


def FOOD_BINDING(address_id: str, lat: float, lng: float) -> _FoodBinding:
    """Food's binding needs the delivery geo, so it is built per order."""
    return _FoodBinding(address_id, lat, lng)


class FoodOrdering:
    def __init__(self, session: SwiggySession) -> None:
        self._session = session

    async def addresses(self) -> list[dict[str, Any]]:
        return normalise_addresses(await self._session.call("get_addresses"))

    async def search_restaurants(self, address_id: str, query: str) -> dict[str, Any]:
        return await self._session.call(
            "search_restaurants", {"addressId": address_id, "query": query}
        )

    async def restaurant_menu(self, restaurant_id: str, address_id: str) -> Any:
        """``addressId`` is required here too, despite the recipe omitting it."""
        return await self._session.call(
            "get_restaurant_menu", {"restaurantId": restaurant_id, "addressId": address_id}
        )

    async def search_menu(self, restaurant_id: str, query: str, address_id: str) -> Any:
        return await self._session.call(
            "search_menu",
            {"restaurantId": restaurant_id, "query": query, "addressId": address_id},
        )

    async def update_cart(
        self,
        restaurant_id: str,
        cart_items: list[dict[str, Any]],
        address_id: str,
        restaurant_name: str | None = None,
        cutlery_opt_in: bool | None = None,
    ) -> Any:
        """Replace the Food cart.

        The parameter is ``cartItems`` (not ``items``), and ``addressId`` is required.

        Each element, per the live schema: ``menu_item_id`` (str) and ``quantity`` (num)
        are required. Optional ``addons``, plus EITHER ``variants`` (legacy:
        ``group_id``/``variation_id``) OR ``variantsV2`` (``group_id`` +
        ``variation_id``, both required) -- an item has one or the other, NEVER both.
        Check which field the ``search_menu`` result used.
        """
        args: dict[str, Any] = {
            "restaurantId": restaurant_id,
            "cartItems": cart_items,
            "addressId": address_id,
        }
        if restaurant_name is not None:
            args["restaurantName"] = restaurant_name
        if cutlery_opt_in is not None:
            args["cutleryOptIn"] = cutlery_opt_in
        return await self._session.call("update_food_cart", args)

    async def get_cart(self) -> dict[str, Any]:
        return await self._session.call("get_food_cart")

    async def flush_cart(self) -> Any:
        """Carts are single-restaurant; switching restaurants flushes."""
        return await self._session.call("flush_food_cart")

    async def list_coupons(self, restaurant_id: str) -> Any:
        return await self._session.call("fetch_food_coupons", {"restaurantId": restaurant_id})

    async def apply_coupon(self, coupon_code: str) -> Any:
        return await self._session.call("apply_food_coupon", {"couponCode": coupon_code})

    async def payment_options(self, address_id: str) -> dict[str, Any]:
        """Food's ``get_payment_options`` DOES take an address, unlike Instamart's."""
        return await self._session.call("get_payment_options", {"addressId": address_id})

    async def place_order(
        self,
        address_id: str,
        payment: PaymentChoice,
        *,
        user_confirmed: Literal[True],
        cart_total: float | None = None,
    ) -> CheckoutResult:
        if user_confirmed is not True:
            raise ValueError("place_order requires explicit user confirmation")
        if cart_total is not None:
            assert_within_ceiling(cart_total, "Food")

        args = {"addressId": address_id, **payment_args(payment)}
        try:
            order = await self._session.call("place_food_order", args)
        except SwiggyToolError:
            raise
        except Exception as err:  # noqa: BLE001
            raise SwiggyIndeterminateOrderError(
                "place_food_order", "get_food_orders", err
            ) from err

        kind = "awaiting-payment" if is_pending_payment(order) else "placed"
        return CheckoutResult(kind, order)

    async def settle(
        self, pending: dict[str, Any], address_id: str, lat: float, lng: float, **kwargs: Any
    ) -> PaymentOutcome:
        return await settle_payment(
            self._session, pending, FOOD_BINDING(address_id, lat, lng), **kwargs
        )

    async def track(self, order_id: str | None = None) -> Any:
        """``track_food_order`` requires NOTHING -- ``orderId`` is optional and there is
        no geo parameter, unlike Instamart's ``track_order``. Omit it for all active
        orders."""
        return await self._session.call("track_food_order", {} if order_id is None else {"orderId": order_id})

    async def orders(self) -> Any:
        return await self._session.call("get_food_orders")

    async def order_details(self, order_id: str) -> Any:
        return await self._session.call("get_food_order_details", {"orderId": order_id})
