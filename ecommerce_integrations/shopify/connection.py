import requests
import json
import frappe
import re

# Other necessary imports
from frappe import _
from .connection import get_shopify_customers

def sync_all_customers():
    """
    Fetches all customers from Shopify and syncs them with ERPNext.
    """
    try:
        customers = get_shopify_customers()
    except Exception as e:
        frappe.log_error(
            f"Failed to fetch customers from Shopify: {frappe.get_traceback()}",
            'Shopify Fetch Customers Error'
        )
        frappe.throw(f"Error fetching customers from Shopify: {e}")

    total_customers = len(customers)
    imported = 0
    failed = 0

    for customer_data in customers:
        customer_id = customer_data.get('id', 'Unknown ID')
        try:
            create_or_update_customer(customer_data)
            imported += 1
        except Exception as e:
            failed += 1
            frappe.log_error(
                f"Error syncing customer {customer_id}: {frappe.get_traceback()}",
                'Shopify Customer Import Error'
            )
            # Continue with the next customer
            continue

    frappe.msgprint(
        _(f"Customer synchronization completed: {imported} imported, {failed} failed out of {total_customers}."),
        title=_("Synchronization Summary"),
        indicator="green" if failed == 0 else "orange"
    )

def create_or_update_customer(customer_data):
    """
    Creates a new customer or updates an existing one based on the Shopify Customer ID.
    """
    custom_shopify_customer_id = str(customer_data.get('id'))
    first_name = customer_data.get('first_name') or ''
    last_name = customer_data.get('last_name') or ''
    customer_name = (first_name + ' ' + last_name).strip() or customer_data.get('email')
    email = customer_data.get('email')
    phone = customer_data.get('phone')
    customer_group = 'All Customer Groups'
    territory = 'All Territories'

    # Check if a customer with the same Shopify Customer ID already exists
    existing_customer = frappe.db.get_value('Customer', {'shopify_customer_id': custom_shopify_customer_id}, 'name')

    if existing_customer:
        # Update existing customer
        customer = frappe.get_doc('Customer', existing_customer)
        customer.customer_name = customer_name
        customer.email_id = email
        customer.phone = phone
        customer.customer_group = customer_group
        customer.territory = territory
        customer.save()
    else:
        # Create new customer
        customer = frappe.get_doc({
            'doctype': 'Customer',
            'customer_name': customer_name,
            'shopify_customer_id': custom_shopify_customer_id,
            'email_id': email,
            'phone': phone,
            'customer_group': customer_group,
            'territory': territory,
        })
        customer.insert()

    frappe.db.commit()

def get_shopify_customers():
    """
    Fetches all customers from Shopify and returns them.
    Implements pagination to fetch all records.
    """
    settings = frappe.get_doc("Shopify Settings")
    base_url = f"https://{settings.shopify_store_name}.myshopify.com/admin/api/{settings.api_version}/customers.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": settings.password,
    }
    
    customers = []
    params = {"limit": 250}
    response = requests.get(base_url, headers=headers, params=params)

    while response.status_code == 200:
        data = response.json()
        customers.extend(data.get("customers", []))

        # Check for the next page info to handle pagination
        link_header = response.headers.get("Link")
        if link_header and 'rel="next"' in link_header:
            match = re.search(r'<(.*?)>; rel="next"', link_header)
            if match:
                next_url = match.group(1)
                response = requests.get(next_url, headers=headers)
            else:
                break
        else:
            break

    if response.status_code != 200:
        frappe.log_error(f"Failed to fetch customers from Shopify: {response.text}", 'Shopify Fetch Customers Error')
        frappe.throw(f"Error fetching customers from Shopify: {response.text}")

    return customers
