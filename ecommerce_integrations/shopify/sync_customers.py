# sync_customers.py

import frappe
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
    existing_customer_name = frappe.db.get_value(
        'Customer',
        {'custom_shopify_customer_id': custom_shopify_customer_id},
        'name'
    )

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
    customer.phone = phone
    customer.custom_shopify_customer_id = custom_shopify_customer_id

    # Save the customer record
    customer.flags.ignore_mandatory = True
    customer.save(ignore_permissions=True)

    # Handle addresses and contact details
    handle_customer_addresses(customer, customer_data)
    handle_customer_contacts(customer, customer_data)

def handle_customer_addresses(customer, customer_data):
    """
    Creates or updates addresses for the given customer.
    """
    addresses = customer_data.get('addresses', [])
    for address_data in addresses:
        create_or_update_address(customer, address_data)

def create_or_update_address(customer, address_data):
    """
    Creates or updates an address based on the Shopify Address ID.
    """
    try:
        shopify_address_id = str(address_data.get('id'))

        # Check if the address already exists
        existing_address_name = frappe.db.get_value(
            'Address',
            {'shopify_address_id': shopify_address_id},
            'name'
        )

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
        frappe.log_error(
            f"Error importing address for customer {customer.name}: {frappe.get_traceback()}",
            'Shopify Address Import Error'
        )
        frappe.throw(f"Error importing address: {e}")

def handle_customer_contacts(customer, customer_data):
    """
    Creates or updates a contact based on the phone number.
    """
    phone = customer_data.get('phone')

    try:
        if not phone:
            frappe.log_error(
                f"Customer {customer.name} has no phone number. Skipping contact creation.",
                "Shopify Contact Import Warning"
            )
            return

        # Find existing contact by phone number only
        existing_contact_name = frappe.db.get_value('Contact', {
            'phone': phone,
        }, 'name')

        contact_fields = {
            'doctype': 'Contact',
            'first_name': customer_data.get('first_name') or customer.customer_name,
            'last_name': customer_data.get('last_name') or '',
            'phone': phone,
            # Omitting 'links' to skip linkage
        }

        if customer_data.get('email'):
            contact_fields['email_id'] = customer_data.get('email')

        if existing_contact_name:
            # Update existing contact without modifying links
            contact = frappe.get_doc('Contact', existing_contact_name)
            contact.update(contact_fields)
        else:
            # Create new contact without setting links
            contact = frappe.get_doc(contact_fields)

        contact.flags.ignore_mandatory = True
        contact.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(
            f"Error importing contact for customer {customer.name}: {frappe.get_traceback()}",
            'Shopify Contact Import Error'
        )
        frappe.throw(f"Error importing contact: {e}")
