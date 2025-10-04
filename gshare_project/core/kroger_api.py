import requests
from decouple import config

def get_kroger_token():
    client_id = config('KROGER_CLIENT_ID')
    client_secret = config('KROGER_CLIENT_SECRET')
    token_url = 'https://api.kroger.com/v1/connect/oauth2/token'
    
    auth_header = requests.auth._basic_auth_str(client_id, client_secret)
    response = requests.post(
        token_url,
        headers={'Authorization': auth_header, 'Content-Type': 'application/x-www-form-urlencoded'},
        data={'grant_type': 'client_credentials', 'scope': 'product.compact'}
    )
    
    if response.status_code == 200:
        return response.json()['access_token']
    else:
        print(f"Error getting token: {response.text}")
        return None

def find_kroger_locations_by_zip(zip_code, radius=10):
    """Finds Kroger-owned stores near a given zip code."""
    token = get_kroger_token()
    if not token:
        return []

    locations_url = (
        f'https://api.kroger.com/v1/locations'
        f'?filter.zipCode.near={zip_code}'
        f'&filter.radiusInMiles={radius}'
    )
    
    response = requests.get(
        locations_url,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    )

    if response.status_code == 200:
        return response.json()['data']
    else:
        print(f"Error finding locations: {response.text}")
        return []

def search_kroger_products(location_id, search_term):
    token = get_kroger_token()
    if not token:
        return []

    product_url = (
        f'https://api.kroger.com/v1/products'
        f'?filter.locationId={location_id}'
        f'&filter.term={search_term}'
    )

    response = requests.get(
        product_url,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    )

    if response.status_code == 200:
        return response.json()['data']
    else:
        print(f"Error searching products: {response.text}")
        return []