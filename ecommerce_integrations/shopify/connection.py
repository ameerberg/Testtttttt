import re

# Updating the function get_shopify_customers() in connection.py to implement pagination

# Extract the function definition from connection.py
if connection_content:
    # Using a regular expression to find the 'get_shopify_customers' function in the connection.py content
    match = re.search(r'def get_shopify_customers\([\s\S]*?\):([\s\S]*?)(?=\ndef |\Z)', connection_content)
    if match:
        original_function = match.group(0)
    else:
        original_function = None

# Prepare the new implementation of get_shopify_customers with pagination
if original_function:
    # Adding pagination handling to the function
    updated_function = """
def get_shopify_customers():
    \"\"\"
    Fetches all customers from Shopify and returns them.
    Implements pagination to fetch all records.
    \"\"\"
    import requests
    import json
    import frappe

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
"""

    # Replacing the original function with the updated one in the file content
    updated_connection_content = connection_content.replace(original_function, updated_function)

    # Saving the updated content back to the connection.py file
    connection_file_path = os.path.join(extraction_path, 'Testtttttt-develop/ecommerce_integrations/shopify/connection.py')
    with open(connection_file_path, 'w') as file:
        file.write(updated_connection_content)

    # Indicate that the update was successful
    updated_status = "Pagination successfully implemented in get_shopify_customers function."
else:
    updated_status = "Failed to locate get_shopify_customers function for updating."

updated_status
