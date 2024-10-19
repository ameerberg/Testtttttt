from typing import Optional

import frappe
from frappe import _
from shopify.resources import Customer
import phonenumbers

from ecommerce_integrations.shopify.connection import temp_shopify_session, get_shopify_customers
from ecommerce_integrations.shopify.constants import SETTING_DOCTYPE, MODULE_NAME
from ecommerce_integrations.shopify.utils import create_shopify_log


class ShopifyCustomer:
    def __init__(self, customer_id: str):
        self.customer_id = str(customer_id)
        self.setting = frappe.get_doc(SETTING_DOCTYPE)

        if not self.setting.is_enabled():
            frappe.throw(_("Cannot create Shopify customer when integration is disabled."))

    def is_synced(self) -> bool:
        return frappe.db.exists("Customer", {"shopify_customer_id": self.customer_id})

    def get_erpnext_customer(self):
        return frappe.get_doc("Customer", {"shopify_customer_id": self.customer_id})

    @temp_shopify_session
    def sync_customer(self):
        if not self.is_synced():
            shopify_customer = Customer.find(self.customer_id)
            customer_dict = shopify_customer.to_dict()
            self._make_customer(customer_dict)

    def _make_customer(self, customer_dict):
        customer_name = f"{customer_dict.get('first_name', '')} {customer_dict.get('last_name', '')}".strip()
        email = customer_dict.get('email')
        phone = customer_dict.get('phone')

        customer_doc = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name or email or phone,
            "shopify_customer_id": self.customer_id,
            "email_id": email,
            "phone": phone,
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
        })

        customer_doc.flags.ignore_mandatory = True
        customer_doc.insert(ignore_permissions=True)

        create_shopify_log(
            status="Success",
            request_data=customer_dict,
            message=f"Customer {customer_name} imported successfully.",
            method="sync_customer",
        )


@frappe.whitelist()
def import_all_customers():
    try:
        # Corrected to properly reference the class method ShopifyCustomer.sync_all_customers
        enqueue(ShopifyCustomer.sync_all_customers, queue='long', timeout=6000)
        frappe.msgprint(_("Customer import has been initiated in the background."))
    except Exception as e:
        frappe.log_error(message=str(e), title="Shopify Customer Import Error")
        frappe.throw(_("An error occurred while importing customers: {0}").format(str(e)))


@temp_shopify_session
def sync_all_customers():
    """Fetches and syncs all customers from Shopify."""
    try:
        customers = get_shopify_customers()  # Initial fetch
        total_customers = len(customers)
        imported = 0
        failed = 0

        for customer_data in customers:
            customer_id = customer_data.get('id', 'Unknown ID')
            try:
                customer_instance = ShopifyCustomer(customer_id)
                customer_instance.sync_customer()
                imported += 1
            except Exception as e:
                failed += 1
                frappe.log_error(
                    f"Error syncing customer {customer_id}: {frappe.get_traceback()}",
                    'Shopify Customer Import Error'
                )
                continue

        frappe.msgprint(
            _(f"Customer synchronization completed: {imported} imported, {failed} failed out of {total_customers}."),
            title=_("Synchronization Summary"),
            indicator="green" if failed == 0 else "orange"
        )

    except Exception as e:
        frappe.log_error(f"Failed to fetch customers from Shopify: {frappe.get_traceback()}", 'Shopify Customer Sync Error')
        frappe.throw(f"Error syncing customers from Shopify: {e}")


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

        existing_address_name = frappe.db.get_value(
            'Address', {'shopify_address_id': shopify_address_id}, 'name'
        )

        address_fields = {
            'doctype': 'Address',
            'shopify_address_id': shopify_address_id,
            'address_title': customer.customer_name,
            'address_type': 'Billing' if address_data.get('default') else 'Shipping',
            'address_line1': address_data.get('address1') or '',
            'address_line2': address_data.get('address2') or '',
            'city': address_data.get('city') or '',
            'state': address_data.get('province') or '',
            'pincode': address_data.get('zip') or '',
            'country': address_data.get('country') or '',
            'phone': address_data.get('phone') or '',
            'email_id': customer.email_id,
            'links': [{
                'link_doctype': 'Customer',
                'link_name': customer.name
            }]
        }

        if existing_address_name:
            address = frappe.get_doc('Address', existing_address_name)
            address.update(address_fields)
        else:
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

        try:
            parsed_phone = phonenumbers.parse(phone, "US")
            phone = phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            frappe.log_error(f"Invalid phone number format for customer {customer.name}: {phone}", "Shopify Contact Import Warning")
            return

        existing_contact_name = frappe.db.get_value('Contact', {'phone': phone}, 'name')

        contact_fields = {
            'doctype': 'Contact',
            'first_name': customer_data.get('first_name') or customer.customer_name,
            'last_name': customer_data.get('last_name') or '',
            'phone': phone,
            'links': [{'link_doctype': 'Customer', 'link_name': customer.name}]
        }

        if customer_data.get('email'):
            contact_fields['email_id'] = customer_data.get('email')

        if existing_contact_name:
            contact = frappe.get_doc('Contact', existing_contact_name)
            contact.update(contact_fields)
        else:
            contact = frappe.get_doc(contact_fields)

        contact.flags.ignore_mandatory = True
        contact.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(
            f"Error importing contact for customer {customer.name}: {frappe.get_traceback()}",
            'Shopify Contact Import Error'
        )
        frappe.throw(f"Error importing contact: {e}")
