# sync_customers.py

import requests
import frappe
from frappe import _
from urllib.parse import urlparse, parse_qs
import time

# Constants
CUSTOMER_LIMIT_PER_REQUEST = 250  # Shopify's maximum limit per request
BATCH_SIZE = 1000  # Number of customers to process per batch
RATE_LIMIT_THRESHOLD = 5  # Threshold to pause API calls when approaching rate limit

def sync_all_customers():
    """
    Fetches all customers from Shopify and syncs them with ERPNext.
    Handles pagination, rate limits, and processes customers in batches.
    """
    # Retrieve Shopify settings from ERPNext
    shopify_settings = frappe.get_single('Shopify Settings')  # Adjust DocType name if different

    # Validate Shopify settings
    if not shopify_settings.store_name or not shopify_settings.access_token:
        frappe.log_error(
            "Shopify Store Name or Access Token is not configured in Shopify Settings.",
            "Shopify Configuration Error"
        )
        return

    store_name = shopify_settings.store_name.strip()
    access_token = shopify_settings.access_token.strip()

    # Ensure store_name is correctly formatted
    if not store_name.endswith('.myshopify.com'):
        store_domain = f"{store_name}.myshopify.com"
    else:
        store_domain = store_name

    customers = []
    has_next_page = True
    page_info = None  # Cursor for pagination
    processed = 0
    total_customers = 0

    frappe.logger().info("Starting Shopify Customer Sync.")

    while has_next_page:
        response = get_shopify_customers(store_domain, access_token, page_info=page_info)
        if response.status_code == 200:
            fetched_customers = response.json().get('customers', [])
            customers.extend(fetched_customers)
            total_customers += len(fetched_customers)
            frappe.logger().info(f"Fetched {len(fetched_customers)} customers. Total fetched: {total_customers}")

            # Parse the 'Link' header to get the next page_info
            link_header = response.headers.get('Link', '')
            if 'rel="next"' in link_header:
                next_link = [link for link in link_header.split(',') if 'rel="next"' in link]
                if next_link:
                    next_url = next_link[0].split(';')[0].strip('<> ')
                    parsed_url = urlparse(next_url)
                    query_params = parse_qs(parsed_url.query)
                    page_info = query_params.get('page_info', [None])[0]
                else:
                    has_next_page = False
            else:
                has_next_page = False

            # Handle rate limiting
            api_call_limit = response.headers.get('X-Shopify-Shop-Api-Call-Limit')
            if api_call_limit:
                try:
                    current, max_limit = map(int, api_call_limit.split('/'))
                    remaining = max_limit - current
                    frappe.logger().info(f"Shopify API Call Limit: {current}/{max_limit}. Remaining: {remaining}")
                    if remaining < RATE_LIMIT_THRESHOLD:
                        frappe.logger().info("Approaching Shopify API rate limit. Sleeping for 1 second.")
                        time.sleep(1)  # Sleep to avoid hitting rate limits
                except Exception as e:
                    frappe.log_error(frappe.get_traceback(), "Shopify API Call Limit Parsing Error")
        else:
            # Truncate error message to 140 characters for the 'Title' field
            error_title = f"Shopify API Error: {response.status_code} - {response.text}"[:140]
            frappe.log_error(error_title, "Shopify Customer Fetch Error")
            has_next_page = False  # Stop the loop on error

    frappe.logger().info(f"Total customers fetched: {len(customers)}. Starting processing.")

    # Process customers in batches
    total_customers = len(customers)
    while processed < total_customers:
        batch = customers[processed:processed + BATCH_SIZE]
        frappe.logger().info(f"Processing batch: {processed + 1} to {processed + len(batch)}")
        for customer_data in batch:
            try:
                create_or_update_customer(customer_data, store_domain, access_token)
            except Exception as e:
                # Truncate error message to 140 characters for the 'Title' field
                error_title = f"Shopify Customer Import Error for ID {customer_data.get('id')}: {str(e)}"[:140]
                frappe.log_error(error_title, "Shopify Customer Import Error")
                continue  # Continue with the next customer
        processed += BATCH_SIZE
        frappe.db.commit()  # Commit after each batch to ensure data persistence
        frappe.logger().info(f"Processed {processed} / {total_customers} customers.")

    frappe.logger().info("Completed syncing all Shopify customers.")

def get_shopify_customers(store_domain, access_token, page_info=None):
    """
    Fetches customers from Shopify with optional pagination using access token.
    """
    url = f"https://{store_domain}/admin/api/2023-10/customers.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json"
    }
    params = {
        'limit': CUSTOMER_LIMIT_PER_REQUEST
    }
    if page_info:
        params['page_info'] = page_info

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)  # Added timeout for network issues
        return response
    except requests.exceptions.RequestException as e:
        # Truncate error message to 140 characters for the 'Title' field
        error_title = f"Shopify API Request Exception: {str(e)}"[:140]
        frappe.log_error(error_title, "Shopify API Request Exception")
        return frappe._dict({'status_code': 500, 'text': str(e), 'headers': {}})

def create_or_update_customer(customer_data, store_domain, access_token):
    """
    Creates a new customer or updates an existing one based on the Shopify Customer ID.
    Identifies contacts by phone number.
    """
    custom_shopify_customer_id = str(customer_data.get('id'))
    first_name = customer_data.get('first_name') or ''
    last_name = customer_data.get('last_name') or ''
    customer_name = (first_name + ' ' + last_name).strip() or customer_data.get('email')
    email = customer_data.get('email')
    phone = customer_data.get('phone')
    customer_group = 'All Customer Groups'  # Adjust as needed
    territory = 'All Territories'  # Adjust as needed

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
            'customer_type': 'Individual',  # Adjust as needed
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
    handle_customer_addresses(customer, customer_data, store_domain, access_token)
    handle_customer_contacts(customer, customer_data)

def handle_customer_addresses(customer, customer_data, store_domain, access_token):
    """
    Creates or updates addresses for the given customer.
    """
    addresses = customer_data.get('addresses', [])
    for address_data in addresses:
        create_or_update_address(customer, address_data, store_domain, access_token)

def create_or_update_address(customer, address_data, store_domain, access_token):
    """
    Creates or updates an address based on the Shopify Address ID.
    """
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
        # Truncate error message to 140 characters for the 'Title' field
        error_title = f"Shopify Address Import Error: {str(e)}"[:140]
        frappe.log_error(error_title, "Shopify Address Import Error")
        frappe.throw(f"Error importing address: {e}")

def handle_customer_contacts(customer, customer_data):
    """
    Creates or updates a contact based on the phone number.
    """
    phone = customer_data.get('phone')

    try:
        if not phone:
            # Truncate warning message to 140 characters
            warning_message = f"Customer {customer.name} has no phone number. Skipping contact creation."[:140]
            frappe.log_error(warning_message, "Shopify Contact Import Warning")
            return

        # Find existing contact by phone number only
        existing_contact_name = frappe.db.get_value('Contact', {
            'phone': phone,
        }, 'name')

        contact_fields = {
            'doctype': 'Contact',
            'first_name': customer_data.get('first_name') or customer.customer_name,
            'last_name': customer_data.get('last_name'),
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
        # Truncate error message to 140 characters for the 'Title' field
        error_title = f"Shopify Contact Import Error for Phone {phone}: {str(e)}"[:140]
        frappe.log_error(error_title, "Shopify Contact Import Error")
        frappe.throw(f"Error importing contact: {e}")
