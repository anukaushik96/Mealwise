"""The Mealwise web app — FastAPI, server-rendered.

Deployed as a single Vercel Python function (see api/index.py). Server-rendered rather
than a JS front end so there is one language and one deployable.

One deliberate limitation: UPI payment is NOT polled. `check_payment_status` runs for up
to ~18 minutes and a Vercel function is capped far below that, so the app hands the user
the payment link and stops. Cash-on-delivery completes in-request.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..ai.recommend import recommend
from ..auth import (
    authorize_url,
    create_pkce_pair,
    create_state,
    exchange_code,
    swiggy_subject,
)
from ..budget import compute_budget, month_window
from ..candidates import fetch_candidates, queries_for
from ..common import read_cart_coords, read_cart_total
from ..crypto import encrypt_token
from ..db import execute, fetchrow
from ..session import clear_session, current_user_id, set_session
from ..store import (
    access_token_for,
    import_history,
    nutrition_since,
    spend_by_source,
    spent_since,
    swiggy_session,
)
from ..swiggy import Cash, InstamartOrdering, UpiQr, method_label
from ..targets import DIETS, GOALS, derive_targets, validate_target_input

_HERE = Path(__file__).parent
app = FastAPI(title="Mealwise", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
templates = Jinja2Templates(directory=_HERE / "templates")

# Cookies must be Secure in production but cannot be over plain-HTTP localhost.
_SECURE_COOKIES = bool(__import__("os").environ.get("VERCEL"))

# Short-lived cookies carrying the PKCE verifier and state across the redirect out to
# Swiggy and back. A serverless function keeps no memory between those two requests.
_PKCE_COOKIE = "swiggy_pkce"
_STATE_COOKIE = "swiggy_state"

# The most recent recommendation / cart, held per session in a cookie so the
# server-rendered flow survives a redirect without a client-side store.
_FLASH_COOKIE = "mealwise_flash"


def _flash_read(request: Request) -> dict:
    raw = request.cookies.get(_FLASH_COOKIE)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _flash_write(response, payload: dict) -> None:
    # 4KB is the practical per-cookie ceiling; a large recommendation is truncated to the
    # essentials rather than silently dropped by the browser.
    response.set_cookie(
        _FLASH_COOKIE,
        json.dumps(payload)[:3800],
        max_age=900,
        httponly=True,
        samesite="lax",
        secure=_SECURE_COOKIES,
        path="/",
    )


def _clear_flash(response) -> None:
    response.delete_cookie(_FLASH_COOKIE, path="/")


# ---------------------------------------------------------------- auth


@app.get("/auth/connect")
async def connect() -> RedirectResponse:
    verifier, challenge = create_pkce_pair()
    state = create_state()
    response = RedirectResponse(authorize_url(challenge=challenge, state=state))
    options = dict(max_age=600, httponly=True, samesite="lax", secure=_SECURE_COOKIES, path="/")
    response.set_cookie(_PKCE_COOKIE, verifier, **options)
    response.set_cookie(_STATE_COOKIE, state, **options)
    return response


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    params = request.query_params
    verifier = request.cookies.get(_PKCE_COOKIE)
    expected_state = request.cookies.get(_STATE_COOKIE)

    def fail(reason: str) -> RedirectResponse:
        response = RedirectResponse(f"/?error={reason}")
        # Single-use: clear before anything that can fail, so replaying this URL cannot
        # re-run the exchange.
        response.delete_cookie(_PKCE_COOKIE, path="/")
        response.delete_cookie(_STATE_COOKIE, path="/")
        return response

    if params.get("error"):
        return fail(params["error"])
    code = params.get("code")
    if not code or not verifier:
        return fail("missing_code")
    # CSRF guard: the state we get back must be the one we sent.
    if not params.get("state") or params.get("state") != expected_state:
        return fail("state_mismatch")

    try:
        tokens = await exchange_code(code=code, verifier=verifier)
        subject = swiggy_subject(tokens.access_token)
    except Exception:
        return fail("token_exchange_failed")

    row = await fetchrow(
        """INSERT INTO users (swiggy_sub) VALUES ($1)
           ON CONFLICT (swiggy_sub) DO UPDATE SET swiggy_sub = EXCLUDED.swiggy_sub
           RETURNING id""",
        subject,
    )
    user_id = str(row["id"])

    await execute(
        """INSERT INTO swiggy_tokens (user_id, access_token_enc, refresh_token_enc, expires_at)
           VALUES ($1,$2,$3,$4)
           ON CONFLICT (user_id) DO UPDATE SET
             access_token_enc = EXCLUDED.access_token_enc,
             refresh_token_enc = EXCLUDED.refresh_token_enc,
             expires_at = EXCLUDED.expires_at,
             updated_at = now()""",
        user_id,
        encrypt_token(tokens.access_token),
        encrypt_token(tokens.refresh_token) if tokens.refresh_token else None,
        tokens.expires_at,
    )

    response = RedirectResponse("/")
    response.delete_cookie(_PKCE_COOKIE, path="/")
    response.delete_cookie(_STATE_COOKIE, path="/")
    set_session(response, user_id, secure=_SECURE_COOKIES)
    return response


@app.post("/auth/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/")
    clear_session(response)
    return response


# ---------------------------------------------------------------- pages


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_id = current_user_id(request)
    if not user_id:
        return templates.TemplateResponse(
            request, "connect.html", {"error": request.query_params.get("error")}
        )

    profile = await fetchrow("SELECT * FROM profiles WHERE user_id = $1", user_id)
    if profile is None:
        # Without a profile the dashboard would present invented defaults as if they were
        # the user's targets. Ask instead of showing fiction.
        return RedirectResponse("/onboarding")

    days_in_period, days_elapsed, period_start = month_window()
    today = dt.date.today()

    nutrition = await nutrition_since(user_id, today)
    spent = await spent_since(user_id, period_start)
    by_source = await spend_by_source(user_id, period_start)
    count_row = await fetchrow("SELECT COUNT(*) AS n FROM orders WHERE user_id = $1", user_id)

    budget = compute_budget(
        budget_inr=profile["budget_inr"] or 15000,
        spent_inr=spent,
        days_in_period=days_in_period,
        days_elapsed=days_elapsed,
    )

    flash = _flash_read(request)
    response = templates.TemplateResponse(
        request,
        "home.html",
        {
            "n": nutrition.as_dict(),
            "t": {
                "kcal": profile["target_kcal"],
                "protein_g": profile["target_protein_g"],
                "veg_servings": profile["target_veg_servings"],
                "fruit_servings": profile["target_fruit_servings"],
            },
            "b": budget,
            "by_source": by_source,
            "history_count": int(count_row["n"]) if count_row else 0,
            "low_confidence": nutrition.has_low_confidence,
            "unestimated": len(nutrition.unestimated or []),
            "rec": flash.get("rec"),
            "cart": flash.get("cart"),
            "outcome": flash.get("outcome"),
            "pay_link": flash.get("pay_link"),
            "error": flash.get("error") or request.query_params.get("error"),
        },
    )
    # Flash content is single-use; leaving it would replay a stale cart or outcome.
    if flash:
        _clear_flash(response)
    return response


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_form(request: Request):
    if not current_user_id(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request,
        "onboarding.html",
        {"form": {}, "goals": list(GOALS.items()), "diets": DIETS, "error": None},
    )


@app.post("/onboarding", response_class=HTMLResponse)
async def onboarding_save(
    request: Request,
    age_years: Annotated[str, Form()],
    sex: Annotated[str, Form()],
    height_cm: Annotated[str, Form()],
    weight_kg: Annotated[str, Form()],
    goal: Annotated[str, Form()],
    diet: Annotated[str, Form()] = "",
    allergies: Annotated[str, Form()] = "",
    dislikes: Annotated[str, Form()] = "",
    budget_inr: Annotated[str, Form()] = "15000",
):
    user_id = current_user_id(request)
    if not user_id:
        return RedirectResponse("/")

    form = {
        "age_years": age_years, "sex": sex, "height_cm": height_cm, "weight_kg": weight_kg,
        "goal": goal, "diet": diet, "allergies": allergies, "dislikes": dislikes,
        "budget_inr": budget_inr,
    }

    def reject(message: str):
        return templates.TemplateResponse(
            request,
            "onboarding.html",
            {"form": form, "goals": list(GOALS.items()), "diets": DIETS, "error": message},
            status_code=400,
        )

    problem = validate_target_input(
        age_years=age_years, sex=sex, height_cm=height_cm, weight_kg=weight_kg, goal=goal
    )
    if problem:
        return reject(problem)
    try:
        budget = int(float(budget_inr))
    except ValueError:
        return reject("Budget must be a number.")
    if not 500 <= budget <= 500000:
        return reject("Monthly food budget must be between ₹500 and ₹5,00,000.")

    targets = derive_targets(
        age_years=int(age_years), sex=sex, height_cm=float(height_cm),
        weight_kg=float(weight_kg), goal=goal,
    )
    to_list = lambda raw: [s.strip() for s in raw.split(",") if s.strip()]  # noqa: E731

    await execute(
        """INSERT INTO profiles
             (user_id, age_years, sex, height_cm, weight_kg, goal, diet, allergies,
              dislikes, budget_inr, target_kcal, target_protein_g, target_carbs_g,
              target_fat_g, target_veg_servings, target_fruit_servings, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16, now())
           ON CONFLICT (user_id) DO UPDATE SET
             age_years=EXCLUDED.age_years, sex=EXCLUDED.sex, height_cm=EXCLUDED.height_cm,
             weight_kg=EXCLUDED.weight_kg, goal=EXCLUDED.goal, diet=EXCLUDED.diet,
             allergies=EXCLUDED.allergies, dislikes=EXCLUDED.dislikes,
             budget_inr=EXCLUDED.budget_inr, target_kcal=EXCLUDED.target_kcal,
             target_protein_g=EXCLUDED.target_protein_g, target_carbs_g=EXCLUDED.target_carbs_g,
             target_fat_g=EXCLUDED.target_fat_g, target_veg_servings=EXCLUDED.target_veg_servings,
             target_fruit_servings=EXCLUDED.target_fruit_servings, updated_at=now()""",
        user_id, int(age_years), sex, int(float(height_cm)), float(weight_kg), goal,
        diet or None, json.dumps(to_list(allergies)), json.dumps(to_list(dislikes)),
        budget, targets.kcal, targets.protein_g, targets.carbs_g, targets.fat_g,
        targets.veg_servings, targets.fruit_servings,
    )
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- actions


@app.post("/import")
async def do_import(request: Request):
    user_id = current_user_id(request)
    if not user_id:
        return RedirectResponse("/")
    response = RedirectResponse("/", status_code=303)
    try:
        result = await import_history(user_id)
        _flash_write(response, {"outcome": f"Imported {result['imported']}, skipped {result['skipped']}."})
    except Exception as error:
        _flash_write(response, {"error": str(error)[:300]})
    return response


@app.post("/recommend")
async def do_recommend(request: Request):
    user_id = current_user_id(request)
    if not user_id:
        return RedirectResponse("/")
    response = RedirectResponse("/", status_code=303)

    try:
        profile = await fetchrow("SELECT * FROM profiles WHERE user_id = $1", user_id)
        if profile is None:
            return RedirectResponse("/onboarding")

        days_in_period, days_elapsed, period_start = month_window()
        nutrition = await nutrition_since(user_id, dt.date.today())
        budget = compute_budget(
            budget_inr=profile["budget_inr"] or 15000,
            spent_inr=await spent_since(user_id, period_start),
            days_in_period=days_in_period,
            days_elapsed=days_elapsed,
        )
        targets = {
            "kcal": profile["target_kcal"],
            "protein_g": profile["target_protein_g"],
            "veg_servings": profile["target_veg_servings"],
            "fruit_servings": profile["target_fruit_servings"],
        }
        counts = nutrition.as_dict()

        token = await access_token_for(user_id)
        _, candidates = await fetch_candidates(
            token,
            queries_for(
                low_protein=counts["protein_g"] < (targets["protein_g"] or 80),
                low_veg=counts["veg_servings"] < (targets["veg_servings"] or 4),
                low_fruit=counts["fruit_servings"] < (targets["fruit_servings"] or 2),
            ),
        )

        result = recommend(
            nutrition=counts,
            targets=targets,
            budget=budget,
            diet=profile["diet"],
            allergies=json.loads(profile["allergies"] or "[]"),
            dislikes=json.loads(profile["dislikes"] or "[]"),
            candidates=candidates,
        )
        await execute(
            "INSERT INTO recommendations (user_id, options, reason) VALUES ($1,$2,$3)",
            user_id,
            json.dumps([o.model_dump() for o in result.options]),
            result.headline,
        )
        _flash_write(response, {"rec": result.model_dump()})
    except Exception as error:
        _flash_write(response, {"error": str(error)[:300]})
    return response


@app.post("/cart")
async def do_cart(
    request: Request,
    spin_id: Annotated[str, Form()],
    quantity: Annotated[int, Form()] = 1,
):
    """Build the cart and show the bill. Places NOTHING.

    This is the screen the docs require before checkout: cart, address and payment
    methods, all seen by a human. `update_cart` REPLACES the cart, so every line goes in
    one call.
    """
    user_id = current_user_id(request)
    if not user_id:
        return RedirectResponse("/")
    response = RedirectResponse("/", status_code=303)

    try:
        token = await access_token_for(user_id)
        async with swiggy_session(token, "instamart") as session:
            im = InstamartOrdering(session)
            addresses = await im.addresses()
            if not addresses:
                raise RuntimeError("no saved Swiggy addresses")
            address = addresses[0]

            await im.update_cart(address["id"], [{"spinId": spin_id, "quantity": quantity}])
            cart = await im.get_cart()
            options = await im.payment_options()

        returned = {str(i.get("spinId")) for i in (cart.get("items") or [])}
        breakdown = cart.get("billBreakdown") or {}
        cod = options.get("cod") or {}
        _flash_write(
            response,
            {
                "cart": {
                    "address_id": address["id"],
                    "total_inr": read_cart_total(cart),
                    "bill_lines": breakdown.get("lineItems") or [],
                    # The server silently drops unavailable lines; surface that rather
                    # than letting the user confirm a basket that is not what they picked.
                    "dropped": [] if spin_id in returned else [spin_id],
                    "cash_available": cod.get("available") is not False,
                    "methods": [
                        {"id": m.get("id"), "label": method_label(m)}
                        for m in (options.get("allMethods") or [])
                    ],
                }
            },
        )
    except Exception as error:
        _flash_write(response, {"error": str(error)[:300]})
    return response


@app.post("/order")
async def do_order(
    request: Request,
    address_id: Annotated[str, Form()],
    max_total_inr: Annotated[int, Form()],
    method: Annotated[str, Form()] = "cash",
):
    """Place the order. NOT idempotent.

    `max_total_inr` is the amount the human agreed to. Instamart pricing is live — a
    surge fee appeared, was renamed and lapsed inside one session — so the total at
    checkout can differ from the confirmation screen. Above the approved figure this
    refuses rather than quietly charging more.
    """
    user_id = current_user_id(request)
    if not user_id:
        return RedirectResponse("/")
    response = RedirectResponse("/", status_code=303)

    try:
        token = await access_token_for(user_id)
        async with swiggy_session(token, "instamart") as session:
            im = InstamartOrdering(session)
            addresses = await im.addresses()
            address = next((a for a in addresses if a["id"] == address_id), None)
            if address is None:
                raise RuntimeError("unknown address")

            # Re-read the live total: the confirmation screen may be stale.
            cart = await im.get_cart()
            total = read_cart_total(cart)
            coords = read_cart_coords(cart)
            if total is None:
                raise RuntimeError("could not read the cart total — refusing to order blind")
            if total > max_total_inr:
                _flash_write(
                    response,
                    {
                        "outcome": f"The cart is now ₹{total:g}, above the ₹{max_total_inr} you "
                        f"approved. Nothing was ordered — review it and confirm again."
                    },
                )
                return response

            result = await im.checkout(
                address["id"],
                UpiQr() if method == "upi-qr" else Cash(),
                user_confirmed=True,
                cart_total=total,
            )

        order = result.order
        await execute(
            """INSERT INTO orders
                 (user_id, source, external_id, ordered_on, total_inr, status, merchant, via_mealwise)
               VALUES ($1,'instamart',$2,$3,$4,$5,'Instamart',true)
               ON CONFLICT (user_id, source, external_id) DO NOTHING""",
            user_id, str(order.get("orderId")), dt.date.today(),
            int(total), str(order.get("status") or ""),
        )

        if result.kind == "placed":
            _flash_write(
                response,
                {"outcome": f"Placed. Order {order.get('orderId')} — pay ₹{total:g} on delivery."},
            )
        else:
            # UPI: hand over the link and stop. Polling for ~18 minutes does not fit in a
            # serverless request.
            _flash_write(
                response,
                {
                    "outcome": f"Order {order.get('orderId')} is awaiting payment.",
                    "pay_link": order.get("bridgeUrl"),
                },
            )
    except Exception as error:
        _flash_write(response, {"error": str(error)[:300]})
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
