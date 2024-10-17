# shopify_requests.py

import frappe
import requests

def get_shopify_customers():
    shopify_settings = frappe.get_doc('Shopify Setting')
    base_url = f"https://{shopify_settings.shopify_url}/admin/api/2023-01/customers.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": shopify_settings.get_password("password")
    }
    customers = []
    params = {'limit': 250}
    url = base_url

    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            customers.extend(data.get('customers', []))
            # Handle pagination
            link_header = response.headers.get('Link')
            url = get_next_page_url(link_header)
        else:
            frappe.throw(f"Error fetching customers from Shopify: {response.content}")
    return customers

def get_next_page_url(link_header):
    if not link_header:
        return None
    for link in link_header.split(','):
        if 'rel="next"' in link:
            next_url = link[link.find('<') + 1:link.find('>')]
            return next_url
    return None
