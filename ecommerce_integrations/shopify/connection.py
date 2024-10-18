# Updated get_shopify_customers function to handle pagination
import functools
import hmac
import hashlib
import base64
import json
import requests
import time

import frappe
from frappe import _
from shopify.session import Session
from shopify.resources import Webhook, Customer

from ecommerce_integrations.shopify.constants import (
    API_VERSION,
    EVENT_MAPPER,
    SETTING_DOCTYPE,
    WEBHOOK_EVENTS,
)
from ecommerce_integrations.shopify.utils import create_shopify_log


def temp_shopify_session(func):
    """Decorator to manage Shopify API session."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if frappe.flags.in_test:
            return func(*args, **kwargs)

        setting = frappe.get_doc(SETTING_DOCTYPE)
        if setting.is_enabled():
            shopify_url = setting.shopify_url
            api_version = API_VERSION
            password = setting.get_password("password")

            # Debug: Log individual components
            frappe.logger().debug(f"Shopify URL: {shopify_url}")
            frappe.logger().debug(f"API Version: {api_version}")
            frappe.logger().debug(f"Password Retrieved: {'Yes' if password else 'No'}")

            if not shopify_url or not password:
                frappe.log_error(
                    frappe.get_traceback(),
                    'Shopify Auth Error: Missing shopify_url or password'
                )
                frappe.throw(_("Shopify URL or Password is not set correctly."))

            auth_details = (shopify_url, api_version, password)

            with Session.temp(*auth_details):
                return func(*args, **kwargs)
        else:
            frappe.throw(_("Shopify integration is not enabled."))

    return wrapper


@temp_shopify_session
def get_shopify_customers():
    """
    Fetch all customers from Shopify using cursor-based pagination with requests.
    """
    customers = []
    settings = frappe.get_doc(SETTING_DOCTYPE)
    shopify_url = settings.shopify_url
    password = settings.get_password("password")
    endpoint = f"https://{shopify_url}/admin/api/{API_VERSION}/customers.json"

    params = {
        "limit": 250
    }
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": password
    }

    while True:
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            frappe.log_error(
                f"Error fetching customers from Shopify: {response.text}",
                "Shopify Customer Fetch Error"
            )
            frappe.throw(f"Error fetching customers from Shopify: {response.status_code}")

        data = response.json().get("customers", [])
        customers.extend(data)

        # Check for pagination link in headers (Link header for cursor-based pagination)
        if "Link" in response.headers:
            links = response.headers["Link"].split(",")
            next_link = None
            for link in links:
                if 'rel="next"' in link:
                    next_link = link.split(";")[0].strip(" <>")
                    break
            if next_link:
                endpoint = next_link
            else:
                break
        else:
            break

    return customers


def get_shopify_webhooks():
    """Fetch webhooks from Shopify and return response."""
    settings = frappe.get_doc(SETTING_DOCTYPE)
    shopify_url = settings.shopify_url
    password = settings.get_password("password")
    endpoint = f"https://{shopify_url}/admin/api/{API_VERSION}/webhooks.json"

    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": password
    }

    response = requests.get(endpoint, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        frappe.log_error(response.text, 'Shopify Fetch Webhooks Error')
        frappe.throw(f"Error fetching webhooks from Shopify: {response.text}")


def unregister_shopify_webhooks():
    """Unregister all webhooks from Shopify."""
    settings = frappe.get_doc(SETTING_DOCTYPE)
    shopify_url = settings.shopify_url
    password = settings.get_password("password")
    endpoint = f"https://{shopify_url}/admin/api/{API_VERSION}/webhooks.json"

    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": password
    }

    response = requests.get(endpoint, headers=headers)
    if response.status_code == 200:
        webhooks = response.json().get("webhooks", [])
        for webhook in webhooks:
            delete_endpoint = f"{endpoint}/{webhook['id']}.json"
            delete_response = requests.delete(delete_endpoint, headers=headers)
            if delete_response.status_code != 200:
                frappe.log_error(
                    delete_response.text, 'Shopify Unregister Webhooks Error'
                )
                frappe.throw(f"Error unregistering webhook {webhook['id']}: {delete_response.text}")
    else:
        frappe.log_error(response.text, 'Shopify Unregister Webhooks Error')
        frappe.throw(f"Error fetching webhooks from Shopify: {response.text}")


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
