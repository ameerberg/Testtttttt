# sync_customers.py

import frappe
from frappe import _
from .connection import get_shopify_customers

def sync_all_customers():
    customers = get_shopify_customers()
    for customer_data in customers:
        create_or_update_customer(customer_data)

def create_or_update_customer(customer_data):
    custom_shopify_customer_id = str(customer_data.get('id'))
    customer_name = (customer_data.get('first_name', '') + ' ' + customer_data.get('last_name', '')).strip() or customer_data.get('email')
    email = customer_data.get('email')
    customer_group = 'All Customer Groups'
    territory = 'All Territories'

    # Check if a customer with the same Shopify Customer ID already exists
    existing_customer_name = frappe.db.get_value('Customer', {'custom_shopify_customer_id': custom_shopify_customer_id}, 'name')

    if existing_customer_name:
        # Update existing customer
        customer = frappe.get_doc('Customer', existing_customer_name)
    else:
        # Create new customer
        customer = frappe.get_doc({
            'doctype': 'Customer',
            'customer_name': customer_name,
            'customer_type': 'Individual',
            'customer_group': customer_group,
            'territory': territory,
            'custom_shopify_customer_id': custom_shopify_customer_id
        })

    # Update customer fields
    customer.customer_name = customer_name
    customer.email_id = email
    customer.custom_shopify_customer_id = custom_shopify_customer_id

    # Save the customer record
    customer.flags.ignore_mandatory = True
    customer.save(ignore_permissions=True)

    # Handle addresses and contact details
    handle_customer_addresses(customer, customer_data)
    handle_customer_contacts(customer, customer_data)

def handle_customer_addresses(customer, customer_data):
    addresses = customer_data.get('addresses', [])
    for address_data in addresses:
        create_or_update_address(customer, address_data)

def create_or_update_address(customer, address_data):
    try:
        shopify_address_id = str(address_data.get('id'))

        # Check if the address already exists
        existing_address_name = frappe.db.get_value('Address', {'shopify_address_id': shopify_address_id}, 'name')

        address_title = customer.customer_name
        address_type = 'Billing' if address_data.get('default') else 'Shipping'

        # Safely get address fields, defaulting to empty strings if None
        address_line1 = address_data.get('address1') or ''
        address_line2 = address_data.get('address2') or ''
        city = address_data.get('city') or ''
        state = address_data.get('province') or ''
        pincode = address_data.get('zip') or ''
        country = address_data.get('country') or ''
        phone = address_data.get('phone') or ''

        address_fields = {
            'doctype': 'Address',
            'shopify_address_id': shopify_address_id,
            'address_title': address_title,
            'address_type': address_type,
            'address_line1': address_line1,
            'address_line2': address_line2,
            'city': city,
            'state': state,
            'pincode': pincode,
            'country': country,
            'phone': phone,
            'email_id': customer.email_id,
            'links': [{
                'link_doctype': 'Customer',
                'link_name': customer.name
            }]
        }

        if existing_address_name:
            # Update existing address
            address = frappe.get_doc('Address', existing_address_name)
            address.update(address_fields)
        else:
            # Create new address
            address = frappe.get_doc(address_fields)

        address.flags.ignore_mandatory = True
        address.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'Shopify Address Import Error')
        frappe.throw(f"Error importing address: {e}")

def handle_customer_contacts(customer, customer_data):
    email = customer_data.get('email')
    first_name = customer_data.get('first_name')
    last_name = customer_data.get('last_name')
    phone = customer_data.get('phone')

    try:
        # Check if contact already exists using custom fields
        existing_contact_name = frappe.db.get_value('Contact', {
            'email_id': email,
            'custom_link_doctype': 'Customer',
            'custom_link_name': customer.name
        }, 'name')

        contact_fields = {
            'doctype': 'Contact',
            'first_name': first_name or customer.customer_name,
            'last_name': last_name,
            'email_id': email,
            'phone': phone,
            'links': [{
                'link_doctype': 'Customer',
                'link_name': customer.name
            }]
        }

        if existing_contact_name:
            # Update existing contact
            contact = frappe.get_doc('Contact', existing_contact_name)
            contact.update(contact_fields)
        else:
            # Create new contact
            contact = frappe.get_doc(contact_fields)

        contact.flags.ignore_mandatory = True
        contact.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), 'Shopify Contact Import Error')
        frappe.throw(f"Error importing contact: {e}")
