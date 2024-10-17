import base64
import functools
import hashlib
import hmac
import json

import frappe
from frappe import _
from shopify.resources import Webhook, Customer  # Added Customer import
from shopify.session import Session

from ecommerce_integrations.shopify.constants import (
    API_VERSION,
    EVENT_MAPPER,
    SETTING_DOCTYPE,
    WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log

import shopify  # Added shopify import


def temp_shopify_session(func):
    """Any function that needs to access Shopify API needs this decorator.
    The decorator starts a temp session that's destroyed when function returns."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # no auth in testing
        if frappe.flags.in_test:
            return func(*args, **kwargs)

        setting = frappe.get_doc(SETTING_DOCTYPE)
        if setting.is_enabled():
            auth_details = (setting.shopify_url, API_VERSION, setting.get_password("password"))

            with Session.temp(*auth_details):
                return func(*args, **kwargs)

    return wrapper


@temp_shopify_session
def get_shopify_customers():
    """Fetch all customers from Shopify, handling pagination."""
    customers = []
    try:
        customer_count = Customer.count()
        pages = (customer_count // 250) + 1
        for page in range(1, pages + 1):
            customers_page = Customer.find(limit=250, page=page)
            customers.extend(customers_page)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'Shopify Customer Fetch Error')
        frappe.throw(f"Error fetching customers from Shopify: {e}")

    # Convert Shopify resources to dictionaries
    return [customer.attributes for customer in customers]


def register_webhooks(shopify_url: str, password: str) -> list[Webhook]:
    """Register required webhooks with Shopify and return registered webhooks."""
    new_webhooks = []

    # Clear all stale webhooks matching current site URL before registering new ones
    unregister_webhooks(shopify_url, password)

    with Session.temp(shopify_url, API_VERSION, password):
        for topic in WEBHOOK_EVENTS:
            webhook = Webhook.create({"topic": topic, "address": get_callback_url(), "format": "json"})

            if webhook.is_valid():
                new_webhooks.append(webhook)
            else:
                create_shopify_log(
                    status="Error",
                    response_data=webhook.to_dict(),
                    exception=webhook.errors.full_messages(),
                )

    return new_webhooks


def unregister_webhooks(shopify_url: str, password: str) -> None:
    """Unregister all webhooks from Shopify that correspond to current site URL."""
    url = get_current_domain_name()

    with Session.temp(shopify_url, API_VERSION, password):
        for webhook in Webhook.find():
            if url in webhook.address:
                webhook.destroy()


def get_current_domain_name() -> str:
    """Get current site domain name, e.g., test.erpnext.com.

    If developer_mode is enabled and localtunnel_url is set in site config, then domain is set to localtunnel_url.
    """
    if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
        return frappe.conf.localtunnel_url
    else:
        return frappe.request.host


def get_callback_url() -> str:
    """Shopify calls this URL when new events occur to subscribed webhooks.

    If developer_mode is enabled and localtunnel_url is set in site config, then callback URL is set to localtunnel_url.
    """
    url = get_current_domain_name()

    return f"https://{url}/api/method/ecommerce_integrations.shopify.connection.store_request_data"


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
    if frappe.request:
        hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")

        _validate_request(frappe.request, hmac_header)

        data = json.loads(frappe.request.data)
        event = frappe.request.headers.get("X-Shopify-Topic")

        process_request(data, event)


def process_request(data, event):
    # Create log
    log = create_shopify_log(method=EVENT_MAPPER[event], request_data=data)

    # Enqueue background job
    frappe.enqueue(
        method=EVENT_MAPPER[event],
        queue="short",
        timeout=300,
        is_async=True,
        **{"payload": data, "request_id": log.name},
    )


def _validate_request(req, hmac_header):
    settings = frappe.get_doc(SETTING_DOCTYPE)
    secret_key = settings.shared_secret

    sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

    if sig != bytes(hmac_header.encode()):
        create_shopify_log(status="Error", request_data=req.data)
        frappe.throw(_("Unverified Webhook Data"))
