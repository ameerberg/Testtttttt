# connection.py

import base64
import functools
import hashlib
import hmac
import json
import requests

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
    """Fetch all customers from Shopify using cursor-based pagination with requests."""
    customers = []
    try:
        settings = frappe.get_doc(SETTING_DOCTYPE)
        shopify_url = settings.shopify_url
        password = settings.get_password("password")
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": password
        }
        params = {'limit': 250}
        last_id = None
        while True:
            if last_id:
                params['since_id'] = last_id
            frappe.logger().debug(f"Fetching customers with params: {params}")
            response = requests.get(
                f"https://{shopify_url}/admin/api/{API_VERSION}/customers.json",
                headers=headers,
                params=params
            )
            frappe.logger().debug(f"Shopify Response Status: {response.status_code}")
            if response.status_code != 200:
                frappe.log_error(response.text, 'Shopify Customer Fetch Error')
                frappe.throw(f"Error fetching customers from Shopify: {response.text}")
            data = response.json()
            customers_page = data.get('customers', [])
            if not customers_page:
                break
            customers.extend(customers_page)
            last_id = customers_page[-1].get('id')
            if len(customers_page) < 250:
                break
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'Shopify Customer Fetch Error')
        frappe.throw(f"Error fetching customers from Shopify: {e}")
    return customers

def register_s(shopify_url: str, password: str) -> list:
    """Register required s with Shopify and return registered s."""
    new_s = []

    # Clear all stale s matching current site URL before registering new ones
    unregister_s(shopify_url, password)

    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": password
    }

    for topic in _EVENTS:
        payload = {
            "": {
                "topic": topic,
                "address": get_callback_url(),
                "format": "json"
            }
        }
        response = requests.post(
            f"https://{shopify_url}/admin/api/{API_VERSION}/s.json",
            headers=headers,
            data=json.dumps(payload)
        )
        if response.status_code == 201:
             = response.json().get('')
            new_s.append()
        else:
            create_shopify_log(
                status="Error",
                response_data=response.text,
                exception=response.json().get('errors')
            )

    return new_s

def unregister_s(shopify_url: str, password: str) -> None:
    """Unregister all webhooks from Shopify that correspond to current site URL."""
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": password
    }
    response = requests.get(
        f"https://{shopify_url}/admin/api/{API_VERSION}/webhooks.json",
        headers=headers
    )
    if response.status_code == 200:
        webhooks = response.json().get('webhooks', [])
        for webhook in webhooks:
            address = webhook.get('address')
            if get_current_domain_name() in address:
                webhook_id = webhook.get('id')
                delete_response = requests.delete(
                    f"https://{shopify_url}/admin/api/{API_VERSION}/webhooks/{webhook_id}.json",
                    headers=headers
                )
                if delete_response.status_code != 200:
                    create_shopify_log(
                        status="Error",
                        response_data=delete_response.text,
                        exception=delete_response.json().get('errors')
                    )
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
